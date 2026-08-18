"""Shared constants and lightweight UI types."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "slurm-avail"
DEFAULT_CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / APP_NAME
    / "config.toml"
)
FORECAST_RESOLUTIONS = (15, 30, 60, 120, 240, 360, 720)
CLUSTER_WIDTH = 28
NODE_LABEL_WIDTH = 7
LEGEND_WIDTH = 26
GAP = 3
FILESYSTEM_TABLE_MIN_WIDTH = 105
LOGIN_TABLE_WIDTH = 118
GPU_LEVELS = "▁▂▃▄▅▆▇█"
FORECAST_LEVELS = GPU_LEVELS
FORECAST_STATE_WIDTH = 4
LOADING_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
VIEWS = ("nodes", "filesystems", "logins", "forecast", "config")

Span = tuple[str, str]
Line = list[Span]
