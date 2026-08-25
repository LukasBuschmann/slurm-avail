from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from slurm_avail.config import ClusterConfig, DashboardSettings
from slurm_avail.ssh_auth import (
    SshControlTarget,
    close_control_session,
    endpoint_probe_command,
    endpoint_unavailable,
    ensure_control_directory,
    interactive_session_command,
    multiplex_options,
    open_interactive_session,
    session_check_command,
    session_exit_command,
)


def interactive_cluster() -> ClusterConfig:
    return ClusterConfig(
        name="REMOTE",
        addresses=["login.example.org"],
        user="researcher",
        authentication="interactive",
    )


def test_control_socket_directory_is_private(tmp_path: Path) -> None:
    directory = ensure_control_directory(tmp_path / "ssh")

    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_interactive_command_contains_session_options_but_no_secret(
    tmp_path: Path,
) -> None:
    cluster = interactive_cluster()
    settings = DashboardSettings(
        control_persist_seconds=900,
        ssh_connect_timeout_seconds=12,
    )
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


def test_exit_command_cannot_fall_back_to_password_authentication(
    tmp_path: Path,
) -> None:
    command = session_exit_command(
        SshControlTarget("login.example.org", "researcher"),
        tmp_path / "ssh",
    )

    assert "BatchMode=yes" in command
    assert "ConnectTimeout=1" in command
    assert command[-3:] == ["-l", "researcher", "login.example.org"]
    assert command[command.index("-O") + 1] == "exit"


def test_close_session_suppresses_output(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("slurm_avail.ssh_auth.subprocess.run", fake_run)

    closed = close_control_session(
        SshControlTarget("login.example.org"),
        tmp_path / "ssh",
    )

    assert closed
    assert len(calls) == 1
    _command, kwargs = calls[0]
    assert kwargs["check"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 2


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
    monkeypatch.setattr(
        "slurm_avail.ssh_auth.interactive_endpoint_available",
        lambda *_args, **_kwargs: True,
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


def test_endpoint_probe_cannot_prompt_for_credentials() -> None:
    command = endpoint_probe_command(
        "login.example.org",
        "researcher",
        DashboardSettings(ssh_connect_timeout_seconds=12),
    )

    assert "BatchMode=yes" in command
    assert "NumberOfPasswordPrompts=0" in command
    assert "PreferredAuthentications=none" in command
    assert "ControlPath=none" in command
    assert "ConnectTimeout=12" in command
    assert command[-2:] == ["login.example.org", "/bin/true"]


def test_endpoint_unavailable_only_matches_connection_failures() -> None:
    assert endpoint_unavailable(
        "ssh: connect to host login.example.org port 22: Connection timed out"
    )
    assert endpoint_unavailable("ssh: Could not resolve hostname login.example.org")
    assert not endpoint_unavailable(
        "researcher@login.example.org: Permission denied (publickey,password)."
    )
