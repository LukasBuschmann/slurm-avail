from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from slurm_avail.collect import estimate_command, parse_estimate_response
from slurm_avail.config import ClusterConfig
from slurm_avail.estimate_views import estimate_table
from slurm_avail.estimator import (
    ESTIMATE_FIELDS,
    adjust_estimate_value,
    with_estimate_value,
)
from slurm_avail.models import Cluster, EstimateRequest, EstimateResult


def test_estimate_command_uses_test_only_and_requested_resources() -> None:
    request = EstimateRequest(
        nodes=2,
        tasks_per_node=4,
        cpus_per_task=8,
        memory_per_node="64G",
        gpus_per_node=2,
        gpu_type="h100",
        time_limit="08:00:00",
        partition="gpu",
        account="project_a",
        qos="normal",
        constraint="gpu&ssd",
        exclusive=True,
    )
    command = estimate_command(
        ClusterConfig(
            name="GPU",
            mode="local",
            slurm_bin_path="/opt/slurm/current/bin",
        ),
        request,
    )

    assert "export PATH=/opt/slurm/current/bin:$PATH" in command
    assert "srun --test-only" in command
    assert "--nodes=2" in command
    assert "--ntasks-per-node=4" in command
    assert "--cpus-per-task=8" in command
    assert "--mem=64G" in command
    assert "--time=08:00:00" in command
    assert "--gres=gpu:h100:2" in command
    assert "--partition=gpu" in command
    assert "--account=project_a" in command
    assert "--qos=normal" in command
    assert "'--constraint=gpu&ssd'" in command
    assert "--exclusive" in command
    assert "__EXIT__" in command


def test_parse_successful_estimate_response() -> None:
    checked_at = datetime(
        2026,
        8,
        20,
        12,
        tzinfo=timezone(timedelta(hours=2)),
    ).timestamp()
    result = parse_estimate_response(
        "\n".join(
            [
                "__TIMEZONE__|+0200",
                "__EXIT__|0",
                (
                    "srun: Job 34703396 to start at 2026-08-20T14:30:00 "
                    "using 64 processors on nodes gpu[01-02] in partition accel"
                ),
            ]
        ),
        "GPU",
        "login1",
        checked_at,
    )

    assert result.status == "estimated"
    assert result.job_id == "34703396"
    assert result.processors == 64
    assert result.nodes == "gpu[01-02]"
    assert result.partition == "accel"
    assert result.start == checked_at + 2.5 * 60 * 60


def test_parse_rejected_estimate_keeps_scheduler_reason() -> None:
    result = parse_estimate_response(
        "\n".join(
            [
                "__TIMEZONE__|+0200",
                "__EXIT__|1",
                "srun: error: Invalid account or account/partition combination",
            ]
        ),
        "GPU",
        "login1",
        100.0,
    )

    assert result.status == "rejected"
    assert result.message == "Invalid account or account/partition combination"


def test_parse_missing_test_only_support_as_error() -> None:
    result = parse_estimate_response(
        "\n".join(
            [
                "__TIMEZONE__|+0200",
                "__EXIT__|1",
                "srun: unrecognized option '--test-only'",
            ]
        ),
        "OLD",
        "login1",
        100.0,
    )

    assert result.status == "error"
    assert "test-only" in result.message


def test_request_editor_rejects_bad_values() -> None:
    memory_field = next(
        field for field in ESTIMATE_FIELDS if field.key == "memory_per_node"
    )
    request = with_estimate_value(EstimateRequest(), memory_field, "64g")

    assert request.memory_per_node == "64G"
    with pytest.raises(ValueError, match="positive value"):
        with_estimate_value(request, memory_field, "everything")


def test_arrow_adjustment_changes_numbers_memory_and_time_components() -> None:
    request = EstimateRequest()
    nodes_field = next(field for field in ESTIMATE_FIELDS if field.key == "nodes")
    memory_field = next(
        field for field in ESTIMATE_FIELDS if field.key == "memory_per_node"
    )
    time_field = next(field for field in ESTIMATE_FIELDS if field.key == "time_limit")

    request = adjust_estimate_value(request, nodes_field, 1)
    request = adjust_estimate_value(request, memory_field, 1)
    request = adjust_estimate_value(request, time_field, 1, time_component=0)
    request = adjust_estimate_value(request, time_field, 1, time_component=1)
    request = adjust_estimate_value(request, time_field, 1, time_component=2)

    assert request.nodes == 2
    assert request.memory_per_node == "5G"
    assert request.time_limit == "02:01:01"


def test_estimate_table_compares_selected_clusters() -> None:
    checked_at = 1_800_000_000.0
    clusters = [
        Cluster(name="ONE", host="login1"),
        Cluster(name="TWO", host="login2"),
    ]
    results = {
        "ONE": EstimateResult(
            cluster_name="ONE",
            status="estimated",
            start=checked_at + 3600,
            partition="gpu",
            nodes="gpu01",
            processors=16,
            checked_at=checked_at,
        )
    }

    headers, body, _selected_row, _width = estimate_table(
        EstimateRequest(),
        clusters,
        {"ONE"},
        results,
        set(),
        0,
        "",
        now_epoch=checked_at,
    )
    text = "\n".join("".join(value for value, _style in row) for row in headers + body)

    assert "srun --test-only" in text
    assert "ONE" in text
    assert "ESTIMATE" in text
    assert "1h" in text
    assert "gpu01" in text
    assert "TWO" in text
    assert "OFF" in text


def test_estimate_table_has_run_button_and_visible_selected_error() -> None:
    clusters = [Cluster(name="GPU", host="login2")]
    result = EstimateResult(
        cluster_name="GPU",
        status="rejected",
        endpoint="login2",
        message="Requested GPU type is not available in this partition",
        checked_at=100.0,
    )
    cluster_selection = len(ESTIMATE_FIELDS) + 1
    headers, body, _selected_row, _width = estimate_table(
        EstimateRequest(),
        clusters,
        {"GPU"},
        {"GPU": result},
        set(),
        cluster_selection,
        "",
        now_epoch=100.0,
    )
    text = "\n".join("".join(value for value, _style in row) for row in headers + body)

    assert "[ RUN TEST ]" in text
    assert "SELECTED RESULT" in text
    assert "endpoint login2" in text
    assert "Requested GPU type is not available" in text


def test_time_adjustment_highlights_one_component() -> None:
    time_index = next(
        index
        for index, field in enumerate(ESTIMATE_FIELDS)
        if field.key == "time_limit"
    )
    _headers, body, _selected_row, _width = estimate_table(
        EstimateRequest(),
        [],
        set(),
        {},
        set(),
        time_index,
        "",
        adjusting=True,
        time_component=1,
    )
    selected_text = "".join(
        value for row in body for value, style in row if style == "selected"
    )

    assert selected_text == "00"
