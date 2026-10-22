"""Node, filesystem, login-node, and legend presentation."""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from .constants import (
    CLUSTER_WIDTH,
    FILESYSTEM_TABLE_MIN_WIDTH,
    LEGEND_WIDTH,
    LOADING_FRAMES,
    LOGIN_TABLE_WIDTH,
    Line,
)
from .models import Cluster, Node
from .text import (
    capacity_bar,
    capacity_segments,
    compact_count,
    compact_memory,
    format_bytes,
    line,
    ownership_legend_lines,
    plain,
    rounded_free_slots,
    visible_length,
)


@dataclass
class UserNodeUsage:
    gpu: int = 0
    cpu: int = 0
    memory: int = 0
    running: bool = False
    reserved: bool = False


@dataclass(frozen=True)
class ResourceCapacity:
    """Non-overlapping parts of one resource meter."""

    free: int
    total: int
    mine: int = 0
    mine_reserved: int = 0

    @property
    def other(self) -> int:
        return max(0, self.total - self.free - self.mine - self.mine_reserved)


@dataclass(frozen=True)
class NodeArchitecture:
    """Scheduler-visible hardware shared by a group of nodes."""

    cpu_total: int
    mem_total: int
    gpu_total: int
    gpu_type: str


def node_resource_capacity(
    node: Node,
    usage: UserNodeUsage,
    resource: str,
) -> ResourceCapacity:
    if resource == "gpu":
        free, total, mine = node.gpu_free, node.gpu_total, usage.gpu
    elif resource == "cpu":
        free, total, mine = node.cpu_free, node.cpu_total, usage.cpu
    else:
        free, total, mine = node.mem_free, node.mem_total, usage.memory

    occupied = max(0, total - free)
    mine = min(mine, occupied) if usage.running else 0
    mine_reserved = (
        max(0, occupied - mine) if usage.reserved and node.unavailable else 0
    )
    return ResourceCapacity(free, total, mine, mine_reserved)


def cluster_resource_capacity(
    cluster: Cluster,
    usage: dict[str, UserNodeUsage],
    resource: str,
) -> ResourceCapacity:
    parts = [
        node_resource_capacity(node, usage[node.name], resource)
        for node in cluster.nodes
    ]
    return ResourceCapacity(
        free=sum(part.free for part in parts),
        total=sum(part.total for part in parts),
        mine=sum(part.mine for part in parts),
        mine_reserved=sum(part.mine_reserved for part in parts),
    )


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
            elif login_node.auth_required:
                status_text = "AUTH"
                status_style = "reserved"
            elif login_node.failure_kind == "timeout":
                status_text = "TIMEOUT"
                status_style = "reserved"
            elif login_node.failure_kind == "scheduler":
                status_text = "SLURM"
                status_style = "reserved"
            elif login_node.failure_kind == "data":
                status_text = "PARSE"
                status_style = "busy"
            elif login_node.failure_kind in ("command", "local"):
                status_text = "ERROR"
                status_style = "drained"
            elif login_node.reachable:
                status_text = "OK"
                status_style = "free"
            else:
                status_text = "SSH"
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
    title: Line = [(cluster.name, "title")]
    if loading_frame:
        title.append((f"  {loading_frame}", "cpu"))
    header = [
        title,
        line(("─" * CLUSTER_WIDTH, "busy")),
    ]

    if cluster.loading and not cluster.nodes and not cluster.error:
        endpoint = cluster.host or "configured endpoint"
        header.extend(
            [
                line(("STATUS", "title")),
                line((f"{loading_frame} CONNECTING", "cpu")),
                plain(endpoint[:CLUSTER_WIDTH]),
                plain(""),
                plain(""),
                line(("NODES", "title"), ("  0", "busy")),
                line(("─" * CLUSTER_WIDTH, "busy")),
            ]
        )
        return header

    if cluster.error:
        header.extend(
            [
                line(("STATUS", "title")),
                line((cluster.failure_label, cluster.failure_style)),
                plain(""),
                plain(""),
                plain(""),
                line(("NODES", "title"), ("  0", "busy")),
                line(("─" * CLUSTER_WIDTH, "busy")),
            ]
        )
        return header

    user_usage = current_user_usage(cluster)
    cpu = cluster_resource_capacity(cluster, user_usage, "cpu")
    memory = cluster_resource_capacity(cluster, user_usage, "memory")
    gpu = cluster_resource_capacity(cluster, user_usage, "gpu")
    header.append(line(("CAPACITY", "title"), ("  free / total", "busy")))
    if cluster.has_gpus:
        header.extend(
            [
                capacity_bar(
                    "GPU",
                    gpu.free,
                    gpu.total,
                    f"{gpu.free}/{gpu.total}",
                    8,
                    "free",
                    mine_busy=gpu.mine,
                    reserved_busy=gpu.mine_reserved,
                ),
                capacity_bar(
                    "CPU",
                    cpu.free,
                    cpu.total,
                    f"{compact_count(cpu.free)}/{compact_count(cpu.total)}",
                    8,
                    "cpu",
                    mine_busy=cpu.mine,
                    reserved_busy=cpu.mine_reserved,
                ),
                capacity_bar(
                    "RAM",
                    memory.free,
                    memory.total,
                    f"{compact_memory(memory.free)}/{compact_memory(memory.total)}",
                    8,
                    "memory",
                    mine_busy=memory.mine,
                    reserved_busy=memory.mine_reserved,
                ),
            ]
        )
    else:
        header.extend(
            [
                capacity_bar(
                    "CPU",
                    cpu.free,
                    cpu.total,
                    f"{compact_count(cpu.free)}/{compact_count(cpu.total)}",
                    8,
                    "cpu",
                    mine_busy=cpu.mine,
                    reserved_busy=cpu.mine_reserved,
                ),
                capacity_bar(
                    "RAM",
                    memory.free,
                    memory.total,
                    f"{compact_memory(memory.free)}/{compact_memory(memory.total)}",
                    8,
                    "memory",
                    mine_busy=memory.mine,
                    reserved_busy=memory.mine_reserved,
                ),
                plain(""),
            ]
        )
    header.extend(
        [
            plain(""),
            line(("NODES", "title"), (f"  {len(cluster.nodes):,}", "busy")),
            line(("─" * CLUSTER_WIDTH, "busy")),
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
NODE_GROUP_GUTTER = "  "
NODE_GROUP_CHILD = "│ "
NODE_GROUP_END = "└ "
NODE_NAME_TOKEN_RE = re.compile(r"[A-Za-z]{4,}")


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
    return f"{node.name} {marker}".rstrip(), style


def clipped_node_label(
    node: Node,
    usage: UserNodeUsage,
    width: int,
    display_name: str | None = None,
) -> tuple[str, str]:
    """Fit a node name at the end of a meter row without hiding its markers."""
    full_label, style = node_label(node, usage)
    marker = full_label[len(node.name) :].strip()
    marker_width = len(marker) + 1 if marker else 0
    name_width = max(1, width - marker_width)
    name = display_name or node.name
    if len(name) > name_width:
        name = name[: max(0, name_width - 1)] + "…"
    label = f"{name} {marker}" if marker else name
    return label[:width], style


def abbreviated_node_names(nodes: list[Node]) -> dict[str, str]:
    """Shorten repeated words only when node names do not fit their rows."""
    token_counts: Counter[str] = Counter()
    for node in nodes:
        available = 7 if node.gpu_total else 8
        if len(node.name) <= available:
            continue
        token_counts.update(
            match.group(0).casefold()
            for match in NODE_NAME_TOKEN_RE.finditer(node.name)
        )

    candidates = sorted(token for token, count in token_counts.items() if count > 1)
    abbreviations: dict[str, str] = {}
    for token in candidates:
        for length in range(1, len(token)):
            prefix = token[:length]
            if sum(candidate.startswith(prefix) for candidate in candidates) == 1:
                abbreviations[token] = prefix
                break

    def replace_token(match: re.Match[str]) -> str:
        original = match.group(0)
        abbreviation = abbreviations.get(original.casefold())
        if abbreviation is None:
            return original
        if original.isupper():
            return abbreviation.upper()
        if original[0].isupper():
            return abbreviation.capitalize()
        return abbreviation

    aliases = {
        node.name: NODE_NAME_TOKEN_RE.sub(replace_token, node.name) for node in nodes
    }
    collisions: defaultdict[str, list[str]] = defaultdict(list)
    for original, alias in aliases.items():
        collisions[alias].append(original)
    for originals in collisions.values():
        if len(originals) > 1:
            for original in originals:
                aliases[original] = original
    return aliases


def node_architecture(node: Node) -> NodeArchitecture:
    return NodeArchitecture(
        cpu_total=node.cpu_total,
        mem_total=node.mem_total,
        gpu_total=node.gpu_total,
        gpu_type=node.gpu_type.strip().casefold(),
    )


def architecture_groups(
    nodes: list[Node],
    focus: str,
) -> list[tuple[NodeArchitecture, list[Node]]]:
    groups: dict[NodeArchitecture, list[Node]] = {}
    for node in nodes:
        groups.setdefault(node_architecture(node), []).append(node)

    def sort_key(
        item: tuple[NodeArchitecture, list[Node]],
    ) -> tuple[int, int, int, str]:
        architecture, _nodes = item
        if focus == "gpu":
            return (
                -architecture.gpu_total,
                -architecture.mem_total,
                -architecture.cpu_total,
                architecture.gpu_type,
            )
        return (
            -architecture.cpu_total,
            -architecture.mem_total,
            -architecture.gpu_total,
            architecture.gpu_type,
        )

    return sorted(groups.items(), key=sort_key)


def architecture_header(
    architecture: NodeArchitecture,
    nodes: list[Node],
    usage: dict[str, UserNodeUsage],
) -> list[Line]:
    node_count = len(nodes)
    noun = "node" if node_count == 1 else "nodes"
    drained_count = sum(node.status == "drained" for node in nodes)
    reserved_count = sum(node.status == "reserved" for node in nodes)
    mine_reserved_count = sum(
        node.status == "reserved" and usage[node.name].reserved for node in nodes
    )
    other_reserved_count = reserved_count - mine_reserved_count
    visible_count = node_count - drained_count - reserved_count
    result: list[Line] = []
    if architecture.gpu_total:
        gpu_config = f"{architecture.gpu_total} GPUs"
        if architecture.gpu_type:
            gpu_config = f"{architecture.gpu_total}×{architecture.gpu_type}"
        result.append(line((gpu_config[:CLUSTER_WIDTH], "free")))
    result.extend(
        [
            line(
                (f"{architecture.cpu_total} CPU", "cpu"),
                (" · ", "busy"),
                (f"{compact_memory(architecture.mem_total)} RAM", "memory"),
            ),
            line((f"{node_count} {noun}", "busy")),
        ]
    )
    summaries: list[tuple[str, str]] = []
    if drained_count:
        summaries.append((f"{drained_count} down/drained", "drained"))
    if other_reserved_count:
        summaries.append((f"{other_reserved_count} reserved", "reserved"))
    if mine_reserved_count:
        summaries.append((f"{mine_reserved_count} reserved (yours)", "mine_reserved"))
    for index, (summary, style) in enumerate(summaries):
        more_children_follow = index < len(summaries) - 1 or visible_count > 0
        gutter = NODE_GROUP_CHILD if more_children_follow else NODE_GROUP_END
        result.append(line((gutter, "busy"), (summary, style)))
    return result


def node_resource_segments(
    capacity: ResourceCapacity,
    width: int,
    free_style: str,
    busy_style: str,
) -> Line:
    return capacity_segments(
        capacity.free,
        capacity.total,
        width,
        free_style,
        busy_style,
        mine_busy=capacity.mine,
        reserved_busy=capacity.mine_reserved,
        thin=True,
    )


def cpu_node_lines(
    node: Node,
    usage: UserNodeUsage | None = None,
    gutter: str = NODE_GROUP_GUTTER,
    display_name: str | None = None,
) -> list[Line]:
    usage = usage or UserNodeUsage()
    blocked_style = STATUS_STYLES.get(node.status, "busy")
    cpu = node_resource_capacity(node, usage, "cpu")
    memory = node_resource_capacity(node, usage, "memory")
    row = line(
        (gutter, "busy" if gutter != NODE_GROUP_GUTTER else "normal"),
        ("C ", "cpu"),
        *node_resource_segments(cpu, 6, "cpu", blocked_style),
        (" M ", "memory"),
        *node_resource_segments(memory, 6, "memory", blocked_style),
    )
    label_width = max(1, CLUSTER_WIDTH - visible_length(row) - 1)
    return [
        line(
            *row,
            (" ", "normal"),
            clipped_node_label(node, usage, label_width, display_name),
        )
    ]


def gpu_node_lines(
    node: Node,
    colored: bool,
    usage: UserNodeUsage | None = None,
    gutter: str = NODE_GROUP_GUTTER,
    display_name: str | None = None,
) -> list[Line]:
    usage = usage or UserNodeUsage()
    gpu = node_resource_capacity(node, usage, "gpu")
    blocked_style = STATUS_STYLES.get(node.status, "busy")
    cpu = node_resource_capacity(node, usage, "cpu")
    memory = node_resource_capacity(node, usage, "memory")
    row = line(
        (gutter, "busy" if gutter != NODE_GROUP_GUTTER else "normal"),
        ("G ", "free"),
        *node_resource_segments(gpu, 4, "free", blocked_style),
        (" C ", "cpu"),
        *node_resource_segments(cpu, 3, "cpu", blocked_style),
        (" M ", "memory"),
        *node_resource_segments(memory, 3, "memory", blocked_style),
    )
    label_width = max(1, CLUSTER_WIDTH - visible_length(row) - 1)
    return [
        line(
            *row,
            (" ", "normal"),
            clipped_node_label(node, usage, label_width, display_name),
        )
    ]


def cluster_body(cluster: Cluster, colored: bool) -> list[Line]:
    if cluster.error:
        return []
    body: list[Line] = []
    user_usage = current_user_usage(cluster)
    display_names = abbreviated_node_names(cluster.nodes)
    focus = "gpu" if cluster.has_gpus else "cpu"
    for architecture, nodes in architecture_groups(cluster.nodes, focus):
        if body:
            body.append(plain(""))
        body.extend(architecture_header(architecture, nodes, user_usage))
        visible_nodes = [
            node for node in nodes if node.status not in ("drained", "reserved")
        ]
        for index, node in enumerate(visible_nodes):
            gutter = (
                NODE_GROUP_END if index == len(visible_nodes) - 1 else NODE_GROUP_CHILD
            )
            if architecture.gpu_total:
                body.extend(
                    gpu_node_lines(
                        node,
                        colored,
                        user_usage[node.name],
                        gutter,
                        display_names[node.name],
                    )
                )
            else:
                body.extend(
                    cpu_node_lines(
                        node,
                        user_usage[node.name],
                        gutter,
                        display_names[node.name],
                    )
                )
    return body


def legend_lines(
    age_seconds: int | None,
    refreshing: bool,
    colored: bool,
    refresh_seconds: int,
) -> list[Line]:
    age = "waiting for data" if age_seconds is None else f"{age_seconds}s ago"

    result = [
        line(("LEGEND", "title")),
        line(("─" * LEGEND_WIDTH, "busy")),
        line(("C ▆", "cpu"), ("  CPU free", "normal")),
        line(("M ▆", "memory"), ("  memory free", "normal")),
        line(("G ▆", "free"), ("  GPU free", "normal")),
        line(("  ▆", "busy"), ("  other use / blocked", "normal")),
        *ownership_legend_lines(),
        line(("X", "exclusive"), ("  another user's node", "normal")),
        line(("F", "busy"), ("  resource fully used", "normal")),
        plain("totals ▆ + free/total"),
        plain("nodes ━ show capacity"),
        plain("bar order: U R other free"),
        plain("header: GPU · CPU · RAM"),
        plain("│/└  group members"),
        plain("names shorten; … = clipped"),
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
        line(("AUTH", "reserved"), (" authentication required", "normal")),
        line(("TIMEOUT", "reserved"), (" request timed out", "normal")),
        line(("SSH", "drained"), (" endpoint unreachable", "normal")),
        line(("SLURM", "reserved"), (" scheduler unavailable", "normal")),
        line(("PARSE", "busy"), (" invalid scheduler data", "normal")),
        line(("ERROR", "drained"), (" remote command failed", "normal")),
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
