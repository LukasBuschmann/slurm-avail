from __future__ import annotations

from slurm_avail.cli import initial_jobs_scope_index
from slurm_avail.config import AppConfig, ClusterConfig


def test_jobs_scope_numbers_start_with_all() -> None:
    config = AppConfig(
        clusters=[
            ClusterConfig(name="CAPELLA", mode="local"),
            ClusterConfig(name="ALPHA", mode="local"),
        ]
    )

    assert initial_jobs_scope_index(config, None) == 0
    assert initial_jobs_scope_index(config, "all") == 0
    assert initial_jobs_scope_index(config, "0") == 0
    assert initial_jobs_scope_index(config, "1") == 1
    assert initial_jobs_scope_index(config, "2") == 2
    assert initial_jobs_scope_index(config, "CAPELLA") == 1
    assert initial_jobs_scope_index(config, "ALPHA") == 2
