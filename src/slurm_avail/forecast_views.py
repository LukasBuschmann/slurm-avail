"""Reservation and running-job forecast presentation."""

from __future__ import annotations

import math
import re
from datetime import datetime

from .constants import (
    FORECAST_LEVELS,
    FORECAST_STATE_WIDTH,
    LEGEND_WIDTH,
    Line,
)
from .models import Cluster, Node, ReservationInterval, RunningInterval
from .text import line, plain, visible_length


def natural_node_key(node: Node) -> tuple[tuple[int, object], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.lower())
        for part in re.split(r"(\d+)", node.name)
        if part
    )


def forecast_glyph(used: int, total: int) -> str:
    if used <= 0 or total <= 0:
        return " "
    level = min(len(FORECAST_LEVELS), math.ceil(len(FORECAST_LEVELS) * used / total))
    return FORECAST_LEVELS[level - 1]


def resolution_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def forecast_shows_clock(resolution_minutes: int) -> bool:
    return resolution_minutes <= 120


def forecast_clock_step_minutes(resolution_minutes: int) -> int:
    if resolution_minutes <= 30:
        return 180
    if resolution_minutes <= 60:
        return 360
    return 720


def place_axis_label(
    characters: list[str],
    occupied: list[bool],
    start: int,
    label: str,
) -> bool:
    if start < 0 or start >= len(characters):
        return False
    end = start + len(label)
    if end <= start or end > len(characters):
        return False
    padded_start = max(0, start - 1)
    padded_end = min(len(characters), end + 1)
    if any(occupied[padded_start:padded_end]):
        return False
    characters[start:end] = label
    occupied[start:end] = [True] * (end - start)
    return True


def window_label(hours: int) -> str:
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days >= 365 and days % 365 == 0:
        years = days // 365
        return f"{years}y"
    return f"{days}d"


def first_forecast_interval(now_epoch: float, resolution_seconds: int) -> float:
    local_now = datetime.fromtimestamp(now_epoch).astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (local_now - local_midnight).total_seconds()
    return (
        local_midnight.timestamp()
        + math.floor(elapsed / resolution_seconds) * resolution_seconds
    )


def forecast_time_headers(
    first_interval: float,
    interval_count: int,
    resolution_seconds: int,
    node_label_width: int,
) -> list[Line]:
    resolution_minutes = resolution_seconds // 60
    show_clock = forecast_shows_clock(resolution_minutes)
    timestamps = [
        datetime.fromtimestamp(first_interval + interval_index * resolution_seconds)
        for interval_index in range(interval_count)
    ]

    date_labels = [" "] * interval_count
    date_occupied = [False] * interval_count
    date_candidates: list[tuple[int, datetime]] = []
    previous_date = timestamps[0].date()
    for interval_index, timestamp in enumerate(timestamps[1:], start=1):
        if timestamp.date() != previous_date:
            date_candidates.append((interval_index, timestamp))
        previous_date = timestamp.date()

    columns_per_day = max(1, round(24 * 60 / resolution_minutes))
    show_month = resolution_minutes >= 360
    if columns_per_day >= 10:
        date_format = "%a %d %b"
        date_stride_days = 1
    elif show_month:
        date_format = "%a %d"
        date_stride_days = max(1, math.ceil(7 / columns_per_day))
    else:
        date_format = "%d %b"
        date_stride_days = max(1, math.ceil(7 / columns_per_day))

    for interval_index, timestamp in date_candidates:
        if timestamp.date().toordinal() % date_stride_days == 0:
            place_axis_label(
                date_labels,
                date_occupied,
                interval_index,
                timestamp.strftime(date_format),
            )

    # Identify a partial day at the left edge using the most descriptive label
    # that still leaves a gap before the first natural calendar label.
    first_occupied = next(
        (index for index, occupied in enumerate(date_occupied) if occupied),
        interval_count + 1,
    )
    edge_space = max(0, first_occupied - 1)
    edge_formats = (
        ("%a %d %b", "%a %d", "%d")
        if columns_per_day >= 10
        else (("%a %d", "%d") if show_month else ("%d %b", "%d"))
    )
    for edge_format in edge_formats:
        edge_label = timestamps[0].strftime(edge_format)
        if len(edge_label) <= edge_space and place_axis_label(
            date_labels,
            date_occupied,
            0,
            edge_label,
        ):
            break

    ticks = ["─"] * interval_count
    previous_date = timestamps[0].date()
    for interval_index, timestamp in enumerate(timestamps[1:], start=1):
        if timestamp.date() != previous_date:
            ticks[interval_index] = "┿"
        previous_date = timestamp.date()

    result: list[Line] = []
    if show_month:
        month_labels = [" "] * interval_count
        month_occupied = [False] * interval_count
        previous_month: tuple[int, int] | None = None
        for interval_index, timestamp in enumerate(timestamps):
            month = (timestamp.year, timestamp.month)
            if interval_index == 0 or month != previous_month:
                place_axis_label(
                    month_labels,
                    month_occupied,
                    interval_index,
                    timestamp.strftime("%b %Y"),
                )
                ticks[interval_index] = "╂"
            previous_month = month
        result.append(
            line(
                (f"{'MONTH':<{node_label_width}}", "title"),
                ("".join(month_labels), "normal"),
            )
        )

    result.append(
        line(
            (f"{'DATE':<{node_label_width}}", "title"),
            ("".join(date_labels), "normal"),
        )
    )
    if show_clock:
        time_labels = [" "] * interval_count
        time_occupied = [False] * interval_count
        clock_step = forecast_clock_step_minutes(resolution_minutes)
        for interval_index, timestamp in enumerate(timestamps):
            minute_of_day = timestamp.hour * 60 + timestamp.minute
            # The grid uses elapsed durations. Around daylight-saving changes,
            # a local clock tick can shift by an hour; label the first cell at
            # or after the intended clock boundary instead of losing the tick.
            if minute_of_day % clock_step >= resolution_minutes:
                continue
            place_axis_label(
                time_labels,
                time_occupied,
                interval_index,
                timestamp.strftime("%H:%M"),
            )
            if ticks[interval_index] == "─":
                ticks[interval_index] = "┬"
        result.append(
            line(
                (f"{'TIME':<{node_label_width}}", "title"),
                ("".join(time_labels), "normal"),
            )
        )

    if ticks and ticks[0] == "─":
        ticks[0] = "╞"
    result.append(
        line(
            (
                f"{'NODE':<{node_label_width - FORECAST_STATE_WIDTH}}",
                "title",
            ),
            (f"{' NOW':<{FORECAST_STATE_WIDTH}}", "title"),
            ("".join(ticks), "busy"),
        )
    )
    return result


def forecast_cluster_menu(clusters: list[Cluster], selected_index: int) -> Line:
    menu: Line = [("CLUSTER  ", "title")]
    for index, cluster in enumerate(clusters):
        if index:
            menu.append(("  ", "normal"))
        label = f"[{index + 1} {cluster.name}]"
        menu.append((label, "title" if index == selected_index else "normal"))
    return menu


def forecast_node_label_width(cluster: Cluster) -> int:
    node_name_width = max(
        len("NODE"),
        max((len(node.name) for node in cluster.nodes), default=0),
    )
    return node_name_width + FORECAST_STATE_WIDTH


def forecast_table(
    clusters: list[Cluster],
    selected_index: int,
    now_epoch: float,
    colored: bool,
    resolution_minutes: int,
    window_hours: int,
    interval_offset: int = 0,
    visible_interval_count: int | None = None,
) -> tuple[list[Line], list[Line], int, str, int, float, float]:
    cluster = clusters[selected_index]
    nodes = sorted(cluster.nodes, key=natural_node_key)
    has_gpus = cluster.has_gpus
    resource_name = "GPU" if has_gpus else "CPU"
    node_label_width = forecast_node_label_width(cluster)
    resolution_seconds = resolution_minutes * 60
    first_interval = first_forecast_interval(now_epoch, resolution_seconds)
    total_interval_count = math.ceil(window_hours * 60 / resolution_minutes)
    interval_offset = min(max(0, interval_offset), total_interval_count - 1)
    if visible_interval_count is None:
        interval_count = total_interval_count - interval_offset
    else:
        interval_count = min(
            max(1, visible_interval_count),
            total_interval_count - interval_offset,
        )
    first_interval += interval_offset * resolution_seconds
    visible_end = first_interval + interval_count * resolution_seconds
    table_width = node_label_width + interval_count
    menu = forecast_cluster_menu(clusters, selected_index)
    table_width = max(table_width, visible_length(menu))

    local_time_label = (
        "date + time" if forecast_shows_clock(resolution_minutes) else "calendar dates"
    )
    headers: list[Line] = [
        line(("EXACT RESERVATIONS + RUNNING JOB LIMITS", "title")),
        menu,
        plain(
            f"{window_label(window_hours)} horizon · "
            f"1 cell = {resolution_label(resolution_minutes)} · "
            f"{resource_name} fill · local "
            f"{local_time_label}"
        ),
        plain(""),
    ]
    headers.extend(
        forecast_time_headers(
            first_interval,
            interval_count,
            resolution_seconds,
            node_label_width,
        )
    )
    headers.append(plain("─" * table_width))

    if cluster.error:
        return (
            headers,
            [line((cluster.error[:table_width], "offline"))],
            table_width,
            resource_name,
            total_interval_count,
            first_interval,
            visible_end,
        )

    reservations_by_node: dict[str, list[ReservationInterval]] = {
        node.name: [] for node in nodes
    }
    for reservation in cluster.reservations:
        for node_name in reservation.nodes:
            if node_name in reservations_by_node:
                reservations_by_node[node_name].append(reservation)
    jobs_by_node: dict[str, list[RunningInterval]] = {node.name: [] for node in nodes}
    for job in cluster.running_jobs:
        for node_name in job.nodes:
            if node_name in jobs_by_node:
                jobs_by_node[node_name].append(job)

    body: list[Line] = []
    for node in nodes:
        node_name_width = node_label_width - FORECAST_STATE_WIDTH
        is_down = node.status == "drained"
        state_marker = " ×" if is_down else ""
        row: Line = [
            (
                f"{node.name:<{node_name_width}}",
                "drained" if is_down else "title",
            ),
            (
                f"{state_marker:<{FORECAST_STATE_WIDTH}}",
                "drained" if is_down else "normal",
            ),
        ]
        running_delta = [0] * (interval_count + 1)
        user_running_delta = [0] * (interval_count + 1)
        active_job_delta = [0] * (interval_count + 1)
        reservation_delta = [0] * (interval_count + 1)
        user_reservation_delta = [0] * (interval_count + 1)
        for job in jobs_by_node[node.name]:
            end_index = min(
                interval_count,
                max(
                    0,
                    math.ceil((job.end - first_interval) / resolution_seconds),
                ),
            )
            if end_index <= 0:
                continue
            units = job.gpu_per_node if has_gpus else job.cpu_per_node
            running_delta[0] += units
            running_delta[end_index] -= units
            if job.mine:
                user_running_delta[0] += units
                user_running_delta[end_index] -= units
            active_job_delta[0] += 1
            active_job_delta[end_index] -= 1
        for reservation in reservations_by_node[node.name]:
            start_index = min(
                interval_count,
                max(
                    0,
                    math.floor(
                        (reservation.start - first_interval) / resolution_seconds
                    ),
                ),
            )
            end_index = min(
                interval_count,
                max(
                    0,
                    math.ceil((reservation.end - first_interval) / resolution_seconds),
                ),
            )
            if start_index >= end_index:
                continue
            reservation_delta[start_index] += 1
            reservation_delta[end_index] -= 1
            if reservation.mine:
                user_reservation_delta[start_index] += 1
                user_reservation_delta[end_index] -= 1

        running = 0
        user_running = 0
        active_job_count = 0
        reservation_count = 0
        user_reservation_count = 0
        for interval_index in range(interval_count):
            total = node.gpu_total if has_gpus else node.cpu_total
            running += running_delta[interval_index]
            user_running += user_running_delta[interval_index]
            active_job_count += active_job_delta[interval_index]
            reservation_count += reservation_delta[interval_index]
            user_reservation_count += user_reservation_delta[interval_index]
            reserved = total if reservation_count else 0
            displayed_running = running
            if node.status == "exclusive" and active_job_count:
                displayed_running = total
            displayed_running = min(total, displayed_running)
            displayed_user_running = min(total, user_running)
            reservation_glyph = forecast_glyph(reserved, total)
            running_glyph = forecast_glyph(displayed_running, total)
            if user_reservation_count and displayed_running:
                glyph = forecast_glyph(
                    max(reserved, displayed_running),
                    total,
                )
                style = "forecast_mine_overlap"
            elif user_reservation_count:
                glyph = reservation_glyph
                style = "forecast_mine_reserved"
            elif displayed_user_running and reserved:
                glyph = forecast_glyph(
                    max(reserved, displayed_running),
                    total,
                )
                style = "forecast_mine_overlap"
            elif displayed_user_running:
                # Color identifies ownership; height continues to represent
                # the node's total running allocation.
                glyph = running_glyph
                style = "forecast_mine_usage"
            elif reserved and displayed_running:
                glyph = forecast_glyph(
                    max(reserved, displayed_running),
                    total,
                )
                style = "forecast_overlap"
            elif reserved:
                glyph = reservation_glyph
                style = "forecast_reserved"
            elif displayed_running:
                glyph = running_glyph
                style = "forecast_running"
            else:
                glyph = " " if colored else "░"
                style = "forecast_empty"
            row.append((glyph, style))
        body.append(row)
    return (
        headers,
        body,
        table_width,
        resource_name,
        total_interval_count,
        first_interval,
        visible_end,
    )


def forecast_legend_lines(
    age_seconds: int | None,
    refreshing: bool,
    resource_name: str,
    resolution_minutes: int,
    window_hours: int,
    visible_start: float,
    visible_end: float,
    refresh_seconds: int,
) -> list[Line]:
    age = "waiting for data" if age_seconds is None else f"{age_seconds}s ago"
    show_clock = forecast_shows_clock(resolution_minutes)
    if show_clock:
        visible_start_label = datetime.fromtimestamp(visible_start).strftime(
            "%b %d %H:%M →"
        )
        visible_end_label = datetime.fromtimestamp(visible_end).strftime("%b %d %H:%M")
    else:
        visible_start_label = datetime.fromtimestamp(visible_start).strftime(
            "%b %d %Y →"
        )
        visible_end_label = datetime.fromtimestamp(visible_end).strftime("%b %d %Y")
    result = [
        line(("LEGEND", "title")),
        plain("-" * LEGEND_WIDTH),
        line((" ", "forecast_empty"), (" unallocated background", "normal")),
        line(("█", "forecast_reserved"), (" reservation", "normal")),
        line(("█", "forecast_running"), (" running to time limit", "normal")),
        line(("█", "forecast_overlap"), (" reservation + running", "normal")),
        line(("█", "forecast_mine_usage"), (" includes your usage", "normal")),
        line(("█", "forecast_mine_reserved"), (" your reservation", "normal")),
        line(("█", "forecast_mine_overlap"), (" yours + overlap", "normal")),
        line(("▁…█", "normal"), (f" {resource_name} fraction", "normal")),
        line(("×", "drained"), (" down / drained now", "normal")),
        plain("fill rises bottom-up"),
        plain("reservations are whole node"),
        plain("recovery time is unknown"),
        plain("no pending-job estimates"),
        plain(""),
        line(("SCALE", "title")),
        plain(f"horizon {window_label(window_hours)}"),
        plain(f"1 cell = {resolution_label(resolution_minutes)}"),
        plain("local date + time" if show_clock else "local calendar dates"),
        plain(visible_start_label),
        plain(visible_end_label),
        plain(""),
        line(("KEYS", "title")),
        plain("+ finer   - coarser"),
        plain("[ / ]  change cluster"),
        plain("1-9  select cluster"),
        plain("↑/↓  scroll nodes"),
        plain("←/→  scroll time"),
        plain("r  refresh now"),
        plain("Tab  switch view"),
        plain("q  quit"),
        plain(""),
        line(("LAST REFRESH", "title")),
        plain(age),
    ]
    if refreshing:
        result.append(line(("refreshing…", "running")))
    else:
        remaining = max(0, refresh_seconds - (age_seconds or 0))
        result.append(plain(f"next in {remaining}s"))
    return result
