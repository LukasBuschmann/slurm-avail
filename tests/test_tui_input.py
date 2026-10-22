from __future__ import annotations

import curses

from slurm_avail.tui import read_keypress


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
