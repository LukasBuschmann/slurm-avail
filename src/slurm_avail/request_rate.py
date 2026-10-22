"""Thread-safe rolling count of cluster command requests."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

_WINDOW_SECONDS = 60.0
_request_times: deque[float] = deque()
_lock = Lock()


def record_request(now: float | None = None) -> None:
    """Record one local or SSH cluster command attempt."""
    with _lock:
        _request_times.append(time.monotonic() if now is None else now)


def requests_last_minute(now: float | None = None) -> int:
    """Return attempts made during the trailing 60 seconds."""
    with _lock:
        current = time.monotonic() if now is None else now
        cutoff = current - _WINDOW_SECONDS
        while _request_times and _request_times[0] <= cutoff:
            _request_times.popleft()
        return len(_request_times)


def reset_request_history() -> None:
    """Start a fresh dashboard-session count."""
    with _lock:
        _request_times.clear()
