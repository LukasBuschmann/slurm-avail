from __future__ import annotations

from datetime import datetime

from slurm_avail.collect import (
    data_command,
    expand_hostlist,
    history_command,
    parse_filesystems,
    parse_history,
    parse_jobs,
    parse_memory_mb,
    parse_nodes,
    parse_schedule,
)
from slurm_avail.config import ClusterConfig, DashboardSettings


def test_expand_hostlist() -> None:
    assert expand_hostlist("gpu[01-02,05],login7") == (
        "gpu01",
        "gpu02",
        "gpu05",
        "login7",
    )


def test_parse_filesystem_posix_kilobytes() -> None:
    filesystems = parse_filesystems("/home|storage 1000 250 750 25% /home\n")

    assert filesystems["/home"].total == 1000 * 1024
    assert filesystems["/home"].available == 750 * 1024


def test_parse_gpu_node_capacity() -> None:
    output = (
        "NodeName=gpu01 CPUTot=64 CPUEfctv=64 CPUAlloc=8 "
        "RealMemory=500000 AllocMem=100000 Gres=gpu:a100:4 "
        "CfgTRES=cpu=64,mem=500000M,gres/gpu=4 "
        "AllocTRES=cpu=8,mem=100000M,gres/gpu=1 "
        "State=MIXED Owner=N/A Partitions=gpu\n"
    )

    nodes = parse_nodes(output, "researcher")

    assert len(nodes) == 1
    node = nodes[0]
    assert node.gpu_total == 4
    assert node.gpu_type == "a100"
    assert node.gpu_alloc == 1
    assert node.gpu_free == 3
    assert node.status == "available"


def test_exclusive_node_has_no_reported_free_capacity() -> None:
    output = (
        "NodeName=cpu01 CPUTot=32 CPUAlloc=0 RealMemory=128000 AllocMem=0 "
        "Gres=(null) CfgTRES=cpu=32,mem=128000M AllocTRES= "
        "State=ALLOCATED Owner=another-user Partitions=cpu\n"
    )

    node = parse_nodes(output, "researcher")[0]

    assert node.status == "exclusive"
    assert node.cpu_free == 0
    assert node.mem_free == 0


def test_parse_memory_tres_units() -> None:
    assert parse_memory_mb("cpu=8,mem=32G,gres/gpu=1") == 32 * 1024
    assert parse_memory_mb("mem=1536M") == 1536


def test_schedule_marks_current_users_jobs_and_reservations() -> None:
    output = "\n".join(
        [
            "__TIMEZONE__|+0200",
            "__RESERVATIONS__",
            (
                "ReservationName=mine StartTime=2026-08-18T12:00:00 "
                "EndTime=2026-08-18T14:00:00 Nodes=gpu01 Users=researcher"
            ),
            (
                "ReservationName=shared StartTime=2026-08-18T12:00:00 "
                "EndTime=2026-08-18T14:00:00 Nodes=gpu02 Users=ALL"
            ),
            "__RUNNING_JOBS__",
            (
                "123|researcher|2026-08-18T14:00:00|gpu01|gres/gpu=1|"
                "cpu=8,mem=32G,gres/gpu=1|8|1"
            ),
            (
                "124|someone-else|2026-08-18T14:00:00|gpu02|gres/gpu=2|"
                "cpu=16,mem=64G,gres/gpu=2|16|1"
            ),
            "__USER_JOBS__",
            (
                "125|gpu|queued-job|PENDING|Priority|0.0001|"
                "2026-08-18T07:30:00|"
                "2026-08-19T08:00:00|Unknown|(Priority)|gpu[03-04]|"
                "08:00:00|2|32|64G|gres/gpu:a100:2|cpu=32,gres/gpu=4|"
                "project_a|normal"
            ),
            "__PRIORITIES__",
            "125|12910|0|100|0|12000|810|0|0|0|",
            "__FAIRSHARE__",
            " project_a||441|0.25|100000|0.02|0.03||0.5",
            " project_a|researcher|1|0.5|500|0.001|0.002|0.25|2.0",
            " project_b|someone-else|1|0.5|500|0.001|0.002|0.25|2.0",
            "__PRIORITY_CONFIG__",
            "PriorityType = priority/multifactor",
            "PriorityWeightFairshare = 1000000000",
        ]
    )

    schedule = parse_schedule(output, "researcher")

    assert [reservation.mine for reservation in schedule.reservations] == [True, False]
    assert [job.mine for job in schedule.running_jobs] == [True, False]
    assert schedule.running_jobs[0].gpu_per_node == 1
    assert schedule.running_jobs[0].cpu_per_node == 8
    assert schedule.running_jobs[0].mem_per_node == 32 * 1024
    assert schedule.jobs[0].placement == "planned"
    assert schedule.jobs[0].submit is not None
    assert schedule.jobs[0].scheduled_nodes == ("gpu03", "gpu04")
    assert schedule.jobs[0].priority_factors is not None
    assert schedule.jobs[0].priority_factors.fairshare == 12000
    assert [(row.account, row.user) for row in schedule.fairshare] == [
        ("project_a", ""),
        ("project_a", "researcher"),
    ]
    assert schedule.priority_config["PriorityType"] == "priority/multifactor"


def test_jobs_only_response_and_command_do_not_require_forecast_data() -> None:
    output = "\n".join(
        [
            "__TIMEZONE__|+0200",
            "__USER_JOBS__",
            (
                "125|gpu|queued-job|PENDING|Resources|1|"
                "2026-08-18T07:30:00|N/A|Unknown|"
                "(Resources)|(null)|01:00:00|1|8|16G|gres/gpu:1|"
                "cpu=8,gres/gpu=1|project_a|normal"
            ),
            (
                "126|gpu|running-job|RUNNING|None|1|"
                "2026-08-18T07:30:00|"
                "2026-08-18T08:00:00|2026-08-18T09:00:00|gpu05|(null)|"
                "01:00:00|1|8|16G|gres/gpu:1|cpu=8,gres/gpu=1|"
                "project_a|normal"
            ),
            "__PRIORITIES__",
            "125|10|0|1|0|8|1|0|0|0|",
            "__FAIRSHARE__",
            "__PRIORITY_CONFIG__",
            "PriorityType = priority/multifactor",
        ]
    )

    jobs_data = parse_jobs(output, "researcher")
    command = data_command(
        ClusterConfig(name="LOCAL", mode="local"),
        DashboardSettings(),
        include_filesystems=False,
        include_schedule=False,
        include_jobs=True,
        include_history=False,
    )

    assert jobs_data.reservations == []
    assert jobs_data.running_jobs == []
    assert [job.job_id for job in jobs_data.jobs] == ["126", "125"]
    assert jobs_data.jobs[0].is_running
    assert jobs_data.jobs[0].scheduled_nodes == ("gpu05",)
    assert "__JOBS__" in command
    assert "__RESERVATIONS__" not in command
    assert "-o '%i|%P|%j|%T|%r|%Q|%V|%S|%e|%N|%n|" in command


def test_jobs_parser_reports_an_unrecognized_old_squeue_layout() -> None:
    output = "\n".join(
        [
            "__TIMEZONE__|+0200",
            "__USER_JOBS__",
            "5208641all.qbashRUNNINGNone10003",
            "__PRIORITIES__",
            "__FAIRSHARE__",
            "__PRIORITY_CONFIG__",
            "PriorityType = priority/basic",
        ]
    )

    jobs_data = parse_jobs(output, "researcher")

    assert jobs_data.jobs == []
    assert jobs_data.jobs_error == (
        "unrecognized squeue field layout (1 fields, expected 19)"
    )


def test_parse_history_returns_completed_jobs_and_skips_live_jobs() -> None:
    output = "\n".join(
        [
            "__TIMEZONE__|+0200",
            (
                "125|training|gpu|COMPLETED|2026-08-18T07:45:00|"
                "2026-08-18T08:00:00|2026-08-18T08:30:00|00:30:00|"
                "01:00:00|1|8|32G|cpu=8,mem=32G,gres/gpu=1|"
                "cpu=8,mem=32G,gres/gpu=1|project_a|normal|gpu01|0:0"
            ),
            (
                "126|still-running|gpu|RUNNING|2026-08-18T08:00:00|"
                "2026-08-18T08:05:00|Unknown|00:10:00|01:00:00|1|8|"
                "32G|cpu=8|cpu=8|project_a|normal|gpu02|0:0"
            ),
        ]
    )

    jobs, error = parse_history(output)

    assert error is None
    assert [job.job_id for job in jobs] == ["125"]
    assert jobs[0].historical
    assert jobs[0].state == "COMPLETED"
    assert jobs[0].scheduled_nodes == ("gpu01",)
    assert jobs[0].elapsed == "00:30:00"
    assert jobs[0].exit_code == "0:0"
    assert jobs[0].submit is not None
    assert jobs[0].end is not None


def test_history_command_uses_portable_absolute_start_date() -> None:
    command = history_command(7, datetime(2026, 8, 19, 13, 0, 0))

    assert "-S 2026-08-12" in command
    assert "now-7days" not in command
