"""Terminal charts of allocated CPU-hours and GPU-hours."""

from __future__ import annotations

import textwrap
from datetime import datetime

from .constants import GPU_LEVELS, Line
from .text import line, plain, visible_length
from .usage import USAGE_WINDOWS, UsageHistory, UsageResult, usage_interval_buckets
from .usage_plot import (
    calendar_usage_buckets,
    nice_usage_scale,
    spread_usage_bars,
    usage_axis_labels,
)


def usage_tick(value: float, step: float | None = None) -> str:
    """Use one suffix for the scale, only if every tick remains an integer."""
    threshold = value if step is None else step
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if threshold >= divisor:
            return f"{value / divisor:.0f}{suffix}"
    return f"{value:,.0f}"


def usage_chart(
    values: list[float],
    label: str,
    style: str,
    height: int,
    *,
    total: float | None = None,
    time_labels: dict[int, str] | None = None,
) -> list[Line]:
    ceiling, step = nice_usage_scale(max(values, default=0), height)
    total = sum(values) if total is None else total
    rows = [line((f"{label}   total {total:,.0f} h", style))]
    ticks = {
        max(0, round(value / ceiling * height) - 1): usage_tick(value, step)
        for value in range(step, ceiling + 1, step)
    }
    for row in range(height - 1, -1, -1):
        cells = []
        for value in values:
            level = min(8, max(0, round((value / ceiling * height - row) * 8)))
            cells.append(GPU_LEVELS[level - 1] if level else " ")
        tick = ticks.get(row, "")
        rows.append(line((f"{tick:>8} │", "busy"), ("".join(cells), style)))
    baseline = ["─"] * len(values)
    for position in time_labels or {}:
        baseline[position] = "┬"
    rows.append(plain("       0 └" + "".join(baseline)))
    return rows


def usage_time_axis(start: datetime, end: datetime, width: int) -> list[Line]:
    labels = usage_axis_labels(start.timestamp(), end.timestamp(), width)
    axis = [" "] * width
    for position, label in labels.items():
        position = min(position, width - len(label))
        axis[position : position + len(label)] = label
    return [plain(" " * 10 + "".join(axis))]


def usage_scope_menu(
    history: UsageHistory,
    names: list[str],
    results: dict[str, UsageResult | None],
    width: int,
) -> list[Line]:
    rows: list[Line] = []
    menu: Line = [("SCOPE  ", "title")]
    for index, name in enumerate(["All", *names]):
        result = results.get(name) if name in results else history.status.get(name)
        marker = str(index)
        suffix = ""
        style = "title" if index == history.scope else "normal"
        if index and result is not None:
            if result.error:
                marker = "x"
                style = "drained"
            elif result.incomplete:
                suffix = " !"
                style = "reserved"
            if result.refreshing:
                suffix += " ~"
        elif index and name in results:
            suffix = " ~"
        label = f"[{marker} {name}{suffix}] "
        if visible_length(menu) + len(label) > width and len(menu) > 1:
            rows.append(menu)
            menu = []
        # Keep the selected name bold even when its status is colored.
        menu.extend(
            [
                (f"[{marker} ", style),
                (name, "title" if index == history.scope else style),
                (f"{suffix}] ", style),
            ]
        )
    if menu:
        rows.append(menu)
    return rows


def usage_table(
    history: UsageHistory,
    names: list[str],
    results: dict[str, UsageResult | None],
    width: int,
    height: int = 5,
) -> tuple[list[Line], list[Line]]:
    start = datetime.fromtimestamp(history.start).astimezone()
    end = datetime.fromtimestamp(history.end).astimezone()
    count = max(1, width - 10)
    edges, period = calendar_usage_buckets(history.start, history.end, count)
    time_labels = usage_axis_labels(history.start, history.end, count)
    available = [
        r for r in results.values() if r is not None and (not r.error or r.stale)
    ]
    jobs = [job for result in available for job in result.jobs]
    partial = any(r is None or r.error or r.incomplete for r in results.values())
    refreshing = any(r is None or r.refreshing for r in results.values())
    stale = any(r.stale for r in available)
    status = []
    if partial:
        status.append("Partial totals")
    if refreshing:
        status.append("Refreshing; previous data shown" if stale else "Loading")
    elif stale:
        status.append("Refresh failed; previous data shown")
    headers = [
        line(("YOUR ALLOCATED COMPUTE TIME", "title")),
        *usage_scope_menu(history, names, results, width),
        plain(
            f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M %Z}"
            f" · {USAGE_WINDOWS[history.window_index][0]}"
        ),
        plain(f"CPU-/GPU-hours per {period} · partial periods at edges"),
    ]
    if status:
        for text in textwrap.wrap(" · ".join(status), max(1, width)):
            headers.append(line((text, "reserved")))
    # Details belong beside the selected scope, never underneath the graphs.
    if history.scope and history.scope <= len(names):
        name = names[history.scope - 1]
        result = results.get(name)
        if result is not None:
            detail = result.error or (
                f"{result.incomplete} incomplete records: " + "; ".join(result.issues)
                if result.incomplete
                else ""
            )
            if detail:
                for text in textwrap.wrap(detail, max(1, width))[:3]:
                    headers.append(line((text, "reserved")))
    body: list[Line] = []
    if available:
        cpu, gpu = usage_interval_buckets(jobs, edges)
        for values, label, style in (
            (cpu, "CPU-hours", "cpu"),
            (gpu, "GPU-hours", "mine"),
        ):
            body.extend(
                usage_chart(
                    spread_usage_bars(values, edges, count),
                    label,
                    style,
                    height,
                    total=sum(values),
                    time_labels=time_labels,
                )
            )
            # Both panels use the same calendar ticks.
            body.extend(usage_time_axis(start, end, count))
    elif not refreshing:
        headers.append(plain("Usage unavailable for this period."))
    return headers, body


def usage_legend_lines() -> list[Line]:
    return [
        line(("USAGE", "title")),
        plain("Allocated CPU / GPU hours"),
        plain("Independent vertical scales"),
        plain("GPU jobs also use CPUs"),
        plain("History retained by Slurm"),
        plain("Refresh every 5 minutes"),
        plain("x  not available"),
        plain("!  incomplete records"),
        plain("~  loading / refreshing"),
        plain(""),
        line(("KEYS", "title")),
        plain("+ shorter   - longer"),
        plain("←/→ previous/next period"),
        plain("End  return to now"),
        plain("[ / ]  change scope"),
        plain("0  all; 1-9  cluster"),
        plain("↑/↓  scroll"),
        plain("r  refresh usage"),
        plain("Tab  switch view"),
        plain("q  quit"),
    ]
