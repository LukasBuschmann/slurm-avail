from __future__ import annotations

from slurm_avail.config import AppConfig, ClusterConfig, DashboardSettings
from slurm_avail.ssh_auth import SshControlTarget, SshSessionResult
from slurm_avail.tui import (
    connect_cluster_with_terminal,
    startup_authentication_clusters,
)


class FakeScreen:
    def timeout(self, _milliseconds: int) -> None:
        pass

    def keypad(self, _enabled: bool) -> None:
        pass

    def touchwin(self) -> None:
        pass

    def clear(self) -> None:
        pass

    def refresh(self) -> None:
        pass


def test_authentication_context_names_cluster_server_and_user(
    monkeypatch,
    capsys,
) -> None:
    cluster = ClusterConfig(
        name="REMOTE",
        addresses=["login.example.org"],
        user="researcher",
        authentication="interactive",
    )
    monkeypatch.setattr(
        "slurm_avail.tui.open_interactive_session",
        lambda *_args, **_kwargs: SshSessionResult(
            "login.example.org",
            connected=True,
        ),
    )

    connected, total, targets, cancelled = connect_cluster_with_terminal(
        FakeScreen(),  # type: ignore[arg-type]
        cluster,
        DashboardSettings(),
        user_override=None,
    )

    output = capsys.readouterr().out
    assert "authentication attempt 1/1 for REMOTE" in output
    assert "server  login.example.org" in output
    assert "user    researcher" in output
    assert "Authentication is handled directly by OpenSSH." in output
    assert "slurm-avail does not read, handle, or store your password." in output
    assert "Config > Startup authentication" in output
    assert connected == total == 1
    assert targets == {SshControlTarget("login.example.org", "researcher")}
    assert not cancelled


def test_authentication_stops_after_first_working_endpoint(
    monkeypatch,
    capsys,
) -> None:
    cluster = ClusterConfig(
        name="REMOTE",
        addresses=["login1.example.org", "login2.example.org"],
        user="researcher",
        authentication="interactive",
    )
    calls: list[str] = []

    def connect(_cluster, endpoint, *_args, **_kwargs):
        calls.append(endpoint)
        return SshSessionResult(endpoint, connected=True)

    monkeypatch.setattr("slurm_avail.tui.open_interactive_session", connect)

    connected, attempted, targets, cancelled = connect_cluster_with_terminal(
        FakeScreen(),  # type: ignore[arg-type]
        cluster,
        DashboardSettings(),
        user_override=None,
    )

    output = capsys.readouterr().out
    assert calls == ["login1.example.org"]
    assert "authentication attempt 2/2" not in output
    assert connected == attempted == 1
    assert targets == {SshControlTarget("login1.example.org", "researcher")}
    assert not cancelled


def test_authentication_tries_next_unavailable_endpoint(
    monkeypatch,
    capsys,
) -> None:
    cluster = ClusterConfig(
        name="REMOTE",
        addresses=["login1.example.org", "login2.example.org"],
        user="researcher",
        authentication="interactive",
    )
    calls: list[str] = []

    def connect(_cluster, endpoint, *_args, **_kwargs):
        calls.append(endpoint)
        return SshSessionResult(
            endpoint,
            connected=endpoint == "login2.example.org",
            message="unavailable" if endpoint == "login1.example.org" else "",
        )

    monkeypatch.setattr("slurm_avail.tui.open_interactive_session", connect)

    connected, attempted, targets, cancelled = connect_cluster_with_terminal(
        FakeScreen(),  # type: ignore[arg-type]
        cluster,
        DashboardSettings(),
        user_override=None,
    )

    output = capsys.readouterr().out
    assert calls == ["login1.example.org", "login2.example.org"]
    assert "authentication attempt 1/2" in output
    assert "authentication attempt 2/2" in output
    assert connected == 1
    assert attempted == 2
    assert targets == {SshControlTarget("login2.example.org", "researcher")}
    assert not cancelled


def test_startup_authentication_respects_global_setting_and_visibility() -> None:
    interactive = ClusterConfig(
        name="VISIBLE",
        addresses=["visible.example.org"],
        authentication="interactive",
    )
    hidden = ClusterConfig(
        name="HIDDEN",
        addresses=["hidden.example.org"],
        authentication="interactive",
        hidden=True,
    )
    batch = ClusterConfig(
        name="BATCH",
        addresses=["batch.example.org"],
        authentication="batch",
    )
    config = AppConfig(clusters=[interactive, hidden, batch])

    assert startup_authentication_clusters(config) == [interactive]

    config.settings.authenticate_on_startup = False
    assert startup_authentication_clusters(config) == []
