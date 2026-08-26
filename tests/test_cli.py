from __future__ import annotations

import io

from slurm_avail.cli import initial_jobs_scope_index, terminal_window_title
from slurm_avail.config import AppConfig, ClusterConfig


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


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


def test_live_dashboard_sets_and_restores_terminal_window_title() -> None:
    output = TtyBuffer()

    with terminal_window_title("slurm-avail", output):
        assert output.getvalue() == "\x1b[22;2t\x1b]2;slurm-avail\x07"

    assert output.getvalue() == ("\x1b[22;2t\x1b]2;slurm-avail\x07\x1b[23;2t")


def test_terminal_window_title_does_not_touch_redirected_output() -> None:
    output = io.StringIO()

    with terminal_window_title("slurm-avail", output):
        pass

    assert output.getvalue() == ""
