"""Styled-text and compact-capacity formatting helpers."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .constants import GPU_LEVELS, VIEW_LABELS, VIEWS, Line, Span


def line(*spans: Span) -> Line:
    return list(spans)


def plain(text: str) -> Line:
    return line((text, "normal"))


def visible_length(spans: Iterable[Span]) -> int:
    return sum(len(text) for text, _style in spans)


def clipped_line(spans: Line, width: int) -> Line:
    """Clip styled text to a fixed column boundary without losing styles."""
    clipped: Line = []
    remaining = max(0, width)
    for text, style in spans:
        if remaining <= 0:
            break
        fragment = text[:remaining]
        if fragment:
            clipped.append((fragment, style))
        remaining -= len(fragment)
    return clipped


def compact_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def compact_memory(value_mb: int) -> str:
    value_gib = value_mb / 1024
    if value_gib >= 1024:
        return f"{value_gib / 1024:.1f}T"
    return f"{value_gib:.0f}G"


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "K", "M", "G", "T", "P", "E"):
        if amount < 1024 or unit == "E":
            return f"{amount:.1f}{unit}" if amount < 100 else f"{amount:.0f}{unit}"
        amount /= 1024
    return f"{amount:.1f}E"


def rounded_free_slots(free: int, total: int, width: int) -> int:
    """Round free capacity upward so any non-zero capacity stays visible."""
    if free <= 0 or total <= 0:
        return 0
    return min(width, max(1, math.ceil(width * free / total)))


def capacity_bar(
    label: str,
    free: int,
    total: int,
    stats: str,
    width: int,
    free_style: str,
    busy_style: str = "busy",
    mine_busy: int = 0,
    reserved_busy: int = 0,
    thin: bool = False,
    display_mode: str = "capacity",
) -> Line:
    """Draw non-overlapping owned, occupied, and free capacity left to right."""
    segments = capacity_segments(
        free,
        total,
        width,
        free_style,
        busy_style,
        mine_busy,
        reserved_busy,
        thin,
        display_mode,
    )
    return line(
        (f"{label}  ", free_style),
        *segments,
        (f"  {stats}", "normal"),
    )


def capacity_segments(
    free: int,
    total: int,
    width: int,
    free_style: str,
    busy_style: str = "busy",
    mine_busy: int = 0,
    reserved_busy: int = 0,
    thin: bool = False,
    display_mode: str = "capacity",
) -> Line:
    """Draw only the colored cells of a capacity bar."""
    free = min(max(0, free), max(0, total))
    occupied = max(0, total - free)
    mine_busy = min(max(0, mine_busy), occupied)
    reserved_busy = min(max(0, reserved_busy), occupied - mine_busy)
    other_busy = occupied - mine_busy - reserved_busy
    values = (mine_busy, reserved_busy, other_busy, free)
    active = [index for index, value in enumerate(values) if value > 0]
    slots = [0] * len(values)
    if active and width > 0:
        if len(active) >= width:
            for index in active[:width]:
                slots[index] = 1
        else:
            denominator = sum(values[index] for index in active)
            exact = [width * value / denominator for value in values]
            slots = [math.floor(share) for share in exact]
            leftover = width - sum(slots)
            ranked = sorted(
                active,
                key=lambda index: exact[index] - math.floor(exact[index]),
                reverse=True,
            )
            for index in ranked[:leftover]:
                slots[index] += 1
            for index in active:
                if slots[index] > 0:
                    continue
                donor = max(
                    (candidate for candidate in active if slots[candidate] > 1),
                    key=lambda candidate: slots[candidate] - exact[candidate],
                )
                slots[donor] -= 1
                slots[index] = 1

    mine_slots, reserved_slots, other_slots, free_slots = slots
    other_style = busy_style
    remaining_style = free_style
    if display_mode == "usage":
        remaining_style = "busy"
        if busy_style == "busy":
            other_style = free_style
    mine_symbol = reserved_symbol = other_symbol = free_symbol = "━"
    if not thin:
        mine_symbol = reserved_symbol = other_symbol = free_symbol = "▆"
    return line(
        (mine_symbol * mine_slots, "mine"),
        (reserved_symbol * reserved_slots, "mine_reserved"),
        (other_symbol * other_slots, other_style),
        (free_symbol * free_slots, remaining_style),
    )


def ownership_legend_lines() -> list[Line]:
    return [
        line(("U ▆", "mine"), (" your usage", "normal")),
        line(("R ▆", "mine_reserved"), (" your reservation", "normal")),
    ]


def free_meter(free: int, total: int) -> str:
    if free <= 0 or total <= 0:
        return "·"
    index = min(8, max(1, math.ceil(8 * free / total)))
    return GPU_LEVELS[index - 1]


def tab_line(active_view: str, context: str, views: tuple[str, ...] = VIEWS) -> Line:
    result: Line = []
    for view in views:
        if result:
            result.append(("  ", "normal"))
        result.append(
            (f"[{VIEW_LABELS[view]}]", "title" if active_view == view else "normal")
        )
    result.append((f"   {context} · Tab switches view", "busy"))
    return result
