"""Configuration view rendering and editing dialogs."""

from __future__ import annotations

import contextlib
import curses
from dataclasses import dataclass
from pathlib import Path

from .config import (
    BOOLEAN_SETTING_FIELDS,
    CLUSTER_OFFSET,
    SETTING_COUNT,
    SETTING_FIELDS,
    AppConfig,
    ClusterConfig,
)
from .constants import LEGEND_WIDTH, VIEW_LABELS, Line
from .text import line, ownership_legend_lines, plain

AUTHENTICATION_LABELS = {
    "batch": "SSH key",
    "interactive": "Password",
}


@dataclass(frozen=True)
class ClusterField:
    key: str
    label: str
    prompt: str
    kind: str = "text"
    choices: tuple[str, ...] = ()


CLUSTER_FIELDS = (
    ClusterField("name", "Name", "Cluster name"),
    ClusterField(
        "mode",
        "Connection",
        "Connection mode",
        "choice",
        ("ssh", "local"),
    ),
    ClusterField(
        "addresses",
        "SSH addresses",
        "SSH addresses, comma separated",
        "list",
    ),
    ClusterField("user", "SSH user", "SSH user, blank to use SSH config", "optional"),
    ClusterField(
        "authentication",
        "SSH login method",
        "SSH login method",
        "choice",
        ("batch", "interactive"),
    ),
    ClusterField(
        "focus",
        "Resource focus",
        "Resource focus",
        "choice",
        ("auto", "cpu", "gpu"),
    ),
    ClusterField("slurm_bin_path", "Slurm bin path", "Slurm bin path, blank for PATH"),
    ClusterField(
        "filesystems",
        "Filesystem paths",
        "Filesystem paths, comma separated",
        "list",
    ),
    ClusterField(
        "exclude_partitions",
        "Excluded partitions",
        "Excluded partitions, comma separated",
        "list",
    ),
    ClusterField(
        "hidden",
        "Visibility",
        "Visibility",
        "choice",
        ("shown", "hidden"),
    ),
)


def visible_cluster_fields(cluster: ClusterConfig) -> tuple[ClusterField, ...]:
    hidden_keys: set[str] = set()
    if cluster.mode != "ssh":
        hidden_keys.update(
            {
                "addresses",
                "user",
                "authentication",
            }
        )
    return tuple(field for field in CLUSTER_FIELDS if field.key not in hidden_keys)


def cluster_field_value(cluster: ClusterConfig, field: ClusterField) -> str:
    value = getattr(cluster, field.key)
    if field.key == "hidden":
        return "hidden" if value else "shown"
    if isinstance(value, list):
        return ", ".join(value) or "none"
    if field.key == "user":
        return value or "SSH config"
    if field.key == "slurm_bin_path":
        return value or "PATH"
    return str(value)


def cluster_field_input(cluster: ClusterConfig, field: ClusterField) -> str:
    value = getattr(cluster, field.key)
    if isinstance(value, list):
        return ",".join(value)
    return "" if value is None else str(value)


def cluster_choice_label(field: ClusterField, choice: str) -> str:
    if field.key == "authentication":
        return AUTHENTICATION_LABELS.get(choice, choice)
    return choice


def set_cluster_field(
    cluster: ClusterConfig,
    field: ClusterField,
    raw_value: str,
) -> None:
    value = raw_value.strip()
    if field.kind == "list":
        setattr(
            cluster,
            field.key,
            [item.strip() for item in value.split(",") if item.strip()],
        )
    elif field.kind == "optional":
        setattr(cluster, field.key, value or None)
    elif field.kind == "integer":
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError(f"{field.label} must be an integer") from error
        if not 1 <= parsed <= 86400:
            raise ValueError(f"{field.label} must be between 1 and 86400")
        setattr(cluster, field.key, parsed)
    elif field.key == "hidden":
        normalized = value.casefold()
        if normalized not in field.choices:
            raise ValueError(f"{field.label} must be shown or hidden")
        cluster.hidden = normalized == "hidden"
    else:
        setattr(cluster, field.key, value)


def cycle_cluster_field(
    cluster: ClusterConfig,
    field: ClusterField,
    direction: int,
) -> None:
    if field.kind != "choice" or not field.choices:
        return
    current = cluster_field_value(cluster, field).casefold()
    try:
        index = field.choices.index(current)
    except ValueError:
        index = 0
    set_cluster_field(
        cluster,
        field,
        field.choices[(index + direction) % len(field.choices)],
    )


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
        line(((message or "Select a setting, tab, or cluster card")[:120], "busy")),
    ]
    body: list[Line] = [line(("GLOBAL SETTINGS", "title"))]
    selected_body_index = 0
    for index, (key, label, _minimum, _maximum) in enumerate(SETTING_FIELDS):
        style = "selected" if selection == index else "normal"
        if selection == index:
            selected_body_index = len(body)
        value = setting_value_text(key, getattr(config.settings, key))
        body.append(line((f"  {label:<28} {value}", style)))
    boolean_offset = len(SETTING_FIELDS)
    for offset, (key, label) in enumerate(BOOLEAN_SETTING_FIELDS):
        index = boolean_offset + offset
        selected = selection == index
        if selected:
            selected_body_index = len(body)
        value = getattr(config.settings, key)
        choices = "[ON]  off" if value else "on  [OFF]"
        style = "selected" if selected else "normal"
        body.append(line((f"  {label:<28} {choices}", style)))

    body.extend([plain(""), line(("TABS · display order", "title"))])
    for offset, tab in enumerate(config.settings.tab_order):
        selected = selection == SETTING_COUNT + offset
        if selected:
            selected_body_index = len(body)
        enabled = tab not in config.settings.disabled_tabs
        state = "[ON]  off" if enabled else "on  [OFF]"
        if tab == "config":
            state = "[ON]  always last"
        style = "selected" if selected else "normal" if enabled else "offline"
        body.append(line((f"  {VIEW_LABELS[tab]:<28} {state}", style)))

    body.extend([plain(""), line(("CLUSTERS", "title"))])
    for cluster_index, cluster in enumerate(config.clusters):
        selection_index = CLUSTER_OFFSET + cluster_index
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
                    "      login method   "
                    + (
                        "local"
                        if cluster.mode == "local"
                        else AUTHENTICATION_LABELS.get(
                            cluster.authentication,
                            cluster.authentication,
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
        plain("Enter/e  edit or toggle"),
        plain("←/→/Space  choose option"),
        plain("c  connect selected"),
        plain("h  toggle tab / cluster"),
        plain("Shift+↑/↓  reorder"),
        plain("a  add cluster"),
        plain("d  delete cluster"),
        plain("s  save + apply"),
        plain("l  reload from disk"),
        plain("↑/↓  select"),
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


def cluster_editor_table(
    cluster: ClusterConfig,
    config_path: Path,
    selection: int,
    dirty: bool,
    message: str,
) -> tuple[list[Line], list[Line], int]:
    state = "UNSAVED CHANGES" if dirty else "saved"
    fields = visible_cluster_fields(cluster)
    headers = [
        line(("CLUSTER CONFIGURATION", "title")),
        plain(f"TOML  {config_path}"),
        plain(f"state: {state}"),
        line(((message or "Select a field to change")[:120], "busy")),
    ]
    body = [line(((cluster.name or "NEW CLUSTER"), "title")), plain("")]
    selected_body_index = 0
    for index, field in enumerate(fields):
        selected = selection == index
        if selected:
            selected_body_index = len(body)
        marker = ">" if selected else " "
        value = cluster_field_value(cluster, field)
        if field.kind == "choice":
            value = "  ".join(
                (
                    f"[{cluster_choice_label(field, choice).upper()}]"
                    if choice == value
                    else cluster_choice_label(field, choice)
                )
                for choice in field.choices
            )
        body.append(
            line(
                (f"{marker} {field.label:<24} ", "selected" if selected else "normal"),
                (value, "selected" if selected else "normal"),
            )
        )
    body.extend(
        [
            plain(""),
            line(("Press s to save and apply immediately.", "title")),
            plain("Press Esc to return to the cluster list."),
        ]
    )
    return headers, body, selected_body_index


def cluster_editor_legend_lines(dirty: bool) -> list[Line]:
    return [
        line(("CLUSTER EDITOR", "title")),
        plain("-" * LEGEND_WIDTH),
        plain("↑/↓  select field"),
        plain("←/→  choose option"),
        plain("Enter/Space  next option"),
        plain("Enter/e  type text"),
        plain("s  save + apply"),
        plain("Esc  cluster list"),
        plain("Tab  switch view"),
        plain("q  quit"),
        plain(""),
        line(("VALUES", "title")),
        plain("Choice fields do not"),
        plain("require typing."),
        plain("Comma separates lists."),
        plain("Blank user uses SSH config."),
        plain("Blank Slurm path uses PATH."),
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


def confirm_prompt(
    screen: curses.window,
    prompt: str,
    styles: dict[str, int],
) -> bool:
    answer = prompt_text(screen, f"{prompt} (type yes)", "", styles)
    return answer is not None and answer.strip().lower() == "yes"
