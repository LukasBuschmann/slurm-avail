from __future__ import annotations

from slurm_avail.jobs_views import job_row, jobs_table, sticky_jobs_headers
from slurm_avail.models import (
    Cluster,
    FairshareAssociation,
    Node,
    PriorityFactors,
    UserJob,
)


def test_jobs_table_shows_scheduler_placement_and_priority_details() -> None:
    job = UserJob(
        job_id="3931173",
        partition="capella",
        name="training",
        state="PENDING",
        reason="Priority",
        priority="0.000003",
        start=1_787_188_250,
        end=None,
        scheduled_nodes=("c1",),
        time_limit="08:00:00",
        node_count=1,
        cpu_count=32,
        memory="300G",
        tres_per_node="gres/gpu:h100:4",
        allocated_tres="",
        account="project_a",
        qos="normal",
        priority_factors=PriorityFactors(
            total=12910,
            fairshare=12906,
            job_size=4,
        ),
    )
    cluster = Cluster(
        name="GPU",
        host="login",
        user="researcher",
        jobs=[job],
        fairshare=[
            FairshareAssociation(
                account="project_a",
                user="researcher",
                raw_shares=1,
                normalized_shares=0.1,
                raw_usage=100,
                normalized_usage=0.01,
                effective_usage=0.02,
                fairshare=0.25,
                level_fairshare=2,
            )
        ],
        priority_config={"PriorityType": "priority/multifactor"},
    )

    headers, body, details, _width, selected_row, selected_job = jobs_table(
        [cluster],
        0,
        0,
    )
    text = "\n".join(
        "".join(value for value, _style in row) for row in headers + body + details
    )
    styles = [style for row in body for _value, style in row]

    assert selected_row == 0
    assert selected_job == 0
    assert "PLANNED" in text
    assert "TIME LIMIT" in text
    assert "NODES" in text
    assert "CPUS" in text
    assert "MEMORY" in text
    assert "GPUS" in text
    assert "STATUS / REASON" not in text
    assert "reason Priority" in text
    assert "time limit 08:00:00  nodes 1  CPUs 32" in text
    assert "4×h100" in text
    assert "fair-share" in text
    assert "priority/multifactor" in text
    assert "selected" in styles
    assert "[0 ALL]" in text


def test_jobs_table_distinguishes_start_estimate_without_nodes() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        user="researcher",
        jobs=[
            UserJob(
                job_id="1",
                partition="cpu",
                name="queued",
                state="PENDING",
                reason="Resources",
                priority="1",
                start=1_787_188_250,
                end=None,
                scheduled_nodes=(),
                time_limit="01:00:00",
                node_count=1,
                cpu_count=8,
                memory="16G",
                tres_per_node="",
                allocated_tres="",
                account="project_a",
                qos="normal",
            )
        ],
    )

    headers, body, details, *_rest = jobs_table([cluster], 0, 0)
    text = "\n".join(
        "".join(value for value, _style in row) for row in headers + body + details
    )

    assert "ESTIMATE" in text
    assert "nodes  not assigned by the scheduler" in text


def test_jobs_all_scope_combines_running_and_pending_across_clusters() -> None:
    running = UserJob(
        job_id="10",
        partition="gpu",
        name="running",
        state="RUNNING",
        reason="None",
        priority="1",
        start=1_787_188_250,
        end=1_787_191_850,
        scheduled_nodes=("gpu01",),
        time_limit="01:00:00",
        node_count=1,
        cpu_count=8,
        memory="16G",
        tres_per_node="gres/gpu:1",
        allocated_tres="cpu=8,gres/gpu=1",
        account="project_a",
        qos="normal",
    )
    pending = UserJob(
        job_id="11",
        partition="cpu",
        name="pending",
        state="PENDING",
        reason="Resources",
        priority="10",
        start=None,
        end=None,
        scheduled_nodes=(),
        time_limit="01:00:00",
        node_count=1,
        cpu_count=8,
        memory="16G",
        tres_per_node="",
        allocated_tres="",
        account="project_b",
        qos="normal",
    )
    clusters = [
        Cluster(name="CAPELLA", host="login", jobs=[running]),
        Cluster(name="ALPHA", host="login", jobs=[pending]),
    ]

    headers, body, *_rest = jobs_table(clusters, 0, 0)
    text = "\n".join("".join(value for value, _style in row) for row in headers + body)

    assert "[0 ALL]" in text
    assert "[1 CAPELLA]" in text
    assert "[2 ALPHA]" in text
    assert "RUNNING" in text
    assert "PENDING" in text
    assert "executing" not in text


def test_jobs_table_separates_history_and_keeps_selected_details_separate() -> None:
    past = UserJob(
        job_id="9",
        partition="gpu",
        name="finished-training",
        state="COMPLETED",
        reason="",
        priority="",
        start=1_787_188_250,
        end=1_787_191_850,
        scheduled_nodes=("gpu01",),
        time_limit="01:00:00",
        node_count=1,
        cpu_count=8,
        memory="16G",
        tres_per_node="gres/gpu:1",
        allocated_tres="cpu=8,gres/gpu=1",
        account="project_a",
        qos="normal",
        submit=1_787_187_950,
        elapsed="01:00:00",
        exit_code="0:0",
        historical=True,
    )
    cluster = Cluster(
        name="CAPELLA",
        host="login",
        nodes=[
            Node(
                name="gpu01",
                state="IDLE",
                status="available",
                owner=None,
                unavailable=False,
                gpu_total=4,
                gpu_alloc=0,
                gpu_busy=0,
                cpu_alloc=0,
                cpu_total=64,
                mem_alloc=0,
                mem_total=512_000,
                gpu_type="h100",
            )
        ],
        past_jobs=[past],
    )

    headers, body, details, _width, selected_body, _job = jobs_table([cluster], 0, 0)
    body_text = "\n".join("".join(value for value, _style in row) for row in body)
    detail_text = "\n".join("".join(value for value, _style in row) for row in details)

    assert "COMPLETED" in body_text
    assert "RUNNING + PENDING" in "\n".join(
        "".join(value for value, _style in row) for row in headers
    )
    assert "RECENT HISTORY" in body_text
    assert "RESULT" in body_text
    assert "ELAPSED" in body_text
    assert "STARTED" in body_text
    assert "ENDED" in body_text
    assert "SUBMITTED" not in body_text
    assert "TIME LIMIT" in body_text
    assert "EXIT" not in body_text
    assert "1×h100" in body_text
    assert all(style == "selected" for _value, style in body[selected_body])
    past_row_styles = [style for _value, style in job_row(cluster, past, False)]
    assert "free" in past_row_styles
    assert "offline" not in past_row_styles
    assert "SELECTED JOB 9" not in body_text
    assert "SELECTED JOB 9" in detail_text
    assert "submit " in detail_text
    assert "time limit 01:00:00" in detail_text
    assert "elapsed 01:00:00" in detail_text
    assert "exit 0:0" in detail_text

    past.state = "CANCELLED"
    cancelled_styles = [style for _value, style in job_row(cluster, past, False)]
    assert "offline" in cancelled_styles
    assert "drained" not in cancelled_styles

    history_title = next(
        index
        for index, row in enumerate(body)
        if any(value == "RECENT HISTORY" for value, _style in row)
    )
    sticky_headers, render_offset = sticky_jobs_headers(
        headers,
        body,
        history_title - 1,
    )
    sticky_text = "\n".join(
        "".join(value for value, _style in row) for row in sticky_headers
    )
    assert sticky_text.startswith("RECENT HISTORY")
    assert "RESULT" in sticky_text
    assert "ELAPSED" in sticky_text
    assert render_offset == history_title + 3
