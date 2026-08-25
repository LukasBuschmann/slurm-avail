"""Configuration view rendering and editing dialogs."""

from __future__ import annotations

import contextlib
import copy
import curses
from pathlib import Path

from .config import SETTING_FIELDS, AppConfig, ClusterConfig
from .constants import LEGEND_WIDTH, Line
from .text import line, ownership_legend_lines, plain


def setting_value_text(key: str, value: int) -> str:
    if key.endswith("_seconds"):
        return f"{value}s"
    if key.endswith("_days"):
        return f"{value}d"
    return str(value)


def config_table(
    config: AppConfig,
    config_path: Path,
    selection: int,
    dirty: bool,
    message: str,
) -> tuple[list[Line], list[Line], int]:
    state = "UNSAVED CHANGES" if dirty else "saved"
    headers = [
        line(("DASHBOARD CONFIGURATION", "title")),
        plain(f"TOML  {config_path}"),
        plain(f"state: {state}"),
        line(((message or "Select a setting or cluster card")[:120], "busy")),
    ]
    body: list[Line] = [line(("GLOBAL SETTINGS", "title"))]
    selected_body_index = 0
    for index, (key, label, _minimum, _maximum) in enumerate(SETTING_FIELDS):
        style = "selected" if selection == index else "normal"
        if selection == index:
            selected_body_index = len(body)
        value = setting_value_text(key, getattr(config.settings, key))
        body.append(line((f"  {label:<28} {value}", style)))

    body.extend([plain(""), line(("CLUSTERS", "title"))])
    setting_count = len(SETTING_FIELDS)
    for cluster_index, cluster in enumerate(config.clusters):
        selection_index = setting_count + cluster_index
        selected = selection == selection_index
        header_style = (
            "selected" if selected else "offline" if cluster.hidden else "title"
        )
        if selection == selection_index:
            selected_body_index = len(body)
        visibility = "HIDDEN" if cluster.hidden else "SHOWN"
        marker = "▶" if selected else " "
        body.append(
            line(
                (
                    f"{marker} {cluster_index + 1:>2}  {cluster.name}  [{visibility}]",
                    header_style,
                )
            )
        )
        if cluster.hidden:
            body.append(plain(""))
            continue
        user = cluster.user or "auto"
        endpoints = "local" if cluster.mode == "local" else ",".join(cluster.addresses)
        filesystems = ",".join(cluster.filesystems) or "none"
        excluded = ",".join(cluster.exclude_partitions) or "none"
        slurm_path = cluster.slurm_bin_path or "PATH"
        body.extend(
            [
                plain(f"      mode / focus   {cluster.mode} / {cluster.focus}"),
                plain(
                    "      authentication "
                    + (
                        "local"
                        if cluster.mode == "local"
                        else (
                            f"interactive · {cluster.control_persist_seconds}s"
                            if cluster.authentication == "interactive"
                            else "batch"
                        )
                    )
                ),
                line(
                    ("      user           ", "normal"),
                    (user, "mine" if cluster.user else "busy"),
                ),
                plain(f"      endpoints      {endpoints}"),
                plain(f"      Slurm bin      {slurm_path}"),
                plain(f"      filesystems    {filesystems}"),
                plain(f"      exclusions     {excluded}"),
                plain(""),
            ]
        )
    return headers, body, selected_body_index


def config_legend_lines(dirty: bool) -> list[Line]:
    return [
        line(("CONFIG EDITOR", "title")),
        plain("-" * LEGEND_WIDTH),
        plain("Enter/e  edit selected"),
        plain("c  connect selected"),
        plain("h  hide/show cluster"),
        plain("Shift+↑/↓  move cluster"),
        plain("a  add cluster"),
        plain("d  delete cluster"),
        plain("s  save + apply"),
        plain("l  reload from disk"),
        plain("↑/↓  select"),
        plain("←/→  scroll sideways"),
        plain("Tab  switch view"),
        plain("q  quit"),
        plain(""),
        line(("PROMPTS", "title")),
        plain("Enter accepts"),
        plain("Esc cancels"),
        plain("Ctrl-U clears value"),
        plain("comma separates lists"),
        plain(""),
        line(("CURRENT USER", "title")),
        *ownership_legend_lines(),
        plain(""),
        line(("STATE", "title")),
        line(
            (
                "unsaved changes" if dirty else "saved on disk",
                "reserved" if dirty else "free",
            )
        ),
    ]


def prompt_text(
    screen: curses.window,
    prompt: str,
    initial: str,
    styles: dict[str, int],
) -> str | None:
    buffer = list(initial)
    with contextlib.suppress(curses.error):
        curses.curs_set(1)
    screen.timeout(-1)
    try:
        while True:
            height, width = screen.getmaxyx()
            y = max(0, height - 1)
            prefix = f"{prompt}: "
            available = max(1, width - len(prefix) - 1)
            value = "".join(buffer)
            visible = value[-available:]
            try:
                screen.move(y, 0)
                screen.clrtoeol()
                screen.addstr(y, 0, prefix[: width - 1], styles["title"])
                x = min(width - 1, len(prefix))
                screen.addstr(y, x, visible[: max(0, width - x - 1)])
                screen.move(y, min(width - 1, x + len(visible)))
                screen.refresh()
            except curses.error:
                pass
            key = screen.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return value
            if key == 27:
                return None
            if key == 21:
                buffer.clear()
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if buffer:
                    buffer.pop()
                continue
            if 32 <= key <= 0x10FFFF:
                with contextlib.suppress(ValueError):
                    buffer.append(chr(key))
    finally:
        screen.timeout(200)
        with contextlib.suppress(curses.error):
            curses.curs_set(0)


def edit_cluster_dialog(
    screen: curses.window,
    styles: dict[str, int],
    existing: ClusterConfig | None,
) -> ClusterConfig | None:
    cluster = copy.deepcopy(existing) if existing else ClusterConfig(name="")
    prompts = (
        ("name", "Cluster name"),
        ("mode", "Mode (ssh/local)"),
        ("focus", "Focus (auto/cpu/gpu)"),
    )
    for attribute, label in prompts:
        value = prompt_text(screen, label, getattr(cluster, attribute), styles)
        if value is None:
            return None
        normalized = value.strip().lower() if attribute != "name" else value.strip()
        setattr(cluster, attribute, normalized)

    if cluster.mode == "ssh":
        addresses = prompt_text(
            screen,
            "SSH addresses (comma separated)",
            ",".join(cluster.addresses),
            styles,
        )
        if addresses is None:
            return None
        cluster.addresses = [
            item.strip() for item in addresses.split(",") if item.strip()
        ]
        user = prompt_text(
            screen,
            "SSH user (blank = SSH config)",
            cluster.user or "",
            styles,
        )
        if user is None:
            return None
        cluster.user = user.strip() or None
        authentication = prompt_text(
            screen,
            "Authentication (batch/interactive)",
            cluster.authentication,
            styles,
        )
        if authentication is None:
            return None
        cluster.authentication = authentication.strip().lower()
        if cluster.authentication == "interactive":
            lifetime = prompt_text(
                screen,
                "Authenticated session lifetime in seconds",
                str(cluster.control_persist_seconds),
                styles,
            )
            if lifetime is None:
                return None
            try:
                cluster.control_persist_seconds = int(lifetime.strip())
            except ValueError:
                cluster.control_persist_seconds = 0
    else:
        cluster.addresses = []
        cluster.user = None
        cluster.authentication = "batch"

    slurm_path = prompt_text(
        screen,
        "Slurm bin path (blank = PATH)",
        cluster.slurm_bin_path,
        styles,
    )
    if slurm_path is None:
        return None
    cluster.slurm_bin_path = slurm_path.strip()
    filesystems = prompt_text(
        screen,
        "Filesystem paths (comma separated)",
        ",".join(cluster.filesystems),
        styles,
    )
    if filesystems is None:
        return None
    cluster.filesystems = [
        item.strip() for item in filesystems.split(",") if item.strip()
    ]
    excluded = prompt_text(
        screen,
        "Excluded partitions (comma separated)",
        ",".join(cluster.exclude_partitions),
        styles,
    )
    if excluded is None:
        return None
    cluster.exclude_partitions = [
        item.strip() for item in excluded.split(",") if item.strip()
    ]
    return cluster


def confirm_prompt(
    screen: curses.window,
    prompt: str,
    styles: dict[str, int],
) -> bool:
    answer = prompt_text(screen, f"{prompt} (type yes)", "", styles)
    return answer is not None and answer.strip().lower() == "yes"
