"""Collect and parse Slurm data locally or through SSH."""

from __future__ import annotations

import concurrent.futures
import copy
import getpass
import math
import re
import shlex
import subprocess
import time
from collections.abc import Iterable
from datetime import datetime

from .config import (
    AppConfig,
    ClusterConfig,
    DashboardSettings,
    active_cluster_configs,
)
from .models import (
    Cluster,
    Filesystem,
    LoginNode,
    Node,
    ReservationInterval,
    RunningInterval,
)

UNAVAILABLE_RE = re.compile(
    r"DOWN|DRAIN|FAIL|MAINT|RESERVED|UNKNOWN|REBOOT|COMPLETING|"
    r"INVAL|NO_RESPOND|POWER",
    re.IGNORECASE,
)
RESERVED_RE = re.compile(r"RESERV", re.IGNORECASE)
DRAINED_RE = re.compile(r"DRAIN", re.IGNORECASE)
SQUEUE_FORMAT = (
    "JobID:0|,EndTime:0|,NodeList:0|,tres-per-node:0|,"
    "tres-alloc:0|,NumCPUs:0|,NumNodes:0"
)
SCHEDULE_COMMAND = rf"""
printf '__SCHEDULE__\n'
printf '__TIMEZONE__|'
date +%z
printf '__RESERVATIONS__\n'
scontrol show reservations -o
printf '__RUNNING_JOBS__\n'
squeue -a -t R -h -O '{SQUEUE_FORMAT}'
"""


def parse_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def tres_value(value: str, key: str) -> int:
    prefix = f"{key}="
    for item in value.split(","):
        if item.startswith(prefix):
            return parse_int(item[len(prefix) :])
    return 0


def node_status(
    state: str,
    owner: str | None,
    current_user: str,
    gpu_total: int,
    gpu_alloc: int,
    cpu_total: int,
    cpu_alloc: int,
    mem_total: int,
    mem_alloc: int,
) -> str:
    if DRAINED_RE.search(state):
        return "drained"
    if RESERVED_RE.search(state):
        return "reserved"
    if UNAVAILABLE_RE.search(state):
        return "drained"
    if owner and owner != current_user:
        return "exclusive"
    if gpu_total and gpu_alloc >= gpu_total:
        return "full"
    if not gpu_total and (
        cpu_alloc >= cpu_total or (mem_total and mem_alloc >= mem_total)
    ):
        return "full"
    return "available"


def parse_nodes(
    output: str,
    current_user: str,
    exclude_partitions: Iterable[str] = (),
) -> list[Node]:
    nodes: list[Node] = []
    excluded = {partition.casefold() for partition in exclude_partitions}

    for raw_line in output.splitlines():
        values: dict[str, str] = {}
        for token in raw_line.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            values[key] = value

        name = values.get("NodeName", "")
        partitions = values.get("Partitions", "")
        cpu_total = parse_int(values.get("CPUEfctv")) or parse_int(values.get("CPUTot"))
        if not name or not cpu_total:
            continue

        node_partitions = {
            partition.casefold() for partition in partitions.split(",") if partition
        }
        if excluded.intersection(node_partitions):
            continue

        state = values.get("State", "UNKNOWN")
        owner_value = values.get("Owner", "N/A")
        owner = owner_value.split("(", 1)[0]
        if owner in ("", "N/A", "None"):
            owner = None
        cfg_tres = values.get("CfgTRES", "")
        alloc_tres = values.get("AllocTRES", "")
        gres = values.get("Gres", "")
        cpu_alloc = parse_int(values.get("CPUAlloc"))
        mem_alloc = parse_int(values.get("AllocMem"))
        mem_total = parse_int(values.get("RealMemory"))

        gpu_total = 0
        gpu_alloc = 0
        if "gpu:" in gres:
            gpu_total = tres_value(cfg_tres, "gres/gpu")
            gpu_alloc = min(gpu_total, tres_value(alloc_tres, "gres/gpu"))

        status = node_status(
            state,
            owner,
            current_user,
            gpu_total,
            gpu_alloc,
            cpu_total,
            cpu_alloc,
            mem_total,
            mem_alloc,
        )
        unavailable = status in ("drained", "reserved", "exclusive")

        nodes.append(
            Node(
                name=name,
                state=state,
                status=status,
                owner=owner,
                unavailable=unavailable,
                gpu_total=gpu_total,
                gpu_alloc=gpu_alloc,
                gpu_busy=gpu_total if unavailable else gpu_alloc,
                cpu_alloc=cpu_alloc,
                cpu_total=cpu_total,
                mem_alloc=mem_alloc,
                mem_total=mem_total,
            )
        )

    def occupancy(node: Node) -> tuple[float, float, float, str]:
        cpu_busy = node.cpu_total if node.unavailable else node.cpu_alloc
        cpu_ratio = cpu_busy / node.cpu_total if node.cpu_total else 1.0
        mem_ratio = node.mem_alloc / node.mem_total if node.mem_total else 1.0
        if node.gpu_total:
            gpu_ratio = node.gpu_busy / node.gpu_total
            return gpu_ratio, cpu_ratio, mem_ratio, node.name
        return cpu_ratio, mem_ratio, 0.0, node.name

    nodes.sort(key=occupancy)
    return nodes


def parse_filesystems(output: str) -> dict[str, Filesystem]:
    filesystems: dict[str, Filesystem] = {}
    for raw_line in output.splitlines():
        label, separator, df_row = raw_line.partition("|")
        if not separator or not label:
            continue
        fields = df_row.split()
        if len(fields) < 6:
            continue
        try:
            total = int(fields[-5]) * 1024
            used = int(fields[-4]) * 1024
            available = int(fields[-3]) * 1024
            percent_used = int(fields[-2].rstrip("%"))
        except ValueError:
            continue
        filesystems[label] = Filesystem(
            label=label,
            total=total,
            used=used,
            available=available,
            percent_used=percent_used,
            mountpoint=fields[-1],
        )
    return filesystems


def split_hostlist(value: str) -> list[str]:
    """Split commas that are outside Slurm host-list brackets."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "[":
            depth += 1
        elif character == "]":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return [part for part in parts if part]


def expand_hostlist(value: str) -> tuple[str, ...]:
    """Expand the ordinary Slurm node-list forms used by these clusters."""
    expanded: list[str] = []
    for part in split_hostlist(value.strip()):
        match = re.search(r"\[([^][]+)\]", part)
        if match is None:
            expanded.append(part)
            continue
        prefix = part[: match.start()]
        suffix = part[match.end() :]
        for item in match.group(1).split(","):
            range_match = re.fullmatch(r"(\d+)-(\d+)", item)
            if range_match is None:
                replacements = (item,)
            else:
                first_text, last_text = range_match.groups()
                first = int(first_text)
                last = int(last_text)
                step = 1 if last >= first else -1
                width = max(len(first_text), len(last_text))
                replacements = tuple(
                    f"{number:0{width}d}" for number in range(first, last + step, step)
                )
            for replacement in replacements:
                expanded.extend(expand_hostlist(prefix + replacement + suffix))
    return tuple(expanded)


def parse_slurm_time(value: str, utc_offset: str) -> float | None:
    if value in ("", "N/A", "Unknown", "None"):
        return None
    try:
        return datetime.strptime(
            value + utc_offset,
            "%Y-%m-%dT%H:%M:%S%z",
        ).timestamp()
    except ValueError:
        return None


def parse_gpu_count(tres: str) -> int:
    generic_count: int | None = None
    typed_count = 0
    for raw_item in tres.split(","):
        item = raw_item.strip()
        equals_match = re.fullmatch(
            r"(?:gres/)?gpu(?::[A-Za-z0-9_.-]+)?=(\d+)",
            item,
        )
        if equals_match:
            count = int(equals_match.group(1))
            key = item.rsplit("=", 1)[0]
            if key in ("gpu", "gres/gpu"):
                generic_count = count
            else:
                typed_count += count
            continue
        colon_match = re.fullmatch(
            r"(?:gres/)?gpu(?::[A-Za-z0-9_.-]+)*:(\d+)",
            item,
        )
        if colon_match:
            typed_count += int(colon_match.group(1))
    return generic_count if generic_count is not None else typed_count


def parse_schedule(
    output: str,
) -> tuple[list[ReservationInterval], list[RunningInterval]]:
    timezone_line, reservation_marker, rest = output.partition("\n__RESERVATIONS__\n")
    if not reservation_marker or not timezone_line.startswith("__TIMEZONE__|"):
        raise RuntimeError("remote schedule metadata marker missing")
    utc_offset = timezone_line.removeprefix("__TIMEZONE__|").strip()
    if not re.fullmatch(r"[+-]\d{4}", utc_offset):
        raise RuntimeError("remote timezone missing")
    reservation_output, job_marker, job_output = rest.partition("__RUNNING_JOBS__\n")
    if not job_marker:
        raise RuntimeError("remote running-job marker missing")

    reservations: list[ReservationInterval] = []
    for raw_line in reservation_output.splitlines():
        values: dict[str, str] = {}
        for token in raw_line.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            values[key] = value
        start = parse_slurm_time(values.get("StartTime", ""), utc_offset)
        end = parse_slurm_time(values.get("EndTime", ""), utc_offset)
        node_list = values.get("Nodes", "")
        if start is None or end is None or not node_list or node_list == "(null)":
            continue
        reservations.append(
            ReservationInterval(
                name=values.get("ReservationName", "reservation"),
                start=start,
                end=end,
                nodes=expand_hostlist(node_list),
            )
        )

    running_jobs: list[RunningInterval] = []
    for raw_line in job_output.splitlines():
        fields = raw_line.split("|")
        if len(fields) != 7:
            continue
        (
            job_id,
            end_text,
            node_list,
            per_node_tres,
            allocated_tres,
            cpu_text,
            node_count_text,
        ) = fields
        end = parse_slurm_time(end_text, utc_offset)
        node_count = parse_int(node_count_text)
        nodes = expand_hostlist(node_list)
        if end is None or not nodes or node_count <= 0:
            continue
        gpu_per_node = parse_gpu_count(per_node_tres)
        if not gpu_per_node:
            gpu_per_node = math.ceil(parse_gpu_count(allocated_tres) / node_count)
        running_jobs.append(
            RunningInterval(
                job_id=job_id,
                end=end,
                nodes=nodes,
                gpu_per_node=gpu_per_node,
                cpu_per_node=math.ceil(parse_int(cpu_text) / node_count),
            )
        )
    return reservations, running_jobs


def ssh_error_message(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        timeout = f"{error.timeout:g}" if error.timeout is not None else "?"
        return f"request timed out after {timeout}s"
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        return error.stderr.strip().splitlines()[0]
    if isinstance(error, FileNotFoundError):
        return f"command not found: {error.filename}"
    return str(error)


def ssh_config_user(hostname: str) -> str:
    try:
        result = subprocess.run(
            ["ssh", "-G", hostname],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        for raw_line in result.stdout.splitlines():
            key, separator, value = raw_line.partition(" ")
            if separator and key.lower() == "user" and value.strip():
                return value.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return getpass.getuser()


def cluster_user(cluster_config: ClusterConfig, user_override: str | None) -> str:
    if cluster_config.mode == "local":
        return getpass.getuser()
    if user_override:
        return user_override
    if cluster_config.user:
        return cluster_config.user
    return ssh_config_user(cluster_config.addresses[0])


def run_ssh(
    hostname: str,
    ssh_user: str,
    remote_command: str,
    timeout: int,
    connect_timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ssh",
            "-l",
            ssh_user,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            f"ServerAliveInterval={connect_timeout}",
            "-o",
            "ServerAliveCountMax=1",
            "-o",
            "LogLevel=ERROR",
            hostname,
            remote_command,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_local(
    command: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def filesystem_command(
    paths: list[str],
    probe_timeout_seconds: int,
) -> str:
    if not paths:
        return "printf '__FILESYSTEMS__\\n'\n"
    command = r"""
printf '__FILESYSTEMS__\n'
emit_fs() {
    label=$1
    path=$2
    if command -v timeout >/dev/null 2>&1; then
        row=$(timeout PROBE_TIMEOUT df -P -k "$path" 2>/dev/null | tail -n 1)
    else
        row=$(df -P -k "$path" 2>/dev/null | tail -n 1)
    fi
    case $row in
        Filesystem*) row= ;;
    esac
    printf '%s|%s\n' "$label" "$row"
}
""".replace("PROBE_TIMEOUT", str(probe_timeout_seconds))
    for path in paths:
        shell_path = '"$HOME"' if path == "~" else shlex.quote(path)
        command += f"emit_fs {shlex.quote(path)} {shell_path}\n"
    return command


def data_command(
    cluster_config: ClusterConfig,
    settings: DashboardSettings,
    include_filesystems: bool,
    include_schedule: bool,
) -> str:
    parts: list[str] = []
    if cluster_config.slurm_bin_path:
        parts.append(
            f"export PATH={shlex.quote(cluster_config.slurm_bin_path)}:$PATH\n"
        )
    parts.append(
        "printf '__USER__'\nid -un\nprintf '__NODES__\\n'\nscontrol show nodes -o\n"
    )
    if include_filesystems:
        parts.append(
            filesystem_command(
                cluster_config.filesystems,
                settings.filesystem_probe_timeout_seconds,
            )
        )
    if include_schedule:
        parts.append(SCHEDULE_COMMAND)
    return "".join(parts)


def run_endpoint(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    command: str,
    settings: DashboardSettings,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    command_timeout = timeout or settings.command_timeout_seconds
    if cluster_config.mode == "local":
        return run_local(command, command_timeout)
    return run_ssh(
        endpoint,
        ssh_user,
        command,
        command_timeout,
        settings.ssh_connect_timeout_seconds,
    )


def fetch_from_endpoint(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    settings: DashboardSettings,
    include_filesystems: bool,
    include_schedule: bool,
) -> Cluster:
    command = data_command(
        cluster_config,
        settings,
        include_filesystems,
        include_schedule,
    )
    result = run_endpoint(
        cluster_config,
        endpoint,
        ssh_user,
        command,
        settings,
    )
    node_and_user_output = result.stdout
    filesystem_output = ""
    reservations: list[ReservationInterval] = []
    running_jobs: list[RunningInterval] = []
    if include_schedule:
        node_and_user_output, marker, schedule_output = result.stdout.partition(
            "__SCHEDULE__\n"
        )
        if not marker:
            raise RuntimeError("remote schedule marker missing")
        reservations, running_jobs = parse_schedule(schedule_output)
    if include_filesystems:
        node_and_user_output, marker, filesystem_output = (
            node_and_user_output.partition("__FILESYSTEMS__\n")
        )
        if not marker:
            raise RuntimeError("remote filesystem marker missing")
    user_output, node_marker, node_output = node_and_user_output.partition(
        "__NODES__\n"
    )
    if not node_marker or not user_output.startswith("__USER__"):
        raise RuntimeError("remote node metadata marker missing")
    current_user = user_output.removeprefix("__USER__").strip()
    if not current_user:
        raise RuntimeError("remote user missing")
    nodes = parse_nodes(
        node_output,
        current_user,
        cluster_config.exclude_partitions,
    )
    if not nodes:
        raise RuntimeError("no Slurm node data returned")
    return Cluster(
        name=cluster_config.name,
        host=endpoint,
        user=current_user,
        mode=cluster_config.mode,
        focus=cluster_config.focus,
        filesystem_paths=tuple(cluster_config.filesystems),
        nodes=nodes,
        filesystems=parse_filesystems(filesystem_output),
        reservations=reservations,
        running_jobs=running_jobs,
    )


def probe_endpoint(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    settings: DashboardSettings,
) -> LoginNode:
    try:
        run_endpoint(
            cluster_config,
            endpoint,
            ssh_user,
            "/bin/true",
            settings,
            timeout=settings.login_probe_timeout_seconds,
        )
        return LoginNode(hostname=endpoint, checked=True, reachable=True)
    except (OSError, subprocess.SubprocessError) as error:
        return LoginNode(
            hostname=endpoint,
            checked=True,
            error=ssh_error_message(error),
        )


def fetch_cluster(
    cluster_config: ClusterConfig,
    settings: DashboardSettings,
    user_override: str | None,
    include_filesystems: bool,
    check_login_nodes: bool,
    include_schedule: bool,
    preferred_host: str | None,
) -> Cluster:
    endpoints = (
        ["local"] if cluster_config.mode == "local" else list(cluster_config.addresses)
    )
    ssh_user = cluster_user(cluster_config, user_override)
    ordered_hosts = list(endpoints)
    if preferred_host in ordered_hosts:
        ordered_hosts.remove(preferred_host)
        ordered_hosts.insert(0, preferred_host)

    login_status = {hostname: LoginNode(hostname=hostname) for hostname in endpoints}
    cluster: Cluster | None = None
    last_error = "no login nodes configured"

    for hostname in ordered_hosts:
        for attempt in range(settings.retries_per_address + 1):
            try:
                cluster = fetch_from_endpoint(
                    cluster_config,
                    hostname,
                    ssh_user,
                    settings,
                    include_filesystems,
                    include_schedule,
                )
                login_status[hostname] = LoginNode(
                    hostname=hostname,
                    checked=True,
                    reachable=True,
                    used=True,
                )
                break
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                last_error = ssh_error_message(error)
                login_status[hostname] = LoginNode(
                    hostname=hostname,
                    checked=True,
                    error=last_error,
                )
                if attempt < settings.retries_per_address:
                    time.sleep(settings.retry_delay_seconds)
        if cluster is not None:
            break

    if check_login_nodes:
        for hostname in endpoints:
            if not login_status[hostname].checked:
                login_status[hostname] = probe_endpoint(
                    cluster_config,
                    hostname,
                    ssh_user,
                    settings,
                )

    statuses = [login_status[hostname] for hostname in endpoints]
    if cluster is None:
        return Cluster(
            name=cluster_config.name,
            host=preferred_host or endpoints[0],
            user=ssh_user,
            mode=cluster_config.mode,
            focus=cluster_config.focus,
            filesystem_paths=tuple(cluster_config.filesystems),
            login_nodes=statuses,
            error=last_error,
        )
    cluster.login_nodes = statuses
    return cluster


def fetch_all_clusters(
    config: AppConfig,
    user_override: str | None = None,
    include_filesystems: bool = True,
    check_login_nodes: bool = True,
    include_schedule: bool = False,
    preferred_hosts: dict[str, str] | None = None,
) -> list[Cluster]:
    preferred_hosts = preferred_hosts or {}
    cluster_configs = active_cluster_configs(config)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(cluster_configs))
    ) as pool:
        futures = [
            pool.submit(
                fetch_cluster,
                cluster_config,
                config.settings,
                user_override,
                include_filesystems,
                check_login_nodes,
                include_schedule,
                preferred_hosts.get(cluster_config.name),
            )
            for cluster_config in cluster_configs
        ]
        return [future.result() for future in futures]


def submit_cluster_refreshes(
    executor: concurrent.futures.ThreadPoolExecutor,
    config: AppConfig,
    user_override: str | None,
    include_filesystems: bool,
    check_login_nodes: bool,
    include_schedule: bool,
    preferred_hosts: dict[str, str] | None = None,
) -> dict[concurrent.futures.Future[Cluster], str]:
    """Submit independent live requests so one slow cluster cannot block peers."""
    preferred_hosts = preferred_hosts or {}
    config_snapshot = copy.deepcopy(config)
    return {
        executor.submit(
            fetch_cluster,
            cluster_config,
            config_snapshot.settings,
            user_override,
            include_filesystems,
            check_login_nodes,
            include_schedule,
            preferred_hosts.get(cluster_config.name),
        ): cluster_config.name
        for cluster_config in active_cluster_configs(config_snapshot)
    }


def merge_cluster_refresh(
    previous: Cluster,
    refreshed: Cluster,
    include_filesystems: bool,
    check_login_nodes: bool,
    include_schedule: bool,
) -> Cluster:
    """Keep data that was intentionally omitted from a partial refresh."""
    refreshed.loading = False
    if not include_filesystems:
        refreshed.filesystems = previous.filesystems
    if not include_schedule:
        refreshed.reservations = previous.reservations
        refreshed.running_jobs = previous.running_jobs
    if not check_login_nodes:
        previous_logins = {login.hostname: login for login in previous.login_nodes}
        merged_logins: list[LoginNode] = []
        for login in refreshed.login_nodes:
            old_login = previous_logins.get(login.hostname)
            if login.checked or old_login is None:
                merged_logins.append(login)
            else:
                merged_logins.append(
                    LoginNode(
                        hostname=login.hostname,
                        checked=old_login.checked,
                        reachable=old_login.reachable,
                        used=login.hostname == refreshed.host,
                        error=old_login.error,
                    )
                )
        refreshed.login_nodes = merged_logins
    return refreshed
