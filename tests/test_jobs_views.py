from __future__ import annotations

from slurm_avail.jobs_views import (
    JobGroupKey,
    fuzzy_job_name_match,
    job_groups_for_scope,
    job_row,
    jobs_filter_line,
    jobs_for_scope,
    jobs_selection_count,
    jobs_table,
    priority_distribution_rows,
    sticky_jobs_headers,
)
from slurm_avail.models import (
    Cluster,
    FairshareAssociation,
    Node,
    PendingJobPriority,
    PriorityFactors,
    UserJob,
)


def named_job(
    job_id: str,
    name: str,
    *,
    historical: bool = False,
) -> UserJob:
    return UserJob(
        job_id=job_id,
        partition="cpu",
        name=name,
        state="COMPLETED" if historical else "PENDING",
        reason="Resources",
        priority="1",
        start=1_787_188_250 if historical else None,
        end=1_787_191_850 if historical else None,
        scheduled_nodes=(),
        time_limit="01:00:00",
        node_count=1,
        cpu_count=1,
        memory="1G",
        tres_per_node="",
        allocated_tres="",
        account="project",
        qos="normal",
        historical=historical,
    )


def test_fuzzy_job_name_filter_matches_words_as_subsequences() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        jobs=[
            named_job("1", "protein-training"),
            named_job("2", "unrelated-analysis"),
        ],
        past_jobs=[named_job("3", "trained-model", historical=True)],
    )

    assert fuzzy_job_name_match("Protein-Training", "PTN trn")
    assert not fuzzy_job_name_match("unrelated-analysis", "PTN trn")
    assert [
        job.job_id for _cluster, job in jobs_for_scope([cluster], 0, "PTN trn")
    ] == ["1"]
    assert [job.job_id for _cluster, job in jobs_for_scope([cluster], 0, "tr mdl")] == [
        "3"
    ]


def test_jobs_table_shows_filtered_details_without_filter_in_header() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        user="researcher",
        jobs=[
            named_job("1", "protein-training"),
            named_job("2", "unrelated-analysis"),
        ],
        past_jobs=[named_job("3", "trained-model", historical=True)],
    )

    headers, body, details, *_rest = jobs_table(
        [cluster],
        0,
        0,
        "ptn trn",
    )
    text = "\n".join(
        "".join(value for value, _style in row) for row in headers + body + details
    )

    assert 'filter "' not in text
    assert "protein-training" in text
    assert "unrelated-analysis" not in text
    assert "trained-model" not in text
    assert "SELECTED JOB 1" in text
    assert "No history jobs match the name filter." in text


def test_jobs_filter_line_marks_editing_and_hides_when_cleared() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        jobs=[named_job("1", "protein-training")],
    )

    text = "".join(
        value for value, _style in jobs_filter_line([cluster], 0, "ptn", True)
    )

    assert 'filter "ptn_" · 1 matches' in text
    assert jobs_filter_line([cluster], 0, "") == []


def test_jobs_table_reports_when_filter_matches_nothing() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        jobs=[named_job("1", "protein-training")],
        past_jobs=[named_job("2", "trained-model", historical=True)],
    )

    headers, body, details, *_rest = jobs_table([cluster], 0, 0, "missing")
    text = "\n".join("".join(value for value, _style in row) for row in headers + body)

    assert 'filter "' not in text
    footer = "".join(
        value for value, _style in jobs_filter_line([cluster], 0, "missing")
    )
    assert 'filter "missing" · 0 matches' in footer
    assert "No current jobs match the name filter." in text
    assert "No history jobs match the name filter." in text
    assert details == []


def test_jobs_table_groups_names_with_five_job_preview_and_summary_details() -> None:
    jobs = [named_job(str(index), "array-task") for index in range(1, 9)]
    jobs[0].state = "RUNNING"
    jobs[1].start = 1_787_188_250
    jobs[1].scheduled_nodes = ("cpu01",)
    cluster = Cluster(name="CPU", host="login", jobs=jobs)

    _headers, body, details, _width, selected_body, selected_item = jobs_table(
        [cluster],
        0,
        0,
    )
    body_text = "\n".join("".join(value for value, _style in row) for row in body)
    detail_text = "\n".join("".join(value for value, _style in row) for row in details)

    assert selected_item == 0
    assert selected_body == 0
    group_row = "".join(value for value, _style in body[0])
    assert group_row.rstrip() == "▾ array-task"
    assert body_text.count("array-task") == 1
    assert "… 3 more" in body_text
    assert "NAME" in "".join(value for value, _style in _headers[-2])
    assert "".join(value for value, _style in _headers[-2]).lstrip().startswith("NAME")
    assert "JOB GROUP" in detail_text
    assert "name        array-task" in detail_text
    assert "scope       8 current jobs" in detail_text
    assert "1 running   7 pending" in detail_text
    assert "1 planned   0 estimate   6 no estimate" in detail_text
    assert jobs_selection_count([cluster], 0) == 1

    singleton = named_job("9", "single-task")
    cluster.jobs.append(singleton)
    _headers, aligned_body, *_rest = jobs_table([cluster], 0, 0)
    grouped_child = "".join(value for value, _style in aligned_body[1])
    singleton_row = next(
        "".join(value for value, _style in row)
        for row in aligned_body
        if "single-task" in "".join(value for value, _style in row)
    )
    assert group_row.index("array-task") == singleton_row.index("single-task")
    assert grouped_child.index("CPU") == singleton_row.index("CPU")


def test_job_groups_keep_current_and_history_sections_separate() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        jobs=[named_job("1", "same-name"), named_job("2", "same-name")],
        past_jobs=[
            named_job("3", "same-name", historical=True),
            named_job("4", "same-name", historical=True),
        ],
    )

    groups = job_groups_for_scope([cluster], 0)

    assert [(group.key.historical, len(group.entries)) for group in groups] == [
        (False, 2),
        (True, 2),
    ]


def test_jobs_group_drilldown_shows_every_matching_job() -> None:
    jobs = [named_job(str(index), "array-task") for index in range(1, 9)]
    cluster = Cluster(name="CPU", host="login", jobs=jobs)
    key = JobGroupKey("array-task", False)

    _headers, body, details, _width, selected_body, selected_item = jobs_table(
        [cluster],
        0,
        5,
        drilldown=key,
    )
    body_text = "\n".join("".join(value for value, _style in row) for row in body)

    assert selected_item == 5
    assert selected_body == 5
    assert len(body) == 8
    assert body_text.count("array-task") == 8
    assert "…" not in body_text
    assert "SELECTED JOB 6" in "\n".join(
        "".join(value for value, _style in row) for row in details
    )
    assert jobs_selection_count([cluster], 0, drilldown=key) == 8


def test_jobs_name_grouping_can_be_disabled() -> None:
    cluster = Cluster(
        name="CPU",
        host="login",
        jobs=[named_job("1", "array-task"), named_job("2", "array-task")],
    )

    _headers, body, details, *_rest = jobs_table(
        [cluster],
        0,
        0,
        group_by_name=False,
    )
    text = "\n".join("".join(value for value, _style in row) for row in body)
    detail_text = "\n".join("".join(value for value, _style in row) for row in details)

    assert "2 jobs" not in text
    assert text.count("array-task") == 2
    assert "SELECTED JOB 1" in detail_text


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
        pending_priorities=[
            PendingJobPriority(job_id="100", priority=2_000, partition="capella"),
            PendingJobPriority(
                job_id="3931173",
                priority=12_910,
                partition="capella",
            ),
            PendingJobPriority(job_id="101", priority=20_000, partition="capella"),
            PendingJobPriority(job_id="102", priority=20_000, partition="other"),
            PendingJobPriority(job_id="103", priority=50_000, partition="capella"),
        ],
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
    live_header = "".join(value for value, _style in headers[-2])
    body_text = "\n".join("".join(value for value, _style in row) for row in body)
    detail_text = "\n".join("".join(value for value, _style in row) for row in details)
    styles = [style for row in body for _value, style in row]

    assert selected_row == 0
    assert selected_job == 0
    assert "PLANNED" in text
    assert "TIME LIMIT" in text
    assert "NODES" in text
    assert "CPUS" in text
    assert "MEMORY" in text
    assert "GPUS" in text
    assert "PLACEMENT" in live_header
    assert "JOB" not in live_header
    assert "STATE" not in live_header
    assert "PRIORITY" not in live_header
    assert "3931173" not in body_text
    assert "SELECTED JOB 3931173" in detail_text
    assert "STATUS / REASON" not in text
    assert "reason Priority" in text
    assert "time limit 08:00:00  nodes 1  CPUs 32" in text
    assert "4×h100" in text
    assert "fair-share" in text
    assert "VISIBLE PENDING PRIORITIES" not in text
    assert "selected 12,910" not in text
    assert "higher 3 · equal 1 · lower 1" in text
    assert "priority/multifactor" in text
    assert "selected" in styles
    assert "[0 ALL]" in text


def test_priority_distribution_gives_zero_its_own_log_bucket() -> None:
    job = UserJob(
        job_id="selected",
        partition="gpu",
        name="queued",
        state="PENDING",
        reason="Priority",
        priority="",
        start=None,
        end=None,
        scheduled_nodes=(),
        time_limit="01:00:00",
        node_count=1,
        cpu_count=1,
        memory="1G",
        tres_per_node="",
        allocated_tres="",
        account="project",
        qos="normal",
    )
    cluster = Cluster(
        name="GPU",
        host="login",
        pending_priorities=[
            PendingJobPriority(job_id="held", priority=0, partition="gpu"),
            PendingJobPriority(job_id="spike", priority=11_113, partition="gpu"),
            PendingJobPriority(
                job_id="selected",
                priority=11_678,
                partition="gpu",
            ),
            PendingJobPriority(job_id="high", priority=118_239, partition="gpu"),
        ],
    )

    rows = priority_distribution_rows(cluster, job)
    text = "\n".join("".join(value for value, _style in row) for row in rows)
    chart = rows[0]
    selected_bucket = next(
        index for index, (_value, style) in enumerate(chart[1:]) if style == "mine"
    )

    assert "0" in text
    assert "118k" in text
    assert "higher 1 · equal 1 · lower 2" in text
    assert selected_bucket == 2


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


def test_jobs_table_hides_cluster_and_scheduler_errors() -> None:
    command = "Command '['ssh', '-l', 'researcher', 'login.example'] failed"
    clusters = [
        Cluster(name="DOWN", host="login", error=command),
        Cluster(
            name="BROKEN",
            host="login",
            jobs_error="squeue failed",
            history_error="sacct failed",
        ),
    ]

    headers, body, *_rest = jobs_table(clusters, 0, 0)
    text = "\n".join("".join(value for value, _style in row) for row in headers + body)

    assert command not in text
    assert "squeue failed" not in text
    assert "sacct failed" not in text


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
    history_header = next(
        "".join(value for value, _style in row)
        for row in body
        if any("RESULT" in value for value, _style in row)
    )
    assert "JOB" not in history_header
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
