"""Current-user live and historical job presentation."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .constants import JOBS_TABLE_WIDTH, LEGEND_WIDTH, Line
from .models import Cluster, FairshareAssociation, PriorityFactors, UserJob
from .text import compact_memory, line, plain, visible_length

CLUSTER_WIDTH = 10
NAME_WIDTH = 24
RESULT_WIDTH = 13
TIME_LIMIT_WIDTH = 11
NODES_WIDTH = 6
CPUS_WIDTH = 6
MEMORY_WIDTH = 9
GPUS_WIDTH = 14
START_WIDTH = 15
PLACEMENT_WIDTH = 11
ELAPSED_WIDTH = 11
FACTOR_BAR_WIDTH = 20
PRIORITY_HISTOGRAM_WIDTH = 64
PRIORITY_LEVELS = "▁▂▃▄▅▆▇█"
GROUP_PREVIEW_COUNT = 5
GROUP_MARKER = "▾ "
GROUP_GUTTER = "  "
GROUP_CHILD = "│ "
GROUP_END = "└ "
DETAIL_BAR_WIDTH = 28
ELAPSED_HISTOGRAM_MAX_BINS = 24
HISTOGRAM_LEVELS = "▁▂▃▄▅▆▇█"
CLUSTER_STYLES = ("cpu", "memory", "exclusive", "mine", "running")
FAILED_STATES = {
    "BOOT_FAIL",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}


@dataclass(frozen=True)
class JobGroupKey:
    """Stable identity for a live or historical group of equally named jobs."""

    name: str
    historical: bool


@dataclass(frozen=True)
class JobGroup:
    """One selectable item in the grouped jobs list."""

    key: JobGroupKey
    entries: tuple[tuple[Cluster, UserJob], ...]

    @property
    def grouped(self) -> bool:
        return len(self.entries) > 1


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


def state_style(job: UserJob) -> str:
    state = job.state.split(maxsplit=1)[0].rstrip("+").upper()
    if job.historical:
        if state == "COMPLETED":
            return "free"
        if state == "CANCELLED":
            return "offline"
        if state in FAILED_STATES:
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
    name_filter: str = "",
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
    ordered = sorted(
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
    return [
        entry for entry in ordered if fuzzy_job_name_match(entry[1].name, name_filter)
    ]


def job_groups_for_scope(
    clusters: list[Cluster],
    selected_scope_index: int,
    name_filter: str = "",
    group_by_name: bool = True,
) -> list[JobGroup]:
    """Return selectable jobs, combining exact names within each table section."""
    entries = jobs_for_scope(clusters, selected_scope_index, name_filter)
    if not group_by_name:
        return [
            JobGroup(
                JobGroupKey(job.name, job.historical),
                ((cluster, job),),
            )
            for cluster, job in entries
        ]

    grouped: dict[JobGroupKey, list[tuple[Cluster, UserJob]]] = {}
    for cluster, job in entries:
        key = JobGroupKey(job.name, job.historical)
        grouped.setdefault(key, []).append((cluster, job))
    return [
        JobGroup(key, tuple(group_entries)) for key, group_entries in grouped.items()
    ]


def jobs_in_group(
    clusters: list[Cluster],
    selected_scope_index: int,
    key: JobGroupKey,
    name_filter: str = "",
) -> list[tuple[Cluster, UserJob]]:
    return [
        entry
        for entry in jobs_for_scope(clusters, selected_scope_index, name_filter)
        if entry[1].name == key.name and entry[1].historical == key.historical
    ]


def jobs_selection_count(
    clusters: list[Cluster],
    selected_scope_index: int,
    name_filter: str = "",
    group_by_name: bool = True,
    drilldown: JobGroupKey | None = None,
) -> int:
    """Number of rows reachable with the jobs selection cursor."""
    if drilldown is None:
        return len(
            job_groups_for_scope(
                clusters,
                selected_scope_index,
                name_filter,
                group_by_name,
            )
        )
    return len(jobs_in_group(clusters, selected_scope_index, drilldown, name_filter))


def selected_job_group(
    clusters: list[Cluster],
    selected_scope_index: int,
    selected_index: int,
    name_filter: str = "",
) -> JobGroup | None:
    groups = job_groups_for_scope(
        clusters,
        selected_scope_index,
        name_filter,
        True,
    )
    if not groups:
        return None
    return groups[min(max(0, selected_index), len(groups) - 1)]


def fuzzy_job_name_match(job_name: str, query: str) -> bool:
    """Match every query word as a case-insensitive subsequence of the name."""
    candidate = job_name.casefold()
    for token in query.casefold().split():
        position = 0
        for character in token:
            position = candidate.find(character, position)
            if position < 0:
                return False
            position += 1
    return True


def jobs_filter_line(
    clusters: list[Cluster],
    selected_scope_index: int,
    name_filter: str,
    editing: bool = False,
) -> Line:
    if not editing and not name_filter:
        return []
    selected_scope_index = min(max(0, selected_scope_index), len(clusters))
    scoped = (
        clusters if selected_scope_index == 0 else [clusters[selected_scope_index - 1]]
    )
    matches = sum(
        fuzzy_job_name_match(job.name, name_filter)
        for cluster in scoped
        for jobs in (cluster.jobs, cluster.past_jobs)
        for job in jobs
    )
    query = name_filter[-39:] + "_" if editing else name_filter[-40:]
    return line((f'filter "{query}" · {matches} matches', "title"))


def compact_job_time(value: float | None) -> str:
    if value is None:
        return "—"
    return datetime.fromtimestamp(value).astimezone().strftime("%b %d %H:%M")


def live_job_row(
    cluster: Cluster,
    job: UserJob,
    selected: bool,
    show_name: bool = True,
) -> Line:
    time_limit, nodes, cpus, memory, gpus = resource_values(cluster, job)
    start = compact_job_time(job.start)
    placement, placement_style = placement_label(job)
    values = (
        clipped_cell(job.name if show_name else "", NAME_WIDTH),
        clipped_cell(cluster.name, CLUSTER_WIDTH),
        clipped_cell(time_limit, TIME_LIMIT_WIDTH),
        clipped_cell(nodes, NODES_WIDTH),
        clipped_cell(cpus, CPUS_WIDTH),
        clipped_cell(memory, MEMORY_WIDTH),
        clipped_cell(gpus, GPUS_WIDTH),
        clipped_cell(start, START_WIDTH),
        clipped_cell(placement, PLACEMENT_WIDTH),
    )
    if selected:
        return line((" ".join(values), "selected"))
    return line(
        (values[0], "normal"),
        (" ", "normal"),
        (values[1], "normal"),
        (" ", "normal"),
        (values[2], "normal"),
        (" ", "normal"),
        (values[3], "normal"),
        (" ", "normal"),
        (values[4], "normal"),
        (" ", "normal"),
        (values[5], "normal"),
        (" ", "normal"),
        (values[6], "normal"),
        (" ", "normal"),
        (values[7], "normal"),
        (" ", "normal"),
        (values[8], placement_style),
    )


def history_job_row(
    cluster: Cluster,
    job: UserJob,
    selected: bool,
    show_name: bool = True,
) -> Line:
    time_limit, nodes, cpus, memory, gpus = resource_values(cluster, job)
    state = job.state.split(maxsplit=1)[0].rstrip("+")
    values = (
        clipped_cell(job.name if show_name else "", NAME_WIDTH),
        clipped_cell(cluster.name, CLUSTER_WIDTH),
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


def job_row(
    cluster: Cluster,
    job: UserJob,
    selected: bool,
    show_name: bool = True,
) -> Line:
    if job.historical:
        return history_job_row(cluster, job, selected, show_name)
    return live_job_row(cluster, job, selected, show_name)


def group_job_row(group: JobGroup, selected: bool) -> Line:
    return line(
        (
            GROUP_MARKER + clipped_cell(group.key.name, NAME_WIDTH),
            "selected" if selected else "title",
        )
    )


def table_job_row(
    cluster: Cluster,
    job: UserJob,
    selected: bool,
    show_name: bool = True,
    gutter: str = GROUP_GUTTER,
) -> Line:
    """Reserve the group-marker gutter without shifting table columns."""
    gutter_style = "normal"
    if gutter != GROUP_GUTTER:
        gutter_style = "busy"
    if selected:
        gutter_style = "selected"
    return line(
        (gutter, gutter_style),
        *job_row(cluster, job, selected, show_name),
    )


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


def priority_distribution_rows(cluster: Cluster, job: UserJob) -> list[Line]:
    """Draw the selected pending job within visible cluster priorities."""
    if job.is_running or job.historical:
        return []

    rows: list[Line] = []
    if cluster.pending_priorities_error:
        rows.append(line(("  Priority distribution unavailable.", "offline")))
        return rows

    selected = next(
        (item for item in cluster.pending_priorities if item.job_id == job.job_id),
        None,
    )
    if selected is None:
        rows.append(
            line(("  Selected job is not in the current queue snapshot.", "offline"))
        )
        return rows

    priorities = [item.priority for item in cluster.pending_priorities]
    positive_priorities = [priority for priority in priorities if priority > 0]
    has_zero_bucket = len(positive_priorities) != len(priorities)
    zero_offset = int(has_zero_bucket)
    if positive_priorities:
        positive_minimum = min(positive_priorities)
        positive_maximum = max(positive_priorities)
        positive_span = positive_maximum - positive_minimum + 1
        positive_bin_count = min(
            PRIORITY_HISTOGRAM_WIDTH - zero_offset,
            positive_span,
        )
        log_minimum = math.log(positive_minimum)
        log_maximum = math.log(positive_maximum)
        log_width = (
            (log_maximum - log_minimum) / positive_bin_count
            if log_maximum > log_minimum
            else 1.0
        )
    else:
        positive_bin_count = 0
        log_minimum = 0.0
        log_maximum = 0.0
        log_width = 1.0
    bin_count = zero_offset + positive_bin_count

    def bucket_index(priority: int) -> int:
        if priority <= 0:
            return 0
        if log_maximum == log_minimum:
            return zero_offset
        return zero_offset + min(
            positive_bin_count - 1,
            int((math.log(priority) - log_minimum) / log_width),
        )

    counts = [0] * bin_count
    for priority in priorities:
        index = bucket_index(priority)
        counts[index] += 1
    selected_bin = bucket_index(selected.priority)
    peak = max(counts)
    glyphs = [
        " "
        if count == 0
        else PRIORITY_LEVELS[
            min(
                len(PRIORITY_LEVELS) - 1,
                math.ceil(len(PRIORITY_LEVELS) * count / peak) - 1,
            )
        ]
        for count in counts
    ]
    chart = line(("  ", "normal"))
    for index, glyph in enumerate(glyphs):
        chart.append((glyph, "mine" if index == selected_bin else "cpu"))
    rows.append(chart)

    tick_count = min(5, bin_count)
    tick_positions = (
        [0]
        if tick_count == 1
        else sorted(
            {
                round(index * (bin_count - 1) / (tick_count - 1))
                for index in range(tick_count)
            }
        )
    )
    axis = ["─"] * bin_count
    for position in tick_positions:
        axis[position] = "┼"
    axis[0] = "├"
    if bin_count > 1:
        axis[-1] = "┤"
    rows.append(plain("  " + "".join(axis)))

    def tick_value(position: int) -> int:
        if position == 0 and has_zero_bucket:
            return 0
        if positive_bin_count <= 1:
            return positive_priorities[0] if positive_priorities else 0
        positive_position = position - zero_offset
        ratio = positive_position / (positive_bin_count - 1)
        return round(math.exp(log_minimum + ratio * (log_maximum - log_minimum)))

    def compact_tick(value: int) -> str:
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.3g}G"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.3g}M"
        if value >= 1_000:
            return f"{value / 1_000:.3g}k"
        return str(value)

    labels = [" "] * bin_count
    occupied = [False] * bin_count
    for position in tick_positions:
        label = compact_tick(tick_value(position))
        start = min(max(0, position - len(label) // 2), bin_count - len(label))
        end = start + len(label)
        if start < 0 or end > bin_count or any(occupied[start:end]):
            continue
        labels[start:end] = label
        occupied[start:end] = [True] * len(label)
    rows.append(plain("  " + "".join(labels).rstrip()))

    higher = sum(priority > selected.priority for priority in priorities)
    equal = sum(priority == selected.priority for priority in priorities)
    lower = len(priorities) - higher - equal
    rows.append(plain(f"  higher {higher:,} · equal {equal:,} · lower {lower:,}"))
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


def proportional_slots(counts: list[int], width: int) -> list[int]:
    """Allocate a fixed-width bar while keeping every non-empty part visible."""
    slots = [0] * len(counts)
    active = [index for index, count in enumerate(counts) if count > 0]
    if not active or width <= 0:
        return slots
    if len(active) >= width:
        for index in active[:width]:
            slots[index] = 1
        return slots

    for index in active:
        slots[index] = 1
    remaining = width - len(active)
    total = sum(counts[index] for index in active)
    exact = [remaining * counts[index] / total for index in active]
    for index, share in zip(active, exact, strict=True):
        slots[index] += math.floor(share)
    leftover = width - sum(slots)
    ranked = sorted(
        range(len(active)),
        key=lambda position: exact[position] - math.floor(exact[position]),
        reverse=True,
    )
    for position in ranked[:leftover]:
        slots[active[position]] += 1
    return slots


def portion_row(
    label: str,
    segments: list[tuple[str, int, str]],
    *,
    show_zero: bool = False,
) -> Line:
    """Render exact counts beside a proportional colored bar."""
    visible = [segment for segment in segments if segment[1] > 0]
    if not visible:
        return plain(f"{label:<10}—")
    slots = proportional_slots(
        [count for _name, count, _style in visible],
        DETAIL_BAR_WIDTH,
    )
    row = line((f"{label:<10}[", "normal"))
    for (_name, _count, style), slot_count in zip(visible, slots, strict=True):
        row.append(("█" * slot_count, style))
    row.append(("]  ", "normal"))
    labels = segments if show_zero else visible
    for index, (name, count, style) in enumerate(labels):
        if index:
            row.append(("  ", "normal"))
        row.append((f"{count:,} {name}", style))
    return row


def requested_memory_mb(job: UserJob) -> int | None:
    """Return total requested memory, including Slurm's per-CPU/node suffix."""
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)([KMGTP]?)([cn]?)\s*",
        job.memory,
        re.IGNORECASE,
    )
    if match is None:
        return None
    amount_text, raw_unit, scope = match.groups()
    factors = {
        "": 1,
        "K": 1 / 1024,
        "M": 1,
        "G": 1024,
        "T": 1024**2,
        "P": 1024**3,
    }
    multiplier = (
        max(1, requested_cpu_count(job))
        if scope.lower() == "c"
        else max(1, job.node_count)
    )
    return math.ceil(float(amount_text) * factors[raw_unit.upper()] * multiplier)


def formatted_memory_total(memory_mb: int) -> str:
    if memory_mb < 1024:
        return f"{memory_mb}M"
    return compact_memory(memory_mb)


def group_resource_row(jobs: list[UserJob]) -> Line:
    cores = sum(requested_cpu_count(job) for job in jobs)
    memory_values = [requested_memory_mb(job) for job in jobs]
    known_memory = [value for value in memory_values if value is not None]
    memory_text = (
        formatted_memory_total(sum(known_memory)) if known_memory else "unknown"
    )
    unknown_count = len(memory_values) - len(known_memory)
    suffix = f"  ·  {unknown_count} RAM requests unknown" if unknown_count else ""
    return plain(f"resources {cores:,} CPU cores  ·  {memory_text} RAM{suffix}")


def parse_slurm_duration(value: str) -> int | None:
    value = value.strip()
    if value and value not in ("—", "N/A", "Unknown", "None", "(null)"):
        day_text, separator, clock_text = value.partition("-")
        if separator and not day_text.isdigit():
            return None
        days = int(day_text) if separator and day_text.isdigit() else 0
        raw_clock = clock_text if separator else day_text
        parts = raw_clock.split(":")
        if all(part.isdigit() for part in parts):
            numbers = [int(part) for part in parts]
            if len(numbers) == 3:
                hours, minutes, seconds = numbers
                return ((days * 24 + hours) * 60 + minutes) * 60 + seconds
            if len(numbers) == 2:
                minutes, seconds = numbers
                return (days * 24 * 60 + minutes) * 60 + seconds
            if len(numbers) == 1 and not separator:
                return numbers[0] * 60
    return None


def elapsed_seconds(job: UserJob) -> int | None:
    parsed = parse_slurm_duration(job.elapsed)
    if parsed is not None:
        return parsed
    if job.start is not None and job.end is not None:
        return max(0, round(job.end - job.start))
    return None


def requested_gpu_count(job: UserJob) -> int:
    allocated = gpu_request_parts(job.allocated_tres)
    if allocated is not None:
        return int(allocated[1])
    requested = gpu_request_parts(job.tres_per_node)
    if requested is None:
        return 0
    count = int(requested[1])
    return count if job.historical else count * max(1, job.node_count)


def formatted_hours(hours: float) -> str:
    if hours == 0:
        return "0"
    if hours < 0.01:
        return "<0.01"
    if hours >= 100:
        return f"{hours:,.0f}"
    precision = 1 if hours >= 10 else 2
    return f"{hours:,.{precision}f}".rstrip("0").rstrip(".")


def group_compute_hours_row(group: JobGroup, jobs: list[UserJob]) -> Line:
    durations = [
        elapsed_seconds(job)
        if group.key.historical
        else parse_slurm_duration(job.time_limit)
        for job in jobs
    ]
    known = [
        (job, duration)
        for job, duration in zip(jobs, durations, strict=True)
        if duration is not None
    ]
    cpu_hours = (
        sum(requested_cpu_count(job) * duration for job, duration in known) / 3600
    )
    gpu_hours = (
        sum(requested_gpu_count(job) * duration for job, duration in known) / 3600
    )
    parts = [f"{formatted_hours(cpu_hours)} CPU h"]
    if any(requested_gpu_count(job) for job in jobs):
        parts.append(f"{formatted_hours(gpu_hours)} GPU h")
    basis = "elapsed" if group.key.historical else "max at time limits"
    unknown_count = len(jobs) - len(known)
    suffix = f"  ·  {unknown_count} jobs unknown" if unknown_count else ""
    return plain(f"compute   {'  ·  '.join(parts)}  {basis}{suffix}")


def compact_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{remainder:02d}s" if remainder else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h" if hours else f"{days}d"


def elapsed_histogram_row(jobs: list[UserJob]) -> Line:
    values = sorted(
        seconds for job in jobs if (seconds := elapsed_seconds(job)) is not None
    )
    if not values:
        return plain("elapsed   unavailable")
    if values[0] == values[-1]:
        return line(
            ("elapsed   [", "normal"),
            ("█" * 8, "cpu"),
            ("]  ", "normal"),
            (f"all {compact_duration(values[0])}", "cpu"),
        )

    bin_count = min(
        ELAPSED_HISTOGRAM_MAX_BINS,
        max(8, math.ceil(math.sqrt(len(values)) * 2)),
    )
    counts = [0] * bin_count
    span = values[-1] - values[0]
    for value in values:
        index = min(bin_count - 1, (value - values[0]) * bin_count // span)
        counts[index] += 1
    maximum = max(counts)
    bars = [
        " "
        if count == 0
        else HISTOGRAM_LEVELS[
            min(
                len(HISTOGRAM_LEVELS) - 1,
                math.ceil(count * len(HISTOGRAM_LEVELS) / maximum) - 1,
            )
        ]
        for count in counts
    ]
    middle = len(values) // 2
    median = (
        values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    )
    return line(
        (f"elapsed   {compact_duration(values[0]):>7} [", "normal"),
        ("".join(bars), "cpu"),
        ("] ", "normal"),
        (f"{compact_duration(values[-1]):<7}", "normal"),
        (f"  median {compact_duration(median)}", "busy"),
    )


def cluster_distribution_row(group: JobGroup) -> Line:
    counts = Counter(cluster.name for cluster, _job in group.entries)
    if len(counts) == 1:
        return plain(f"clusters  {next(iter(counts))}")
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return portion_row(
        "clusters",
        [
            (name, count, CLUSTER_STYLES[index % len(CLUSTER_STYLES)])
            for index, (name, count) in enumerate(ordered)
        ],
    )


def selected_job_details(cluster: Cluster, job: UserJob) -> list[Line]:
    placement, placement_style = placement_label(job)
    status = job.state if job.historical else placement
    status_style = state_style(job) if job.historical else placement_style
    rows: list[Line] = [
        line(("─" * JOBS_TABLE_WIDTH, "busy")),
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

    rows.extend(priority_distribution_rows(cluster, job))

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


def selected_group_details(group: JobGroup) -> list[Line]:
    jobs = [job for _cluster, job in group.entries]
    rows = [
        line(("─" * JOBS_TABLE_WIDTH, "busy")),
        line(
            ("JOB GROUP  ", "title"),
            (group.key.name, "title"),
            (
                f"  {len(jobs):,} "
                f"{'history' if group.key.historical else 'current'} jobs",
                "normal",
            ),
        ),
        cluster_distribution_row(group),
        group_resource_row(jobs),
        group_compute_hours_row(group, jobs),
    ]
    if group.key.historical:
        states = Counter(
            job.state.split(maxsplit=1)[0].rstrip("+").upper() for job in jobs
        )
        succeeded = states.pop("COMPLETED", 0)
        cancelled = states.pop("CANCELLED", 0)
        failed = sum(states.pop(state, 0) for state in FAILED_STATES)
        other = sum(states.values())
        rows.append(
            portion_row(
                "results",
                [
                    ("succeeded", succeeded, "free"),
                    ("cancelled", cancelled, "offline"),
                    ("failed", failed, "drained"),
                    *([("other", other, "normal")] if other else []),
                ],
                show_zero=True,
            )
        )
        rows.append(elapsed_histogram_row(jobs))
    else:
        running = sum(job.is_running for job in jobs)
        pending = len(jobs) - running
        rows.append(
            portion_row(
                "status",
                [
                    ("running", running, "running"),
                    ("pending", pending, "reserved"),
                ],
                show_zero=True,
            )
        )
    rows.append(line(("Enter opens every job in this group.", "busy")))
    return rows


def jobs_table(
    clusters: list[Cluster],
    selected_scope_index: int,
    selected_job_index: int,
    name_filter: str = "",
    group_by_name: bool = True,
    drilldown: JobGroupKey | None = None,
) -> tuple[list[Line], list[Line], list[Line], int, int, int]:
    selected_scope_index = min(max(0, selected_scope_index), len(clusters))
    scoped_clusters = (
        clusters if selected_scope_index == 0 else [clusters[selected_scope_index - 1]]
    )
    menu = jobs_scope_menu(clusters, selected_scope_index)
    resource_columns = (
        ("TIME LIMIT", TIME_LIMIT_WIDTH),
        ("NODES", NODES_WIDTH),
        ("CPUS", CPUS_WIDTH),
        ("MEMORY", MEMORY_WIDTH),
        ("GPUS", GPUS_WIDTH),
    )
    live_column_header = GROUP_GUTTER + table_header(
        (
            ("NAME", NAME_WIDTH),
            ("CLUSTER", CLUSTER_WIDTH),
            *resource_columns,
            ("START", START_WIDTH),
            ("PLACEMENT", PLACEMENT_WIDTH),
        )
    )
    history_column_header = GROUP_GUTTER + table_header(
        (
            ("NAME", NAME_WIDTH),
            ("CLUSTER", CLUSTER_WIDTH),
            ("RESULT", RESULT_WIDTH),
            ("ELAPSED", ELAPSED_WIDTH),
            *resource_columns,
            ("STARTED", START_WIDTH),
            ("ENDED", START_WIDTH),
        )
    )
    if drilldown is not None:
        entries = jobs_in_group(
            clusters,
            selected_scope_index,
            drilldown,
            name_filter,
        )
        selection_count = len(entries)
        selected_job_index = (
            min(max(0, selected_job_index), selection_count - 1)
            if selection_count
            else 0
        )
        column_header = (
            history_column_header if drilldown.historical else live_column_header
        )
        headers = [
            menu,
            plain(""),
            line(
                (f'JOBS NAMED "{drilldown.name}"', "title"),
                (f"  {selection_count:,} jobs", "busy"),
            ),
            line((column_header, "title")),
            plain("─" * len(column_header)),
        ]
        body: list[Line] = []
        selected_body_index = 0
        for index, (cluster, job) in enumerate(entries):
            if index == selected_job_index:
                selected_body_index = len(body)
            body.append(table_job_row(cluster, job, index == selected_job_index))
        details: list[Line] = []
        if entries:
            selected_cluster, selected_job = entries[selected_job_index]
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

    groups = job_groups_for_scope(
        clusters,
        selected_scope_index,
        name_filter,
        group_by_name,
    )
    selected_job_index = (
        min(max(0, selected_job_index), len(groups) - 1) if groups else 0
    )
    live_groups = [group for group in groups if not group.key.historical]
    past_groups = [group for group in groups if group.key.historical]
    live_count = sum(len(group.entries) for group in live_groups)
    running_count = sum(
        job.is_running for group in live_groups for _cluster, job in group.entries
    )
    pending_count = live_count - running_count
    past_count = sum(len(group.entries) for group in past_groups)
    headers = [
        menu,
        plain(""),
        line(
            ("RUNNING + PENDING", "title"),
            ("  ", "normal"),
            (f"{running_count:,} running", "running"),
            (" · ", "busy"),
            (f"{pending_count:,} pending", "reserved"),
        ),
        line((live_column_header, "title")),
        plain("─" * len(live_column_header)),
    ]
    body = []
    selected_body_index = 0
    group_index = 0

    def append_group(group: JobGroup) -> None:
        nonlocal group_index, selected_body_index
        selected = group_index == selected_job_index
        if selected:
            selected_body_index = len(body)
        if group.grouped and group_by_name:
            body.append(group_job_row(group, selected))
            preview = group.entries[:GROUP_PREVIEW_COUNT]
            remaining = len(group.entries) - GROUP_PREVIEW_COUNT
            for index, (cluster, job) in enumerate(preview):
                gutter = (
                    GROUP_END
                    if remaining <= 0 and index == len(preview) - 1
                    else GROUP_CHILD
                )
                body.append(
                    table_job_row(
                        cluster,
                        job,
                        False,
                        show_name=False,
                        gutter=gutter,
                    )
                )
            if remaining > 0:
                body.append(
                    line(
                        (
                            GROUP_END + f"… {remaining:,} more",
                            "busy",
                        )
                    )
                )
        else:
            cluster, job = group.entries[0]
            body.append(table_job_row(cluster, job, selected))
        group_index += 1

    for group in live_groups:
        append_group(group)
    live_sources = [
        cluster
        for cluster in scoped_clusters
        if not cluster.error and not cluster.jobs_error
    ]
    if not live_groups:
        if any(cluster.loading for cluster in scoped_clusters):
            body.append(line(("Loading live jobs…", "cpu")))
        elif name_filter and live_sources:
            body.append(plain("No current jobs match the name filter."))
        elif live_sources:
            body.append(plain("No running or pending jobs for the selected user(s)."))

    body.extend(
        (
            plain(""),
            line(("RECENT HISTORY", "title"), (f"  {past_count:,} jobs", "busy")),
            line((history_column_header, "title")),
            plain("─" * len(history_column_header)),
        )
    )
    for group in past_groups:
        append_group(group)
    history_sources = [
        cluster
        for cluster in scoped_clusters
        if not cluster.error and not cluster.history_error
    ]
    if not past_groups:
        if any(cluster.loading for cluster in scoped_clusters):
            body.append(line(("Loading job history…", "cpu")))
        elif name_filter and history_sources:
            body.append(plain("No history jobs match the name filter."))
        elif history_sources:
            body.append(plain("No recent job history for the selected user(s)."))
    details: list[Line] = []
    if groups:
        selected_group = groups[selected_job_index]
        if selected_group.grouped and group_by_name:
            details = selected_group_details(selected_group)
        else:
            selected_cluster, selected_job = selected_group.entries[0]
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
    sticky[-3] = body[history_title_index]
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
    group_by_name: bool = True,
    drilldown: bool = False,
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
        line(("selected", "selected"), (" row", "normal")),
        *(
            [line((GROUP_MARKER, "title"), ("grouped job name", "normal"))]
            if group_by_name and not drilldown
            else []
        ),
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
        plain("↑/↓  select job / group"),
        *(
            [
                (
                    plain("Esc  grouped list")
                    if drilldown
                    else plain("Enter  inspect group")
                ),
                plain("all matching jobs shown")
                if drilldown
                else plain("group preview: first 5"),
            ]
            if group_by_name
            else []
        ),
        plain("Ctrl+F  toggle filter"),
        plain("type filters live"),
        plain("Enter/Esc  finish filter"),
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
