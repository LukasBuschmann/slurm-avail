from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from slurm_avail.config import ClusterConfig, DashboardSettings
from slurm_avail.ssh_auth import (
    ensure_control_directory,
    interactive_session_command,
    multiplex_options,
    open_interactive_session,
    session_check_command,
)


def interactive_cluster() -> ClusterConfig:
    return ClusterConfig(
        name="REMOTE",
        addresses=["login.example.org"],
        user="researcher",
        authentication="interactive",
        control_persist_seconds=900,
    )


def test_control_socket_directory_is_private(tmp_path: Path) -> None:
    directory = ensure_control_directory(tmp_path / "ssh")

    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_interactive_command_contains_session_options_but_no_secret(
    tmp_path: Path,
) -> None:
    cluster = interactive_cluster()
    settings = DashboardSettings(ssh_connect_timeout_seconds=12)
    command = interactive_session_command(
        cluster,
        "login.example.org",
        "researcher",
        settings,
        tmp_path / "ssh",
    )
    command_text = " ".join(command)

    assert command[:5] == ["ssh", "-M", "-N", "-f", "-T"]
    assert "BatchMode=no" in command
    assert "ControlPersist=900s" in command
    assert "ConnectTimeout=12" in command
    assert command[-1] == "login.example.org"
    assert "password" not in command_text.casefold()


def test_batch_requests_reuse_only_interactive_control_sessions(
    tmp_path: Path,
) -> None:
    cluster = interactive_cluster()
    options = multiplex_options(cluster, tmp_path / "ssh")
    check_command = session_check_command(
        cluster,
        "login.example.org",
        "researcher",
        tmp_path / "ssh",
    )

    assert any(value.startswith("ControlPath=") for value in options)
    assert "ControlMaster=no" in options
    assert "-O" in check_command
    assert "check" in check_command

    cluster.authentication = "batch"
    assert multiplex_options(cluster, tmp_path / "unused") == []


def test_open_session_inherits_terminal_without_capturing_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cluster = interactive_cluster()
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        "slurm_avail.ssh_auth.control_session_active",
        lambda *_args, **_kwargs: False,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("slurm_avail.ssh_auth.subprocess.run", fake_run)

    result = open_interactive_session(
        cluster,
        "login.example.org",
        "researcher",
        DashboardSettings(),
        tmp_path / "ssh",
    )

    assert result.connected
    assert len(calls) == 1
    _command, kwargs = calls[0]
    assert kwargs == {"check": False}
