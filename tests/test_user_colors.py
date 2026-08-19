from __future__ import annotations

import time

from slurm_avail.forecast_views import forecast_table
from slurm_avail.models import (
    Cluster,
    Node,
    ReservationInterval,
    RunningInterval,
)
from slurm_avail.node_views import cluster_body, cluster_header


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

    lines = cluster_header(cluster) + cluster_body(cluster, colored=True)
    styles = [style for row in lines for _text, style in row]

    assert "mine" in styles
    assert "mine_reserved" in styles


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
