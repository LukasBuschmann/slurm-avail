"""Scheduler estimate form, comparison table, and legend."""

from __future__ import annotations

import textwrap
import time
from datetime import datetime

from .constants import LEGEND_WIDTH, Line
from .estimator import (
    ESTIMATE_FIELDS,
    format_estimate_time,
    parse_estimate_time,
    request_value_text,
)
from .models import Cluster, EstimateRequest, EstimateResult
from .text import line, plain, visible_length

CLUSTER_WIDTH = 14
STATUS_WIDTH = 11
START_WIDTH = 16
WAIT_WIDTH = 10
PARTITION_WIDTH = 16
NODES_WIDTH = 24
PROCESSORS_WIDTH = 6


def estimate_wait_text(start: float | None, checked_at: float) -> str:
    if start is None:
        return "-"
    seconds = max(0, round(start - checked_at))
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def estimate_start_text(start: float | None) -> str:
    if start is None:
        return "-"
    return datetime.fromtimestamp(start).astimezone().strftime("%Y-%m-%d %H:%M")


def clipped(value: object, width: int) -> str:
    text = str(value)
    if len(text) > width:
        return text[: max(0, width - 1)] + "…"
    return f"{text:<{width}}"


def result_values(
    result: EstimateResult | None,
    pending: bool,
    selected: bool,
    now_epoch: float,
) -> tuple[tuple[str, str], str, str, str, str, str, str]:
    if not selected:
        return ("OFF", "busy"), "-", "-", "-", "-", "-", "not selected"
    if pending:
        return ("TESTING", "cpu"), "-", "-", "-", "-", "-", ""
    if result is None:
        return ("NOT TESTED", "busy"), "-", "-", "-", "-", "-", "press t"
    if result.status == "estimated":
        immediate = result.start is not None and result.start <= now_epoch + 60
        status = (
            "NOW" if immediate else "ESTIMATE",
            "free" if immediate else "reserved",
        )
        return (
            status,
            estimate_start_text(result.start),
            estimate_wait_text(result.start, result.checked_at),
            result.partition or "default",
            result.nodes or "-",
            str(result.processors) if result.processors else "-",
            "",
        )
    if result.status == "rejected":
        return ("REJECTED", "drained"), "-", "-", "-", "-", "-", result.message
    if result.status == "unknown":
        return ("NO ESTIMATE", "offline"), "-", "-", "-", "-", "-", result.message
    return ("ERROR", "offline"), "-", "-", "-", "-", "-", result.message


def request_field_line(
    request: EstimateRequest,
    field_index: int,
    selected: bool,
    adjusting: bool,
    time_component: int,
) -> Line:
    field = ESTIMATE_FIELDS[field_index]
    value = request_value_text(request, field)
    marker = "◆" if selected and adjusting else ">" if selected else " "
    prefix = f"{marker} {field.label:<22} "
    if not selected or not adjusting:
        return line(((prefix + value), "selected" if selected else "normal"))
    if field.kind != "time":
        return line((prefix, "normal"), (value, "selected"))

    normalized = format_estimate_time(parse_estimate_time(request.time_limit))
    parts = normalized.split(":")
    spans: Line = [(prefix, "normal")]
    for index, part in enumerate(parts):
        if index:
            spans.append((":", "normal"))
        spans.append((part, "selected" if index == time_component else "normal"))
    spans.append(("   H : M : S", "busy"))
    return spans


def selected_result_lines(
    cluster: Cluster,
    included: bool,
    pending: bool,
    result: EstimateResult | None,
    now_epoch: float,
) -> list[Line]:
    rows = [plain(""), line(("SELECTED RESULT", "title"))]
    status, start, wait, partition, nodes, processors, detail = result_values(
        result,
        pending,
        included,
        now_epoch,
    )
    rows.append(
        line(
            (f"{cluster.name}  ", "title"),
            (status[0], status[1]),
            (
                f"  endpoint {result.endpoint}"
                if result is not None and result.endpoint
                else "",
                "normal",
            ),
        )
    )
    if result is not None and result.status == "estimated":
        rows.append(
            plain(
                f"start {start}  wait {wait}  partition {partition}  "
                f"nodes {nodes}  processors {processors}"
            )
        )
    elif detail:
        rows.extend(
            line((wrapped_line, "offline"))
            for wrapped_line in textwrap.wrap(detail, width=110) or [detail]
        )
    return rows


def estimate_table(
    request: EstimateRequest,
    clusters: list[Cluster],
    selected_clusters: set[str],
    results: dict[str, EstimateResult],
    pending_clusters: set[str],
    selection: int,
    message: str,
    now_epoch: float | None = None,
    adjusting: bool = False,
    time_component: int = 0,
) -> tuple[list[Line], list[Line], int, int]:
    now_epoch = time.time() if now_epoch is None else now_epoch
    headers = [
        line(("SCHEDULER TEST REQUEST", "title")),
        plain("srun --test-only asks Slurm for a start estimate. It submits no job."),
        line(
            (
                (message or "Edit the request, choose clusters, then press t")[:140],
                "busy",
            )
        ),
    ]
    body: list[Line] = [line(("REQUEST", "title"))]
    selected_body_index = 0
    for index, _field in enumerate(ESTIMATE_FIELDS):
        if selection == index:
            selected_body_index = len(body)
        body.append(
            request_field_line(
                request,
                index,
                selection == index,
                adjusting and selection == index,
                time_component,
            )
        )

    field_count = len(ESTIMATE_FIELDS)
    run_selected = selection == field_count
    if run_selected:
        selected_body_index = len(body) + 1
    run_label = (
        f"[ TESTING {len(pending_clusters)} CLUSTERS ]"
        if pending_clusters
        else "[ RUN TEST ]"
    )
    body.extend(
        [
            plain(""),
            line((f"  {run_label}", "selected" if run_selected else "title")),
            plain(""),
            line(("CLUSTERS AND RESULTS", "title")),
        ]
    )
    result_header = (
        f"{'USE':<4} {'CLUSTER':<{CLUSTER_WIDTH}} "
        f"{'STATUS':<{STATUS_WIDTH}} {'START':<{START_WIDTH}} "
        f"{'WAIT':<{WAIT_WIDTH}} {'PARTITION':<{PARTITION_WIDTH}} "
        f"{'NODES':<{NODES_WIDTH}} {'CPUS':<{PROCESSORS_WIDTH}}"
    )
    body.extend([plain(result_header), plain("-" * len(result_header))])
    selected_folded = {name.casefold() for name in selected_clusters}
    pending_folded = {name.casefold() for name in pending_clusters}
    results_folded = {name.casefold(): result for name, result in results.items()}
    selected_cluster: Cluster | None = None
    selected_result: EstimateResult | None = None
    selected_included = False
    selected_pending = False
    for cluster_index, cluster in enumerate(clusters):
        selection_index = field_count + 1 + cluster_index
        row_selected = selection == selection_index
        if row_selected:
            selected_body_index = len(body)
        included = cluster.name.casefold() in selected_folded
        result = results_folded.get(cluster.name.casefold())
        status, start, wait, partition, nodes, processors, detail = result_values(
            result,
            cluster.name.casefold() in pending_folded,
            included,
            now_epoch,
        )
        marker = ">" if row_selected else " "
        checkbox = "[x]" if included else "[ ]"
        cursor_style = "selected" if row_selected else "normal"
        body.append(
            line(
                (f"{marker}{checkbox} ", cursor_style),
                (clipped(cluster.name, CLUSTER_WIDTH) + " ", cursor_style),
                (clipped(status[0], STATUS_WIDTH) + " ", status[1]),
                (clipped(start, START_WIDTH) + " ", "normal"),
                (clipped(wait, WAIT_WIDTH) + " ", "normal"),
                (clipped(partition, PARTITION_WIDTH) + " ", "normal"),
                (clipped(nodes, NODES_WIDTH) + " ", "normal"),
                (clipped(processors, PROCESSORS_WIDTH), "normal"),
            )
        )
        if row_selected:
            selected_cluster = cluster
            selected_result = result
            selected_included = included
            selected_pending = cluster.name.casefold() in pending_folded

    if selected_cluster is not None:
        body.extend(
            selected_result_lines(
                selected_cluster,
                selected_included,
                selected_pending,
                selected_result,
                now_epoch,
            )
        )

    table_width = max(visible_length(row) for row in headers + body)
    return headers, body, selected_body_index, table_width


def estimate_legend_lines(
    running: bool,
    adjusting: bool = False,
    adjusting_time: bool = False,
) -> list[Line]:
    controls = (
        [
            plain("↑/↓  change value"),
            plain("←/→  choose H/M/S" if adjusting_time else "←/→  change value"),
            plain("Enter/Esc  finish"),
        ]
        if adjusting
        else [
            plain("Enter  adjust / run"),
            plain("e  type exact value"),
            plain("Space  toggle cluster"),
            plain("t  run test shortcut"),
            plain("↑/↓  select"),
            plain("←/→  scroll sideways"),
        ]
    )
    return [
        line(("ESTIMATOR", "title")),
        plain("-" * LEGEND_WIDTH),
        *controls,
        plain("PgUp/PgDn  page"),
        plain("Tab  switch view"),
        plain("q  quit"),
        plain(""),
        line(("METHOD", "title")),
        plain("No job is submitted."),
        plain("Each test queries Slurm."),
        plain("Blank optional fields use"),
        plain("each cluster's defaults."),
        plain("The start time can change"),
        plain("as the queue changes."),
        plain(""),
        line(("STATE", "title")),
        line(("testing" if running else "ready", "cpu" if running else "free")),
    ]
