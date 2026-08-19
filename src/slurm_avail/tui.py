"""Interactive curses application."""

from __future__ import annotations

import concurrent.futures
import contextlib
import copy
import curses
import math
import time
from pathlib import Path

from .collect import merge_cluster_refresh, submit_cluster_refreshes
from .config import (
    SETTING_FIELDS,
    AppConfig,
    load_config,
    save_config,
    validate_config,
)
from .config_views import (
    config_legend_lines,
    config_table,
    confirm_prompt,
    edit_cluster_dialog,
    prompt_text,
)
from .constants import (
    CLUSTER_WIDTH,
    FORECAST_RESOLUTIONS,
    GAP,
    LEGEND_WIDTH,
    LOGIN_TABLE_WIDTH,
    VIEWS,
    Line,
)
from .forecast_views import (
    forecast_legend_lines,
    forecast_node_label_width,
    forecast_table,
)
from .jobs_views import (
    jobs_for_scope,
    jobs_legend_lines,
    jobs_table,
    sticky_jobs_headers,
)
from .node_views import (
    cluster_body,
    cluster_header,
    filesystem_legend_lines,
    filesystem_table,
    filesystem_table_width,
    legend_lines,
    login_legend_lines,
    login_table,
)
from .output import placeholder_clusters
from .text import clipped_line, tab_line, visible_length


def init_colors() -> dict[str, int]:
    curses.start_color()
    with contextlib.suppress(curses.error):
        curses.use_default_colors()

    if curses.COLORS >= 256:
        colors = {
            "free": 82,
            "busy": 244,
            "cpu": 45,
            "memory": 213,
            "drained": 203,
            "reserved": 220,
            "running": 39,
            "exclusive": 141,
            "offline": 240,
            "mine": 208,
            "mine_reserved": 51,
        }
        forecast_background = 238
        forecast_colors = {
            "forecast_empty": 244,
            "forecast_reserved": 220,
            "forecast_running": 39,
            "forecast_overlap": 141,
            "forecast_mine_usage": 208,
            "forecast_mine_reserved": 51,
            "forecast_mine_overlap": 201,
        }
    else:
        colors = {
            "free": curses.COLOR_GREEN,
            "busy": curses.COLOR_WHITE,
            "cpu": curses.COLOR_CYAN,
            "memory": curses.COLOR_MAGENTA,
            "drained": curses.COLOR_RED,
            "reserved": curses.COLOR_YELLOW,
            "running": curses.COLOR_CYAN,
            "exclusive": curses.COLOR_BLUE,
            "offline": curses.COLOR_WHITE,
            "mine": curses.COLOR_YELLOW,
            "mine_reserved": curses.COLOR_CYAN,
        }
        forecast_background = curses.COLOR_BLACK
        forecast_colors = {
            "forecast_empty": curses.COLOR_WHITE,
            "forecast_reserved": curses.COLOR_YELLOW,
            "forecast_running": curses.COLOR_CYAN,
            "forecast_overlap": curses.COLOR_MAGENTA,
            "forecast_mine_usage": curses.COLOR_YELLOW,
            "forecast_mine_reserved": curses.COLOR_CYAN,
            "forecast_mine_overlap": curses.COLOR_MAGENTA,
        }

    pairs: dict[str, int] = {"normal": curses.A_NORMAL, "title": curses.A_BOLD}
    for pair_number, (name, color) in enumerate(colors.items(), start=1):
        curses.init_pair(pair_number, color, -1)
        pairs[name] = curses.color_pair(pair_number)
    pair_number = len(colors) + 1
    for name, color in forecast_colors.items():
        curses.init_pair(pair_number, color, forecast_background)
        pairs[name] = curses.color_pair(pair_number)
        pair_number += 1
    pairs["busy"] |= curses.A_DIM
    pairs["offline"] |= curses.A_DIM
    pairs["mine"] |= curses.A_BOLD
    pairs["mine_reserved"] |= curses.A_BOLD
    pairs["forecast_mine_usage"] |= curses.A_BOLD
    pairs["forecast_mine_reserved"] |= curses.A_BOLD
    pairs["forecast_mine_overlap"] |= curses.A_BOLD
    pairs["title"] |= pairs["cpu"]
    pairs["selected"] = pairs["title"] | curses.A_REVERSE
    return pairs


def draw_spans(
    screen: curses.window,
    y: int,
    canvas_x: int,
    spans: Line,
    horizontal_offset: int,
    screen_width: int,
    styles: dict[str, int],
) -> None:
    position = canvas_x
    visible_start = horizontal_offset
    visible_end = horizontal_offset + screen_width

    for text, style_name in spans:
        span_start = position
        span_end = span_start + len(text)
        position = span_end
        if span_end <= visible_start or span_start >= visible_end:
            continue

        start = max(span_start, visible_start)
        end = min(span_end, visible_end)
        fragment = text[start - span_start : end - span_start]
        x = start - visible_start
        with contextlib.suppress(curses.error):
            screen.addstr(y, x, fragment, styles.get(style_name, curses.A_NORMAL))


def panel_layout(screen_width: int) -> tuple[int, int, int]:
    legend_x = max(0, screen_width - LEGEND_WIDTH)
    content_width = max(1, legend_x - GAP)
    divider_x = content_width + 1
    return content_width, divider_x, legend_x


def draw_fixed_legend(
    screen: curses.window,
    legend: list[Line],
    content_y: int,
    screen_height: int,
    screen_width: int,
    divider_x: int,
    legend_x: int,
    styles: dict[str, int],
) -> None:
    for row, legend_line in enumerate(legend):
        y = content_y + row
        if y >= screen_height:
            break
        draw_spans(
            screen,
            y,
            legend_x,
            legend_line,
            0,
            screen_width,
            styles,
        )
    for y in range(content_y, screen_height):
        draw_spans(
            screen,
            y,
            divider_x,
            [("│", "busy")],
            0,
            screen_width,
            styles,
        )


def dashboard(
    screen: curses.window,
    initial_view: str,
    initial_config: AppConfig,
    config_path: Path,
    user_override: str | None,
    initial_cluster_index: int,
    initial_jobs_scope_index: int,
) -> None:
    screen.keypad(True)
    with contextlib.suppress(curses.error):
        curses.curs_set(0)
    screen.timeout(200)
    styles = init_colors()

    runtime_config = copy.deepcopy(initial_config)
    editable_config = copy.deepcopy(initial_config)
    config_dirty = False
    config_message = ""
    config_selection = 0
    config_generation = 0
    clusters = placeholder_clusters(runtime_config, user_override)
    last_node_refresh: float | None = None
    last_filesystem_refresh: float | None = None
    last_login_refresh: float | None = None
    last_jobs_refresh: float | None = None
    last_history_refresh: float | None = None
    last_forecast_refresh: float | None = None
    next_node_refresh = 0.0
    next_filesystem_refresh = 0.0
    next_login_refresh = 0.0
    next_jobs_refresh = 0.0
    next_history_refresh = 0.0
    next_forecast_refresh = 0.0
    active_view = initial_view
    forecast_cluster_index = initial_cluster_index
    jobs_cluster_index = initial_jobs_scope_index
    jobs_job_indices: dict[str, int] = {}
    jobs_selection_changed = True
    forecast_resolution_index = FORECAST_RESOLUTIONS.index(60)
    vertical_offsets = {view: 0 for view in VIEWS}
    horizontal_offsets = {view: 0 for view in VIEWS}

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=32)
    refresh_includes_filesystems = True
    refresh_checks_login_nodes = True
    refresh_includes_schedule = True
    refresh_includes_jobs = True
    refresh_includes_history = True
    refresh_generation = config_generation
    refresh_futures = submit_cluster_refreshes(
        executor,
        runtime_config,
        user_override,
        refresh_includes_filesystems,
        refresh_checks_login_nodes,
        refresh_includes_schedule,
        refresh_includes_jobs,
        refresh_includes_history,
    )

    try:
        while True:
            now = time.monotonic()
            completed_futures = [future for future in refresh_futures if future.done()]
            for future in completed_futures:
                cluster_name = refresh_futures.pop(future)
                if refresh_generation != config_generation:
                    continue
                cluster_index = next(
                    (
                        index
                        for index, cluster in enumerate(clusters)
                        if cluster.name == cluster_name
                    ),
                    None,
                )
                if cluster_index is None:
                    continue
                previous = clusters[cluster_index]
                try:
                    refreshed = future.result()
                except Exception as error:
                    refreshed = copy.deepcopy(previous)
                    refreshed.loading = False
                    refreshed.error = str(error)
                clusters[cluster_index] = merge_cluster_refresh(
                    previous,
                    refreshed,
                    refresh_includes_filesystems,
                    refresh_checks_login_nodes,
                    refresh_includes_schedule,
                    refresh_includes_jobs,
                    refresh_includes_history,
                )
                last_node_refresh = now
                if refresh_includes_filesystems:
                    last_filesystem_refresh = now
                if refresh_checks_login_nodes:
                    last_login_refresh = now
                if refresh_includes_schedule:
                    last_forecast_refresh = now
                if refresh_includes_schedule or refresh_includes_jobs:
                    last_jobs_refresh = now
                if refresh_includes_history:
                    last_history_refresh = now

            if (
                completed_futures
                and not refresh_futures
                and refresh_generation == config_generation
            ):
                settings = runtime_config.settings
                retry_delay = (
                    settings.failed_retry_seconds
                    if any(cluster.error for cluster in clusters)
                    else settings.node_refresh_seconds
                )
                next_node_refresh = now + retry_delay
                if refresh_includes_filesystems:
                    next_filesystem_refresh = now + settings.filesystem_refresh_seconds
                if refresh_checks_login_nodes:
                    next_login_refresh = now + settings.login_refresh_seconds
                if refresh_includes_schedule:
                    next_forecast_refresh = now + settings.forecast_refresh_seconds
                if refresh_includes_schedule or refresh_includes_jobs:
                    next_jobs_refresh = now + settings.jobs_refresh_seconds
                if refresh_includes_history:
                    next_history_refresh = now + settings.jobs_history_refresh_seconds

            if not refresh_futures:
                filesystem_due = now >= next_filesystem_refresh
                login_due = now >= next_login_refresh
                node_due = now >= next_node_refresh
                jobs_due = now >= next_jobs_refresh
                history_due = now >= next_history_refresh
                forecast_due = now >= next_forecast_refresh
                if (
                    filesystem_due
                    or login_due
                    or node_due
                    or jobs_due
                    or history_due
                    or forecast_due
                ):
                    refresh_includes_filesystems = filesystem_due
                    refresh_checks_login_nodes = login_due
                    refresh_includes_schedule = forecast_due
                    refresh_includes_jobs = jobs_due
                    refresh_includes_history = history_due
                    preferred_hosts = {
                        cluster.name: cluster.host for cluster in clusters
                    }
                    for cluster in clusters:
                        cluster.loading = True
                    refresh_futures = submit_cluster_refreshes(
                        executor,
                        runtime_config,
                        user_override,
                        refresh_includes_filesystems,
                        refresh_checks_login_nodes,
                        refresh_includes_schedule,
                        refresh_includes_jobs,
                        refresh_includes_history,
                        preferred_hosts,
                    )
                    refresh_generation = config_generation

            screen_height, screen_width = screen.getmaxyx()
            node_age_seconds = (
                None if last_node_refresh is None else int(now - last_node_refresh)
            )
            filesystem_age_seconds = (
                None
                if last_filesystem_refresh is None
                else int(now - last_filesystem_refresh)
            )
            login_age_seconds = (
                None if last_login_refresh is None else int(now - last_login_refresh)
            )
            jobs_age_seconds = (
                None if last_jobs_refresh is None else int(now - last_jobs_refresh)
            )
            history_age_seconds = (
                None
                if last_history_refresh is None
                else int(now - last_history_refresh)
            )
            forecast_age_seconds = (
                None
                if last_forecast_refresh is None
                else int(now - last_forecast_refresh)
            )
            content_y = 2
            viewport_height = max(1, screen_height - content_y)
            content_width, fixed_divider_x, fixed_legend_x = panel_layout(screen_width)

            screen.erase()
            draw_spans(
                screen,
                0,
                0,
                tab_line(
                    active_view,
                    f"{len(clusters)} shown · {config_path.name}",
                ),
                0,
                screen_width,
                styles,
            )

            if active_view == "nodes":
                headers = [cluster_header(cluster) for cluster in clusters]
                bodies = [cluster_body(cluster, colored=True) for cluster in clusters]
                header_height = max(len(header) for header in headers)
                max_body_height = max((len(body) for body in bodies), default=0)
                body_height = max(1, viewport_height - header_height)
                max_vertical = max(0, max_body_height - body_height)
                vertical_offsets[active_view] = min(
                    max(0, vertical_offsets[active_view]), max_vertical
                )

                cluster_display_width = CLUSTER_WIDTH
                canvas_width = (
                    len(clusters) * cluster_display_width + (len(clusters) - 1) * GAP
                )
                max_horizontal = max(0, canvas_width - content_width)
                horizontal_offsets[active_view] = min(
                    max(0, horizontal_offsets[active_view]), max_horizontal
                )
                horizontal_offset = horizontal_offsets[active_view]
                vertical_offset = vertical_offsets[active_view]
                page_height = body_height
                legend = legend_lines(
                    age_seconds=node_age_seconds,
                    refreshing=bool(refresh_futures),
                    colored=True,
                    refresh_seconds=runtime_config.settings.node_refresh_seconds,
                )

                for index, (header, body) in enumerate(
                    zip(headers, bodies, strict=True)
                ):
                    x = index * (cluster_display_width + GAP)
                    for row, header_line in enumerate(header):
                        y = content_y + row
                        if y >= screen_height:
                            break
                        draw_spans(
                            screen,
                            y,
                            x,
                            clipped_line(header_line, cluster_display_width),
                            horizontal_offset,
                            content_width,
                            styles,
                        )

                    for viewport_row in range(body_height):
                        body_index = vertical_offset + viewport_row
                        y = content_y + header_height + viewport_row
                        if y >= screen_height or body_index >= len(body):
                            break
                        draw_spans(
                            screen,
                            y,
                            x,
                            clipped_line(body[body_index], cluster_display_width),
                            horizontal_offset,
                            content_width,
                            styles,
                        )

                for index in range(1, len(clusters)):
                    divider_x = index * cluster_display_width + (index - 1) * GAP + 1
                    for y in range(content_y, screen_height):
                        draw_spans(
                            screen,
                            y,
                            divider_x,
                            [("│", "busy")],
                            horizontal_offset,
                            content_width,
                            styles,
                        )

                draw_fixed_legend(
                    screen,
                    legend,
                    content_y,
                    screen_height,
                    screen_width,
                    fixed_divider_x,
                    fixed_legend_x,
                    styles,
                )
            elif active_view == "filesystems":
                table = filesystem_table(clusters)
                table_header = table[:4]
                table_body = table[4:]
                legend = filesystem_legend_lines(
                    age_seconds=filesystem_age_seconds,
                    refreshing=(bool(refresh_futures) and refresh_includes_filesystems),
                    refresh_seconds=(
                        runtime_config.settings.filesystem_refresh_seconds
                    ),
                )
                header_height = len(table_header)
                body_height = max(1, viewport_height - header_height)
                max_vertical = max(0, len(table_body) - body_height)
                vertical_offsets[active_view] = min(
                    max(0, vertical_offsets[active_view]), max_vertical
                )

                max_horizontal = max(
                    0,
                    filesystem_table_width(clusters) - content_width,
                )
                horizontal_offsets[active_view] = min(
                    max(0, horizontal_offsets[active_view]), max_horizontal
                )
                horizontal_offset = horizontal_offsets[active_view]
                vertical_offset = vertical_offsets[active_view]
                page_height = body_height

                for row, header_line in enumerate(table_header):
                    draw_spans(
                        screen,
                        content_y + row,
                        0,
                        header_line,
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                for viewport_row in range(body_height):
                    body_index = vertical_offset + viewport_row
                    if body_index >= len(table_body):
                        break
                    draw_spans(
                        screen,
                        content_y + header_height + viewport_row,
                        0,
                        table_body[body_index],
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                draw_fixed_legend(
                    screen,
                    legend,
                    content_y,
                    screen_height,
                    screen_width,
                    fixed_divider_x,
                    fixed_legend_x,
                    styles,
                )
            elif active_view == "logins":
                table = login_table(clusters)
                table_header = table[:4]
                table_body = table[4:]
                legend = login_legend_lines(
                    age_seconds=login_age_seconds,
                    refreshing=(bool(refresh_futures) and refresh_checks_login_nodes),
                    refresh_seconds=runtime_config.settings.login_refresh_seconds,
                )
                header_height = len(table_header)
                body_height = max(1, viewport_height - header_height)
                max_vertical = max(0, len(table_body) - body_height)
                vertical_offsets[active_view] = min(
                    max(0, vertical_offsets[active_view]), max_vertical
                )

                max_horizontal = max(0, LOGIN_TABLE_WIDTH - content_width)
                horizontal_offsets[active_view] = min(
                    max(0, horizontal_offsets[active_view]), max_horizontal
                )
                horizontal_offset = horizontal_offsets[active_view]
                vertical_offset = vertical_offsets[active_view]
                page_height = body_height

                for row, header_line in enumerate(table_header):
                    draw_spans(
                        screen,
                        content_y + row,
                        0,
                        header_line,
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                for viewport_row in range(body_height):
                    body_index = vertical_offset + viewport_row
                    if body_index >= len(table_body):
                        break
                    draw_spans(
                        screen,
                        content_y + header_height + viewport_row,
                        0,
                        table_body[body_index],
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                draw_fixed_legend(
                    screen,
                    legend,
                    content_y,
                    screen_height,
                    screen_width,
                    fixed_divider_x,
                    fixed_legend_x,
                    styles,
                )
            elif active_view == "jobs":
                scope_key = (
                    "ALL"
                    if jobs_cluster_index == 0
                    else clusters[jobs_cluster_index - 1].name
                )
                selected_job_index = jobs_job_indices.get(scope_key, 0)
                (
                    headers,
                    body,
                    details,
                    table_width,
                    selected_body_index,
                    selected_job_index,
                ) = jobs_table(
                    clusters,
                    jobs_cluster_index,
                    selected_job_index,
                )
                jobs_job_indices[scope_key] = selected_job_index
                legend = jobs_legend_lines(
                    age_seconds=jobs_age_seconds,
                    refreshing=(
                        bool(refresh_futures)
                        and (refresh_includes_jobs or refresh_includes_schedule)
                    ),
                    refresh_seconds=(runtime_config.settings.jobs_refresh_seconds),
                    history_age_seconds=history_age_seconds,
                    history_refreshing=(
                        bool(refresh_futures) and refresh_includes_history
                    ),
                    history_refresh_seconds=(
                        runtime_config.settings.jobs_history_refresh_seconds
                    ),
                )
                header_height = len(headers)
                available_below_header = max(1, viewport_height - header_height)
                detail_height = min(
                    len(details),
                    max(0, available_below_header - 1),
                )
                body_height = max(1, available_below_header - detail_height)
                max_vertical = max(0, len(body) - body_height)
                vertical_offset = min(
                    max(0, vertical_offsets[active_view]), max_vertical
                )
                if jobs_selection_changed:
                    if selected_body_index < vertical_offset:
                        vertical_offset = selected_body_index
                    elif selected_body_index >= vertical_offset + body_height:
                        vertical_offset = selected_body_index - body_height + 1
                    jobs_selection_changed = False
                vertical_offsets[active_view] = vertical_offset
                page_height = body_height
                display_headers, render_vertical_offset = sticky_jobs_headers(
                    headers,
                    body,
                    vertical_offset,
                )

                max_horizontal = max(0, table_width - content_width)
                horizontal_offsets[active_view] = min(
                    max(0, horizontal_offsets[active_view]), max_horizontal
                )
                horizontal_offset = horizontal_offsets[active_view]

                for row, header_line in enumerate(display_headers):
                    y = content_y + row
                    if y >= screen_height:
                        break
                    draw_spans(
                        screen,
                        y,
                        0,
                        header_line,
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                for viewport_row in range(body_height):
                    body_index = render_vertical_offset + viewport_row
                    y = content_y + header_height + viewport_row
                    if y >= screen_height or body_index >= len(body):
                        break
                    draw_spans(
                        screen,
                        y,
                        0,
                        body[body_index],
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                detail_y = content_y + header_height + body_height
                for detail_row in details[:detail_height]:
                    if detail_y >= screen_height:
                        break
                    draw_spans(
                        screen,
                        detail_y,
                        0,
                        detail_row,
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                    detail_y += 1
                draw_fixed_legend(
                    screen,
                    legend,
                    content_y,
                    screen_height,
                    screen_width,
                    fixed_divider_x,
                    fixed_legend_x,
                    styles,
                )
            elif active_view == "forecast":
                resolution_minutes = FORECAST_RESOLUTIONS[forecast_resolution_index]
                node_label_width = forecast_node_label_width(
                    clusters[forecast_cluster_index]
                )
                visible_interval_count = max(
                    1,
                    content_width - node_label_width,
                )
                total_interval_count = math.ceil(
                    runtime_config.settings.forecast_horizon_days
                    * 24
                    * 60
                    / resolution_minutes
                )
                max_horizontal = max(
                    0,
                    total_interval_count - visible_interval_count,
                )
                horizontal_offsets[active_view] = min(
                    max(0, horizontal_offsets[active_view]),
                    max_horizontal,
                )
                horizontal_offset = horizontal_offsets[active_view]
                (
                    headers,
                    body,
                    _table_width,
                    resource_name,
                    _total_interval_count,
                    visible_start,
                    visible_end,
                ) = forecast_table(
                    clusters,
                    forecast_cluster_index,
                    time.time(),
                    colored=True,
                    resolution_minutes=resolution_minutes,
                    window_hours=(runtime_config.settings.forecast_horizon_days * 24),
                    interval_offset=horizontal_offset,
                    visible_interval_count=visible_interval_count,
                )
                legend = forecast_legend_lines(
                    age_seconds=forecast_age_seconds,
                    refreshing=(bool(refresh_futures) and refresh_includes_schedule),
                    resource_name=resource_name,
                    resolution_minutes=resolution_minutes,
                    window_hours=(runtime_config.settings.forecast_horizon_days * 24),
                    visible_start=visible_start,
                    visible_end=visible_end,
                    refresh_seconds=(runtime_config.settings.forecast_refresh_seconds),
                )
                header_height = len(headers)
                body_height = max(1, viewport_height - header_height)
                max_vertical = max(0, len(body) - body_height)
                vertical_offsets[active_view] = min(
                    max(0, vertical_offsets[active_view]), max_vertical
                )

                vertical_offset = vertical_offsets[active_view]
                page_height = body_height

                for row, header_line in enumerate(headers):
                    y = content_y + row
                    if y >= screen_height:
                        break
                    draw_spans(
                        screen,
                        y,
                        0,
                        header_line,
                        0,
                        content_width,
                        styles,
                    )

                for viewport_row in range(body_height):
                    body_index = vertical_offset + viewport_row
                    y = content_y + header_height + viewport_row
                    if y >= screen_height or body_index >= len(body):
                        break
                    draw_spans(
                        screen,
                        y,
                        0,
                        body[body_index],
                        0,
                        content_width,
                        styles,
                    )

                draw_fixed_legend(
                    screen,
                    legend,
                    content_y,
                    screen_height,
                    screen_width,
                    fixed_divider_x,
                    fixed_legend_x,
                    styles,
                )

            else:
                selection_count = len(SETTING_FIELDS) + len(editable_config.clusters)
                config_selection = min(max(0, config_selection), selection_count - 1)
                headers, body, selected_body_index = config_table(
                    editable_config,
                    config_path,
                    config_selection,
                    config_dirty,
                    config_message,
                )
                legend = config_legend_lines(config_dirty)
                header_height = len(headers)
                body_height = max(1, viewport_height - header_height)
                max_vertical = max(0, len(body) - body_height)
                vertical_offset = min(
                    max(0, vertical_offsets[active_view]), max_vertical
                )
                if selected_body_index < vertical_offset:
                    vertical_offset = selected_body_index
                elif selected_body_index >= vertical_offset + body_height:
                    vertical_offset = selected_body_index - body_height + 1
                vertical_offsets[active_view] = vertical_offset
                page_height = body_height

                table_width = max(
                    [visible_length(row) for row in headers + body] or [content_width]
                )
                max_horizontal = max(0, table_width - content_width)
                horizontal_offsets[active_view] = min(
                    max(0, horizontal_offsets[active_view]), max_horizontal
                )
                horizontal_offset = horizontal_offsets[active_view]

                for row, header_line in enumerate(headers):
                    draw_spans(
                        screen,
                        content_y + row,
                        0,
                        header_line,
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                for viewport_row in range(body_height):
                    body_index = vertical_offset + viewport_row
                    if body_index >= len(body):
                        break
                    draw_spans(
                        screen,
                        content_y + header_height + viewport_row,
                        0,
                        body[body_index],
                        horizontal_offset,
                        content_width,
                        styles,
                    )
                draw_fixed_legend(
                    screen,
                    legend,
                    content_y,
                    screen_height,
                    screen_width,
                    fixed_divider_x,
                    fixed_legend_x,
                    styles,
                )

            screen.refresh()
            key = screen.getch()
            if key in (ord("q"), ord("Q")):
                break
            backtab_key = getattr(curses, "KEY_BTAB", None)
            if key == 9 or (backtab_key is not None and key == backtab_key):
                view_index = VIEWS.index(active_view)
                active_view = VIEWS[(view_index + 1) % len(VIEWS)]
                if active_view == "jobs":
                    jobs_selection_changed = True
            elif active_view == "jobs" and key in (ord("["), ord("{")):
                jobs_cluster_index = (jobs_cluster_index - 1) % (len(clusters) + 1)
                vertical_offsets[active_view] = 0
                horizontal_offsets[active_view] = 0
                jobs_selection_changed = True
            elif active_view == "jobs" and key in (ord("]"), ord("}")):
                jobs_cluster_index = (jobs_cluster_index + 1) % (len(clusters) + 1)
                vertical_offsets[active_view] = 0
                horizontal_offsets[active_view] = 0
                jobs_selection_changed = True
            elif active_view == "jobs" and ord("0") <= key <= ord("0") + min(
                9, len(clusters)
            ):
                selected_index = key - ord("0")
                if selected_index != jobs_cluster_index:
                    jobs_cluster_index = selected_index
                    vertical_offsets[active_view] = 0
                    horizontal_offsets[active_view] = 0
                    jobs_selection_changed = True
            elif active_view == "jobs" and key == curses.KEY_UP:
                scope_key = (
                    "ALL"
                    if jobs_cluster_index == 0
                    else clusters[jobs_cluster_index - 1].name
                )
                selected_job = jobs_job_indices.get(scope_key, 0)
                if selected_job > 0:
                    jobs_job_indices[scope_key] = selected_job - 1
                    jobs_selection_changed = True
            elif active_view == "jobs" and key == curses.KEY_DOWN:
                scope_key = (
                    "ALL"
                    if jobs_cluster_index == 0
                    else clusters[jobs_cluster_index - 1].name
                )
                selected_job = jobs_job_indices.get(scope_key, 0)
                if selected_job + 1 < len(jobs_for_scope(clusters, jobs_cluster_index)):
                    jobs_job_indices[scope_key] = selected_job + 1
                    jobs_selection_changed = True
            elif active_view == "forecast" and key in (ord("["), ord("{")):
                forecast_cluster_index = (forecast_cluster_index - 1) % len(clusters)
                horizontal_offsets[active_view] = 0
            elif active_view == "forecast" and key in (ord("]"), ord("}")):
                forecast_cluster_index = (forecast_cluster_index + 1) % len(clusters)
                horizontal_offsets[active_view] = 0
            elif active_view == "forecast" and ord("1") <= key < ord("1") + min(
                9, len(clusters)
            ):
                selected_index = key - ord("1")
                if selected_index != forecast_cluster_index:
                    forecast_cluster_index = selected_index
                    horizontal_offsets[active_view] = 0
            elif active_view == "forecast" and key in (ord("+"), ord("=")):
                old_resolution = FORECAST_RESOLUTIONS[forecast_resolution_index]
                finer_index = max(
                    0,
                    forecast_resolution_index - 1,
                )
                if finer_index != forecast_resolution_index:
                    forecast_resolution_index = finer_index
                    new_resolution = FORECAST_RESOLUTIONS[finer_index]
                    horizontal_offsets[active_view] = round(
                        horizontal_offsets[active_view]
                        * old_resolution
                        / new_resolution
                    )
            elif active_view == "forecast" and key in (ord("-"), ord("_")):
                old_resolution = FORECAST_RESOLUTIONS[forecast_resolution_index]
                coarser_index = min(
                    len(FORECAST_RESOLUTIONS) - 1,
                    forecast_resolution_index + 1,
                )
                if coarser_index != forecast_resolution_index:
                    forecast_resolution_index = coarser_index
                    new_resolution = FORECAST_RESOLUTIONS[coarser_index]
                    horizontal_offsets[active_view] = round(
                        horizontal_offsets[active_view]
                        * old_resolution
                        / new_resolution
                    )
            elif active_view == "config" and key in (
                curses.KEY_SR,
                curses.KEY_SF,
            ):
                cluster_index = config_selection - len(SETTING_FIELDS)
                if cluster_index < 0:
                    config_message = "Select a cluster card to move"
                else:
                    direction = -1 if key == curses.KEY_SR else 1
                    new_index = min(
                        len(editable_config.clusters) - 1,
                        max(0, cluster_index + direction),
                    )
                    if new_index != cluster_index:
                        clusters_to_order = editable_config.clusters
                        (
                            clusters_to_order[cluster_index],
                            clusters_to_order[new_index],
                        ) = (
                            clusters_to_order[new_index],
                            clusters_to_order[cluster_index],
                        )
                        config_selection = len(SETTING_FIELDS) + new_index
                        config_dirty = True
                        config_message = (
                            f"Moved {clusters_to_order[new_index].name}; "
                            "press s to apply"
                        )
            elif active_view == "config" and key == curses.KEY_UP:
                config_selection -= 1
            elif active_view == "config" and key == curses.KEY_DOWN:
                config_selection += 1
            elif active_view == "config" and key == curses.KEY_PPAGE:
                config_selection -= max(1, page_height - 1)
            elif active_view == "config" and key == curses.KEY_NPAGE:
                config_selection += max(1, page_height - 1)
            elif active_view == "config" and key == curses.KEY_HOME:
                config_selection = 0
            elif active_view == "config" and key == curses.KEY_END:
                config_selection = (
                    len(SETTING_FIELDS) + len(editable_config.clusters) - 1
                )
            elif active_view == "config" and key in (
                10,
                13,
                curses.KEY_ENTER,
                ord("e"),
                ord("E"),
            ):
                if config_selection < len(SETTING_FIELDS):
                    key_name, label, minimum, maximum = SETTING_FIELDS[config_selection]
                    entered = prompt_text(
                        screen,
                        f"{label} ({minimum}-{maximum})",
                        str(getattr(editable_config.settings, key_name)),
                        styles,
                    )
                    if entered is not None:
                        try:
                            value = int(entered.strip())
                            if not minimum <= value <= maximum:
                                raise ValueError
                            setattr(editable_config.settings, key_name, value)
                            config_dirty = True
                            config_message = f"Changed {label}; press s to apply"
                        except ValueError:
                            config_message = f"{label} must be {minimum}-{maximum}"
                else:
                    cluster_index = config_selection - len(SETTING_FIELDS)
                    edited = edit_cluster_dialog(
                        screen,
                        styles,
                        editable_config.clusters[cluster_index],
                    )
                    if edited is not None:
                        candidate = copy.deepcopy(editable_config)
                        candidate.clusters[cluster_index] = edited
                        try:
                            validate_config(candidate)
                            editable_config = candidate
                            config_dirty = True
                            config_message = f"Changed {edited.name}; press s to apply"
                        except ValueError as error:
                            config_message = str(error)
            elif active_view == "config" and key in (ord("h"), ord("H")):
                cluster_index = config_selection - len(SETTING_FIELDS)
                if cluster_index < 0:
                    config_message = "Select a cluster card to hide or show"
                else:
                    candidate = copy.deepcopy(editable_config)
                    cluster = candidate.clusters[cluster_index]
                    cluster.hidden = not cluster.hidden
                    try:
                        validate_config(candidate)
                        editable_config = candidate
                        config_dirty = True
                        state = "Hidden" if cluster.hidden else "Shown"
                        config_message = f"{state} {cluster.name}; press s to apply"
                    except ValueError as error:
                        config_message = str(error)
            elif active_view == "config" and key in (ord("a"), ord("A")):
                added = edit_cluster_dialog(screen, styles, None)
                if added is not None:
                    candidate = copy.deepcopy(editable_config)
                    candidate.clusters.append(added)
                    try:
                        validate_config(candidate)
                        editable_config = candidate
                        config_selection = (
                            len(SETTING_FIELDS) + len(editable_config.clusters) - 1
                        )
                        config_dirty = True
                        config_message = f"Added {added.name}; press s to apply"
                    except ValueError as error:
                        config_message = str(error)
            elif active_view == "config" and key in (ord("d"), ord("D")):
                cluster_index = config_selection - len(SETTING_FIELDS)
                if cluster_index < 0:
                    config_message = "Global settings cannot be deleted"
                elif len(editable_config.clusters) == 1:
                    config_message = "At least one cluster is required"
                else:
                    cluster_name = editable_config.clusters[cluster_index].name
                    if confirm_prompt(
                        screen,
                        f"Delete {cluster_name}",
                        styles,
                    ):
                        candidate = copy.deepcopy(editable_config)
                        del candidate.clusters[cluster_index]
                        try:
                            validate_config(candidate)
                            editable_config = candidate
                            config_selection = min(
                                config_selection,
                                len(SETTING_FIELDS) + len(editable_config.clusters) - 1,
                            )
                            config_dirty = True
                            config_message = f"Deleted {cluster_name}; press s to apply"
                        except ValueError as error:
                            config_message = str(error)
            elif active_view == "config" and key in (ord("s"), ord("S")):
                try:
                    save_config(editable_config, config_path)
                    runtime_config = copy.deepcopy(editable_config)
                    config_dirty = False
                    config_message = "Saved and applied"
                    config_generation += 1
                    for future in refresh_futures:
                        future.cancel()
                    refresh_futures.clear()
                    clusters = placeholder_clusters(
                        runtime_config,
                        user_override,
                    )
                    forecast_cluster_index = min(
                        forecast_cluster_index,
                        len(clusters) - 1,
                    )
                    jobs_cluster_index = min(jobs_cluster_index, len(clusters))
                    jobs_job_indices.clear()
                    jobs_selection_changed = True
                    last_node_refresh = None
                    last_filesystem_refresh = None
                    last_login_refresh = None
                    last_jobs_refresh = None
                    last_history_refresh = None
                    last_forecast_refresh = None
                    next_node_refresh = 0.0
                    next_filesystem_refresh = 0.0
                    next_login_refresh = 0.0
                    next_jobs_refresh = 0.0
                    next_history_refresh = 0.0
                    next_forecast_refresh = 0.0
                    for view in VIEWS:
                        if view != "config":
                            vertical_offsets[view] = 0
                            horizontal_offsets[view] = 0
                except (OSError, ValueError) as error:
                    config_message = f"Save failed: {error}"
            elif active_view == "config" and key in (ord("l"), ord("L")):
                try:
                    loaded_config = load_config(config_path, create=False)
                    editable_config = copy.deepcopy(loaded_config)
                    runtime_config = copy.deepcopy(loaded_config)
                    config_dirty = False
                    config_message = "Reloaded and applied from disk"
                    config_generation += 1
                    for future in refresh_futures:
                        future.cancel()
                    refresh_futures.clear()
                    clusters = placeholder_clusters(
                        runtime_config,
                        user_override,
                    )
                    forecast_cluster_index = min(
                        forecast_cluster_index,
                        len(clusters) - 1,
                    )
                    jobs_cluster_index = min(jobs_cluster_index, len(clusters))
                    jobs_job_indices.clear()
                    jobs_selection_changed = True
                    last_node_refresh = None
                    last_filesystem_refresh = None
                    last_login_refresh = None
                    last_jobs_refresh = None
                    last_history_refresh = None
                    last_forecast_refresh = None
                    next_node_refresh = 0.0
                    next_filesystem_refresh = 0.0
                    next_login_refresh = 0.0
                    next_jobs_refresh = 0.0
                    next_history_refresh = 0.0
                    next_forecast_refresh = 0.0
                except (OSError, ValueError) as error:
                    config_message = f"Reload failed: {error}"
            elif key == curses.KEY_UP:
                vertical_offsets[active_view] -= 1
            elif key == curses.KEY_DOWN:
                vertical_offsets[active_view] += 1
            elif key == curses.KEY_PPAGE:
                vertical_offsets[active_view] -= max(1, page_height - 1)
            elif key == curses.KEY_NPAGE:
                vertical_offsets[active_view] += max(1, page_height - 1)
            elif key == curses.KEY_HOME:
                vertical_offsets[active_view] = 0
            elif key == curses.KEY_END:
                vertical_offsets[active_view] = max_vertical
            elif key == curses.KEY_LEFT:
                horizontal_offsets[active_view] -= 4
            elif key == curses.KEY_RIGHT:
                horizontal_offsets[active_view] += 4
            elif key in (ord("r"), ord("R")) and not refresh_futures:
                refresh_includes_filesystems = (
                    active_view == "filesystems" or now >= next_filesystem_refresh
                )
                refresh_checks_login_nodes = (
                    active_view == "logins" or now >= next_login_refresh
                )
                refresh_includes_schedule = (
                    active_view == "forecast" or now >= next_forecast_refresh
                )
                refresh_includes_jobs = (
                    active_view == "jobs" or now >= next_jobs_refresh
                )
                refresh_includes_history = (
                    active_view == "jobs" or now >= next_history_refresh
                )
                preferred_hosts = {cluster.name: cluster.host for cluster in clusters}
                for cluster in clusters:
                    cluster.loading = True
                refresh_futures = submit_cluster_refreshes(
                    executor,
                    runtime_config,
                    user_override,
                    refresh_includes_filesystems,
                    refresh_checks_login_nodes,
                    refresh_includes_schedule,
                    refresh_includes_jobs,
                    refresh_includes_history,
                    preferred_hosts,
                )
                refresh_generation = config_generation
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
