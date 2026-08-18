from __future__ import annotations

import stat
from pathlib import Path

from slurm_avail.config import (
    AppConfig,
    ClusterConfig,
    DashboardSettings,
    load_config,
    save_config,
)


def test_config_round_trip_and_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    expected = AppConfig(
        settings=DashboardSettings(node_refresh_seconds=12),
        clusters=[
            ClusterConfig(
                name="REMOTE",
                addresses=["login1.example.org", "login2.example.org"],
                user="researcher",
                focus="gpu",
                filesystems=["/home", "/scratch"],
                slurm_bin_path="/opt/slurm/bin",
                exclude_partitions=["interactive"],
            )
        ],
    )

    save_config(expected, path)

    assert load_config(path) == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_config_gets_a_generic_local_cluster(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    config = load_config(path)

    assert path.exists()
    assert len(config.clusters) == 1
    assert config.clusters[0].name == "LOCAL"
    assert config.clusters[0].mode == "local"
