"""Command-line entry point for slurm-avail."""

from __future__ import annotations

import argparse
import contextlib
import curses
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .collect import fetch_all_clusters
from .config import AppConfig, active_cluster_configs, load_config
from .constants import DEFAULT_CONFIG_PATH, VIEWS
from .output import (
    render_config_once,
    render_filesystems_once,
    render_forecast_once,
    render_logins_once,
    render_once,
)
from .tui import dashboard


def package_version() -> str:
    """Return the installed version, with a useful source-tree fallback."""
    try:
        return version("slurm-avail")
    except PackageNotFoundError:
        return "0.1.1.dev0"


def initial_cluster_index(config: AppConfig, requested: str | None) -> int:
    clusters = active_cluster_configs(config)
    if requested is None:
        return 0
    if requested.isdigit():
        index = int(requested) - 1
        if 0 <= index < len(clusters):
            return index
    requested_name = requested.casefold()
    for index, cluster in enumerate(clusters):
        if cluster.name.casefold() == requested_name:
            return index
    raise ValueError(f"unknown cluster: {requested}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="slurm-avail",
        description="Monitor availability across one or more Slurm clusters.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print one uncolored snapshot instead of opening the live UI",
    )
    parser.add_argument(
        "--view",
        choices=VIEWS,
        help="initial view (default: config on first run, otherwise nodes)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="open the configuration view",
    )
    parser.add_argument(
        "-u",
        "--user",
        help="override the configured/SSH-config user for every SSH cluster",
    )
    parser.add_argument(
        "--cluster",
        help="initial forecast cluster name or 1-based number (default: first)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"TOML configuration path (default: {DEFAULT_CONFIG_PATH})",
    )
    arguments = parser.parse_args()
    config_path = arguments.config.expanduser()
    first_run = not config_path.exists()
    initial_view = (
        "config"
        if arguments.init or (first_run and arguments.view is None)
        else arguments.view or "nodes"
    )
    try:
        config = load_config(config_path)
        selected_cluster_index = initial_cluster_index(
            config,
            arguments.cluster,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    context = f"{len(active_cluster_configs(config))} shown · {config_path.name}"

    if arguments.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        if initial_view == "config":
            render_config_once(config, config_path)
            return 0
        clusters = fetch_all_clusters(
            config,
            arguments.user,
            include_filesystems=initial_view == "filesystems",
            check_login_nodes=initial_view == "logins",
            include_schedule=initial_view == "forecast",
        )
        if initial_view == "filesystems":
            render_filesystems_once(clusters, context, config.settings)
        elif initial_view == "logins":
            render_logins_once(clusters, context, config.settings)
        elif initial_view == "forecast":
            render_forecast_once(
                clusters,
                context,
                selected_cluster_index,
                config.settings,
            )
        else:
            render_once(clusters, context, config.settings)
        return 0

    with contextlib.suppress(KeyboardInterrupt):
        curses.wrapper(
            dashboard,
            initial_view,
            config,
            config_path,
            arguments.user,
            selected_cluster_index,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
