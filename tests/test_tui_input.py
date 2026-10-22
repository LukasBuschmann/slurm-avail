from __future__ import annotations

import curses

from slurm_avail.constants import LEGEND_WIDTH
from slurm_avail.tui import LEFT_PADDING, draw_spans, panel_layout, read_keypress


class InputScreen:
    def __init__(self, keys: list[int]):
        self.keys = iter(keys)
        self.timeouts: list[int] = []

    def getch(self) -> int:
        return next(self.keys)

    def timeout(self, milliseconds: int) -> None:
        self.timeouts.append(milliseconds)


def test_repeated_navigation_is_batched_and_next_action_is_preserved():
    screen = InputScreen(
        [curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_DOWN, ord("q")]
    )

    key, repeat, pending = read_keypress(screen, None)  # type: ignore[arg-type]

    assert (key, repeat, pending) == (curses.KEY_DOWN, 3, ord("q"))
    assert screen.timeouts == [0, 200]
    assert read_keypress(screen, pending) == (ord("q"), 1, None)  # type: ignore[arg-type]


def test_non_navigation_input_is_not_read_ahead():
    screen = InputScreen([ord("a"), ord("b")])

    assert read_keypress(screen, None) == (ord("a"), 1, None)  # type: ignore[arg-type]
    assert screen.timeouts == []
    assert read_keypress(screen, None) == (ord("b"), 1, None)  # type: ignore[arg-type]


def test_direction_change_starts_a_new_batch():
    screen = InputScreen(
        [curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_UP, curses.KEY_UP, ord("q")]
    )

    first = read_keypress(screen, None)  # type: ignore[arg-type]
    second = read_keypress(screen, first[2])  # type: ignore[arg-type]

    assert first == (curses.KEY_DOWN, 2, curses.KEY_UP)
    assert second == (curses.KEY_UP, 2, ord("q"))


def test_tui_drawing_reserves_a_left_margin():
    class DrawScreen:
        def __init__(self):
            self.writes = []

        def getmaxyx(self):
            return 10, 80

        def addstr(self, y, x, text, style):
            self.writes.append((y, x, text, style))

    screen = DrawScreen()
    draw_spans(
        screen,  # type: ignore[arg-type]
        0,
        0,
        [("content", "normal")],
        0,
        20,
        {"normal": 0},
    )

    assert screen.writes == [(0, LEFT_PADDING, "content", 0)]
    _content_width, _divider_x, legend_x = panel_layout(80)
    assert legend_x + LEFT_PADDING == 80 - LEGEND_WIDTH
