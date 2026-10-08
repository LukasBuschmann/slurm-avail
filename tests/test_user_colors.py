from __future__ import annotations

import time

from slurm_avail.forecast_views import forecast_table
from slurm_avail.models import (
    Cluster,
    Node,
    ReservationInterval,
    RunningInterval,
)
from slurm_avail.node_views import cluster_body, cluster_header, gpu_node_lines
from slurm_avail.text import visible_length


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
    header_styles = [style for row in header for text, style in row if text]
    body_styles = [style for row in body for _text, style in row]

    assert "GPU [" in header_text
    assert "CPU [" in header_text
    assert "RAM [" in header_text
    assert "2/8" in header_text
    assert "mine" not in header_styles
    assert "mine_reserved" not in header_styles
    assert "mine" in body_styles
    assert "mine_reserved" in body_styles


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
        if "".join(text for text, _style in row) == "NODES"
    )
    cpu_nodes_row = next(
        index
        for index, row in enumerate(cpu_header)
        if "".join(text for text, _style in row) == "NODES"
    )

    assert gpu_nodes_row == cpu_nodes_row


def test_large_gpu_count_uses_scaled_bar_with_cpu_and_memory_below() -> None:
    node = gpu_node("g28")
    node.gpu_total = 28
    node.gpu_alloc = 2
    node.gpu_busy = 2

    rows = gpu_node_lines(node, colored=True)
    row_text = ["".join(text for text, _style in row) for row in rows]

    assert len(rows) == 2
    assert "G [" in row_text[0]
    assert "26/28" in row_text[0]
    assert "░" in row_text[0]
    assert "C " in row_text[1]
    assert " M " in row_text[1]
    assert visible_length(rows[0]) <= 28


def test_small_gpu_count_keeps_one_square_per_gpu() -> None:
    rows = gpu_node_lines(gpu_node("gpu01"), colored=True)
    row_text = "".join(text for text, _style in rows[0])
    square_styles = [
        style for text, style in rows[0] if text and set(text) <= {"■", "□"}
    ]

    assert len(rows) == 1
    assert row_text.count("■") == 4
    assert "G [" in row_text
    assert square_styles == ["free", "busy"]


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
    highlighted_text = "".join(
        text for row in lines for text, style in row if style == "mine"
    )

    assert highlighted_text.count("█") >= 2


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
