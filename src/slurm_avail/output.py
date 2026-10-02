"""Non-interactive dashboard output and loading placeholders."""

from __future__ import annotations

import time
from pathlib import Path

from .collect import cluster_user
from .config import AppConfig, DashboardSettings, active_cluster_configs
from .config_views import config_table
from .constants import CLUSTER_WIDTH, GAP, LOGIN_TABLE_WIDTH, Line
from .estimate_views import estimate_legend_lines, estimate_table
from .forecast_views import forecast_legend_lines, forecast_table
from .jobs_views import jobs_legend_lines, jobs_table
from .models import Cluster, EstimateRequest, LoginNode
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
from .text import tab_line
from .usage import UsageHistory, UsageResult
from .usage_views import usage_table


def placeholder_clusters(
    config: AppConfig,
    user_override: str | None,
) -> list[Cluster]:
    clusters: list[Cluster] = []
    for cluster_config in active_cluster_configs(config):
        endpoints = (
            ["local"] if cluster_config.mode == "local" else cluster_config.addresses
        )
        user = cluster_user(cluster_config, user_override)
        clusters.append(
            Cluster(
                name=cluster_config.name,
                host=endpoints[0],
                user=user,
                mode=cluster_config.mode,
                focus=cluster_config.focus,
                filesystem_paths=tuple(cluster_config.filesystems),
                login_nodes=[LoginNode(hostname=endpoint) for endpoint in endpoints],
                loading=True,
            )
        )
    return clusters


def spans_text(spans: Line) -> str:
    return "".join(text for text, _style in spans)


def render_once(
    clusters: list[Cluster],
    context: str,
    settings: DashboardSettings,
) -> None:
    print(spans_text(tab_line("nodes", context)))
    print()
    headers = [cluster_header(cluster) for cluster in clusters]
    bodies = [cluster_body(cluster, colored=False) for cluster in clusters]
    legend = legend_lines(
        age_seconds=0,
        refreshing=False,
        colored=False,
        refresh_seconds=settings.node_refresh_seconds,
    )
    header_height = max(len(header) for header in headers)
    body_height = max((len(body) for body in bodies), default=0)
    total_height = max(header_height + body_height, len(legend))

    for row in range(total_height):
        columns: list[str] = []
        for header, body in zip(headers, bodies, strict=True):
            if row < len(header):
                text = spans_text(header[row])
            elif row - header_height < len(body):
                text = spans_text(body[row - header_height])
            else:
                text = ""
            columns.append(text[:CLUSTER_WIDTH].ljust(CLUSTER_WIDTH))

        legend_text = spans_text(legend[row]) if row < len(legend) else ""
        print(" │ ".join(columns) + " │ " + legend_text)


def render_filesystems_once(
    clusters: list[Cluster],
    context: str,
    settings: DashboardSettings,
) -> None:
    print(spans_text(tab_line("filesystems", context)))
    print()
    table = filesystem_table(clusters)
    legend = filesystem_legend_lines(
        age_seconds=0,
        refreshing=False,
        refresh_seconds=settings.filesystem_refresh_seconds,
    )
    table_width = filesystem_table_width(clusters)
    total_height = max(len(table), len(legend))
    for row in range(total_height):
        table_text = spans_text(table[row]) if row < len(table) else ""
        legend_text = spans_text(legend[row]) if row < len(legend) else ""
        print(table_text[:table_width].ljust(table_width) + " │ " + legend_text)


def render_logins_once(
    clusters: list[Cluster],
    context: str,
    settings: DashboardSettings,
) -> None:
    print(spans_text(tab_line("logins", context)))
    print()
    table = login_table(clusters)
    legend = login_legend_lines(
        age_seconds=0,
        refreshing=False,
        refresh_seconds=settings.login_refresh_seconds,
    )
    total_height = max(len(table), len(legend))
    for row in range(total_height):
        table_text = spans_text(table[row]) if row < len(table) else ""
        legend_text = spans_text(legend[row]) if row < len(legend) else ""
        print(
            table_text[:LOGIN_TABLE_WIDTH].ljust(LOGIN_TABLE_WIDTH)
            + " │ "
            + legend_text
        )


def render_forecast_once(
    clusters: list[Cluster],
    context: str,
    selected_index: int,
    settings: DashboardSettings,
) -> None:
    print(spans_text(tab_line("forecast", context)))
    print()
    (
        headers,
        body,
        table_width,
        resource_name,
        _total_intervals,
        visible_start,
        visible_end,
    ) = forecast_table(
        clusters,
        selected_index,
        time.time(),
        colored=False,
        resolution_minutes=60,
        window_hours=settings.forecast_horizon_days * 24,
    )
    legend = forecast_legend_lines(
        age_seconds=0,
        refreshing=False,
        resource_name=resource_name,
        resolution_minutes=60,
        window_hours=settings.forecast_horizon_days * 24,
        visible_start=visible_start,
        visible_end=visible_end,
        refresh_seconds=settings.forecast_refresh_seconds,
    )
    header_height = len(headers)
    total_height = max(header_height + len(body), len(legend))
    legend_x = table_width + GAP
    for row in range(total_height):
        if row < header_height:
            table_text = spans_text(headers[row])
        elif row - header_height < len(body):
            table_text = spans_text(body[row - header_height])
        else:
            table_text = ""
        legend_text = spans_text(legend[row]) if row < len(legend) else ""
        print(table_text[:table_width].ljust(legend_x) + "│ " + legend_text)


def render_jobs_once(
    clusters: list[Cluster],
    context: str,
    selected_index: int,
    settings: DashboardSettings,
) -> None:
    print(spans_text(tab_line("jobs", context)))
    print()
    headers, body, details, table_width, _selected_body, _selected_job = jobs_table(
        clusters,
        selected_index,
        0,
    )
    legend = jobs_legend_lines(
        age_seconds=0,
        refreshing=False,
        refresh_seconds=settings.jobs_refresh_seconds,
        history_age_seconds=0,
        history_refreshing=False,
        history_refresh_seconds=settings.jobs_history_refresh_seconds,
    )
    table_rows = headers + body + details
    total_height = max(len(table_rows), len(legend))
    legend_x = table_width + GAP
    for row in range(total_height):
        table_text = spans_text(table_rows[row]) if row < len(table_rows) else ""
        legend_text = spans_text(legend[row]) if row < len(legend) else ""
        print(table_text[:table_width].ljust(legend_x) + "│ " + legend_text)


def render_estimate_once(
    clusters: list[Cluster],
    context: str,
) -> None:
    print(spans_text(tab_line("estimate", context)))
    print()
    headers, body, _selected_body, table_width = estimate_table(
        EstimateRequest(),
        clusters,
        {cluster.name for cluster in clusters},
        {},
        set(),
        0,
        "Open the live UI to edit and test this request",
    )
    legend = estimate_legend_lines(False)
    table_rows = headers + body
    total_height = max(len(table_rows), len(legend))
    legend_x = table_width + GAP
    for row in range(total_height):
        table_text = spans_text(table_rows[row]) if row < len(table_rows) else ""
        legend_text = spans_text(legend[row]) if row < len(legend) else ""
        print(table_text[:table_width].ljust(legend_x) + "│ " + legend_text)


def render_config_once(config: AppConfig, config_path: Path) -> None:
    print(spans_text(tab_line("config", f"config {config_path}")))
    print()
    headers, body, _selected = config_table(
        config,
        config_path,
        0,
        False,
        "",
    )
    for row in headers + body:
        print(spans_text(row))


def render_usage_once(
    history: UsageHistory,
    names: list[str],
    results: dict[str, UsageResult | None],
    context: str,
) -> None:
    print(spans_text(tab_line("usage", context)))
    headers, body = usage_table(history, names, results, width=100)
    for row in headers + body:
        print(spans_text(row))
