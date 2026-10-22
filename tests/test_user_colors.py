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
    cluster_body_layout,
    cluster_header,
    gpu_node_lines,
    legend_lines,
    node_architecture,
    node_detail_lines,
    selectable_nodes,
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
    assert "6/8" in header_text
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
    assert capacity_styles == ["mine", "mine_reserved", "free", "busy"]


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
    assert "USAGE  used / total" in normal_text
    assert "NODES  1" in normal_text
    assert failed_text[2] == "STATUS"
    assert "NODES  0" in failed_text
    assert len(normal_header) == len(failed_header)


def test_node_status_uses_name_color_without_a_suffix_marker() -> None:
    node = gpu_node("gpu01", reserved=True)

    row = gpu_node_lines(node, colored=True)[0]
    text = "".join(value for value, _style in row)

    assert text.endswith("gpu01")
    assert row[-1] == ("gpu01", "reserved")


def test_long_node_name_is_truncated_after_bars_without_a_status_suffix() -> None:
    node = gpu_node("compute-node-with-a-long-name", reserved=True)

    rows = gpu_node_lines(node, colored=True)
    text = "".join(value for value, _style in rows[0])

    assert len(rows) == 1
    assert text.startswith("  G ")
    assert text.endswith("comput…")
    assert " R" not in text
    assert visible_length(rows[0]) == 28


def test_node_cursor_uses_visible_architecture_order_and_highlights_row() -> None:
    smaller = gpu_node("gpu-small")
    larger = gpu_node("gpu-large")
    larger.gpu_total = 8
    hidden = gpu_node("gpu-reserved", reserved=True)
    cluster = Cluster(
        name="GPU",
        host="login",
        focus="gpu",
        nodes=[smaller, hidden, larger],
    )

    assert [node.name for node in selectable_nodes(cluster)] == [
        "gpu-large",
        "gpu-small",
    ]

    body = cluster_body(
        cluster,
        colored=True,
        selected_node_name="gpu-small",
    )
    selected_rows = [
        row for row in body if any(text and style == "selected" for text, style in row)
    ]
    assert len(selected_rows) == 1
    assert "gpu-sm…" in "".join(text for text, _style in selected_rows[0])
    assert selected_rows[0][0] == ("└ ", "selected")
    assert all(style == "selected" for _text, style in selected_rows[0])


def test_node_layout_starts_with_five_per_architecture_and_shares_extra_rows() -> None:
    groups: list[list[Node]] = []
    for prefix, cores in (("a", 96), ("b", 64), ("c", 32)):
        nodes = [cpu_node(f"{prefix}{index:02}") for index in range(10)]
        for node in nodes:
            node.cpu_total = cores
        groups.append(nodes)
    cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[node for group in groups for node in group],
    )

    compact_body, compact_positions, _compact_architectures, starts = (
        cluster_body_layout(
            cluster,
            colored=True,
            max_height=26,
        )
    )
    expanded_body, expanded_positions, _expanded_architectures, _expanded_starts = (
        cluster_body_layout(
            cluster,
            colored=True,
            max_height=30,
        )
    )

    assert len(compact_body) == 26
    assert [
        sum(name.startswith(prefix) for name in compact_positions)
        for prefix in ("a", "b", "c")
    ] == [5, 5, 5]
    assert (
        sum("… 5 more" in "".join(text for text, _style in row) for row in compact_body)
        == 3
    )
    assert len(expanded_body) == 30
    assert [
        sum(name.startswith(prefix) for name in expanded_positions)
        for prefix in ("a", "b", "c")
    ] == [7, 6, 6]
    assert all(start == 0 for start in starts.values())


def test_node_layout_marks_selected_and_open_architecture() -> None:
    nodes = [cpu_node(f"n{index}") for index in range(6)]
    cluster = Cluster(name="CPU", host="login", focus="cpu", nodes=nodes)
    architecture = node_architecture(nodes[0])

    body, _nodes, architecture_positions, _starts = cluster_body_layout(
        cluster,
        colored=True,
        selected_architecture=architecture,
        open_architecture=architecture,
        max_height=8,
    )

    assert architecture_positions[architecture] == 0
    assert "".join(text for text, _style in body[0]).startswith("32 CPU")
    assert all(
        style == "selected" for header_row in body[:2] for _text, style in header_row
    )
    assert any(style != "selected" for _text, style in body[2])


def test_open_architecture_reports_nodes_above_and_below_its_slice() -> None:
    nodes = [cpu_node(f"n{index:02}") for index in range(12)]
    cluster = Cluster(name="CPU", host="login", focus="cpu", nodes=nodes)
    architecture = node_architecture(nodes[0])

    body, positions, _architecture_positions, starts = cluster_body_layout(
        cluster,
        colored=True,
        selected_architecture=architecture,
        open_architecture=architecture,
        selected_node_name="n07",
        anchor_node_name="n07",
        max_height=9,
    )
    text = ["".join(value for value, _style in row) for row in body]

    assert starts[architecture] == 3
    assert {f"n{index:02}" for index in range(3, 8)} <= positions.keys()
    assert "│ … 3 more" in text
    assert "└ … 4 more" in text


def test_node_layout_scrolls_only_the_selected_architecture_slice() -> None:
    first = [cpu_node(f"a{index:02}") for index in range(10)]
    second = [cpu_node(f"b{index:02}") for index in range(10)]
    for node in first:
        node.cpu_total = 64
    cluster = Cluster(
        name="CPU",
        host="login",
        focus="cpu",
        nodes=[*first, *second],
    )

    _body, positions, _architecture_positions, starts = cluster_body_layout(
        cluster,
        colored=True,
        anchor_node_name="a09",
        max_height=17,
    )

    assert {f"a{index:02}" for index in range(5, 10)} <= positions.keys()
    assert {f"b{index:02}" for index in range(5)} <= positions.keys()
    assert starts[node_architecture(first[0])] == 5
    assert starts[node_architecture(second[0])] == 0


def test_node_details_show_full_name_and_exact_scheduler_counts() -> None:
    node = gpu_node("compute-node-with-a-full-name")
    node.gpu_type = "h100"
    cluster = Cluster(name="GPU", host="login", focus="gpu", nodes=[node])

    text = "\n".join(
        "".join(value for value, _style in row)
        for row in node_detail_lines(cluster, node)
    )

    assert "compute-node-with-a-full-name" in text
    assert "2 allocated · 2 free · 4 total · type  h100" in text
    assert "8 allocated · 24 free · 32 total" in text
    assert "16,384 MiB allocated · 114,688 MiB free · 131,072 MiB total" in text


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
    assert segment_styles == ["free", "busy", "cpu", "busy", "memory", "busy"]


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
    assert sum("4×h100" in row for row in body_text) == 2
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


def test_nodes_legend_describes_group_layout_without_status_markers() -> None:
    legend = legend_lines(0, False, True, 10)
    legend_text = "\n".join("".join(text for text, _style in row) for row in legend)

    assert "… N more above/below" in legend_text
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


def test_usage_emphasis_colors_occupied_capacity_and_mutes_free_capacity() -> None:
    row = capacity_bar(
        "C",
        free=1,
        total=100,
        stats="1/100",
        width=6,
        free_style="cpu",
        mine_busy=1,
        reserved_busy=1,
        display_mode="usage",
    )
    cells = [(text, style) for text, style in row if text and set(text) == {"▆"}]

    assert cells == [
        ("▆", "mine"),
        ("▆", "mine_reserved"),
        ("▆▆▆", "cpu"),
        ("▆", "busy"),
    ]


def test_node_display_mode_reverses_resource_emphasis_in_header_and_nodes() -> None:
    cluster = Cluster(
        name="GPU",
        host="login",
        focus="gpu",
        nodes=[gpu_node("gpu01")],
    )

    capacity_header = cluster_header(cluster, "capacity")
    usage_header = cluster_header(cluster, "usage")
    capacity_body = cluster_body(cluster, colored=True, display_mode="capacity")
    usage_body = cluster_body(cluster, colored=True, display_mode="usage")

    def bar_styles(rows: list[list[tuple[str, str]]], label: str, symbol: str):
        row = next(
            row
            for row in rows
            if any(text.strip() == label for text, _style in row)
            and symbol in "".join(text for text, _style in row)
        )
        return [style for text, style in row if text and set(text) == {symbol}]

    assert bar_styles(capacity_header, "GPU", "▆") == ["busy", "free"]
    assert bar_styles(usage_header, "GPU", "▆") == ["free", "busy"]
    assert bar_styles(capacity_body, "G", "━")[:2] == ["busy", "free"]
    assert bar_styles(usage_body, "G", "━")[:2] == ["free", "busy"]

    capacity_text = "\n".join(
        "".join(text for text, _style in row) for row in capacity_header
    )
    usage_text = "\n".join(
        "".join(text for text, _style in row) for row in usage_header
    )
    assert "CAPACITY  free / total" in capacity_text
    assert "GPU  " in capacity_text and "2/4" in capacity_text
    assert "24/32" in capacity_text
    assert "112G/128G" in capacity_text
    assert "USAGE  used / total" in usage_text
    assert "GPU  " in usage_text and "2/4" in usage_text
    assert "8/32" in usage_text
    assert "16G/128G" in usage_text


def test_nodes_legend_describes_selected_emphasis() -> None:
    capacity = legend_lines(0, False, True, 10, "capacity")
    usage = legend_lines(0, False, True, 10, "usage")
    capacity_text = "\n".join("".join(text for text, _style in row) for row in capacity)
    usage_text = "\n".join("".join(text for text, _style in row) for row in usage)

    assert "CPU free" in capacity_text
    assert "other use / blocked" in capacity_text
    assert "emphasis: capacity" in capacity_text
    assert "CPU usage" in usage_text
    assert "free capacity" in usage_text
    assert "emphasis: usage" in usage_text
    assert "totals ▆ + used/total" in usage_text
    assert " U " not in usage_text
    assert " R " not in usage_text
    assert "X  another user's node" not in usage_text
    assert "F  resource fully used" not in usage_text


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
