from __future__ import annotations

import subprocess

from slurm_avail.collect import run_endpoint
from slurm_avail.config import ClusterConfig, DashboardSettings
from slurm_avail.request_rate import (
    record_request,
    requests_last_minute,
    reset_request_history,
)


def test_request_count_uses_a_rolling_minute() -> None:
    reset_request_history()
    record_request(100.0)
    record_request(120.0)
    record_request(159.9)

    assert requests_last_minute(159.9) == 3
    assert requests_last_minute(160.0) == 2
    assert requests_last_minute(180.0) == 1
    assert requests_last_minute(220.0) == 0


def test_run_endpoint_counts_each_command_attempt(monkeypatch) -> None:
    reset_request_history()
    monkeypatch.setattr(
        "slurm_avail.collect.run_local",
        lambda command, timeout: subprocess.CompletedProcess(command, 0),
    )
    cluster = ClusterConfig(name="LOCAL", mode="local")

    run_endpoint(cluster, "local", "researcher", "true", DashboardSettings())
    run_endpoint(cluster, "local", "researcher", "true", DashboardSettings())

    assert requests_last_minute() == 2
