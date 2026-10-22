from __future__ import annotations

import concurrent.futures
import curses

import pytest

from slurm_avail.cli import main
from slurm_avail.config import (
    CLUSTER_OFFSET,
    SETTING_COUNT,
    AppConfig,
    ClusterConfig,
    active_views,
    app_config_from_dict,
    config_to_dict,
    load_config,
    save_config,
    validate_config,
)
from slurm_avail.config_views import config_table
from slurm_avail.constants import VIEWS
from slurm_avail.models import Cluster, UserJob
from slurm_avail.text import tab_line
from slurm_avail.tui import dashboard
from slurm_avail.usage import UsageHistory, UsageResult


def local_config():
    return AppConfig(clusters=[ClusterConfig(name="LOCAL", mode="local")])


def test_existing_config_keeps_all_tabs_and_partial_order_appends_new_tabs():
    config = app_config_from_dict({"clusters": [{"name": "LOCAL", "mode": "local"}]})
    assert active_views(config.settings) == VIEWS
    config.settings.tab_order = ["config", "usage", "jobs"]
    validate_config(config)
    assert config.settings.tab_order[:2] == ["usage", "jobs"]
    assert config.settings.tab_order[-1] == "config"
    assert set(config.settings.tab_order) == set(VIEWS)


def test_tab_settings_round_trip_and_config_stays_last(tmp_path):
    config = local_config()
    config.settings.tab_order = ["usage", "config", "nodes"]
    config.settings.disabled_tabs = ["nodes", "forecast"]
    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded == config
    assert app_config_from_dict(config_to_dict(config)) == config
    visible = active_views(loaded.settings)
    assert visible[0] == "usage" and visible[-1] == "config"
    assert "nodes" not in visible and "forecast" not in visible
    config.settings.disabled_tabs = list(VIEWS[:-1])
    validate_config(config)
    assert active_views(config.settings) == ("config",)


@pytest.mark.parametrize(
    "key, value, error",
    [
        ("disabled_tabs", ["config"], "cannot be disabled"),
        ("tab_order", ["nodes", "nodes"], "duplicate"),
        ("disabled_tabs", ["missing"], "unknown"),
        ("tab_order", "usage", "array"),
        ("disabled_tabs", [1], "array"),
    ],
)
def test_bad_tab_settings_are_rejected(key, value, error):
    config = local_config()
    setattr(config.settings, key, value)
    with pytest.raises(ValueError, match=error):
        validate_config(config)


def test_tab_bar_and_config_selection_use_configured_order(tmp_path):
    config = local_config()
    config.settings.tab_order = ["usage", *[v for v in VIEWS if v != "usage"]]
    config.settings.disabled_tabs = ["jobs"]
    text = "".join(t for t, _ in tab_line("usage", "", active_views(config.settings)))
    assert text.startswith("[USAGE]") and "[JOBS]" not in text
    assert text.rindex("[CONFIG]") > text.rindex("[FORECAST]")
    _, body, index = config_table(
        config, tmp_path / "config.toml", SETTING_COUNT, False, ""
    )
    assert "USAGE" in "".join(t for t, _ in body[index])
    _, body, index = config_table(
        config, tmp_path / "config.toml", CLUSTER_OFFSET, False, ""
    )
    assert "LOCAL" in "".join(t for t, _ in body[index])


class Screen:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.frames = []

    def keypad(self, _value):
        pass

    def timeout(self, _value):
        pass

    def getmaxyx(self):
        return 40, 130

    def erase(self):
        self.rows = [[" "] * 130 for _ in range(40)]

    def addstr(self, y, x, text, _style):
        assert 0 <= y < 40
        assert x >= 0 and x + len(text) <= 130
        self.rows[y][x : x + len(text)] = text

    def refresh(self):
        self.frames.append("\n".join("".join(row) for row in self.rows))

    def getch(self):
        return next(self.keys)


@pytest.fixture
def tui(monkeypatch):
    active = []
    requests = []

    def tabs(view, *args):
        active.append(view)
        return tab_line(view, *args)

    def submit(*args):
        requests.append(args)
        return {}

    monkeypatch.setattr("slurm_avail.tui.init_colors", lambda: {})
    monkeypatch.setattr("slurm_avail.tui.submit_cluster_refreshes", submit)
    monkeypatch.setattr("slurm_avail.tui.tab_line", tabs)
    monkeypatch.setattr(UsageHistory, "update", lambda *_args: {"LOCAL": UsageResult()})
    return active, requests


def test_tui_saves_tab_order_visibility_and_navigates_both_directions(tui, tmp_path):
    config = local_config()
    keys = (
        [curses.KEY_HOME]
        + [curses.KEY_DOWN] * SETTING_COUNT
        + [ord("h")]
        + [curses.KEY_DOWN] * 4
        + [curses.KEY_SR] * 4
        + [ord("s"), 9, 9, curses.KEY_BTAB, ord("q")]
    )
    path = tmp_path / "config.toml"
    dashboard(Screen(keys), "config", config, path, None, 0, 0)
    loaded = load_config(path)
    assert loaded.settings.tab_order[0] == "usage"
    assert loaded.settings.disabled_tabs == ["nodes"]
    assert loaded.settings.tab_order[-1] == "config"
    active, _ = tui
    assert active[-3:] == ["usage", "filesystems", "usage"]


def test_config_tab_cannot_be_disabled_or_moved(tui, tmp_path):
    keys = [curses.KEY_DOWN] * (CLUSTER_OFFSET - 1) + [
        ord(" "),
        curses.KEY_LEFT,
        curses.KEY_SR,
        curses.KEY_SF,
        ord("s"),
        ord("q"),
    ]
    path = tmp_path / "config.toml"
    screen = Screen(keys)
    dashboard(screen, "config", local_config(), path, None, 0, 0)
    loaded = load_config(path)
    assert active_views(loaded.settings) == VIEWS
    assert any("Config is always enabled and stays last" in f for f in screen.frames)


def test_reload_applies_new_tab_order_and_visibility(tui, tmp_path):
    saved = local_config()
    saved.settings.disabled_tabs = [v for v in VIEWS if v not in ("usage", "config")]
    path = tmp_path / "config.toml"
    save_config(saved, path)
    dashboard(
        Screen([ord("l"), 9, ord("q")]), "config", local_config(), path, None, 0, 0
    )
    active, _ = tui
    assert active == ["config", "config", "usage"]


def test_tabs_can_be_reenabled_from_config(tui, tmp_path):
    config = local_config()
    config.settings.disabled_tabs = list(VIEWS[:-1])
    keys = [curses.KEY_DOWN] * SETTING_COUNT + [curses.KEY_RIGHT, ord("s"), 9, ord("q")]
    path = tmp_path / "config.toml"
    dashboard(Screen(keys), "config", config, path, None, 0, 0)
    assert active_views(load_config(path).settings) == ("nodes", "config")
    active, _ = tui
    assert active[-1] == "nodes"


def test_config_only_navigation_does_not_start_live_polling(tui, tmp_path):
    config = local_config()
    config.settings.disabled_tabs = list(VIEWS[:-1])
    dashboard(
        Screen([9, curses.KEY_BTAB, ord("r"), ord("q")]),
        "nodes",
        config,
        tmp_path / "config.toml",
        None,
        0,
        0,
    )
    active, requests = tui
    assert active == ["config"] * 4
    assert requests == []


def test_dashboard_shows_request_count_in_bottom_right(tui, monkeypatch, tmp_path):
    monkeypatch.setattr("slurm_avail.tui.requests_last_minute", lambda _now: 17)
    screen = Screen([ord("q")])

    dashboard(screen, "nodes", local_config(), tmp_path / "config.toml", None, 0, 0)

    lines = screen.frames[-1].splitlines()
    assert "REQUESTS / MIN" in lines[-2]
    assert lines[-1].rstrip().endswith("17")


def test_jobs_ctrl_f_filters_live_without_blocking_navigation(
    tui, monkeypatch, tmp_path
):
    def job(job_id, name):
        return UserJob(
            job_id=job_id,
            partition="cpu",
            name=name,
            state="PENDING",
            reason="Resources",
            priority="1",
            start=None,
            end=None,
            scheduled_nodes=(),
            time_limit="01:00:00",
            node_count=1,
            cpu_count=1,
            memory="1G",
            tres_per_node="",
            allocated_tres="",
            account="project",
            qos="normal",
        )

    cluster = Cluster(
        name="LOCAL",
        host="local",
        user="researcher",
        jobs=[
            job("1", "protein-training"),
            job("2", "unrelated-analysis"),
        ],
    )
    monkeypatch.setattr(
        "slurm_avail.tui.placeholder_clusters",
        lambda *_args: [cluster],
    )
    screen = Screen(
        [
            ord("f") & 0x1F,
            ord("a"),
            curses.KEY_DOWN,
            curses.KEY_PPAGE,
            curses.KEY_NPAGE,
            curses.KEY_LEFT,
            curses.KEY_RIGHT,
            127,
            *map(ord, "ptn trn"),
            10,
            ord("q"),
        ]
    )

    dashboard(screen, "jobs", local_config(), tmp_path / "config.toml", None, 0, 0)

    filtered_frame = screen.frames[-1]
    assert 'filter "_" · 2 matches' in screen.frames[1]
    assert 'filter "a_" · 2 matches' in screen.frames[2]
    assert "SELECTED JOB 2" in screen.frames[3]
    assert 'filter "a_" · 2 matches' in screen.frames[7]
    assert 'filter "ptn trn" · 1 matches' in filtered_frame
    assert "protein-training" in filtered_frame
    assert "unrelated-analysis" not in filtered_frame
    assert 'filter "ptn trn" · 1 matches' in filtered_frame.splitlines()[-1]
    assert 'filter "' not in "\n".join(filtered_frame.splitlines()[:-1])


@pytest.mark.parametrize("finish_keys", [[], [10], [9, curses.KEY_BTAB]])
def test_jobs_ctrl_f_clears_and_hides_filter(tui, monkeypatch, tmp_path, finish_keys):
    cluster = Cluster(
        name="LOCAL",
        host="local",
        jobs=[
            UserJob(
                job_id="1",
                partition="cpu",
                name="queue-worker",
                state="PENDING",
                reason="Resources",
                priority="1",
                start=None,
                end=None,
                scheduled_nodes=(),
                time_limit="01:00:00",
                node_count=1,
                cpu_count=1,
                memory="1G",
                tres_per_node="",
                allocated_tres="",
                account="project",
                qos="normal",
            )
        ],
    )
    monkeypatch.setattr(
        "slurm_avail.tui.placeholder_clusters", lambda *_args: [cluster]
    )
    screen = Screen([6, *map(ord, "missing"), *finish_keys, 6, ord("q")])

    dashboard(screen, "jobs", local_config(), tmp_path / "config.toml", None, 0, 0)

    assert 'filter "missing' in screen.frames[8]
    assert "queue-worker" not in screen.frames[8]
    assert 'filter "' not in screen.frames[-1]
    assert "No current jobs match" not in screen.frames[-1]
    assert "queue-worker" in screen.frames[-1]


def test_jobs_empty_filter_toggles_off(tui, tmp_path):
    screen = Screen([6, 6, ord("q")])
    dashboard(screen, "jobs", local_config(), tmp_path / "config.toml", None, 0, 0)
    assert 'filter "_"' in screen.frames[1].splitlines()[-1]
    assert 'filter "' not in screen.frames[-1]


def test_jobs_filter_input_remains_responsive_during_pending_refresh(
    tui, monkeypatch, tmp_path
):
    pending = concurrent.futures.Future()
    pending.set_running_or_notify_cancel()
    monkeypatch.setattr(
        "slurm_avail.tui.submit_cluster_refreshes",
        lambda *_args: {pending: "LOCAL"},
    )
    screen = Screen([6, *map(ord, "abc"), curses.KEY_DOWN, 6, 9, ord("q")])

    dashboard(screen, "jobs", local_config(), tmp_path / "config.toml", None, 0, 0)

    assert not pending.done()
    assert 'filter "abc_"' in screen.frames[4].splitlines()[-1]
    assert 'filter "' not in screen.frames[6]
    active, _requests = tui
    assert active[-1] != "jobs"


def test_jobs_live_filter_accepts_q_as_search_text(tui, monkeypatch, tmp_path):
    cluster = Cluster(
        name="LOCAL",
        host="local",
        user="researcher",
        jobs=[
            UserJob(
                job_id="1",
                partition="cpu",
                name="queue-worker",
                state="PENDING",
                reason="Resources",
                priority="1",
                start=None,
                end=None,
                scheduled_nodes=(),
                time_limit="01:00:00",
                node_count=1,
                cpu_count=1,
                memory="1G",
                tres_per_node="",
                allocated_tres="",
                account="project",
                qos="normal",
            )
        ],
    )
    monkeypatch.setattr(
        "slurm_avail.tui.placeholder_clusters",
        lambda *_args: [cluster],
    )
    screen = Screen([ord("f") & 0x1F, ord("q"), 10, ord("q")])

    dashboard(screen, "jobs", local_config(), tmp_path / "config.toml", None, 0, 0)

    assert 'filter "q_" · 1 matches' in screen.frames[2]
    assert 'filter "q" · 1 matches' in screen.frames[-1]


def test_disabled_views_skip_unneeded_refreshes(tui, tmp_path):
    config = local_config()
    config.settings.disabled_tabs = ["jobs", "filesystems", "logins", "forecast"]
    dashboard(
        Screen([ord("r"), ord("q")]),
        "nodes",
        config,
        tmp_path / "config.toml",
        None,
        0,
        0,
    )
    _, requests = tui
    assert requests
    assert all(args[3:8] == (False, False, True, False, False) for args in requests)


def test_cluster_selection_after_tabs_still_edits_cluster(tui, tmp_path):
    screen = Screen([curses.KEY_END, 10, 27, ord("q")])
    dashboard(screen, "config", local_config(), tmp_path / "config.toml", None, 0, 0)
    assert "CLUSTER CONFIGURATION" in screen.frames[2]
    assert "LOCAL" in screen.frames[2]


def test_cli_defaults_to_first_enabled_tab_and_rejects_disabled(
    monkeypatch, tmp_path, capsys
):
    config = local_config()
    config.settings.tab_order = ["estimate", *[v for v in VIEWS if v != "estimate"]]
    config.settings.disabled_tabs = ["nodes"]
    path = tmp_path / "config.toml"
    save_config(config, path)
    monkeypatch.setattr("sys.argv", ["slurm-avail", "--once", "--config", str(path)])
    assert main() == 0
    output = capsys.readouterr().out
    assert output.startswith("[ESTIMATE]")
    assert "[NODES]" not in output
    monkeypatch.setattr(
        "sys.argv", ["slurm-avail", "--once", "--config", str(path), "--view", "nodes"]
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "tab 'nodes' is disabled" in capsys.readouterr().err
