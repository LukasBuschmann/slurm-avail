from __future__ import annotations

import time

from slurm_avail.forecast_views import forecast_table
from slurm_avail.models import (
    Cluster,
    Node,
    ReservationInterval,
    RunningInterval,
)
from slurm_avail.node_views import (
    abbreviated_node_names,
    architecture_groups,
    cluster_body,
    cluster_header,
    gpu_node_lines,
    legend_lines,
)
from slurm_avail.text import capacity_bar, visible_length


def gpu_node(name: str, *, reserved: bool = False) -> Node:
    return Node(
        name=name,
        state="RESERVED" if reserved else "MIXED",
        status="reserved" if reserved else "available",
        owner=None,
        unavailable=reserved,
        gpu_total=4,
        gpu_alloc=0 if reserved else 2,
        gpu_busy=4 if reserved else 2,
        cpu_alloc=8,
        cpu_total=32,
        mem_alloc=16 * 1024,
        mem_total=128 * 1024,
    )


def cpu_node(name: str) -> Node:
    return Node(
        name=name,
        state="MIXED",
        status="available",
        owner=None,
        unavailable=False,
        gpu_total=0,
        gpu_alloc=0,
        gpu_busy=0,
        cpu_alloc=8,
        cpu_total=32,
        mem_alloc=16 * 1024,
        mem_total=128 * 1024,
    )


def test_node_view_colors_current_users_gpu_allocation_and_reservation() -> None:
    now = time.time()
    cluster = Cluster(
        name="GPU",
        host="login",
        user="researcher",
        focus="gpu",
        nodes=[gpu_node("gpu01"), gpu_node("gpu02", reserved=True)],
        running_jobs=[
            RunningInterval(
                job_id="123",
                end=now + 3600,
                nodes=("gpu01",),
                gpu_per_node=1,
                cpu_per_node=8,
                mem_per_node=16 * 1024,
                mine=True,
            )
        ],
        reservations=[
            ReservationInterval(
                name="mine",
                start=now - 60,
                end=now + 3600,
                nodes=("gpu02",),
                mine=True,
            )
        ],
    )

    header = cluster_header(cluster)
    body = cluster_body(cluster, colored=True)
    header_text = "\n".join("".join(text for text, _style in row) for row in header)
    body_text = "\n".join("".join(text for text, _style in row) for row in body)
    header_styles = [style for row in header for text, style in row if text]
    body_styles = [style for row in body for text, style in row if text]

    assert "GPU  " in header_text
    assert "CPU  " in header_text
    assert "RAM  " in header_text
    assert "[" not in header_text
    assert "]" not in header_text
    assert "2/8" in header_text
    assert "mine" in header_styles
    assert "mine_reserved" in header_styles
    assert "mine" in body_styles
    assert "mine_reserved" in body_styles
    assert "1 reserved (yours)" in body_text

    gpu_row = next(
        row
        for row in header
        if "".join(text for text, _style in row).startswith("GPU  ")
    )
    capacity_styles = [style for text, style in gpu_row if text and set(text) == {"▆"}]
    assert capacity_styles == ["mine", "mine_reserved", "busy", "free"]


def test_gpu_and_cpu_cluster_node_sections_start_at_same_height() -> None:
    gpu_cluster = Cluster(
        name="GPU",
        host="login",
        focus="gpu",
        nodes=[gpu_node("gpu01")],
    )
    cpu_cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[cpu_node("cpu01")],
    )

    gpu_header = cluster_header(gpu_cluster)
    cpu_header = cluster_header(cpu_cluster)
    gpu_nodes_row = next(
        index
        for index, row in enumerate(gpu_header)
        if "".join(text for text, _style in row).startswith("NODES")
    )
    cpu_nodes_row = next(
        index
        for index, row in enumerate(cpu_header)
        if "".join(text for text, _style in row).startswith("NODES")
    )

    assert gpu_nodes_row == cpu_nodes_row


def test_node_headers_use_compact_titles_and_aligned_status_sections() -> None:
    normal = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[cpu_node("cpu01")],
    )
    failed = Cluster(
        name="DOWN",
        host="login",
        failure_kind="unreachable",
        error="connection failed",
    )

    normal_header = cluster_header(normal)
    failed_header = cluster_header(failed)
    normal_text = ["".join(text for text, _style in row) for row in normal_header]
    failed_text = ["".join(text for text, _style in row) for row in failed_header]

    assert normal_text[0] == "CPU"
    assert "===" not in normal_text[0]
    assert "CAPACITY  free / total" in normal_text
    assert "NODES  1" in normal_text
    assert failed_text[2] == "STATUS"
    assert "NODES  0" in failed_text
    assert len(normal_header) == len(failed_header)


def test_node_status_marker_is_separate_from_hostname() -> None:
    node = gpu_node("gpu01", reserved=True)

    text = "".join(value for value, _style in gpu_node_lines(node, colored=True)[0])

    assert text.endswith("gpu01 R")


def test_long_node_name_is_truncated_after_bars_without_hiding_status() -> None:
    node = gpu_node("compute-node-with-a-long-name", reserved=True)

    rows = gpu_node_lines(node, colored=True)
    text = "".join(value for value, _style in rows[0])

    assert len(rows) == 1
    assert text.startswith("  G ")
    assert text.endswith("comp… R")
    assert visible_length(rows[0]) == 28


def test_repeated_parts_of_long_node_names_are_abbreviated() -> None:
    first = cpu_node("compute-0-0C")
    second = cpu_node("compute-0-1C")
    cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[first, second],
    )

    body_text = [
        "".join(text for text, _style in row)
        for row in cluster_body(cluster, colored=True)
    ]

    assert any(text.endswith("c-0-0C") for text in body_text)
    assert any(text.endswith("c-0-1C") for text in body_text)
    assert not any("compute" in text for text in body_text)


def test_common_name_abbreviations_use_unambiguous_prefixes() -> None:
    nodes = [
        cpu_node("compute-0"),
        cpu_node("compute-1"),
        cpu_node("cluster-0"),
        cpu_node("cluster-1"),
    ]

    aliases = abbreviated_node_names(nodes)

    assert aliases == {
        "compute-0": "co-0",
        "compute-1": "co-1",
        "cluster-0": "cl-0",
        "cluster-1": "cl-1",
    }


def test_gpu_node_uses_three_scaled_bars_on_one_line() -> None:
    node = gpu_node("g28")
    node.gpu_total = 28
    node.gpu_alloc = 2
    node.gpu_busy = 2

    rows = gpu_node_lines(node, colored=True)
    row_text = ["".join(text for text, _style in row) for row in rows]

    assert len(rows) == 1
    assert "G " in row_text[0]
    assert "[" not in row_text[0]
    assert "]" not in row_text[0]
    assert "━" in row_text[0]
    assert not set(row_text[0]) & {"▆"}
    assert any("━" in text and style == "free" for text, style in rows[0])
    assert " C " in row_text[0]
    assert " M " in row_text[0]
    assert visible_length(rows[0]) <= 28


def test_gpu_node_bar_size_does_not_depend_on_gpu_count() -> None:
    rows = gpu_node_lines(gpu_node("gpu01"), colored=True)
    row_text = "".join(text for text, _style in rows[0])
    segment_styles = [
        style for text, style in rows[0] if text and set(text) <= {"━", "─"}
    ]

    assert len(rows) == 1
    assert row_text.count("━") == 10
    assert "G " in row_text
    assert "[" not in row_text
    assert "]" not in row_text
    assert segment_styles == ["busy", "free", "busy", "cpu", "busy", "memory"]


def test_cluster_body_groups_nodes_by_scheduler_visible_architecture() -> None:
    first = gpu_node("gpu01")
    first.gpu_type = "h100"
    second = gpu_node("gpu02")
    second.gpu_type = "H100"
    third = gpu_node("gpu03")
    third.gpu_type = "h100"
    third.mem_total = 256 * 1024
    cluster = Cluster(
        name="GPU",
        host="login",
        focus="gpu",
        nodes=[first, second, third],
    )

    body = cluster_body(cluster, colored=True)
    body_text = ["".join(text for text, _style in row) for row in body]

    assert body_text.count("2 nodes") == 1
    assert body_text.count("1 node") == 1
    assert body_text.count("4×h100") == 2
    assert "32 CPU · 128G RAM" in body_text
    assert "32 CPU · 256G RAM" in body_text
    assert body_text.index("32 CPU · 256G RAM") < body_text.index("32 CPU · 128G RAM")
    assert sum(text.endswith(("gpu01", "gpu02", "gpu03")) for text in body_text) == 3
    assert all(
        text.startswith(("│ ", "└ "))
        for text in body_text
        if text.endswith(("gpu01", "gpu02", "gpu03"))
    )


def test_architecture_group_summarizes_drained_and_reserved_nodes() -> None:
    available = gpu_node("gpu01")
    drained = gpu_node("gpu02")
    drained.state = "DOWN+DRAIN"
    drained.status = "drained"
    drained.unavailable = True
    reserved = gpu_node("gpu03", reserved=True)
    cluster = Cluster(
        name="GPU",
        host="login",
        focus="gpu",
        nodes=[available, drained, reserved],
    )

    body = cluster_body(cluster, colored=True)
    body_text = ["".join(text for text, _style in row) for row in body]
    drained_row = next(
        row for row in body if "down/drained" in "".join(x for x, _ in row)
    )
    reserved_row = next(row for row in body if "reserved" in "".join(x for x, _ in row))

    assert "3 nodes" in body_text
    assert "│ 1 down/drained" in body_text
    assert "│ 1 reserved" in body_text
    assert sum(text.endswith("gpu01") for text in body_text) == 1
    assert not any("gpu02" in text or "gpu03" in text for text in body_text)
    assert drained_row == [("│ ", "busy"), ("1 down/drained", "drained")]
    assert reserved_row == [("│ ", "busy"), ("1 reserved", "reserved")]


def test_last_hidden_status_closes_group_when_no_nodes_are_visible() -> None:
    drained = cpu_node("cpu01")
    drained.status = "drained"
    drained.unavailable = True
    reserved = cpu_node("cpu02")
    reserved.status = "reserved"
    reserved.unavailable = True
    cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[drained, reserved],
    )

    body_text = [
        "".join(text for text, _style in row)
        for row in cluster_body(cluster, colored=True)
    ]

    assert "│ 1 down/drained" in body_text
    assert "└ 1 reserved" in body_text


def test_architecture_group_omits_empty_status_summaries() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[cpu_node("cpu01")],
    )

    body_text = "\n".join(
        "".join(text for text, _style in row)
        for row in cluster_body(cluster, colored=True)
    )

    assert "down/drained" not in body_text
    assert "reserved" not in body_text


def test_nodes_legend_describes_group_layout_without_hidden_statuses() -> None:
    legend = legend_lines(0, False, True, 10)
    legend_text = "\n".join("".join(text for text, _style in row) for row in legend)

    assert "hidden" not in legend_text
    assert "down/drained" not in legend_text
    assert "│/└  group members" in legend_text
    assert "D ■" not in legend_text
    assert "R ■" not in legend_text


def test_architecture_sort_uses_cluster_focus_and_memory_tiebreaker() -> None:
    gpu8_mem128 = gpu_node("g8m128")
    gpu8_mem128.gpu_total = 8
    gpu8_mem128.mem_total = 128 * 1024
    gpu8_mem128.cpu_total = 128
    gpu8_mem256_cpu32 = gpu_node("g8m256c32")
    gpu8_mem256_cpu32.gpu_total = 8
    gpu8_mem256_cpu32.mem_total = 256 * 1024
    gpu8_mem256_cpu32.cpu_total = 32
    gpu8_mem256_cpu64 = gpu_node("g8m256c64")
    gpu8_mem256_cpu64.gpu_total = 8
    gpu8_mem256_cpu64.mem_total = 256 * 1024
    gpu8_mem256_cpu64.cpu_total = 64
    gpu4_mem512 = gpu_node("g4m512")
    gpu4_mem512.gpu_total = 4
    gpu4_mem512.mem_total = 512 * 1024
    gpu4_mem512.cpu_total = 256

    gpu_groups = architecture_groups(
        [gpu4_mem512, gpu8_mem128, gpu8_mem256_cpu32, gpu8_mem256_cpu64],
        "gpu",
    )
    assert [
        (group.gpu_total, group.mem_total, group.cpu_total)
        for group, _nodes in gpu_groups
    ] == [
        (8, 256 * 1024, 64),
        (8, 256 * 1024, 32),
        (8, 128 * 1024, 128),
        (4, 512 * 1024, 256),
    ]

    cpu128_mem128 = cpu_node("c128m128")
    cpu128_mem128.cpu_total = 128
    cpu128_mem128.mem_total = 128 * 1024
    cpu128_mem256 = cpu_node("c128m256")
    cpu128_mem256.cpu_total = 128
    cpu128_mem256.mem_total = 256 * 1024
    cpu64_mem512 = cpu_node("c64m512")
    cpu64_mem512.cpu_total = 64
    cpu64_mem512.mem_total = 512 * 1024

    cpu_groups = architecture_groups(
        [cpu64_mem512, cpu128_mem128, cpu128_mem256],
        "cpu",
    )
    assert [(group.cpu_total, group.mem_total) for group, _nodes in cpu_groups] == [
        (128, 256 * 1024),
        (128, 128 * 1024),
        (64, 512 * 1024),
    ]


def test_capacity_bar_uses_non_overlapping_owned_other_and_free_parts() -> None:
    row = capacity_bar(
        "C",
        free=1,
        total=100,
        stats="1/100",
        width=6,
        free_style="cpu",
        mine_busy=1,
        reserved_busy=1,
    )
    cells = [(text, style) for text, style in row if text and set(text) == {"▆"}]

    assert cells == [
        ("▆", "mine"),
        ("▆", "mine_reserved"),
        ("▆▆▆", "busy"),
        ("▆", "cpu"),
    ]
    assert sum(len(text) for text, _style in cells) == 6


def test_node_capacity_bars_are_thin_while_cluster_totals_are_thick() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[cpu_node("cpu01")],
    )

    header_text = "".join(
        text for row in cluster_header(cluster) for text, _style in row
    )
    body_text = "".join(
        text for row in cluster_body(cluster, colored=True) for text, _style in row
    )

    assert "▆" in header_text
    assert "━" in body_text
    assert "▆" not in body_text


def test_cpu_and_memory_bars_color_current_users_share() -> None:
    now = time.time()
    cluster = Cluster(
        name="CPU",
        host="login",
        user="researcher",
        focus="cpu",
        nodes=[cpu_node("cpu01")],
        running_jobs=[
            RunningInterval(
                job_id="123",
                end=now + 3600,
                nodes=("cpu01",),
                gpu_per_node=0,
                cpu_per_node=8,
                mem_per_node=16 * 1024,
                mine=True,
            )
        ],
    )

    lines = cluster_body(cluster, colored=True)
    header_styles = [
        style for row in cluster_header(cluster) for text, style in row if text
    ]
    highlighted_text = "".join(
        text for row in lines for text, style in row if style == "mine"
    )

    assert "mine" in header_styles
    assert highlighted_text.count("━") >= 2


def test_forecast_colors_current_users_usage_and_reservation() -> None:
    now = time.time()
    cluster = Cluster(
        name="GPU",
        host="login",
        user="researcher",
        focus="gpu",
        nodes=[gpu_node("gpu01"), gpu_node("gpu02", reserved=True)],
        running_jobs=[
            RunningInterval(
                job_id="123",
                end=now + 3600,
                nodes=("gpu01",),
                gpu_per_node=1,
                cpu_per_node=8,
                mine=True,
            )
        ],
        reservations=[
            ReservationInterval(
                name="mine",
                start=now - 60,
                end=now + 3600,
                nodes=("gpu02",),
                mine=True,
            )
        ],
    )

    _headers, body, *_rest = forecast_table(
        [cluster],
        selected_index=0,
        now_epoch=now,
        colored=True,
        resolution_minutes=60,
        window_hours=2,
    )
    styles = [style for row in body for _text, style in row]

    assert "forecast_mine_usage" in styles
    assert "forecast_mine_reserved" in styles


def test_unreachable_cluster_views_do_not_expose_transport_error() -> None:
    error = "Command '['ssh', '-l', 'researcher', 'login.example'] failed"
    cluster = Cluster(
        name="GPU",
        host="login",
        failure_kind="unreachable",
        error=error,
    )

    header_text = "\n".join(
        "".join(value for value, _style in row) for row in cluster_header(cluster)
    )
    _headers, body, *_rest = forecast_table(
        [cluster],
        selected_index=0,
        now_epoch=time.time(),
        colored=True,
        resolution_minutes=60,
        window_hours=2,
    )
    body_text = "\n".join("".join(value for value, _style in row) for row in body)

    assert "SSH UNREACHABLE" in header_text
    assert error not in header_text
    assert body_text == "SSH UNREACHABLE"


def test_scheduler_failure_is_distinct_from_ssh_failure() -> None:
    cluster = Cluster(
        name="GPU",
        host="login",
        failure_kind="scheduler",
        error="Slurm configuration unavailable",
    )

    header_text = "\n".join(
        "".join(value for value, _style in row) for row in cluster_header(cluster)
    )

    assert "SLURM UNAVAILABLE" in header_text
    assert "SSH UNREACHABLE" not in header_text


def test_authentication_required_is_distinct_from_cluster_down() -> None:
    cluster = Cluster(
        name="GPU",
        host="login",
        auth_required=True,
        error="authentication required",
    )

    header = cluster_header(cluster)
    header_text = "\n".join("".join(value for value, _style in row) for row in header)
    styles = [style for row in header for _value, style in row]

    assert "AUTH REQUIRED" in header_text
    assert "reserved" in styles
