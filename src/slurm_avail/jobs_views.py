"""Current-user live and historical job presentation."""

from __future__ import annotations

import math
import re
from datetime import datetime

from .constants import JOBS_TABLE_WIDTH, LEGEND_WIDTH, Line
from .models import Cluster, FairshareAssociation, PriorityFactors, UserJob
from .text import line, plain, visible_length

CLUSTER_WIDTH = 10
JOB_WIDTH = 11
NAME_WIDTH = 20
STATE_WIDTH = 10
RESULT_WIDTH = 13
TIME_LIMIT_WIDTH = 11
NODES_WIDTH = 6
CPUS_WIDTH = 6
MEMORY_WIDTH = 9
GPUS_WIDTH = 14
PRIORITY_WIDTH = 9
START_WIDTH = 15
PLACEMENT_WIDTH = 11
ELAPSED_WIDTH = 11
FACTOR_BAR_WIDTH = 20


def clipped_cell(value: str, width: int) -> str:
    if len(value) <= width:
        return f"{value:<{width}}"
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def gpu_request_parts(tres: str) -> tuple[str, str] | None:
    matches = re.findall(
        r"(?:gres/)?gpu(?::([A-Za-z0-9_.-]+))?[:=](\d+)",
        tres,
    )
    if not matches:
        return None
    return next((match for match in matches if match[0]), matches[0])


def available_value(value: str) -> str:
    return "—" if value in ("", "0", "N/A", "None", "(null)") else value


def requested_cpu_count(job: UserJob) -> int:
    if job.cpu_count:
        return job.cpu_count
    match = re.search(r"(?:^|,)cpu=(\d+)(?:,|$)", job.tres_per_node)
    return int(match.group(1)) if match else 0


def inferred_gpu_request(cluster: Cluster, job: UserJob) -> str:
    requests = tuple(
        parts
        for tres in (job.tres_per_node, job.allocated_tres)
        if (parts := gpu_request_parts(tres)) is not None
    )
    if not requests:
        return "—"
    gpu_type, count = next(
        (parts for parts in requests if parts[0]),
        requests[0],
    )
    if not gpu_type:
        node_types = {
            node.gpu_type
            for node in cluster.nodes
            if node.gpu_type
            and (not job.scheduled_nodes or node.name in job.scheduled_nodes)
        }
        if len(node_types) == 1:
            gpu_type = node_types.pop()
    return f"{count}×{gpu_type or 'GPU'}"


def resource_values(cluster: Cluster, job: UserJob) -> tuple[str, str, str, str, str]:
    cpus = requested_cpu_count(job)
    return (
        available_value(job.time_limit),
        str(job.node_count) if job.node_count else "—",
        str(cpus) if cpus else "—",
        available_value(job.memory),
        inferred_gpu_request(cluster, job),
    )


def resource_summary(cluster: Cluster, job: UserJob) -> str:
    time_limit, nodes, cpus, memory, gpus = resource_values(cluster, job)
    return (
        f"time limit {time_limit}  nodes {nodes}  CPUs {cpus}  "
        f"memory {memory}  GPUs {gpus}"
    )


def table_header(columns: tuple[tuple[str, int], ...]) -> str:
    return " ".join(clipped_cell(label, width) for label, width in columns)


def placement_label(job: UserJob) -> tuple[str, str]:
    if job.historical:
        return "FINISHED", "normal"
    if job.placement == "running":
        return "RUNNING", "running"
    if job.placement == "planned":
        return "PLANNED", "free"
    if job.placement == "estimate":
        return "ESTIMATE", "reserved"
    return "NO ESTIMATE", "busy"


def priority_label(job: UserJob) -> str:
    if job.is_running or job.historical:
        return "—"
    if job.priority_factors is not None:
        return str(job.priority_factors.total)
    return job.priority or "—"


def state_style(job: UserJob) -> str:
    state = job.state.split(maxsplit=1)[0].rstrip("+").upper()
    if job.historical:
        if state == "COMPLETED":
            return "free"
        if state == "CANCELLED":
            return "offline"
        if state in {
            "BOOT_FAIL",
            "DEADLINE",
            "FAILED",
            "NODE_FAIL",
            "OUT_OF_MEMORY",
            "PREEMPTED",
            "REVOKED",
            "TIMEOUT",
        }:
            return "drained"
        return "normal"
    return "running" if job.is_running else "reserved"


def jobs_scope_menu(clusters: list[Cluster], selected_scope_index: int) -> Line:
    menu: Line = [("CLUSTER  ", "title")]
    scopes = [
        ("ALL", 0),
        *((cluster.name, index + 1) for index, cluster in enumerate(clusters)),
    ]
    for label, scope_index in scopes:
        if len(menu) > 1:
            menu.append(("  ", "normal"))
        text = f"[{scope_index} {label}]"
        style = "title" if scope_index == selected_scope_index else "normal"
        menu.append((text, style))
    return menu


def jobs_for_scope(
    clusters: list[Cluster],
    selected_scope_index: int,
) -> list[tuple[Cluster, UserJob]]:
    selected_scope_index = min(max(0, selected_scope_index), len(clusters))
    scoped_clusters = (
        clusters if selected_scope_index == 0 else [clusters[selected_scope_index - 1]]
    )
    entries = [
        (cluster, job)
        for cluster in scoped_clusters
        for job in (*cluster.jobs, *cluster.past_jobs)
    ]
    return sorted(
        entries,
        key=lambda entry: (
            2 if entry[1].historical else (0 if entry[1].is_running else 1),
            -(entry[1].end or entry[1].start or entry[1].submit or 0)
            if entry[1].historical
            else 0,
            entry[0].name.casefold(),
            entry[1].job_id,
        ),
    )


def compact_job_time(value: float | None) -> str:
    if value is None:
        return "—"
    return datetime.fromtimestamp(value).astimezone().strftime("%b %d %H:%M")


def live_job_row(cluster: Cluster, job: UserJob, selected: bool) -> Line:
    time_limit, nodes, cpus, memory, gpus = resource_values(cluster, job)
    start = compact_job_time(job.start)
    placement, placement_style = placement_label(job)
    state = job.state.split(maxsplit=1)[0].rstrip("+")
    values = (
        clipped_cell(cluster.name, CLUSTER_WIDTH),
        clipped_cell(job.job_id, JOB_WIDTH),
        clipped_cell(job.name, NAME_WIDTH),
        clipped_cell(state, STATE_WIDTH),
        clipped_cell(time_limit, TIME_LIMIT_WIDTH),
        clipped_cell(nodes, NODES_WIDTH),
        clipped_cell(cpus, CPUS_WIDTH),
        clipped_cell(memory, MEMORY_WIDTH),
        clipped_cell(gpus, GPUS_WIDTH),
        clipped_cell(priority_label(job), PRIORITY_WIDTH),
        clipped_cell(start, START_WIDTH),
        clipped_cell(placement, PLACEMENT_WIDTH),
    )
    if selected:
        return line((" ".join(values), "selected"))
    return line(
        (values[0], "normal"),
        (" ", "normal"),
        (values[1], "mine"),
        (" ", "normal"),
        (values[2], "normal"),
        (" ", "normal"),
        (values[3], state_style(job)),
        (" ", "normal"),
        (values[4], "normal"),
        (" ", "normal"),
        (values[5], "normal"),
        (" ", "normal"),
        (values[6], "normal"),
        (" ", "normal"),
        (values[7], "normal"),
        (" ", "normal"),
        (values[8], "normal"),
        (" ", "normal"),
        (values[9], "normal"),
        (" ", "normal"),
        (values[10], "normal"),
        (" ", "normal"),
        (values[11], placement_style),
    )


def history_job_row(cluster: Cluster, job: UserJob, selected: bool) -> Line:
    time_limit, nodes, cpus, memory, gpus = resource_values(cluster, job)
    state = job.state.split(maxsplit=1)[0].rstrip("+")
    values = (
        clipped_cell(cluster.name, CLUSTER_WIDTH),
        clipped_cell(job.job_id, JOB_WIDTH),
        clipped_cell(job.name, NAME_WIDTH),
        clipped_cell(state, RESULT_WIDTH),
        clipped_cell(available_value(job.elapsed), ELAPSED_WIDTH),
        clipped_cell(time_limit, TIME_LIMIT_WIDTH),
        clipped_cell(nodes, NODES_WIDTH),
        clipped_cell(cpus, CPUS_WIDTH),
        clipped_cell(memory, MEMORY_WIDTH),
        clipped_cell(gpus, GPUS_WIDTH),
        clipped_cell(compact_job_time(job.start), START_WIDTH),
        clipped_cell(compact_job_time(job.end), START_WIDTH),
    )
    if selected:
        return line((" ".join(values), "selected"))
    styles = (
        "normal",
        "mine",
        "normal",
        state_style(job),
        "normal",
        "normal",
        "normal",
        "normal",
        "normal",
        "normal",
        "normal",
        "normal",
    )
    row: Line = []
    for index, (value, style) in enumerate(zip(values, styles, strict=True)):
        if index:
            row.append((" ", "normal"))
        row.append((value, style))
    return row


def job_row(cluster: Cluster, job: UserJob, selected: bool) -> Line:
    if job.historical:
        return history_job_row(cluster, job, selected)
    return live_job_row(cluster, job, selected)


def factor_rows(factors: PriorityFactors) -> list[Line]:
    components = (
        ("fair-share", factors.fairshare, "mine"),
        ("age", factors.age, "cpu"),
        ("job size", factors.job_size, "memory"),
        ("association", factors.association, "exclusive"),
        ("partition", factors.partition, "free"),
        ("QoS", factors.qos, "reserved"),
        ("site", factors.site, "running"),
        ("nice", factors.nice, "drained"),
    )
    nonzero = [component for component in components if component[1] != 0]
    if not nonzero:
        return [plain("  No non-zero weighted components were returned.")]
    denominator = max(1, sum(max(0, value) for _label, value, _style in nonzero))
    rows: list[Line] = []
    for label, value, style in nonzero:
        slots = min(
            FACTOR_BAR_WIDTH,
            max(1, math.ceil(FACTOR_BAR_WIDTH * abs(value) / denominator)),
        )
        percent = 100 * value / denominator
        rows.append(
            line(
                (f"  {label:<12} ", "normal"),
                ("█" * slots, style),
                ("░" * (FACTOR_BAR_WIDTH - slots), "busy"),
                (f"  {value:>10}  {percent:>6.1f}%", "normal"),
            )
        )
    return rows


def optional_number(value: float | None, *, percent: bool = False) -> str:
    if value is None:
        return "—"
    if percent:
        return f"{value * 100:.4g}%"
    if abs(value) >= 1_000_000:
        return f"{value:.4g}"
    return f"{value:.6g}"


def association_row(association: FairshareAssociation) -> Line:
    owner = association.user or "account"
    shares = optional_number(association.normalized_shares, percent=True)
    usage = optional_number(association.effective_usage, percent=True)
    return plain(
        f"  {owner:<16} shares {shares:>10}"
        f"  usage {usage:>10}"
        f"  fair-share {optional_number(association.fairshare):>10}"
        f"  level-FS {optional_number(association.level_fairshare):>10}"
    )


def job_time(value: float | None) -> str:
    if value is None:
        return "—"
    return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def selected_job_details(cluster: Cluster, job: UserJob) -> list[Line]:
    placement, placement_style = placement_label(job)
    status = job.state if job.historical else placement
    status_style = state_style(job) if job.historical else placement_style
    rows: list[Line] = [
        plain("═" * JOBS_TABLE_WIDTH),
        line((f"SELECTED JOB {job.job_id} · {cluster.name}", "title")),
        line(
            ("owner ", "normal"),
            (cluster.user or "—", "mine"),
            (
                f"  name {job.name}  account {job.account or '—'}  "
                f"partition {job.partition or '—'}  QoS {job.qos or '—'}",
                "normal",
            ),
        ),
        line(
            ("status ", "normal"),
            (status, status_style),
            (
                f"  reason {job.reason}"
                if not job.historical and not job.is_running and job.reason
                else "",
                "normal",
            ),
        ),
        plain("request  " + resource_summary(cluster, job)),
        plain(
            f"submit {job_time(job.submit)}  start {job_time(job.start)}  "
            f"end {job_time(job.end)}"
        ),
    ]
    if job.scheduled_nodes:
        rows.append(plain("nodes  " + ", ".join(job.scheduled_nodes)))
    elif job.is_running:
        rows.append(plain("nodes  not returned by the scheduler"))
    elif not job.historical and job.start is not None:
        rows.append(plain("nodes  not assigned by the scheduler"))
    elif not job.historical:
        rows.append(plain(f"wait   {job.reason or 'no reason returned'}"))

    if job.historical:
        rows.append(
            plain(f"result elapsed {job.elapsed or '—'}  exit {job.exit_code or '—'}")
        )
        return rows

    rows.append(line(("WEIGHTED PRIORITY", "title")))
    if job.is_running:
        rows.append(plain("  Priority factors no longer affect a running job."))
    elif job.priority_factors is None:
        message = cluster.priority_error or "sprio returned no factors for this job"
        rows.append(line((f"  {message}", "offline")))
    else:
        rows.append(plain(f"  total        {job.priority_factors.total}"))
        rows.extend(factor_rows(job.priority_factors))

    rows.append(line(("FAIR-SHARE ASSOCIATION", "title")))
    associations = [
        association
        for association in cluster.fairshare
        if association.account == job.account
    ]
    if associations:
        rows.extend(association_row(association) for association in associations)
    else:
        message = cluster.fairshare_error or "sshare returned no matching association"
        rows.append(line((f"  {message}", "offline")))
    priority_type = cluster.priority_config.get("PriorityType", "not reported")
    rows.append(plain(f"scheduler {priority_type}"))
    return rows


def jobs_table(
    clusters: list[Cluster],
    selected_scope_index: int,
    selected_job_index: int,
) -> tuple[list[Line], list[Line], list[Line], int, int, int]:
    selected_scope_index = min(max(0, selected_scope_index), len(clusters))
    scoped_clusters = (
        clusters if selected_scope_index == 0 else [clusters[selected_scope_index - 1]]
    )
    jobs = jobs_for_scope(clusters, selected_scope_index)
    selected_job_index = min(max(0, selected_job_index), len(jobs) - 1) if jobs else 0
    menu = jobs_scope_menu(clusters, selected_scope_index)
    resource_columns = (
        ("TIME LIMIT", TIME_LIMIT_WIDTH),
        ("NODES", NODES_WIDTH),
        ("CPUS", CPUS_WIDTH),
        ("MEMORY", MEMORY_WIDTH),
        ("GPUS", GPUS_WIDTH),
    )
    live_column_header = table_header(
        (
            ("CLUSTER", CLUSTER_WIDTH),
            ("JOB", JOB_WIDTH),
            ("NAME", NAME_WIDTH),
            ("STATE", STATE_WIDTH),
            *resource_columns,
            ("PRIORITY", PRIORITY_WIDTH),
            ("START", START_WIDTH),
            ("PLACEMENT", PLACEMENT_WIDTH),
        )
    )
    history_column_header = table_header(
        (
            ("CLUSTER", CLUSTER_WIDTH),
            ("JOB", JOB_WIDTH),
            ("NAME", NAME_WIDTH),
            ("RESULT", RESULT_WIDTH),
            ("ELAPSED", ELAPSED_WIDTH),
            *resource_columns,
            ("STARTED", START_WIDTH),
            ("ENDED", START_WIDTH),
        )
    )
    context = "current users · scheduler start estimates are not guarantees"
    if selected_scope_index != 0:
        context = (
            f"user {scoped_clusters[0].user or '—'} · "
            "scheduler start estimates are not guarantees"
        )
    headers = [
        line(("RUNNING + PENDING", "title")),
        menu,
        plain(context),
        plain(""),
        line((live_column_header, "title")),
        plain("-" * len(live_column_header)),
    ]
    live_jobs = [entry for entry in jobs if not entry[1].historical]
    past_jobs = [entry for entry in jobs if entry[1].historical]
    body: list[Line] = []
    selected_body_index = 0
    job_index = 0
    for cluster, job in live_jobs:
        if job_index == selected_job_index:
            selected_body_index = len(body)
        body.append(job_row(cluster, job, job_index == selected_job_index))
        job_index += 1
    live_sources = [
        cluster
        for cluster in scoped_clusters
        if not cluster.error and not cluster.jobs_error
    ]
    if not live_jobs:
        if any(cluster.loading for cluster in scoped_clusters):
            body.append(line(("Loading live jobs…", "cpu")))
        elif live_sources:
            body.append(plain("No running or pending jobs for the selected user(s)."))

    body.extend(
        (
            plain(""),
            line(("RECENT HISTORY", "title")),
            line((history_column_header, "title")),
            plain("-" * len(history_column_header)),
        )
    )
    for cluster, job in past_jobs:
        if job_index == selected_job_index:
            selected_body_index = len(body)
        body.append(job_row(cluster, job, job_index == selected_job_index))
        job_index += 1
    history_sources = [
        cluster
        for cluster in scoped_clusters
        if not cluster.error and not cluster.history_error
    ]
    if not past_jobs:
        if any(cluster.loading for cluster in scoped_clusters):
            body.append(line(("Loading job history…", "cpu")))
        elif history_sources:
            body.append(plain("No recent job history for the selected user(s)."))
    details: list[Line] = []
    if jobs:
        selected_cluster, selected_job = jobs[selected_job_index]
        details = selected_job_details(selected_cluster, selected_job)
    table_width = max(
        JOBS_TABLE_WIDTH,
        max((visible_length(row) for row in headers + body + details), default=0),
    )
    return (
        headers,
        body,
        details,
        table_width,
        selected_body_index,
        selected_job_index,
    )


def sticky_jobs_headers(
    headers: list[Line],
    body: list[Line],
    vertical_offset: int,
) -> tuple[list[Line], int]:
    """Switch the fixed table header after the live section scrolls away."""
    history_title_index = next(
        (
            index
            for index, row in enumerate(body)
            if any(value == "RECENT HISTORY" for value, _style in row)
        ),
        None,
    )
    if history_title_index is None or vertical_offset < history_title_index - 1:
        return headers, vertical_offset
    history_rows_start = history_title_index + 3
    sticky = list(headers)
    sticky[0] = body[history_title_index]
    sticky[-2] = body[history_title_index + 1]
    sticky[-1] = body[history_title_index + 2]
    return sticky, max(vertical_offset, history_rows_start)


def jobs_legend_lines(
    age_seconds: int | None,
    refreshing: bool,
    refresh_seconds: int,
    history_age_seconds: int | None,
    history_refreshing: bool,
    history_refresh_seconds: int,
) -> list[Line]:
    age = "waiting for data" if age_seconds is None else f"{age_seconds}s ago"
    history_age = (
        "waiting for data"
        if history_age_seconds is None
        else f"{history_age_seconds}s ago"
    )
    result = [
        line(("LEGEND", "title")),
        plain("-" * LEGEND_WIDTH),
        line(("selected", "selected"), (" job", "normal")),
        line(("RUNNING", "running"), (" executing now", "normal")),
        line(("COMPLETED", "free"), (" successful past job", "normal")),
        line(("CANCELLED", "offline"), (" cancelled past job", "normal")),
        line(("FAILED", "drained"), (" failed past job", "normal")),
        line(("PLANNED", "free"), (" start + nodes", "normal")),
        line(("ESTIMATE", "reserved"), (" start only", "normal")),
        line(("NO ESTIMATE", "busy"), (" none returned", "normal")),
        line(("█", "mine"), (" fair-share priority", "normal")),
        line(("█", "cpu"), (" age priority", "normal")),
        line(("█", "memory"), (" job-size priority", "normal")),
        plain("bars are weighted points"),
        plain("values are scheduler facts"),
        plain("start times can move"),
        plain(""),
        line(("KEYS", "title")),
        plain("↑/↓  select job"),
        plain("[ / ]  change scope"),
        plain("0-9  All / cluster"),
        plain("Pg  page through jobs"),
        plain("←/→  scroll columns"),
        plain("r  refresh now"),
        plain("Tab  switch view"),
        plain("q  quit"),
        plain(""),
        line(("JOBS REFRESH", "title")),
        plain(age),
    ]
    if refreshing:
        result.append(line(("refreshing…", "running")))
    else:
        remaining = max(0, refresh_seconds - (age_seconds or 0))
        result.append(plain(f"next in {remaining}s"))
    result.extend([plain(""), line(("HISTORY REFRESH", "title")), plain(history_age)])
    if history_refreshing:
        result.append(line(("refreshing…", "running")))
    else:
        remaining = max(0, history_refresh_seconds - (history_age_seconds or 0))
        result.append(plain(f"next in {remaining}s"))
    return result
