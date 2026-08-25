"""Tracked subprocesses that can be stopped during dashboard shutdown."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Sequence


class CommandCancelled(subprocess.SubprocessError):
    """Raised when shutdown prevents a command from starting."""


_active_lock = threading.Lock()
_active_processes: set[subprocess.Popen[str]] = set()
_cancelling = threading.Event()


def reset_command_cancellation() -> None:
    """Allow commands for a new dashboard run."""
    _cancelling.clear()


def cancellation_requested() -> bool:
    return _cancelling.is_set()


def active_command_count() -> int:
    with _active_lock:
        return len(_active_processes)


def wait_or_cancel(seconds: float) -> bool:
    """Wait for a retry delay. Return true if shutdown interrupted it."""
    return _cancelling.wait(max(0.0, seconds))


def _signal_process(process: subprocess.Popen[str], signal_number: int) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal_number)


def _stop_process(process: subprocess.Popen[str]) -> None:
    _signal_process(process, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.1)
    _signal_process(process, signal.SIGKILL)


def cancel_active_commands() -> None:
    """Prevent new commands and stop every command owned by this process."""
    _cancelling.set()
    with _active_lock:
        active = list(_active_processes)
    for process in active:
        _signal_process(process, signal.SIGTERM)

    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline and any(
        process.poll() is None for process in active
    ):
        time.sleep(0.01)
    for process in active:
        _signal_process(process, signal.SIGKILL)


def run_captured(
    command: Sequence[str],
    *,
    check: bool,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a captured text command that dashboard shutdown can terminate."""
    with _active_lock:
        if _cancelling.is_set():
            raise CommandCancelled("command cancelled")
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        _active_processes.add(process)

    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _stop_process(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                error.cmd,
                error.timeout,
                output=stdout,
                stderr=stderr,
            ) from error
        except BaseException:
            _stop_process(process)
            with contextlib.suppress(OSError):
                process.communicate()
            raise
    finally:
        with _active_lock:
            _active_processes.discard(process)

    completed = subprocess.CompletedProcess(
        list(command),
        process.returncode,
        stdout,
        stderr,
    )
    if check and process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode,
            list(command),
            output=stdout,
            stderr=stderr,
        )
    return completed
