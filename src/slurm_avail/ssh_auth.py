"""OpenSSH connection sharing for interactive authentication."""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ClusterConfig, DashboardSettings


@dataclass(frozen=True)
class SshSessionResult:
    endpoint: str
    connected: bool
    reused: bool = False
    message: str = ""


def control_directory() -> Path:
    """Return a per-user directory for OpenSSH control sockets."""
    runtime_root = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_root:
        return Path(runtime_root) / "slurm-avail" / "ssh"
    return Path.home() / ".cache" / "slurm-avail" / "ssh"


def ensure_control_directory(directory: Path | None = None) -> Path:
    """Create and verify the private control-socket directory."""
    directory = control_directory() if directory is None else directory
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or directory.is_symlink():
        raise PermissionError(f"SSH control path is not a directory: {directory}")
    if metadata.st_uid != os.getuid():
        raise PermissionError(f"SSH control directory has another owner: {directory}")
    os.chmod(directory, 0o700)
    return directory


def control_path(directory: Path | None = None) -> str:
    """Use OpenSSH's connection hash to keep socket paths short and unique."""
    return str(ensure_control_directory(directory) / "ssh-%C")


def multiplex_options(
    cluster_config: ClusterConfig,
    directory: Path | None = None,
) -> list[str]:
    """Options that let batch requests reuse an interactive master session."""
    if cluster_config.mode != "ssh" or cluster_config.authentication != "interactive":
        return []
    return [
        "-o",
        f"ControlPath={control_path(directory)}",
        "-o",
        "ControlMaster=no",
    ]


def session_check_command(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    directory: Path | None = None,
) -> list[str]:
    return [
        "ssh",
        "-o",
        f"ControlPath={control_path(directory)}",
        "-O",
        "check",
        "-l",
        ssh_user,
        endpoint,
    ]


def interactive_session_command(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    settings: DashboardSettings,
    directory: Path | None = None,
) -> list[str]:
    """Build a password-capable command without placing a secret in its arguments."""
    return [
        "ssh",
        "-M",
        "-N",
        "-f",
        "-T",
        "-l",
        ssh_user,
        "-o",
        f"ControlPath={control_path(directory)}",
        "-o",
        f"ControlPersist={cluster_config.control_persist_seconds}s",
        "-o",
        "BatchMode=no",
        "-o",
        f"ConnectTimeout={settings.ssh_connect_timeout_seconds}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        endpoint,
    ]


def control_session_active(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    directory: Path | None = None,
) -> bool:
    result = subprocess.run(
        session_check_command(cluster_config, endpoint, ssh_user, directory),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def open_interactive_session(
    cluster_config: ClusterConfig,
    endpoint: str,
    ssh_user: str,
    settings: DashboardSettings,
    directory: Path | None = None,
) -> SshSessionResult:
    """Let OpenSSH read credentials directly from the user's terminal."""
    try:
        if control_session_active(
            cluster_config,
            endpoint,
            ssh_user,
            directory,
        ):
            return SshSessionResult(endpoint, connected=True, reused=True)
        result = subprocess.run(
            interactive_session_command(
                cluster_config,
                endpoint,
                ssh_user,
                settings,
                directory,
            ),
            check=False,
        )
    except KeyboardInterrupt:
        return SshSessionResult(endpoint, connected=False, message="cancelled")
    except (OSError, subprocess.SubprocessError):
        return SshSessionResult(endpoint, connected=False, message="SSH failed")
    if result.returncode == 0:
        return SshSessionResult(endpoint, connected=True)
    return SshSessionResult(endpoint, connected=False, message="authentication failed")
