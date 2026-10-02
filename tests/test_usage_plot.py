from __future__ import annotations

import time
from datetime import datetime

import pytest

from slurm_avail.usage import UsageJob, usage_interval_buckets
from slurm_avail.usage_plot import (
    calendar_usage_buckets,
    nice_usage_scale,
    spread_usage_bars,
    usage_axis_labels,
)
from slurm_avail.usage_views import usage_chart, usage_time_axis


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def text(rows):
    return "\n".join("".join(part for part, _ in row) for row in rows)


def test_nice_scales_distinguish_integer_gpu_ticks():
    assert nice_usage_scale(34000, 7) == (40000, 10000)
    assert nice_usage_scale(1380, 7) == (1500, 500)
    rows = usage_chart([1380], "GPU-hours", "mine", 7)
    ticks = [text([row]).split("│")[0].strip() for row in rows[1:-1]]
    ticks = [tick for tick in ticks if tick]
    assert ticks == ["1,500", "1,000", "500"]
    assert len(ticks) == len(set(ticks))
    assert nice_usage_scale(0.2, 7) == (1, 1)


def test_daily_buckets_align_at_midnight_and_clip_edges():
    start, end = stamp("2026-07-04T14:00"), stamp("2026-10-02T14:00")
    edges, period = calendar_usage_buckets(start, end, 103)
    assert period == "day"
    assert edges[0] == start and edges[-1] == end
    assert all(datetime.fromtimestamp(t).hour == 0 for t in edges[1:-1])
    jobs = [UsageJob("1", "", start - 3600, end + 3600, 8, 2)]
    cpu, gpu = usage_interval_buckets(jobs, edges)
    assert sum(cpu) == pytest.approx((end - start) / 3600 * 8)
    assert sum(gpu) == pytest.approx((end - start) / 3600 * 2)
    assert cpu[0] == 10 * 8
    assert cpu[-1] == 14 * 8
    _, small_period = calendar_usage_buckets(start, end, 41)
    assert small_period == "week"


def test_monthly_buckets_include_leap_day():
    start, end = stamp("2024-01-01"), stamp("2025-01-01")
    edges, period = calendar_usage_buckets(start, end, 90)
    assert period == "month" and len(edges) == 13
    assert datetime.fromtimestamp(edges[2]) == datetime(2024, 3, 1)
    job = UsageJob("1", "", stamp("2024-02-01"), stamp("2024-03-01"), 1, 1)
    cpu, _ = usage_interval_buckets([job], edges)
    assert cpu[1] == 29 * 24


def test_calendar_days_follow_dst_without_losing_compute_time(monkeypatch):
    with monkeypatch.context() as context:
        context.setenv("TZ", "Europe/Berlin")
        time.tzset()
        try:
            start, end = stamp("2026-03-28"), stamp("2026-04-01")
            edges, period = calendar_usage_buckets(start, end, 90)
            cpu, _ = usage_interval_buckets(
                [UsageJob("1", "", start, end, 1, 0)], edges
            )
            assert period == "day"
            assert cpu == [24, 23, 24, 24]
            start, end = stamp("2026-10-24"), stamp("2026-10-28")
            edges, _ = calendar_usage_buckets(start, end, 90)
            cpu, _ = usage_interval_buckets(
                [UsageJob("1", "", start, end, 1, 0)], edges
            )
            assert cpu == [24, 25, 24, 24]
        finally:
            context.undo()
            time.tzset()


def test_wider_calendar_bars_do_not_multiply_totals():
    values = [8, 16]
    columns = spread_usage_bars(values, [0, 3600, 7200], 40)
    assert columns == [8] * 20 + [16] * 20
    rendered = text(usage_chart(columns, "CPU-hours", "cpu", 5, total=sum(values)))
    assert "total 24 h" in rendered
    # A few seconds in the first partial period must still be visible.
    assert spread_usage_bars([8, 16], [0, 1, 86400], 40) == [8] + [16] * 39


@pytest.mark.parametrize("width", [30, 41, 90, 150])
def test_timeline_labels_simplify_and_do_not_overlap(width):
    months = usage_axis_labels(stamp("2026-07-01"), stamp("2026-10-01"), width)
    assert months and all(":" not in label for label in months.values())
    assert "Jul 2026" in months.values()
    assert all(
        not any(c.isdigit() for c in label.replace("2026", ""))
        for label in months.values()
    )
    years = usage_axis_labels(stamp("2020-01-01"), stamp("2026-01-01"), width)
    assert len(years) >= 2 and all(
        label.isdigit() and len(label) == 4 for label in years.values()
    )
    for start, end in [
        ("2026-01-01T06:00", "2026-01-01T12:00"),
        ("2026-07-01", "2026-07-31"),
        ("2025-10-01", "2026-10-01"),
        ("2020-01-01", "2026-01-01"),
    ]:
        labels = usage_axis_labels(stamp(start), stamp(end), width)
        occupied = set()
        for position, label in labels.items():
            position = min(position, width - len(label))
            columns = set(range(position, position + len(label)))
            assert not columns & occupied
            occupied |= columns
        rows = usage_time_axis(
            datetime.fromisoformat(start), datetime.fromisoformat(end), width
        )
        assert all(sum(len(t) for t, _ in row) <= width + 10 for row in rows)


def test_month_labels_mark_year_changes():
    labels = usage_axis_labels(stamp("2025-10-01"), stamp("2026-10-01"), 110)
    assert "Oct 2025" in labels.values()
    assert any("2026" in label for label in labels.values())
