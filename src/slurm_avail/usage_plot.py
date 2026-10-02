"""Calendar periods and readable scales for usage graphs."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def nice_usage_scale(peak: float, height: int) -> tuple[int, int]:
    """Choose whole-hour tick steps from 1, 2, 5 times powers of ten."""
    target = max(1.0, peak / max(1, min(4, height)))
    power = 10 ** math.floor(math.log10(target))
    step = next(int(n * power) for n in (1, 2, 5, 10) if n * power >= target)
    return max(step, math.ceil(peak / step) * step), step


def calendar_boundaries(start: float, end: float, unit: str, step: int = 1):
    """Yield local calendar boundaries, advancing elapsed hours across DST folds."""
    current = datetime.fromtimestamp(start).replace(minute=0, second=0, microsecond=0)
    if unit == "hour":
        current = current.replace(hour=current.hour // step * step)
        stamp = current.timestamp()
        while stamp <= end:
            if stamp >= start:
                yield stamp
            stamp += step * 3600
        return
    current = current.replace(hour=0)
    if unit == "week":
        current -= timedelta(days=current.weekday())
    elif unit == "month":
        current = current.replace(day=1, month=(current.month - 1) // step * step + 1)
    elif unit == "year":
        current = current.replace(
            month=1, day=1, year=max(1, current.year // step * step)
        )
    while current.timestamp() <= end:
        stamp = current.timestamp()
        if stamp >= start:
            yield stamp
        if unit in ("day", "week"):
            current += timedelta(days=step * (7 if unit == "week" else 1))
        elif unit == "month":
            month = current.month - 1 + step
            current = current.replace(
                year=current.year + month // 12, month=month % 12 + 1
            )
        else:
            current = current.replace(year=current.year + step)


def calendar_usage_buckets(
    start: float, end: float, width: int
) -> tuple[list[float], str]:
    """Use daily bars for 7–90 days when they fit, then weeks/months/years."""
    days = (end - start) / 86400
    candidates = []
    if days <= 2:
        candidates.append(("hour", 1, "hour"))
    if days <= 120:
        candidates.extend([("day", 1, "day"), ("week", 1, "week")])
    if days <= 3 * 366:
        candidates.extend([("month", 1, "month"), ("month", 3, "quarter")])
    candidates.extend(
        [
            ("year", 1, "year"),
            ("year", 2, "2 years"),
            ("year", 5, "5 years"),
            ("year", 10, "10 years"),
        ]
    )
    for unit, step, label in candidates:
        edges = [start]
        for stamp in calendar_boundaries(start, end, unit, step):
            if start < stamp < end:
                edges.append(stamp)
            if len(edges) > max(1, width):
                break
        if len(edges) <= max(1, width):
            return edges + [end], label
    return [start, end], "selected period"


def spread_usage_bars(
    values: list[float], edges: list[float], width: int
) -> list[float]:
    """Give each calendar bucket its proportional width; totals use original values."""
    if not values or width <= 0:
        return []
    duration = edges[-1] - edges[0]
    result: list[float] = []
    for index, value in enumerate(values):
        desired_end = round((edges[index + 1] - edges[0]) / duration * width)
        remaining_bars = len(values) - index - 1
        # Even a short partial edge period gets a cell rather than disappearing.
        last_column = min(width - remaining_bars, max(len(result) + 1, desired_end))
        result.extend([value] * max(0, last_column - len(result)))
    return result


def usage_axis_labels(start: float, end: float, width: int) -> dict[int, str]:
    """Place non-overlapping time/date/month/year labels at calendar boundaries."""
    if width <= 0 or end <= start:
        return {}
    days = (end - start) / 86400
    if days <= 2:
        unit, step = "hour", 1
    elif days <= 45:
        unit, step = "day", 1
    elif days <= 2 * 366:
        unit, step = "month", 1
    else:
        unit, step = "year", 1
    boundary_unit = "week" if unit == "day" and days > 14 else unit
    dates = list(calendar_boundaries(start, end, boundary_unit, step))
    # Keep labels legible on narrow displays without changing data aggregation.
    label_spacing = {"hour": 9, "day": 12, "month": 6, "year": 7}[unit]
    target = max(2, width // label_spacing)
    if len(dates) > target:
        needed = math.ceil(len(dates) / target)
        stride = next((n for n in (1, 2, 3, 6, 12, 24) if n >= needed), needed)
        dates = list(calendar_boundaries(start, end, boundary_unit, stride))
    if not dates:
        dates = [start]
    elif dates[0] > start:
        dates.insert(0, start)
    labels: dict[int, str] = {}
    occupied = [False] * width
    previous_date: datetime | None = None
    for stamp in dates:
        date = datetime.fromtimestamp(stamp)
        new_year = previous_date is None or previous_date.year != date.year
        if unit == "year":
            label = f"{date:%Y}"
        elif unit == "month":
            label = date.strftime("%b %Y" if new_year else "%b")
        elif unit == "day":
            needs_year = new_year and (
                datetime.fromtimestamp(start).year != datetime.fromtimestamp(end).year
                or date.year != datetime.now().year
            )
            label = date.strftime("%b %d %Y" if needs_year else "%b %d")
        else:
            new_day = previous_date is None or date.date() != previous_date.date()
            needs_year = (
                datetime.fromtimestamp(start).year != datetime.fromtimestamp(end).year
            )
            date_format = "%b %d %Y" if needs_year else "%b %d %H:%M"
            label = date.strftime(date_format if new_day else "%H:%M")
        tick = max(0, round((stamp - start) / (end - start) * (width - 1)))
        position = min(width - len(label), tick)
        if position < 0 or any(
            occupied[max(0, position - 1) : position + len(label) + 1]
        ):
            continue
        labels[tick] = label
        occupied[position : position + len(label)] = [True] * len(label)
        previous_date = date
    return labels
