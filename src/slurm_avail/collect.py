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
from datetime import datetime, timedelta

from .config import (
    AppConfig,
    ClusterConfig,
    DashboardSettings,
    active_cluster_configs,
)
from .estimator import validate_estimate_request
from .models import (
    Cluster,
    EstimateRequest,
    EstimateResult,
    FairshareAssociation,
    Filesystem,
    LoginNode,
    Node,
    PendingJobPriority,
    PriorityFactors,
    ReservationInterval,
    RunningInterval,
    SchedulerData,
    UserJob,
)
from .processes import run_captured, wait_or_cancel
from .ssh_auth import multiplex_options

UNAVAILABLE_RE = re.compile(
    r"DOWN|DRAIN|FAIL|MAINT|RESERVED|UNKNOWN|REBOOT|COMPLETING|"
    r"INVAL|NO_RESPOND|POWER",
    re.IGNORECASE,
)
RESERVED_RE = re.compile(r"RESERV", re.IGNORECASE)
DRAINED_RE = re.compile(r"DRAIN", re.IGNORECASE)
SQUEUE_FORMAT = (
    "JobID:0|,UserName:0|,EndTime:0|,NodeList:0|,tres-per-node:0|,"
    "tres-alloc:0|,NumCPUs:0|,NumNodes:0"
)
JOBS_FORMAT = (
    "JobID:0|,Partition:0|,Name:0|,State:0|,Reason:0|,Priority:0|,"
    "SubmitTime:0|,StartTime:0|,EndTime:0|,NodeList:0|,SchedNodes:0|,"
    "TimeLimit:0|,NumNodes:0|,NumCPUs:0|,MinMemory:0|,tres-per-node:0|,"
    "tres-alloc:0|,Account:0|,QOS:0"
)
JOBS_FALLBACK_FORMAT = "%i|%P|%j|%T|%r|%Q|%V|%S|%e|%N|%n|%l|%D|%C|%m|%b||%a|%q"
HISTORY_FORMAT = (
    "JobIDRaw,JobName,Partition,State,Submit,Start,End,Elapsed,Timelimit,"
    "NNodes,NCPUS,ReqMem,ReqTRES,AllocTRES,Account,QOS,NodeList,ExitCode"
)
JOB_DATA_COMMAND = rf"""
printf '__USER_JOBS__\n'
dashboard_user=$(id -un)
dashboard_jobs_error=
if ! dashboard_jobs=$(
    squeue -u "$dashboard_user" -t PD,R -h -O '{JOBS_FORMAT}' 2>&1
); then
    dashboard_jobs_error=$(printf '%s\n' "$dashboard_jobs" | sed -n '1p')
    dashboard_jobs=
elif [ -n "$dashboard_jobs" ] && ! printf '%s\n' "$dashboard_jobs" | grep -q '|'; then
    if ! dashboard_jobs=$(
        squeue -u "$dashboard_user" -t PD,R -h -o '{JOBS_FALLBACK_FORMAT}' 2>&1
    ); then
        dashboard_jobs_error=$(printf '%s\n' "$dashboard_jobs" | sed -n '1p')
        dashboard_jobs=
    fi
fi
if [ -z "$dashboard_jobs_error" ]; then
    printf '%s\n' "$dashboard_jobs"
else
    printf '__ERROR__|%s\n' "$dashboard_jobs_error"
fi
printf '__PENDING_PRIORITIES__\n'
if pending_priority_rows=$(squeue -a -t PD -h -o '%i|%Q|%P' 2>&1); then
    printf '%s\n' "$pending_priority_rows"
else
    printf '__ERROR__|%s\n' "$(printf '%s\n' "$pending_priority_rows" | sed -n '1p')"
fi
printf '__PRIORITIES__\n'
if [ -n "$dashboard_jobs" ]; then
    if priority_rows=$(
        sprio -u "$dashboard_user" -h \
            -o '%i|%Y|%S|%A|%B|%F|%J|%P|%Q|%N|%T' 2>&1
    ); then
        printf '%s\n' "$priority_rows"
    else
        printf '__ERROR__|%s\n' "$(printf '%s\n' "$priority_rows" | sed -n '1p')"
    fi
fi
printf '__FAIRSHARE__\n'
if [ -n "$dashboard_jobs" ]; then
    fairshare_ok=0
    fairshare_error=
    dashboard_accounts=$(
        printf '%s\n' "$dashboard_jobs" |
            awk -F'|' 'NF >= 18 && $18 != "" {{ print $18 }}' |
            sort -u
    )
    fairshare_fields='Account,User,RawShares,NormShares,RawUsage,'\
'NormUsage,EffectvUsage,FairShare,LevelFS'
    for dashboard_account in $dashboard_accounts; do
        [ -n "$dashboard_account" ] || continue
        if fairshare_rows=$(
            sshare -A "$dashboard_account" -u "$dashboard_user" \
                -l -h -P -o "$fairshare_fields" 2>&1
        ); then
            printf '%s\n' "$fairshare_rows"
            fairshare_ok=1
        else
            fairshare_error=$(printf '%s\n' "$fairshare_rows" | sed -n '1p')
        fi
    done
    if [ "$fairshare_ok" -eq 0 ] && [ -n "$fairshare_error" ]; then
        printf '__ERROR__|%s\n' "$fairshare_error"
    fi
fi
printf '__PRIORITY_CONFIG__\n'
if priority_config=$(scontrol show config 2>&1); then
    priority_pattern='^[[:space:]]*(PriorityType|PriorityWeightAge|'
    priority_pattern="${{priority_pattern}}PriorityWeightAssoc|PriorityWeightFairshare|"
    priority_pattern="${{priority_pattern}}PriorityWeightJobSize|PriorityWeightPartition|"
    priority_pattern="${{priority_pattern}}PriorityWeightQOS|PriorityWeightTRES|"
    priority_pattern="${{priority_pattern}}PriorityDecayHalfLife|PriorityCalcPeriod|"
    priority_pattern="${{priority_pattern}}PriorityFlags|PriorityUsageResetPeriod)"
    priority_pattern="${{priority_pattern}}[[:space:]]*="
    printf '%s\n' "$priority_config" | grep -E "$priority_pattern" || true
else
    printf '__ERROR__|%s\n' "$(printf '%s\n' "$priority_config" | sed -n '1p')"
fi
"""
SCHEDULE_COMMAND = rf"""
printf '__SCHEDULE__\n'
printf '__TIMEZONE__|'
date +%z
printf '__RESERVATIONS__\n'
scontrol show reservations -o
printf '__RUNNING_JOBS__\n'
squeue -a -t R -h -O '{SQUEUE_FORMAT}'
{JOB_DATA_COMMAND}
"""
JOBS_COMMAND = f"""
printf '__JOBS__\n'
printf '__TIMEZONE__|'
date +%z
{JOB_DATA_COMMAND}
"""


class SlurmUnavailableError(RuntimeError):
    """The endpoint answered, but its Slurm client returned no node data."""


class DataCollectionError(RuntimeError):
    """The endpoint answered with an invalid or incomplete dashboard response."""


def history_command(
    history_days: int,
    reference_time: datetime | None = None,
) -> str:
    reference_time = reference_time or datetime.now().astimezone()
    start_date = (reference_time - timedelta(days=history_days)).strftime("%Y-%m-%d")
    return rf"""
printf '__HISTORY__\n'
printf '__TIMEZONE__|'
date +%z
dashboard_user=$(id -un)
if history_rows=$(
    sacct -u "$dashboard_user" -X -n -P -S {start_date} \
        -o '{HISTORY_FORMAT}' 2>&1
); then
    printf '%s\n' "$history_rows"
else
    printf '__ERROR__|%s\n' "$(printf '%s\n' "$history_rows" | sed -n '1p')"
fi
    """


def estimate_command(
    cluster_config: ClusterConfig,
    request: EstimateRequest,
) -> str:
    """Build a non-submitting Slurm scheduler test command."""
    validate_estimate_request(request)
    arguments = [
        "srun",
        "--test-only",
        f"--nodes={request.nodes}",
        f"--ntasks-per-node={request.tasks_per_node}",
        f"--cpus-per-task={request.cpus_per_task}",
        f"--mem={request.memory_per_node}",
        f"--time={request.time_limit}",
    ]
    if request.gpus_per_node:
        gpu_request = "gpu"
        if request.gpu_type:
            gpu_request += f":{request.gpu_type}"
        gpu_request += f":{request.gpus_per_node}"
        arguments.append(f"--gres={gpu_request}")
    if request.partition:
        arguments.append(f"--partition={request.partition}")
    if request.account:
        arguments.append(f"--account={request.account}")
    if request.qos:
        arguments.append(f"--qos={request.qos}")
    if request.constraint:
        arguments.append(f"--constraint={request.constraint}")
    if request.exclusive:
        arguments.append("--exclusive")

    command_lines: list[str] = []
    if cluster_config.slurm_bin_path:
        command_lines.append(
            f"export PATH={shlex.quote(cluster_config.slurm_bin_path)}:$PATH"
        )
    command_lines.extend(
        [
            (
                "unset SLURM_JOB_ID SLURM_JOBID SLURM_STEP_ID SLURM_STEPID "
                "SLURM_ACCOUNT SLURM_PARTITION SLURM_QOS SLURM_CONSTRAINT "
                "SLURM_NTASKS SLURM_NTASKS_PER_NODE SLURM_CPUS_PER_TASK "
                "SLURM_MEM_PER_NODE SLURM_MEM_PER_CPU SLURM_MEM_PER_GPU "
                "SLURM_GPUS SLURM_GPUS_PER_NODE SLURM_GRES SLURM_TIMELIMIT"
            ),
            "printf '__ESTIMATE__\\n'",
            "printf '__TIMEZONE__|'",
            "date +%z",
            f"slurm_avail_estimate_output=$(LC_ALL=C {shlex.join(arguments)} 2>&1)",
            "slurm_avail_estimate_status=$?",
            "printf '__EXIT__|%s\\n' \"$slurm_avail_estimate_status\"",
            "printf '%s\\n' \"$slurm_avail_estimate_output\"",
        ]
    )
    return "\n".join(command_lines) + "\n"


def parse_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def parse_float(value: str | None) -> float | None:
    if value is None or value.strip() in ("", "N/A", "None", "(null)"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


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
        gpu_type = ""
        if "gpu:" in gres:
            gpu_total = tres_value(cfg_tres, "gres/gpu")
            gpu_alloc = min(gpu_total, tres_value(alloc_tres, "gres/gpu"))
            gpu_match = re.search(r"(?:^|,)gpu:([^:,(]+):\d+", gres)
            if gpu_match:
                gpu_type = gpu_match.group(1)

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
                gpu_type=gpu_type,
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
    if value in ("", "N/A", "Unknown", "None", "(null)"):
        return None
    try:
        return datetime.strptime(
            value + utc_offset,
            "%Y-%m-%dT%H:%M:%S%z",
        ).timestamp()
    except ValueError:
        return None


def parse_estimate_response(
    output: str,
    cluster_name: str,
    endpoint: str,
    checked_at: float,
) -> EstimateResult:
    timezone_line, exit_marker, rest = output.partition("\n__EXIT__|")
    if not exit_marker or not timezone_line.startswith("__TIMEZONE__|"):
        raise RuntimeError("remote estimate metadata marker missing")
    utc_offset = timezone_line.removeprefix("__TIMEZONE__|").strip()
    if not re.fullmatch(r"[+-]\d{4}", utc_offset):
        raise RuntimeError("remote estimate timezone missing")
    exit_text, separator, scheduler_output = rest.partition("\n")
    if not separator:
        scheduler_output = ""
    try:
        exit_code = int(exit_text.strip())
    except ValueError as error:
        raise RuntimeError("remote estimate exit status missing") from error

    clean_lines = []
    for raw_line in scheduler_output.splitlines():
        cleaned = re.sub(r"^srun:\s*(?:error:\s*)?", "", raw_line.strip())
        if cleaned:
            clean_lines.append(cleaned)
    message = "; ".join(clean_lines)[:500]
    if exit_code != 0:
        command_error = exit_code in (126, 127) or re.search(
            r"command not found|unrecognized option|unknown option|invalid option",
            message,
            re.I,
        )
        return EstimateResult(
            cluster_name=cluster_name,
            status="error" if command_error else "rejected",
            endpoint=endpoint,
            message=message or f"srun exited with status {exit_code}",
            checked_at=checked_at,
        )

    start_match = re.search(r"\bto start at\s+(\S+)", scheduler_output, re.I)
    if start_match is None:
        return EstimateResult(
            cluster_name=cluster_name,
            status="unknown",
            endpoint=endpoint,
            message=message or "Slurm returned no start estimate",
            checked_at=checked_at,
        )
    start = parse_slurm_time(start_match.group(1), utc_offset)
    if start is None:
        return EstimateResult(
            cluster_name=cluster_name,
            status="unknown",
            endpoint=endpoint,
            message=message or "Slurm returned an unknown start time",
            checked_at=checked_at,
        )

    job_match = re.search(r"\bJob\s+(\S+)", scheduler_output, re.I)
    processor_match = re.search(
        r"\busing\s+(\d+)\s+processors?",
        scheduler_output,
        re.I,
    )
    node_match = re.search(r"\bon\s+nodes?\s+(\S+)", scheduler_output, re.I)
    partition_match = re.search(
        r"\bin\s+partition\s+(\S+)",
        scheduler_output,
        re.I,
    )
    return EstimateResult(
        cluster_name=cluster_name,
        status="estimated",
        endpoint=endpoint,
        start=start,
        job_id=job_match.group(1) if job_match else "",
        processors=int(processor_match.group(1)) if processor_match else 0,
        nodes=node_match.group(1) if node_match else "",
        partition=partition_match.group(1) if partition_match else "",
        checked_at=checked_at,
    )


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


def parse_memory_mb(tres: str) -> int:
    """Return Slurm's allocated-memory TRES in MiB."""
    factors = {
        "": 1,
        "K": 1 / 1024,
        "M": 1,
        "G": 1024,
        "T": 1024**2,
        "P": 1024**3,
    }
    for raw_item in tres.split(","):
        match = re.fullmatch(r"\s*mem=(\d+(?:\.\d+)?)([KMGTP]?)\s*", raw_item)
        if match:
            amount, unit = match.groups()
            return math.ceil(float(amount) * factors[unit])
    return 0


def reservation_includes_user(users: str, current_user: str) -> bool:
    """Identify explicit positive membership without treating ALL as personal."""
    if not current_user:
        return False
    return any(
        item.lstrip("+") == current_user and not item.startswith("-")
        for item in users.split(",")
    )


def parse_schedule(
    output: str,
    current_user: str,
) -> SchedulerData:
    timezone_line, reservation_marker, rest = output.partition("\n__RESERVATIONS__\n")
    if not reservation_marker or not timezone_line.startswith("__TIMEZONE__|"):
        raise RuntimeError("remote schedule metadata marker missing")
    utc_offset = timezone_line.removeprefix("__TIMEZONE__|").strip()
    if not re.fullmatch(r"[+-]\d{4}", utc_offset):
        raise RuntimeError("remote timezone missing")
    reservation_output, running_marker, rest = rest.partition("__RUNNING_JOBS__\n")
    if not running_marker:
        raise RuntimeError("remote running-job marker missing")
    running_output, jobs_marker, rest = rest.partition("__USER_JOBS__\n")
    if not jobs_marker:
        raise RuntimeError("remote user-job marker missing")
    user_jobs_output, pending_marker, rest = rest.partition("__PENDING_PRIORITIES__\n")
    if not pending_marker:
        raise RuntimeError("remote pending-priority marker missing")
    pending_priority_output, priority_marker, rest = rest.partition("__PRIORITIES__\n")
    if not priority_marker:
        raise RuntimeError("remote priority marker missing")
    priority_output, fairshare_marker, rest = rest.partition("__FAIRSHARE__\n")
    if not fairshare_marker:
        raise RuntimeError("remote fair-share marker missing")
    fairshare_output, config_marker, config_output = rest.partition(
        "__PRIORITY_CONFIG__\n"
    )
    if not config_marker:
        raise RuntimeError("remote priority-config marker missing")

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
                mine=reservation_includes_user(values.get("Users", ""), current_user),
            )
        )

    running_jobs: list[RunningInterval] = []
    for raw_line in running_output.splitlines():
        fields = raw_line.split("|")
        if len(fields) != 8:
            continue
        (
            job_id,
            job_user,
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
                mem_per_node=math.ceil(parse_memory_mb(allocated_tres) / node_count),
                mine=job_user == current_user,
            )
        )

    jobs: list[UserJob] = []
    jobs_error: str | None = None
    for raw_line in user_jobs_output.splitlines():
        if raw_line.startswith("__ERROR__|"):
            jobs_error = raw_line.partition("|")[2] or "squeue unavailable"
            continue
        fields = raw_line.split("|")
        if len(fields) != 19:
            if raw_line.strip() and jobs_error is None:
                jobs_error = (
                    "unrecognized squeue field layout "
                    f"({len(fields)} fields, expected 19)"
                )
            continue
        (
            job_id,
            partition,
            name,
            state,
            reason,
            priority,
            submit_text,
            start_text,
            end_text,
            node_list,
            scheduled_nodes,
            time_limit,
            node_count,
            cpu_count,
            memory,
            tres_per_node,
            allocated_tres,
            account,
            qos,
        ) = fields
        if not job_id:
            continue
        is_running = state.upper() in ("R", "RUNNING")
        node_text = node_list if is_running else scheduled_nodes
        jobs.append(
            UserJob(
                job_id=job_id,
                partition=partition,
                name=name,
                state=state,
                reason=reason,
                priority=priority,
                start=parse_slurm_time(start_text, utc_offset),
                end=parse_slurm_time(end_text, utc_offset),
                scheduled_nodes=(
                    ()
                    if node_text in ("", "N/A", "None", "(null)")
                    or (node_text.startswith("(") and node_text.endswith(")"))
                    else expand_hostlist(node_text)
                ),
                time_limit=time_limit,
                node_count=parse_int(node_count),
                cpu_count=parse_int(cpu_count),
                memory=memory,
                tres_per_node=tres_per_node,
                allocated_tres=allocated_tres,
                account=account.strip(),
                qos=qos,
                submit=parse_slurm_time(submit_text, utc_offset),
            )
        )

    priority_error: str | None = None
    priority_by_job: dict[str, PriorityFactors] = {}
    for raw_line in priority_output.splitlines():
        if raw_line.startswith("__ERROR__|"):
            priority_error = raw_line.partition("|")[2] or "sprio unavailable"
            continue
        fields = raw_line.split("|", 10)
        if len(fields) != 11:
            continue
        job_id = fields[0].strip()
        if not job_id:
            continue
        priority_by_job[job_id] = PriorityFactors(
            total=parse_int(fields[1]),
            site=parse_int(fields[2]),
            age=parse_int(fields[3]),
            association=parse_int(fields[4]),
            fairshare=parse_int(fields[5]),
            job_size=parse_int(fields[6]),
            partition=parse_int(fields[7]),
            qos=parse_int(fields[8]),
            nice=parse_int(fields[9]),
            tres=fields[10].strip(),
        )
    jobs.sort(key=lambda job: (not job.is_running, job.job_id))
    for job in jobs:
        job.priority_factors = priority_by_job.get(job.job_id)

    pending_priorities: list[PendingJobPriority] = []
    pending_priorities_error: str | None = None
    for raw_line in pending_priority_output.splitlines():
        if raw_line.startswith("__ERROR__|"):
            pending_priorities_error = (
                raw_line.partition("|")[2] or "pending priorities unavailable"
            )
            continue
        fields = raw_line.split("|", 2)
        if len(fields) != 3:
            continue
        job_id, priority_text, partition = (field.strip() for field in fields)
        if not job_id or not re.fullmatch(r"-?\d+", priority_text):
            continue
        pending_priorities.append(
            PendingJobPriority(
                job_id=job_id,
                priority=int(priority_text),
                partition=partition.rstrip("*"),
            )
        )

    job_accounts = {job.account for job in jobs if job.account}
    fairshare: list[FairshareAssociation] = []
    fairshare_error: str | None = None
    seen_associations: set[tuple[str, str]] = set()
    for raw_line in fairshare_output.splitlines():
        if raw_line.startswith("__ERROR__|"):
            fairshare_error = raw_line.partition("|")[2] or "sshare unavailable"
            continue
        fields = raw_line.split("|")
        if len(fields) < 9:
            continue
        account = fields[0].strip()
        user = fields[1].strip()
        if account not in job_accounts or user not in ("", current_user):
            continue
        association_key = (account, user)
        if association_key in seen_associations:
            continue
        seen_associations.add(association_key)
        fairshare.append(
            FairshareAssociation(
                account=account,
                user=user,
                raw_shares=parse_float(fields[2]),
                normalized_shares=parse_float(fields[3]),
                raw_usage=parse_float(fields[4]),
                normalized_usage=parse_float(fields[5]),
                effective_usage=parse_float(fields[6]),
                fairshare=parse_float(fields[7]),
                level_fairshare=parse_float(fields[8]),
            )
        )

    priority_config: dict[str, str] = {}
    for raw_line in config_output.splitlines():
        if raw_line.startswith("__ERROR__|"):
            if priority_error is None:
                priority_error = raw_line.partition("|")[2] or "config unavailable"
            continue
        key, separator, value = raw_line.partition("=")
        if separator:
            priority_config[key.strip()] = value.strip()

    return SchedulerData(
        reservations=reservations,
        running_jobs=running_jobs,
        jobs=jobs,
        pending_priorities=pending_priorities,
        fairshare=fairshare,
        priority_config=priority_config,
        jobs_error=jobs_error,
        priority_error=priority_error,
        pending_priorities_error=pending_priorities_error,
        fairshare_error=fairshare_error,
    )


def parse_jobs(output: str, current_user: str) -> SchedulerData:
    """Parse a jobs-only response using the shared scheduler-data parser."""
    timezone_line, jobs_marker, rest = output.partition("\n__USER_JOBS__\n")
    if not jobs_marker or not timezone_line.startswith("__TIMEZONE__|"):
        raise RuntimeError("remote jobs metadata marker missing")
    synthetic_schedule = (
        f"{timezone_line}\n__RESERVATIONS__\n__RUNNING_JOBS__\n__USER_JOBS__\n{rest}"
    )
    return parse_schedule(synthetic_schedule, current_user)


def parse_history(output: str) -> tuple[list[UserJob], str | None]:
    timezone_line, separator, history_output = output.partition("\n")
    if not separator or not timezone_line.startswith("__TIMEZONE__|"):
        raise RuntimeError("remote history metadata marker missing")
    utc_offset = timezone_line.removeprefix("__TIMEZONE__|").strip()
    if not re.fullmatch(r"[+-]\d{4}", utc_offset):
        raise RuntimeError("remote history timezone missing")

    jobs: list[UserJob] = []
    history_error: str | None = None
    for raw_line in history_output.splitlines():
        if raw_line.startswith("__ERROR__|"):
            history_error = raw_line.partition("|")[2] or "sacct unavailable"
            continue
        fields = raw_line.split("|")
        if len(fields) != 18:
            continue
        (
            job_id,
            name,
            partition,
            state,
            submit_text,
            start_text,
            end_text,
            elapsed,
            time_limit,
            node_count,
            cpu_count,
            memory,
            requested_tres,
            allocated_tres,
            account,
            qos,
            node_list,
            exit_code,
        ) = fields
        state_name = state.split(maxsplit=1)[0].rstrip("+").upper()
        if not job_id or state_name in ("PENDING", "PD", "RUNNING", "R"):
            continue
        nodes = (
            ()
            if node_list in ("", "None", "None assigned", "Unknown", "(null)")
            else expand_hostlist(node_list)
        )
        jobs.append(
            UserJob(
                job_id=job_id,
                partition=partition,
                name=name,
                state=state,
                reason="",
                priority="",
                start=parse_slurm_time(start_text, utc_offset),
                end=parse_slurm_time(end_text, utc_offset),
                scheduled_nodes=nodes,
                time_limit=time_limit,
                node_count=parse_int(node_count),
                cpu_count=parse_int(cpu_count),
                memory=memory,
                tres_per_node=requested_tres,
                allocated_tres=allocated_tres,
                account=account.strip(),
                qos=qos,
                submit=parse_slurm_time(submit_text, utc_offset),
                elapsed=elapsed,
                exit_code=exit_code,
                historical=True,
            )
        )
    return jobs, history_error


def ssh_authentication_required(error: BaseException) -> bool:
    if not isinstance(error, subprocess.CalledProcessError):
        return False
    stderr = error.stderr or ""
    return bool(
        re.search(
            r"permission denied|authentication failed|"
            r"no supported authentication methods|too many authentication failures",
            stderr,
            re.IGNORECASE,
        )
    )


def ssh_error_message(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        timeout = f"{error.timeout:g}" if error.timeout is not None else "?"
        return f"request timed out after {timeout}s"
    if ssh_authentication_required(error):
        return "authentication required"
    if isinstance(error, subprocess.CalledProcessError):
        if error.stderr:
            return error.stderr.strip().splitlines()[0]
        return "request failed"
    if isinstance(error, FileNotFoundError):
        return f"command not found: {error.filename}"
    if isinstance(error, subprocess.SubprocessError):
        return "request failed"
    return str(error)


def endpoint_failure(
    error: BaseException,
    mode: str,
) -> tuple[str, bool, str]:
    """Classify a failed request and report whether the endpoint answered."""
    message = ssh_error_message(error)
    if isinstance(error, SlurmUnavailableError):
        return "scheduler", True, message
    if isinstance(error, DataCollectionError):
        return "data", True, message
    if ssh_authentication_required(error):
        return "auth", False, message
    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout", False, message
    if isinstance(error, subprocess.CalledProcessError):
        stderr = error.stderr or ""
        if re.search(
            r"(?:^|\n)(?:bash: )?(?:scontrol|squeue|sacct|sprio|sshare|srun):|"
            r"Slurm configuration|configuration source|slurmctld",
            stderr,
            re.IGNORECASE,
        ):
            return "scheduler", True, message
        if mode == "ssh" and error.returncode == 255:
            return "unreachable", False, message
        return ("local" if mode == "local" else "command"), True, message
    if isinstance(error, FileNotFoundError):
        return ("local" if mode == "local" else "command"), False, message
    if isinstance(error, OSError):
        return ("local" if mode == "local" else "command"), False, message
    return ("local" if mode == "local" else "data"), True, message


def preferred_failure(
    failures: list[tuple[str, str]],
) -> tuple[str, str]:
    priorities = {
        "scheduler": 70,
        "data": 60,
        "command": 50,
        "local": 50,
        "auth": 40,
        "timeout": 30,
        "unreachable": 20,
    }
    return max(failures, key=lambda item: priorities.get(item[0], 0))


def ssh_config_user(hostname: str) -> str:
    try:
        result = run_captured(
            ["ssh", "-G", hostname],
            check=True,
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
    cluster_config: ClusterConfig,
    hostname: str,
    ssh_user: str,
    remote_command: str,
    timeout: int,
    connect_timeout: int,
) -> subprocess.CompletedProcess[str]:
    if cluster_config.remote_shell == "login":
        remote_command = f"/bin/bash -lc {shlex.quote(remote_command)}"
    return run_captured(
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
            *multiplex_options(cluster_config),
            hostname,
            remote_command,
        ],
        check=True,
        timeout=timeout,
    )


def run_local(
    command: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return run_captured(
        ["/bin/sh", "-lc", command],
        check=True,
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
    include_jobs: bool,
    include_history: bool,
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
    elif include_jobs:
        parts.append(JOBS_COMMAND)
    if include_history:
        parts.append(history_command(settings.jobs_history_days))
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
        cluster_config,
        endpoint,
        ssh_user,
        command,
        command_timeout,
        settings.ssh_connect_timeout_seconds,
    )


def fetch_estimate_from_endpoint(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    settings: DashboardSettings,
    request: EstimateRequest,
) -> EstimateResult:
    checked_at = time.time()
    result = run_endpoint(
        cluster_config,
        endpoint,
        ssh_user,
        estimate_command(cluster_config, request),
        settings,
    )
    marker = "__ESTIMATE__\n"
    if not result.stdout.startswith(marker):
        raise RuntimeError("remote estimate marker missing")
    return parse_estimate_response(
        result.stdout.removeprefix(marker),
        cluster_config.name,
        endpoint,
        checked_at,
    )


def fetch_cluster_estimate(
    cluster_config: ClusterConfig,
    settings: DashboardSettings,
    user_override: str | None,
    request: EstimateRequest,
    preferred_host: str | None = None,
) -> EstimateResult:
    """Run one test-only request with normal endpoint failover."""
    endpoints = (
        ["local"] if cluster_config.mode == "local" else list(cluster_config.addresses)
    )
    ssh_user = cluster_user(cluster_config, user_override)
    ordered_hosts = list(endpoints)
    if preferred_host in ordered_hosts:
        ordered_hosts.remove(preferred_host)
        ordered_hosts.insert(0, preferred_host)

    last_error = "no login nodes configured"
    for endpoint in ordered_hosts:
        for attempt in range(settings.retries_per_address + 1):
            try:
                return fetch_estimate_from_endpoint(
                    cluster_config,
                    endpoint,
                    ssh_user,
                    settings,
                    request,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                last_error = ssh_error_message(error)
                if attempt < settings.retries_per_address and wait_or_cancel(
                    settings.retry_delay_seconds
                ):
                    break
    return EstimateResult(
        cluster_name=cluster_config.name,
        status="error",
        endpoint=preferred_host or (endpoints[0] if endpoints else ""),
        message=last_error,
        checked_at=time.time(),
    )


def fetch_from_endpoint(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    settings: DashboardSettings,
    include_filesystems: bool,
    include_schedule: bool,
    include_jobs: bool,
    include_history: bool,
) -> Cluster:
    command = data_command(
        cluster_config,
        settings,
        include_filesystems,
        include_schedule,
        include_jobs,
        include_history,
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
    schedule_output = ""
    jobs_output = ""
    history_output = ""
    if include_history:
        node_and_user_output, marker, history_output = node_and_user_output.partition(
            "__HISTORY__\n"
        )
        if not marker:
            raise DataCollectionError("remote history marker missing")
    if include_schedule:
        node_and_user_output, marker, schedule_output = node_and_user_output.partition(
            "__SCHEDULE__\n"
        )
        if not marker:
            raise DataCollectionError("remote schedule marker missing")
    elif include_jobs:
        node_and_user_output, marker, jobs_output = node_and_user_output.partition(
            "__JOBS__\n"
        )
        if not marker:
            raise DataCollectionError("remote jobs marker missing")
    if include_filesystems:
        node_and_user_output, marker, filesystem_output = (
            node_and_user_output.partition("__FILESYSTEMS__\n")
        )
        if not marker:
            raise DataCollectionError("remote filesystem marker missing")
    user_output, node_marker, node_output = node_and_user_output.partition(
        "__NODES__\n"
    )
    if not node_marker or not user_output.startswith("__USER__"):
        raise DataCollectionError("remote node metadata marker missing")
    current_user = user_output.removeprefix("__USER__").strip()
    if not current_user:
        raise DataCollectionError("remote user missing")
    scheduler_data = SchedulerData()
    if include_schedule:
        scheduler_data = parse_schedule(schedule_output, current_user)
    elif include_jobs:
        scheduler_data = parse_jobs(jobs_output, current_user)
    if include_history:
        scheduler_data.past_jobs, scheduler_data.history_error = parse_history(
            history_output
        )
    nodes = parse_nodes(
        node_output,
        current_user,
        cluster_config.exclude_partitions,
    )
    if not nodes:
        if "NodeName=" in node_output:
            raise DataCollectionError("no Slurm nodes remain after filtering")
        detail = next(
            (
                line.strip()
                for line in (result.stderr or "").splitlines()
                if line.strip()
            ),
            "Slurm returned no node data",
        )
        raise SlurmUnavailableError(detail)
    return Cluster(
        name=cluster_config.name,
        host=endpoint,
        user=current_user,
        mode=cluster_config.mode,
        focus=cluster_config.focus,
        filesystem_paths=tuple(cluster_config.filesystems),
        nodes=nodes,
        filesystems=parse_filesystems(filesystem_output),
        reservations=scheduler_data.reservations,
        running_jobs=scheduler_data.running_jobs,
        jobs=scheduler_data.jobs,
        past_jobs=scheduler_data.past_jobs,
        pending_priorities=scheduler_data.pending_priorities,
        fairshare=scheduler_data.fairshare,
        priority_config=scheduler_data.priority_config,
        jobs_error=scheduler_data.jobs_error,
        history_error=scheduler_data.history_error,
        priority_error=scheduler_data.priority_error,
        pending_priorities_error=scheduler_data.pending_priorities_error,
        fairshare_error=scheduler_data.fairshare_error,
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
        failure_kind, reachable, message = endpoint_failure(
            error,
            cluster_config.mode,
        )
        return LoginNode(
            hostname=endpoint,
            checked=True,
            reachable=reachable,
            auth_required=failure_kind == "auth",
            failure_kind=failure_kind,
            error=message,
        )


def fetch_cluster(
    cluster_config: ClusterConfig,
    settings: DashboardSettings,
    user_override: str | None,
    include_filesystems: bool,
    check_login_nodes: bool,
    include_schedule: bool,
    include_jobs: bool,
    include_history: bool,
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
    failures: list[tuple[str, str]] = []

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
                    include_jobs,
                    include_history,
                )
                login_status[hostname] = LoginNode(
                    hostname=hostname,
                    checked=True,
                    reachable=True,
                    used=True,
                )
                break
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                failure_kind, reachable, message = endpoint_failure(
                    error,
                    cluster_config.mode,
                )
                failures.append((failure_kind, message))
                login_status[hostname] = LoginNode(
                    hostname=hostname,
                    checked=True,
                    reachable=reachable,
                    auth_required=failure_kind == "auth",
                    failure_kind=failure_kind,
                    error=message,
                )
                if attempt < settings.retries_per_address and wait_or_cancel(
                    settings.retry_delay_seconds
                ):
                    break
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
        failure_kind, last_error = (
            preferred_failure(failures)
            if failures
            else ("data", "no login nodes configured")
        )
        return Cluster(
            name=cluster_config.name,
            host=preferred_host or endpoints[0],
            user=ssh_user,
            mode=cluster_config.mode,
            focus=cluster_config.focus,
            filesystem_paths=tuple(cluster_config.filesystems),
            login_nodes=statuses,
            auth_required=failure_kind == "auth",
            failure_kind=failure_kind,
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
    include_jobs: bool = False,
    include_history: bool = False,
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
                include_jobs,
                include_history,
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
    include_jobs: bool,
    include_history: bool,
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
            include_jobs,
            include_history,
            preferred_hosts.get(cluster_config.name),
        ): cluster_config.name
        for cluster_config in active_cluster_configs(config_snapshot)
    }


def submit_estimate_requests(
    executor: concurrent.futures.ThreadPoolExecutor,
    config: AppConfig,
    user_override: str | None,
    request: EstimateRequest,
    cluster_names: set[str],
    preferred_hosts: dict[str, str] | None = None,
) -> dict[concurrent.futures.Future[EstimateResult], str]:
    """Test the same request against each selected cluster independently."""
    validate_estimate_request(request)
    preferred_hosts = preferred_hosts or {}
    config_snapshot = copy.deepcopy(config)
    request_snapshot = copy.deepcopy(request)
    selected = {name.casefold() for name in cluster_names}
    return {
        executor.submit(
            fetch_cluster_estimate,
            cluster_config,
            config_snapshot.settings,
            user_override,
            request_snapshot,
            preferred_hosts.get(cluster_config.name),
        ): cluster_config.name
        for cluster_config in active_cluster_configs(config_snapshot)
        if cluster_config.name.casefold() in selected
    }


def merge_cluster_refresh(
    previous: Cluster,
    refreshed: Cluster,
    include_filesystems: bool,
    check_login_nodes: bool,
    include_schedule: bool,
    include_jobs: bool,
    include_history: bool,
) -> Cluster:
    """Keep data that was intentionally omitted from a partial refresh."""
    refreshed.loading = False
    if not include_filesystems:
        refreshed.filesystems = previous.filesystems
    if not include_schedule:
        refreshed.reservations = previous.reservations
        refreshed.running_jobs = previous.running_jobs
    if not include_schedule and not include_jobs:
        refreshed.jobs = previous.jobs
        refreshed.pending_priorities = previous.pending_priorities
        refreshed.fairshare = previous.fairshare
        refreshed.priority_config = previous.priority_config
        refreshed.jobs_error = previous.jobs_error
        refreshed.priority_error = previous.priority_error
        refreshed.pending_priorities_error = previous.pending_priorities_error
        refreshed.fairshare_error = previous.fairshare_error
    if not include_history:
        refreshed.past_jobs = previous.past_jobs
        refreshed.history_error = previous.history_error
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
                        auth_required=old_login.auth_required,
                        failure_kind=old_login.failure_kind,
                        error=old_login.error,
                    )
                )
        refreshed.login_nodes = merged_logins
    return refreshed
