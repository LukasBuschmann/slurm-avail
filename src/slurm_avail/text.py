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
    highlighted_busy: int = 0,
    highlight_style: str = "mine",
    show_busy_when_present: bool = False,
) -> Line:
    free_slots = rounded_free_slots(free, total, width)
    if (
        free > 0
        and free_slots == width
        and (highlighted_busy > 0 or (show_busy_when_present and free < total))
    ):
        free_slots -= 1
    busy_slots = width - free_slots
    highlight_slots = min(
        busy_slots,
        rounded_free_slots(highlighted_busy, total, width),
    )
    other_busy_slots = busy_slots - highlight_slots
    return line(
        (f"{label} [", free_style),
        ("█" * free_slots, free_style),
        ("░" * other_busy_slots, busy_style),
        ("█" * highlight_slots, highlight_style),
        (f"] {stats}", "normal"),
    )


def ownership_legend_lines() -> list[Line]:
    return [
        line(("U ■", "mine"), (" your usage", "normal")),
        line(("R ■", "mine_reserved"), (" your reservation", "normal")),
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
