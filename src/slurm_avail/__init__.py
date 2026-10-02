"""A terminal dashboard for one or more Slurm clusters."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("slurm-avail")
except PackageNotFoundError:
    __version__ = "0.4.0.dev0"

__all__ = ["__version__"]
