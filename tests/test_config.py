from __future__ import annotations

import stat
from pathlib import Path

import pytest

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
        settings=DashboardSettings(
            authenticate_on_startup=False,
            control_persist_seconds=900,
            node_refresh_seconds=12,
        ),
        clusters=[
            ClusterConfig(
                name="REMOTE",
                addresses=["login1.example.org", "login2.example.org"],
                user="researcher",
                authentication="interactive",
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
    config_text = path.read_text(encoding="utf-8")
    assert 'authentication = "interactive"' in config_text
    assert "authenticate_on_startup = false" in config_text
    settings_section = config_text.split("[[clusters]]", 1)[0]
    cluster_section = config_text.split("[[clusters]]", 1)[1]
    assert "control_persist_seconds = 900" in settings_section
    assert "control_persist_seconds" not in cluster_section
    assert "password" not in config_text.casefold()


def test_missing_config_gets_a_generic_local_cluster(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    config = load_config(path)

    assert path.exists()
    assert len(config.clusters) == 1
    assert config.clusters[0].name == "LOCAL"
    assert config.clusters[0].mode == "local"


def test_invalid_authentication_mode_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = AppConfig(
        clusters=[
            ClusterConfig(
                name="REMOTE",
                addresses=["login.example.org"],
                authentication="password-in-config",
            )
        ]
    )

    with pytest.raises(ValueError, match="authentication must be batch or interactive"):
        save_config(config, path)


def test_password_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
version = 1

[[clusters]]
name = "REMOTE"
mode = "ssh"
addresses = ["login.example.org"]
password = "must-not-be-read"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not contain passwords"):
        load_config(path, create=False)


def test_existing_ssh_config_defaults_to_batch_authentication(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
version = 1

[[clusters]]
name = "REMOTE"
mode = "ssh"
addresses = ["login.example.org"]
""",
        encoding="utf-8",
    )

    cluster = load_config(path, create=False).clusters[0]

    assert cluster.authentication == "batch"


def test_legacy_cluster_session_lifetime_moves_to_global_settings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
version = 1

[[clusters]]
name = "REMOTE"
mode = "ssh"
addresses = ["login.example.org"]
authentication = "interactive"
control_persist_seconds = 900
""",
        encoding="utf-8",
    )

    config = load_config(path, create=False)

    assert config.settings.control_persist_seconds == 900


def test_existing_config_defaults_to_startup_authentication(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
version = 1

[[clusters]]
name = "LOCAL"
mode = "local"
""",
        encoding="utf-8",
    )

    config = load_config(path, create=False)

    assert config.settings.authenticate_on_startup is True


def test_startup_authentication_setting_must_be_boolean(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
version = 1

[settings]
authenticate_on_startup = "yes"

[[clusters]]
name = "LOCAL"
mode = "local"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be true or false"):
        load_config(path, create=False)
