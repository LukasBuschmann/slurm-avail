"""Node, filesystem, login-node, and legend presentation."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .constants import (
    CLUSTER_WIDTH,
    FILESYSTEM_TABLE_MIN_WIDTH,
    LEGEND_WIDTH,
    LOADING_FRAMES,
    LOGIN_TABLE_WIDTH,
    NODE_LABEL_WIDTH,
    Line,
)
from .models import Cluster, Node
from .text import (
    capacity_bar,
    compact_count,
    compact_memory,
    format_bytes,
    free_meter,
    line,
    ownership_legend_lines,
    plain,
    rounded_free_slots,
)


@dataclass
class UserNodeUsage:
    gpu: int = 0
    cpu: int = 0
    memory: int = 0
    running: bool = False
    reserved: bool = False


def current_user_usage(
    cluster: Cluster,
    now_epoch: float | None = None,
) -> dict[str, UserNodeUsage]:
    """Aggregate the current user's live allocations per node."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    usage = {node.name: UserNodeUsage() for node in cluster.nodes}
    nodes = {node.name: node for node in cluster.nodes}

    for job in cluster.running_jobs:
        if not job.mine or job.end <= now_epoch:
            continue
        for node_name in job.nodes:
            item = usage.get(node_name)
            if item is None:
                continue
            item.gpu += job.gpu_per_node
            item.cpu += job.cpu_per_node
            item.memory += job.mem_per_node
            item.running = True

    # Owner is set for user-exclusive Slurm nodes. It also provides a useful
    # fallback if a scheduler snapshot and node snapshot arrive out of phase.
    for node_name, node in nodes.items():
        if node.owner == cluster.user and cluster.user:
            item = usage[node_name]
            item.gpu = max(item.gpu, node.gpu_alloc)
            item.cpu = max(item.cpu, node.cpu_alloc)
            item.memory = max(item.memory, node.mem_alloc)
            item.running = True

    for reservation in cluster.reservations:
        if not reservation.mine or not (
            reservation.start <= now_epoch < reservation.end
        ):
            continue
        for node_name in reservation.nodes:
            item = usage.get(node_name)
            if item is not None:
                item.reserved = True
    return usage


def filesystem_labels(clusters: list[Cluster]) -> list[str]:
    labels: list[str] = []
    for cluster in clusters:
        for label in cluster.filesystem_paths:
            if label not in labels:
                labels.append(label)
    return labels


def filesystem_table_width(clusters: list[Cluster]) -> int:
    label_width = max(
        15,
        max((len(label) for label in filesystem_labels(clusters)), default=0),
    )
    return max(
        FILESYSTEM_TABLE_MIN_WIDTH,
        label_width + 50 + 9 * len(clusters),
    )


def filesystem_table(clusters: list[Cluster]) -> list[Line]:
    table_width = filesystem_table_width(clusters)
    labels = filesystem_labels(clusters)
    label_width = max(15, max((len(label) for label in labels), default=0))
    lines: list[Line] = [
        line(("FILESYSTEM AVAILABILITY AND CAPACITY", "title")),
        plain("-" * table_width),
        plain(
            f"{'FILESYSTEM':<{label_width}} {'FREE SPACE':<12} "
            f"{'SIZE':>8} {'USED':>8} "
            f"{'FREE':>8} {'USE':>5} "
            + " ".join(f"{cluster.name[:7]:^8}" for cluster in clusters)
        ),
        plain("-" * table_width),
    ]

    for label in labels:
        mounted = [cluster.filesystems.get(label) for cluster in clusters]
        filesystem = next((item for item in mounted if item is not None), None)
        if filesystem is None:
            total = used = available = 0
            size_text = used_text = free_text = use_text = "—"
        else:
            total = filesystem.total
            used = filesystem.used
            available = filesystem.available
            size_text = format_bytes(total)
            used_text = format_bytes(used)
            free_text = format_bytes(available)
            use_text = f"{filesystem.percent_used}%"

        row: Line = [(f"{label:<{label_width}} [", "normal")]
        if filesystem is None:
            row.append(("·" * 10, "busy"))
        else:
            free_slots = rounded_free_slots(available, total, 10)
            row.extend(
                [
                    ("█" * free_slots, "free"),
                    ("░" * (10 - free_slots), "busy"),
                ]
            )
        row.append(
            (
                f"] {size_text:>8} {used_text:>8} {free_text:>8} {use_text:>5} ",
                "normal",
            )
        )
        for item in mounted:
            if item is None:
                row.append((f"{'—':^8}", "busy"))
            else:
                row.append((f"{'●':^8}", "free"))
            row.append((" ", "normal"))
        lines.append(row)
    return lines


def login_table(clusters: list[Cluster]) -> list[Line]:
    lines: list[Line] = [
        line(("DATA ENDPOINT HEALTH AND FAILOVER", "title")),
        plain("-" * LOGIN_TABLE_WIDTH),
        plain(
            f"{'CLUSTER':<10} {'MODE':<6} {'USER':<14} {'ENDPOINT':<36} "
            f"{'STATUS':<7} {'ROLE':<6} DETAIL"
        ),
        plain("-" * LOGIN_TABLE_WIDTH),
    ]
    for cluster in clusters:
        for index, login_node in enumerate(cluster.login_nodes):
            cluster_name = cluster.name if index == 0 else ""
            mode = cluster.mode if index == 0 else ""
            user = cluster.user if index == 0 else ""
            if not login_node.checked:
                status_text = "WAIT"
                status_style = "busy"
            elif login_node.reachable:
                status_text = "OK"
                status_style = "free"
            else:
                status_text = "FAIL"
                status_style = "drained"
            role_text = "DATA" if login_node.used else ""
            detail = (login_node.error or "").replace("\n", " ")[:38]
            lines.append(
                line(
                    (f"{cluster_name:<10} ", "title" if cluster_name else "normal"),
                    (f"{mode:<6} ", "normal"),
                    (f"{user:<14} ", "mine" if user else "normal"),
                    (f"{login_node.hostname:<36} ", "normal"),
                    (f"{status_text:<7} ", status_style),
                    (f"{role_text:<6} ", "cpu" if role_text else "normal"),
                    (detail, "normal"),
                )
            )
        lines.append(plain(""))
    return lines


def cluster_header(cluster: Cluster) -> list[Line]:
    loading_frame = (
        LOADING_FRAMES[int(time.monotonic() * 8) % len(LOADING_FRAMES)]
        if cluster.loading
        else ""
    )
    title = f"=== {cluster.name} ==="
    if loading_frame:
        title += f" {loading_frame}"
    header = [
        line((title, "title")),
        plain("-" * CLUSTER_WIDTH),
    ]

    if cluster.loading and not cluster.nodes and not cluster.error:
        endpoint = cluster.host or "configured endpoint"
        header.extend(
            [
                plain(""),
                line((f"{loading_frame} CONNECTING", "cpu")),
                plain(endpoint[:CLUSTER_WIDTH]),
                plain(""),
                plain(""),
                plain(""),
                plain("NODES"),
                plain("-" * CLUSTER_WIDTH),
            ]
        )
        return header

    if cluster.error:
        message = cluster.error.replace("\n", " ")[:CLUSTER_WIDTH]
        header.extend(
            [
                plain(""),
                line(("UNAVAILABLE", "offline")),
                plain(message),
                plain(""),
                plain(""),
                plain(""),
                plain("NODES"),
                plain("-" * CLUSTER_WIDTH),
            ]
        )
        return header

    total_cpu = sum(node.cpu_total for node in cluster.nodes)
    free_cpu = sum(node.cpu_free for node in cluster.nodes)
    total_mem = sum(node.mem_total for node in cluster.nodes)
    free_mem = sum(node.mem_free for node in cluster.nodes)
    total_gpu = sum(node.gpu_total for node in cluster.nodes)
    free_gpu = sum(node.gpu_free for node in cluster.nodes)
    header.append(plain("FREE / TOTAL"))
    if cluster.has_gpus:
        header.extend(
            [
                capacity_bar(
                    "GPU",
                    free_gpu,
                    total_gpu,
                    f"{free_gpu}/{total_gpu}",
                    5,
                    "free",
                ),
                capacity_bar(
                    "CPU",
                    free_cpu,
                    total_cpu,
                    f"{compact_count(free_cpu)}/{compact_count(total_cpu)}",
                    5,
                    "cpu",
                ),
                capacity_bar(
                    "RAM",
                    free_mem,
                    total_mem,
                    f"{compact_memory(free_mem)}/{compact_memory(total_mem)}",
                    5,
                    "memory",
                ),
            ]
        )
    else:
        user_usage = current_user_usage(cluster)
        user_cpu = sum(
            node.cpu_total
            if user_usage[node.name].reserved and node.unavailable
            else user_usage[node.name].cpu
            for node in cluster.nodes
        )
        user_mem = sum(
            node.mem_total
            if user_usage[node.name].reserved and node.unavailable
            else user_usage[node.name].memory
            for node in cluster.nodes
        )
        header.extend(
            [
                capacity_bar(
                    "CPU",
                    free_cpu,
                    total_cpu,
                    f"{compact_count(free_cpu)}/{compact_count(total_cpu)}",
                    5,
                    "cpu",
                    highlighted_busy=user_cpu,
                ),
                capacity_bar(
                    "RAM",
                    free_mem,
                    total_mem,
                    f"{compact_memory(free_mem)}/{compact_memory(total_mem)}",
                    5,
                    "memory",
                    highlighted_busy=user_mem,
                ),
                plain(""),
            ]
        )
    header.extend(
        [
            plain(f"{len(cluster.nodes)} nodes"),
            plain(""),
            plain("NODES"),
            plain("-" * CLUSTER_WIDTH),
        ]
    )
    return header


STATUS_MARKERS = {
    "drained": "D",
    "reserved": "R",
    "exclusive": "X",
    "full": "F",
}
STATUS_STYLES = {
    "drained": "drained",
    "reserved": "reserved",
    "exclusive": "exclusive",
    "full": "busy",
}
GPU_SQUARE_LIMIT = 8
GPU_BAR_WIDTH = 6


def node_label(
    node: Node,
    usage: UserNodeUsage | None = None,
) -> tuple[str, str]:
    usage = usage or UserNodeUsage()
    marker = STATUS_MARKERS.get(node.status, "")
    style = STATUS_STYLES.get(node.status, "normal")
    if usage.reserved:
        if "R" not in marker:
            marker += "R"
        if node.status != "drained":
            style = "mine_reserved"
    if usage.running:
        marker += "U"
        if node.status not in ("drained", "reserved") and not usage.reserved:
            style = "mine"
    return f"{node.name}{marker}", style


def wrapped_node_label(label: str, style: str) -> list[Line]:
    """Show a long node name above its meters instead of letting it overflow."""
    return [
        line((label[start : start + CLUSTER_WIDTH], style))
        for start in range(0, len(label), CLUSTER_WIDTH)
    ]


def cpu_node_lines(node: Node, usage: UserNodeUsage | None = None) -> list[Line]:
    usage = usage or UserNodeUsage()
    label, label_style = node_label(node, usage)
    cpu_stats = f"{node.cpu_free}/{node.cpu_total}"
    mem_free_gib = round(node.mem_free / 1024)
    mem_total_gib = round(node.mem_total / 1024)
    mem_stats = f"{mem_free_gib}/{mem_total_gib}G"

    blocked_style = STATUS_STYLES.get(node.status, "busy")
    highlight_style = "mine_reserved" if usage.reserved else "mine"
    highlighted_cpu = (
        node.cpu_total if usage.reserved and node.unavailable else usage.cpu
    )
    highlighted_mem = (
        node.mem_total if usage.reserved and node.unavailable else usage.memory
    )
    cpu_bar = capacity_bar(
        "C",
        node.cpu_free,
        node.cpu_total,
        cpu_stats,
        6,
        "cpu",
        blocked_style,
        highlighted_cpu,
        highlight_style,
    )
    mem_bar = capacity_bar(
        "M",
        node.mem_free,
        node.mem_total,
        mem_stats,
        6,
        "memory",
        blocked_style,
        highlighted_mem,
        highlight_style,
    )
    if len(label) <= NODE_LABEL_WIDTH:
        label_lines: list[Line] = []
        resource_prefix: Line = [(f"{label:<{NODE_LABEL_WIDTH}}", label_style)]
    else:
        label_lines = wrapped_node_label(label, label_style)
        resource_prefix = [(" " * NODE_LABEL_WIDTH, "normal")]
    return label_lines + [
        line(*resource_prefix, *cpu_bar),
        line((" " * NODE_LABEL_WIDTH, "normal"), *mem_bar),
    ]


def gpu_node_lines(
    node: Node,
    colored: bool,
    usage: UserNodeUsage | None = None,
) -> list[Line]:
    usage = usage or UserNodeUsage()
    label, label_style = node_label(node, usage)
    free_symbol = "■" if colored else "□"
    if len(label) <= NODE_LABEL_WIDTH:
        label_lines: list[Line] = []
        resource_prefix: Line = [(f"{label:<{NODE_LABEL_WIDTH}}", label_style)]
    else:
        label_lines = wrapped_node_label(label, label_style)
        resource_prefix = [(" " * NODE_LABEL_WIDTH, "normal")]
    user_gpus = min(node.gpu_alloc, usage.gpu)
    other_allocated_gpus = max(0, node.gpu_alloc - user_gpus)
    blocked_gpus = max(0, node.gpu_busy - node.gpu_alloc)
    blocked_style = STATUS_STYLES.get(node.status, "busy")
    if usage.reserved:
        blocked_style = "mine_reserved"
    if node.gpu_total > GPU_SQUARE_LIMIT:
        highlighted_gpus = (
            node.gpu_total if usage.reserved and node.unavailable else user_gpus
        )
        gpu_bar = capacity_bar(
            "G",
            node.gpu_free,
            node.gpu_total,
            f"{node.gpu_free}/{node.gpu_total}",
            GPU_BAR_WIDTH,
            "free",
            blocked_style,
            highlighted_gpus,
            "mine_reserved" if usage.reserved else "mine",
            show_busy_when_present=True,
        )
        resource_line = line(*resource_prefix, *gpu_bar)
        secondary_line = line(
            (" " * NODE_LABEL_WIDTH, "normal"),
            ("C ", "cpu"),
            (free_meter(node.cpu_free, node.cpu_total), "cpu"),
            (" M ", "memory"),
            (free_meter(node.mem_free, node.mem_total), "memory"),
        )
        return label_lines + [resource_line, secondary_line]

    spans = [*resource_prefix, ("G ", "free"), ("[", label_style)]
    spans.append((free_symbol * node.gpu_free, "free"))
    spans.append(("■" * other_allocated_gpus, "busy"))
    spans.append(("■" * user_gpus, "mine"))
    spans.append(("■" * blocked_gpus, blocked_style))
    spans.extend(
        [
            ("] ", "normal"),
            ("C ", "cpu"),
            (free_meter(node.cpu_free, node.cpu_total), "cpu"),
            (" M ", "memory"),
            (free_meter(node.mem_free, node.mem_total), "memory"),
        ]
    )
    return label_lines + [spans]


def cluster_body(cluster: Cluster, colored: bool) -> list[Line]:
    if cluster.error:
        return []
    body: list[Line] = []
    user_usage = current_user_usage(cluster)
    if cluster.has_gpus:
        for node in cluster.nodes:
            body.extend(gpu_node_lines(node, colored, user_usage[node.name]))
    else:
        for node in cluster.nodes:
            body.extend(cpu_node_lines(node, user_usage[node.name]))
    return body


def legend_lines(
    age_seconds: int | None,
    refreshing: bool,
    colored: bool,
    refresh_seconds: int,
) -> list[Line]:
    free_symbol = "■" if colored else "□"
    age = "waiting for data" if age_seconds is None else f"{age_seconds}s ago"

    result = [
        line(("LEGEND", "title")),
        plain("-" * LEGEND_WIDTH),
        line(("C █", "cpu"), ("  CPU free", "normal")),
        line(("M █", "memory"), ("  memory free", "normal")),
        line((free_symbol, "free"), ("  GPU free", "normal")),
        line(("■", "busy"), ("  GPU allocated", "normal")),
        line(("D ■", "drained"), (" drained/down", "normal")),
        line(("R ■", "reserved"), (" active reservation", "normal")),
        line(("X ■", "exclusive"), (" another user's node", "normal")),
        line(("F ■", "busy"), (" resource fully used", "normal")),
        *ownership_legend_lines(),
        plain("GPU squares or bars"),
        plain("large counts: free/total"),
        plain("C/M glyphs show free"),
        plain("bars/numbers: free/total"),
        plain("long names wrap above bars"),
        plain(""),
        line(("KEYS", "title")),
        plain("Tab views   r refresh"),
        plain("arrows scroll   Pg page"),
        plain("Home/End top/bottom"),
        plain("q quit"),
        plain(""),
        line(("LAST REFRESH", "title")),
        plain(age),
    ]
    if refreshing:
        result.append(line(("refreshing…", "cpu")))
    else:
        remaining = max(0, refresh_seconds - (age_seconds or 0))
        result.append(plain(f"next in {remaining}s"))
    return result


def filesystem_legend_lines(
    age_seconds: int | None,
    refreshing: bool,
    refresh_seconds: int,
) -> list[Line]:
    age = "waiting for data" if age_seconds is None else f"{age_seconds}s ago"
    result = [
        line(("LEGEND", "title")),
        plain("-" * LEGEND_WIDTH),
        line(("█", "free"), (" free space", "normal")),
        line(("░", "busy"), (" used space", "normal")),
        line(("●", "free"), (" mounted/responding", "normal")),
        line(("—", "busy"), (" unavailable/not mounted", "normal")),
        *ownership_legend_lines(),
        plain("capacity uses first"),
        plain("responding cluster"),
        plain(""),
        line(("KEYS", "title")),
        plain("Tab  switch view"),
        plain("↑/↓  scroll rows"),
        plain("PgUp/PgDn  page"),
        plain("←/→  scroll sideways"),
        plain("r  refresh now"),
        plain("q  quit"),
        plain(""),
        line(("LAST REFRESH", "title")),
        plain(age),
    ]
    if refreshing:
        result.append(line(("refreshing…", "cpu")))
    else:
        remaining = max(0, refresh_seconds - (age_seconds or 0))
        result.append(plain(f"next in {remaining}s"))
    return result


def login_legend_lines(
    age_seconds: int | None,
    refreshing: bool,
    refresh_seconds: int,
) -> list[Line]:
    age = "waiting for data" if age_seconds is None else f"{age_seconds}s ago"
    result = [
        line(("LEGEND", "title")),
        plain("-" * LEGEND_WIDTH),
        line(("OK", "free"), (" reachable", "normal")),
        line(("FAIL", "drained"), (" unavailable", "normal")),
        line(("DATA", "cpu"), (" current source", "normal")),
        *ownership_legend_lines(),
        plain("automatic failover"),
        plain(f"checks every {refresh_seconds}s"),
        plain(""),
        line(("KEYS", "title")),
        plain("Tab  switch view"),
        plain("↑/↓  scroll rows"),
        plain("←/→  scroll sideways"),
        plain("r  check now"),
        plain("q  quit"),
        plain(""),
        line(("LAST CHECK", "title")),
        plain(age),
    ]
    if refreshing:
        result.append(line(("checking…", "cpu")))
    else:
        remaining = max(0, refresh_seconds - (age_seconds or 0))
        result.append(plain(f"next in {remaining}s"))
    return result
