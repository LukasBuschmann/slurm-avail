"""Allocated compute time from Slurm accounting, independent of live refreshes."""

from __future__ import annotations

import concurrent.futures
import copy
import re
import shlex
import subprocess
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .collect import (
    cluster_user,
    parse_gpu_count,
    parse_slurm_time,
    run_endpoint,
    ssh_error_message,
)
from .config import AppConfig, ClusterConfig, DashboardSettings, active_cluster_configs

USAGE_WINDOWS = (
    ("6h", 21600),
    ("1d", 86400),
    ("7d", 604800),
    ("30d", 2592000),
    ("90d", 7776000),
    ("1y", 31536000),
    ("3y", 3 * 31536000),
    ("5y", 5 * 31536000),
    ("10y", 10 * 31536000),
)
USAGE_REFRESH_SECONDS = 300
# Slurm uses a zero start timestamp as its internal "not specified" sentinel.
# Asking sacct for exactly the Unix epoch therefore falls back to its default
# window (midnight today) instead of querying retained history.
USAGE_HISTORY_START = 1.0
USAGE_REFRESH_OVERLAP_SECONDS = 86400


@dataclass(frozen=True)
class UsageJob:
    job_id: str
    submit: str
    start: float
    end: float
    cpus: int
    gpus: int
    running: bool = False


@dataclass
class UsageResult:
    jobs: list[UsageJob] = field(default_factory=list)
    error: str | None = None
    incomplete: int = 0
    checked_at: float = field(default_factory=time.time)
    issues: list[str] = field(default_factory=list)
    refreshing: bool = False
    stale: bool = False


def usage_command(
    cluster: ClusterConfig,
    start: float,
    end: float,
    *,
    expand_arrays: bool = True,
) -> str:
    """Use UTC for both query bounds and returned timestamps, including across DST."""

    def stamp(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S")

    prefix = ""
    if cluster.slurm_bin_path:
        prefix = f"export PATH={shlex.quote(cluster.slurm_bin_path)}:$PATH\n"
    array_option = " --array" if expand_arrays else ""
    return prefix + (
        "export TZ=UTC0 LC_ALL=C SLURM_TIME_FORMAT=standard\n"
        f'sacct -u "$(id -un)" --local -X --duplicates{array_option} -n -P '
        f"-S {stamp(start)} -E {stamp(end)} "
        "-o JobIDRaw,Submit,Start,End,State,AllocCPUS,AllocTRES\n"
    )


def parse_usage(output: str, now: float) -> UsageResult:
    result = UsageResult()
    seen: set[UsageJob] = set()

    def omit(job_id: str, reason: str) -> None:
        result.incomplete += 1
        if len(result.issues) < 3:
            result.issues.append(f"{job_id}: {reason}")

    for row in output.splitlines():
        if not row.strip():
            continue
        fields = [value.strip() for value in row.split("|")]
        if len(fields) == 8 and not fields[-1]:
            fields.pop()  # Tolerate parsable output with a trailing delimiter.
        if len(fields) != 7:
            omit("record", f"expected 7 fields, received {len(fields)}")
            continue
        job_id, submit, start_text, end_text, state, cpu_text, tres = fields
        state = state.split(maxsplit=1)[0].rstrip("+").upper() if state else ""
        # Allocation rows only. Ignore unstarted jobs, which consumed no time.
        if "." in job_id or state in ("PENDING", "REVOKED"):
            continue
        start = parse_slurm_time(start_text, "+0000")
        if start is None:
            if start_text not in ("", "Unknown", "N/A", "None"):
                omit(job_id, "invalid start time")
            continue
        end = parse_slurm_time(end_text, "+0000")
        running = state in ("RUNNING", "SUSPENDED", "COMPLETING", "RESIZING")
        if running:
            end = now
        # Failed launch records can have no allocation and equal start/end times.
        if end == start:
            continue
        if end is None or end < start:
            omit(job_id, "missing or invalid end time")
            continue
        if not cpu_text.isdigit():
            omit(job_id, "missing or invalid allocated CPU count")
            continue
        if not tres:
            omit(job_id, "missing allocated resources (AllocTRES)")
            continue
        job = UsageJob(
            job_id,
            submit,
            start,
            min(end, now),
            int(cpu_text),
            parse_gpu_count(tres),
            running,
        )
        if job.end > job.start and job not in seen:
            result.jobs.append(job)
            seen.add(job)
    return result


def fetch_usage(
    cluster: ClusterConfig,
    settings: DashboardSettings,
    user_override: str | None,
    start: float,
    end: float,
    preferred_host: str | None = None,
) -> UsageResult:
    endpoints = ["local"] if cluster.mode == "local" else list(cluster.addresses)
    if preferred_host in endpoints:
        endpoints.remove(preferred_host)
        endpoints.insert(0, preferred_host)
    user = cluster_user(cluster, user_override)
    last_error = "no endpoints configured"
    expand_arrays = True
    for endpoint in endpoints:
        while True:
            command = usage_command(cluster, start, end, expand_arrays=expand_arrays)
            if cluster.mode == "ssh":
                # Some sites set SLURM_CONF or other required variables in /etc/profile.
                command = "/bin/sh -lc " + shlex.quote(command)
            try:
                response = run_endpoint(cluster, endpoint, user, command, settings)
                return parse_usage(response.stdout, min(end, time.time()))
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                last_error = ssh_error_message(error)
                stderr = getattr(error, "stderr", "") or ""
                if isinstance(stderr, bytes):
                    stderr = stderr.decode(errors="replace")
                if expand_arrays and re.search(
                    r"(?:unrecognized|unknown|invalid) option[^\n]*['\"]?--array",
                    stderr,
                    re.IGNORECASE,
                ):
                    expand_arrays = False
                    continue
                break
    safe_error = "".join(c for c in last_error if c.isprintable())[:240]
    return UsageResult(error=safe_error)


def usage_buckets(
    jobs: list[UsageJob],
    start: float,
    end: float,
    count: int,
) -> tuple[list[float], list[float]]:
    """Split allocated resource-hours by overlap, never by job completion date."""
    if count <= 0 or end <= start:
        return [0.0] * count, [0.0] * count
    step = (end - start) / count
    return usage_interval_buckets(
        jobs, [start + i * step for i in range(count)] + [end]
    )


def usage_interval_buckets(
    jobs: list[UsageJob],
    edges: list[float],
) -> tuple[list[float], list[float]]:
    """Allocate time to variable-length calendar periods, including DST changes."""
    count = max(0, len(edges) - 1)
    cpu, gpu = [0.0] * count, [0.0] * count
    if not count:
        return cpu, gpu
    for job in jobs:
        left, right = max(edges[0], job.start), min(edges[-1], job.end)
        if right <= left:
            continue
        first = max(0, bisect_right(edges, left) - 1)
        last = min(count, bisect_left(edges, right))
        for index in range(first, last):
            hours = (min(right, edges[index + 1]) - max(left, edges[index])) / 3600
            cpu[index] += job.cpus * hours
            gpu[index] += job.gpus * hours
    return cpu, gpu


def usage_totals(jobs: list[UsageJob]) -> tuple[float, float]:
    cpu = 0.0
    gpu = 0.0
    for job in jobs:
        hours = max(0.0, job.end - job.start) / 3600
        cpu += hours * job.cpus
        gpu += hours * job.gpus
    return cpu, gpu


def incremental_usage_start(result: UsageResult) -> float:
    start = max(
        USAGE_HISTORY_START,
        result.checked_at - USAGE_REFRESH_OVERLAP_SECONDS,
    )
    running_starts = [job.start for job in result.jobs if job.running]
    return min([start, *running_starts])


def merge_usage_results(
    previous: UsageResult,
    current: UsageResult,
    replace_start: float,
) -> UsageResult:
    """Replace the refreshed tail while retaining older accounting rows."""
    current_keys = {(job.job_id, job.submit, job.start) for job in current.jobs}
    jobs = {
        job
        for job in previous.jobs
        if job.start < replace_start
        and (job.job_id, job.submit, job.start) not in current_keys
    }
    jobs.update(current.jobs)
    issues = list(dict.fromkeys([*previous.issues, *current.issues]))[:3]
    return UsageResult(
        jobs=sorted(jobs, key=lambda job: (job.start, job.job_id)),
        error=current.error,
        incomplete=max(previous.incomplete, current.incomplete),
        checked_at=current.checked_at,
        issues=issues,
    )


class UsageHistory:
    """Full retained-history cache and background requests owned by the UI thread."""

    def __init__(self) -> None:
        self.window_index = 3
        self.end = float(int(time.time()))
        self.follow_now = True
        self.scope = 0
        self.generation = -1
        self.cache: OrderedDict[str, UsageResult] = OrderedDict()
        self.pending: dict[
            concurrent.futures.Future[UsageResult], tuple[str, float, float]
        ] = {}
        self.invalidated: set[str] = set()
        self.status: dict[str, UsageResult] = {}

    @property
    def start(self) -> float:
        return self.end - USAGE_WINDOWS[self.window_index][1]

    def zoom(self, direction: int) -> None:
        self.window_index = min(
            len(USAGE_WINDOWS) - 1, max(0, self.window_index + direction)
        )

    def move(self, direction: int) -> None:
        was_following = self.follow_now
        now = float(int(time.time()))
        self.end = min(now, self.end + direction * USAGE_WINDOWS[self.window_index][1])
        self.follow_now = self.end >= now
        if self.follow_now and not was_following:
            self.invalidated.update(self.cache)

    def refresh(self) -> None:
        if self.follow_now:
            self.end = float(int(time.time()))
        self.invalidated.update(self.cache)

    def reset(self, generation: int) -> None:
        for future in self.pending:
            future.cancel()
        self.pending.clear()
        self.cache.clear()
        self.invalidated.clear()
        self.status.clear()
        self.generation = generation

    def update(
        self,
        executor: concurrent.futures.ThreadPoolExecutor,
        config: AppConfig,
        user_override: str | None,
        generation: int,
        preferred: dict[str, str],
    ) -> dict[str, UsageResult | None]:
        if generation != self.generation:
            self.reset(generation)
        for future in list(self.pending):
            if future.done():
                cluster_name, request_start, _request_end = self.pending.pop(future)
                try:
                    result = future.result()
                except Exception:
                    result = UsageResult(error="Accounting request failed")
                previous = self.cache.get(cluster_name)
                if (
                    result.error
                    and previous is not None
                    and (previous.error is None or previous.stale)
                ):
                    result = replace(previous, error=result.error, stale=True)
                elif previous is not None and request_start > USAGE_HISTORY_START:
                    result = merge_usage_results(previous, result, request_start)
                self.cache[cluster_name] = result
                self.status[cluster_name] = result
                self.invalidated.discard(cluster_name)
        if self.follow_now and time.time() - self.end >= USAGE_REFRESH_SECONDS:
            self.refresh()
        clusters = active_cluster_configs(config)
        self.scope = min(self.scope, len(clusters))
        selected = (
            clusters if self.scope == 0 else clusters[self.scope - 1 : self.scope]
        )
        wanted = {cluster.name for cluster in selected}
        for future, (cluster_name, _start, _end) in list(self.pending.items()):
            if cluster_name not in wanted and future.cancel():
                del self.pending[future]
        results: dict[str, UsageResult | None] = {}
        for cluster in selected:
            result = self.cache.get(cluster.name)
            if result is not None:
                self.cache.move_to_end(cluster.name)
            needs_refresh = result is None or cluster.name in self.invalidated
            results[cluster.name] = (
                replace(
                    result, refreshing=True, stale=result.error is None or result.stale
                )
                if result is not None and needs_refresh
                else result
            )
            if (
                needs_refresh
                and cluster.name
                not in {pending[0] for pending in self.pending.values()}
                and len(self.pending) < 4
            ):
                request_end = time.time()
                request_start = (
                    incremental_usage_start(result)
                    if result is not None
                    else USAGE_HISTORY_START
                )
                future = executor.submit(
                    fetch_usage,
                    copy.deepcopy(cluster),
                    copy.deepcopy(config.settings),
                    user_override,
                    request_start,
                    request_end,
                    preferred.get(cluster.name),
                )
                self.pending[future] = (
                    cluster.name,
                    request_start,
                    request_end,
                )
        return results
