from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import time

import pytest

from slurm_avail.processes import (
    CommandCancelled,
    active_command_count,
    cancel_active_commands,
    reset_command_cancellation,
    run_captured,
)


def test_shutdown_terminates_active_commands() -> None:
    reset_command_cancellation()
    started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_captured,
                [sys.executable, "-c", "import time; time.sleep(30)"],
                check=True,
                timeout=60,
            )
            deadline = time.monotonic() + 2
            while active_command_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert active_command_count() == 1

            cancel_active_commands()

            with pytest.raises(subprocess.CalledProcessError):
                future.result(timeout=2)
        assert time.monotonic() - started < 3
    finally:
        reset_command_cancellation()


def test_shutdown_prevents_late_commands_from_starting() -> None:
    reset_command_cancellation()
    try:
        cancel_active_commands()

        with pytest.raises(CommandCancelled):
            run_captured(
                [sys.executable, "-c", "print('not started')"],
                check=True,
                timeout=1,
            )
    finally:
        reset_command_cancellation()
