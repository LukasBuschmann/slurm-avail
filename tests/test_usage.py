from __future__ import annotations

import concurrent.futures
import subprocess
from datetime import UTC, datetime

import pytest

from slurm_avail.config import AppConfig, ClusterConfig, DashboardSettings
from slurm_avail.usage import (
    USAGE_WINDOWS,
    UsageHistory,
    UsageJob,
    UsageResult,
    fetch_usage,
    parse_usage,
    usage_buckets,
    usage_command,
)
from slurm_avail.usage_views import usage_scope_menu, usage_table, usage_tick


def epoch(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def record(
    job_id="12",
    start="2026-01-01T00:00:00",
    end="2026-01-01T03:00:00",
    state="COMPLETED",
    tres="cpu=8,gres/gpu=2,gres/gpu:a100=2",
):
    return f"{job_id}|2025-12-31T23:00:00|{start}|{end}|{state}|8|{tres}"


def test_allocation_hours_split_at_boundaries_and_preserve_totals():
    jobs = [UsageJob("1", "", -1800, 9000, 8, 2)]
    cpu, gpu = usage_buckets(jobs, 0, 10800, 3)
    assert cpu == pytest.approx([8, 8, 4])
    assert gpu == pytest.approx([2, 2, 1])
    # Changing resolution must not change allocated time within the window.
    for count in (1, 7, 100):
        cpu, gpu = usage_buckets(jobs, 0, 10800, count)
        assert sum(cpu) == pytest.approx(20)
        assert sum(gpu) == pytest.approx(5)


def test_jobs_outside_window_and_exact_boundaries():
    jobs = [
        UsageJob("1", "", -3600, 0, 8, 2),
        UsageJob("2", "", 3600, 7200, 8, 2),
        UsageJob("3", "", 0, 3600, 4, 1),
    ]
    assert usage_buckets(jobs, 0, 3600, 1) == ([4], [1])
    assert usage_buckets(jobs, 0, 0, 1) == ([0], [0])


def test_running_and_failed_jobs_count_but_pending_and_steps_do_not():
    output = "\n".join(
        [
            record(),
            record("13", end="Unknown", state="RUNNING"),
            record("14", state="FAILED"),
            record("15", state="PENDING"),
            record("12.batch"),
            record("16", start="Unknown", state="CANCELLED"),
        ]
    )
    now = epoch("2026-01-01T02:00:00")
    result = parse_usage(output, now)
    assert [j.job_id for j in result.jobs] == ["12", "13", "14"]
    assert all(j.end == now and j.gpus == 2 for j in result.jobs)
    assert result.incomplete == 0


def test_duplicates_arrays_and_requeued_allocations():
    output = "\n".join(
        [
            record("12_1"),
            record("12_1"),
            record("12_2"),
            record("12_1", start="2026-01-02T00:00:00", end="2026-01-02T01:00:00"),
        ]
    )
    result = parse_usage(output, epoch("2026-01-03T00:00:00"))
    assert len(result.jobs) == 3
    cpu, gpu = usage_buckets(result.jobs, epoch("2026-01-01"), epoch("2026-01-03"), 2)
    assert cpu == [48, 8]
    assert gpu == [12, 2]


def test_missing_accounting_fields_are_marked_incomplete():
    result = parse_usage("unexpected layout\n" + record(tres=""), epoch("2026-01-02"))
    assert result.incomplete == 2
    assert not result.jobs
    assert "missing allocated resources" in result.issues[1]


def test_failed_launch_without_allocation_is_not_a_parsing_error():
    # Actual layout observed on Capella: no runtime, no CPUs and no AllocTRES.
    row = "1|2026-08-18T07:40:18|2026-08-18T07:40:18|2026-08-18T07:40:18|FAILED|0|"
    result = parse_usage(row, epoch("2026-08-19"))
    assert not result.jobs
    assert result.incomplete == 0
    assert not result.issues


def test_parser_tolerates_whitespace_trailing_delimiter_and_state_suffix():
    row = " | ".join(record(state="RUNNING+").split("|")) + " |"
    result = parse_usage(row, epoch("2026-01-01T04:00:00"))
    assert result.incomplete == 0
    assert result.jobs[0].end == epoch("2026-01-01T04:00:00")


def test_old_sacct_retries_without_array_option_in_login_shell(monkeypatch):
    import shlex

    commands = []

    def run(_cluster, _endpoint, _user, command, _settings):
        wrapper = shlex.split(command)
        assert wrapper[:2] == ["/bin/sh", "-lc"]
        commands.append(wrapper[2])
        if "--array" in command:
            raise subprocess.CalledProcessError(
                1,
                ["ssh"],
                stderr="locale warning\nsacct: unrecognized option '--array'",
            )
        return subprocess.CompletedProcess([], 0, stdout=record())

    monkeypatch.setattr("slurm_avail.usage.run_endpoint", run)
    cluster = ClusterConfig(name="OLD", addresses=["login"], user="me")
    result = fetch_usage(
        cluster, DashboardSettings(), None, epoch("2026-01-01"), epoch("2026-01-02")
    )
    assert len(commands) == 2
    assert "--array" in commands[0] and "--array" not in commands[1]
    assert "--local -X --duplicates" in commands[1]
    assert result.error is None and len(result.jobs) == 1


def test_real_accounting_error_is_preserved_without_unsupported_option_retry(
    monkeypatch,
):
    calls = []

    def run(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(
            1,
            ["ssh", "private-command"],
            stderr="sacct: Accounting storage is disabled",
        )

    monkeypatch.setattr("slurm_avail.usage.run_endpoint", run)
    result = fetch_usage(
        ClusterConfig(name="LOCAL", mode="local"), DashboardSettings(), None, 0, 3600
    )
    assert result.error == "sacct: Accounting storage is disabled"
    assert len(calls) == 1


def test_query_uses_utc_explicit_bounds_and_allocation_records():
    command = usage_command(
        ClusterConfig(name="A", slurm_bin_path="/slurm bin"),
        epoch("2026-03-28"),
        epoch("2026-03-30"),
    )
    assert "TZ=UTC0" in command
    assert "-S 2026-03-28T00:00:00 -E 2026-03-30T00:00:00" in command
    assert "--local -X --duplicates --array" in command
    assert "export PATH='/slurm bin':$PATH" in command


def test_fetch_fails_over_without_fetching_live_nodes(monkeypatch):
    calls = []

    def run(_cluster, endpoint, _user, command, _settings):
        calls.append(endpoint)
        assert "scontrol" not in command and "squeue" not in command
        if endpoint == "preferred":
            raise subprocess.TimeoutExpired("ssh", 20)
        return subprocess.CompletedProcess([], 0, stdout=record())

    monkeypatch.setattr("slurm_avail.usage.run_endpoint", run)
    cluster = ClusterConfig(name="A", addresses=["backup", "preferred"], user="me")
    result = fetch_usage(
        cluster,
        DashboardSettings(),
        None,
        epoch("2026-01-01"),
        epoch("2026-01-02"),
        "preferred",
    )
    assert calls == ["preferred", "backup"]
    assert len(result.jobs) == 1 and result.error is None


class ControlledExecutor:
    def __init__(self):
        self.requests = []

    def submit(self, function, *args):
        future = concurrent.futures.Future()
        self.requests.append((future, function, args))
        return future


def config():
    return AppConfig(
        clusters=[
            ClusterConfig(name="A", mode="local"),
            ClusterConfig(name="B", mode="local"),
            ClusterConfig(name="HIDDEN", mode="local", hidden=True),
        ]
    )


def test_requests_are_nonblocking_cached_and_scoped():
    history, executor = UsageHistory(), ControlledExecutor()
    results = history.update(executor, config(), None, 0, {})
    assert results == {"A": None, "B": None}
    assert len(executor.requests) == 2
    history.update(executor, config(), None, 0, {})
    assert len(executor.requests) == 2
    for future, _, _ in executor.requests:
        future.set_result(UsageResult())
    history.scope = 1
    results = history.update(executor, config(), None, 0, {})
    assert list(results) == ["A"]
    assert results["A"] is not None
    history.scope = 0
    history.update(executor, config(), None, 0, {})
    assert len(executor.requests) == 2
    history.refresh()
    history.update(executor, config(), None, 0, {})
    assert len(executor.requests) == 4


@pytest.mark.parametrize("refresh_type", ["manual", "automatic", "historical"])
def test_refresh_keeps_previous_graphs_until_replacement_arrives(
    monkeypatch, refresh_type
):
    now = [2000000000.0]
    monkeypatch.setattr("slurm_avail.usage.time.time", lambda: now[0])
    history, executor = UsageHistory(), ControlledExecutor()
    history.scope = 1
    history.update(executor, config(), None, 0, {})
    previous = UsageResult(jobs=[UsageJob("1", "", now[0] - 3600, now[0], 8, 2)])
    executor.requests[0][0].set_result(previous)
    history.update(executor, config(), None, 0, {})
    now[0] += 301
    if refresh_type == "historical":
        history.follow_now = False
    if refresh_type != "automatic":
        history.refresh()
    results = history.update(executor, config(), None, 0, {})
    assert results["A"].jobs == previous.jobs
    assert results["A"].refreshing and results["A"].stale
    headers, body = usage_table(history, ["A", "B"], results, 80)
    assert "previous data shown" in "".join(t for row in headers for t, _ in row)
    assert "CPU-hours   total 8 h" in "".join(t for row in body for t, _ in row)
    assert len(executor.requests) == 2
    fresh = UsageResult(jobs=[UsageJob("2", "", now[0] - 3600, now[0], 16, 4)])
    executor.requests[1][0].set_result(fresh)
    results = history.update(executor, config(), None, 0, {})
    assert results["A"].jobs == fresh.jobs
    assert not results["A"].refreshing and not results["A"].stale
    # A different requested period must never display unrelated cached data.
    history.move(-1)
    results = history.update(executor, config(), None, 0, {})
    assert results["A"] is None


def test_failed_refresh_preserves_last_successful_snapshot():
    history, executor = UsageHistory(), ControlledExecutor()
    history.scope = 1
    history.update(executor, config(), None, 0, {})
    previous = UsageResult(
        jobs=[UsageJob("1", "", history.end - 3600, history.end, 8, 2)]
    )
    executor.requests[0][0].set_result(previous)
    history.update(executor, config(), None, 0, {})
    history.refresh()
    history.update(executor, config(), None, 0, {})
    executor.requests[1][0].set_result(UsageResult(error="request timed out after 20s"))
    results = history.update(executor, config(), None, 0, {})
    assert results["A"].jobs == previous.jobs
    assert results["A"].stale and results["A"].error
    headers, body = usage_table(history, ["A", "B"], results, 80)
    header_text = "".join(t for row in headers for t, _ in row)
    body_text = "".join(t for row in body for t, _ in row)
    assert "[x A]" in header_text
    assert "timed out" in header_text and "timed out" not in body_text
    assert "CPU-hours   total 8 h" in body_text


def test_return_to_present_does_not_reuse_a_separate_historical_period(monkeypatch):
    monkeypatch.setattr("slurm_avail.usage.time.time", lambda: 2000000000)
    history, executor = UsageHistory(), ControlledExecutor()
    history.scope = 1
    history.move(-1)
    history.update(executor, config(), None, 0, {})
    executor.requests[0][0].set_result(UsageResult())
    history.update(executor, config(), None, 0, {})
    history.follow_now = True
    history.refresh()
    assert history.update(executor, config(), None, 0, {})["A"] is None


def test_compact_axis_labels_and_month_default():
    assert UsageHistory().window_index == 3
    assert usage_tick(55700) == "56k"
    assert usage_tick(1000) == "1k"
    assert usage_tick(1280000) == "1M"
    assert usage_tick(778) == "778"
    assert usage_tick(0.001) == "0"
    assert usage_tick(0) == "0"


def test_selector_wraps_and_keeps_other_clusters_availability_visible():
    history = UsageHistory()
    history.scope = 1
    history.status["BARNARD"] = UsageResult(error="unavailable")
    names = ["CAPELLA", "ALPHA", "BARNARD", "ROMEO", "BIO"]
    rows = usage_scope_menu(history, names, {"CAPELLA": UsageResult()}, 51)
    text = "".join(t for row in rows for t, _ in row)
    assert "[x BARNARD]" in text
    assert all(sum(len(t) for t, _ in row) <= 51 for row in rows)
    assert len(rows) > 1


def test_stale_running_query_cannot_replace_changed_range_or_config():
    history, executor = UsageHistory(), ControlledExecutor()
    history.scope = 1
    history.update(executor, config(), None, 0, {})
    old = executor.requests[0][0]
    old.set_running_or_notify_cancel()
    history.zoom(-1)
    history.update(executor, config(), None, 1, {})
    old.set_result(UsageResult(error="old config"))
    current = executor.requests[-1][0]
    current.set_result(UsageResult())
    results = history.update(executor, config(), None, 1, {})
    assert results["A"].error is None
    assert len(history.cache) == 1


def test_navigation_limits_and_return_to_now(monkeypatch):
    monkeypatch.setattr("slurm_avail.usage.time.time", lambda: 2000000000)
    history = UsageHistory()
    history.move(-1)
    assert history.end == 2000000000 - USAGE_WINDOWS[3][1]
    assert not history.follow_now
    history.move(1)
    assert history.follow_now and history.end == 2000000000
    history.move(1)
    assert history.end == 2000000000
    for _ in range(10):
        history.zoom(-1)
    assert history.window_index == 0
    for _ in range(10):
        history.zoom(1)
    assert history.window_index == len(USAGE_WINDOWS) - 1


def test_charts_combine_clusters_without_deduplicating_shared_job_ids():
    history = UsageHistory()
    history.end = 10800
    history.window_index = 0
    job = UsageJob("12", "", 0, 10800, 8, 2)
    results = {"A": UsageResult(jobs=[job]), "B": UsageResult(jobs=[job])}
    headers, body = usage_table(history, ["A", "B"], results, 80)
    text = "\n".join("".join(part for part, _ in row) for row in headers + body)
    assert "CPU-hours   total 48 h" in text
    assert "GPU-hours   total 12 h" in text
    assert "Partial" not in text
    assert max(sum(len(part) for part, _ in row) for row in body) <= 80


@pytest.mark.parametrize(
    "other", [None, UsageResult(error="unavailable"), UsageResult(incomplete=1)]
)
def test_partial_data_is_labelled(other):
    history = UsageHistory()
    headers, body = usage_table(
        history, ["A", "B"], {"A": UsageResult(), "B": other}, 80
    )
    assert "Partial totals" in "".join(part for row in headers for part, _ in row)
    assert "Partial totals" not in "".join(part for row in body for part, _ in row)


def test_unavailable_is_not_rendered_as_zero_usage():
    headers, body = usage_table(
        UsageHistory(), ["A"], {"A": UsageResult(error="unavailable")}, 80
    )
    assert not body
    text = "".join(part for row in headers for part, _ in row)
    assert "[x A]" in text
    assert "Usage unavailable" in text
    assert "total 0" not in text


def test_cli_usage_once_uses_accounting_only(monkeypatch, tmp_path, capsys):
    from slurm_avail.cli import main
    from slurm_avail.config import save_config

    path = tmp_path / "config.toml"
    save_config(config(), path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "slurm-avail",
            "--once",
            "--view",
            "usage",
            "--cluster",
            "B",
            "--config",
            str(path),
        ],
    )
    seen = []

    def fetch(cluster, *_args):
        seen.append(cluster.name)
        return UsageResult()

    monkeypatch.setattr("slurm_avail.cli.fetch_usage", fetch)
    monkeypatch.setattr(
        "slurm_avail.cli.fetch_all_clusters",
        lambda *_a, **_kw: pytest.fail("Usage should not query live nodes"),
    )
    assert main() == 0
    assert seen == ["B"]
    assert "YOUR ALLOCATED COMPUTE TIME" in capsys.readouterr().out


@pytest.mark.parametrize("size", [(24, 80), (40, 140)])
def test_dashboard_usage_keys_and_rendering(monkeypatch, tmp_path, size):
    import curses

    from slurm_avail.tui import dashboard

    states = []
    frames = []

    def update(history, *_args):
        states.append((history.window_index, history.scope, history.follow_now))
        job = UsageJob("1", "", history.end - 3600, history.end, 8, 2)
        names = ["A", "B"] if history.scope == 0 else [["A", "B"][history.scope - 1]]
        return {name: UsageResult(jobs=[job]) for name in names}

    class Screen:
        def __init__(self):
            self.keys = iter(
                [
                    ord("+"),
                    ord("-"),
                    curses.KEY_LEFT,
                    curses.KEY_END,
                    ord("]"),
                    ord("0"),
                    ord("2"),
                    ord("r"),
                    ord("q"),
                ]
            )

        def keypad(self, _value):
            pass

        def timeout(self, _value):
            pass

        def getmaxyx(self):
            return size

        def erase(self):
            self.rows = [[" "] * size[1] for _ in range(size[0])]

        def addstr(self, y, x, text, _style):
            assert 0 <= y < size[0]
            assert x >= 0 and x + len(text) <= size[1]
            self.rows[y][x : x + len(text)] = text

        def refresh(self):
            frames.append("\n".join("".join(row) for row in self.rows))

        def getch(self):
            return next(self.keys)

    monkeypatch.setattr("slurm_avail.tui.init_colors", lambda: {})
    monkeypatch.setattr("slurm_avail.tui.submit_cluster_refreshes", lambda *_a: {})
    monkeypatch.setattr(UsageHistory, "update", update)
    dashboard(Screen(), "usage", config(), tmp_path / "config.toml", None, 0, 0)
    assert states == [
        (3, 0, True),
        (2, 0, True),
        (3, 0, True),
        (3, 0, False),
        (3, 0, True),
        (3, 1, True),
        (3, 0, True),
        (3, 2, True),
        (3, 2, True),
    ]
    assert all("CPU-hours" in frame and "GPU-hours" in frame for frame in frames)
    assert "total 16 h" in frames[0]
    assert "total 8 h" in frames[-1]
