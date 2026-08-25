"""TOML configuration models, validation, and persistence."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DashboardSettings:
    node_refresh_seconds: int = 10
    filesystem_refresh_seconds: int = 60
    login_refresh_seconds: int = 60
    jobs_refresh_seconds: int = 10
    jobs_history_refresh_seconds: int = 60
    jobs_history_days: int = 7
    forecast_refresh_seconds: int = 300
    failed_retry_seconds: int = 10
    ssh_connect_timeout_seconds: int = 8
    command_timeout_seconds: int = 20
    login_probe_timeout_seconds: int = 10
    filesystem_probe_timeout_seconds: int = 2
    retries_per_address: int = 0
    retry_delay_seconds: int = 1
    forecast_horizon_days: int = 365


@dataclass
class ClusterConfig:
    name: str
    mode: str = "ssh"
    addresses: list[str] = field(default_factory=list)
    user: str | None = None
    authentication: str = "batch"
    control_persist_seconds: int = 3600
    focus: str = "auto"
    hidden: bool = False
    filesystems: list[str] = field(default_factory=list)
    slurm_bin_path: str = ""
    exclude_partitions: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    version: int = 1
    settings: DashboardSettings = field(default_factory=DashboardSettings)
    clusters: list[ClusterConfig] = field(default_factory=list)


SETTING_FIELDS = (
    ("node_refresh_seconds", "Node refresh", 1, 3600),
    ("filesystem_refresh_seconds", "Filesystem refresh", 1, 86400),
    ("login_refresh_seconds", "Endpoint refresh", 1, 86400),
    ("jobs_refresh_seconds", "Jobs refresh", 1, 86400),
    ("jobs_history_refresh_seconds", "Job history refresh", 1, 86400),
    ("jobs_history_days", "Job history days", 1, 3650),
    ("forecast_refresh_seconds", "Forecast refresh", 1, 86400),
    ("failed_retry_seconds", "Retry after failure", 1, 3600),
    ("ssh_connect_timeout_seconds", "SSH connect timeout", 1, 300),
    ("command_timeout_seconds", "Command timeout", 1, 3600),
    ("login_probe_timeout_seconds", "Endpoint probe timeout", 1, 300),
    ("filesystem_probe_timeout_seconds", "Filesystem probe timeout", 1, 60),
    ("retries_per_address", "Retries per address", 0, 10),
    ("retry_delay_seconds", "Delay between retries", 0, 60),
    ("forecast_horizon_days", "Forecast horizon days", 1, 3650),
)
SETTING_LIMITS = {
    key: (minimum, maximum) for key, _label, minimum, maximum in SETTING_FIELDS
}


def default_app_config() -> AppConfig:
    return AppConfig(
        clusters=[
            ClusterConfig(
                name="LOCAL",
                mode="local",
                focus="auto",
                filesystems=["/home"],
            )
        ]
    )


def config_to_dict(config: AppConfig) -> dict[str, object]:
    return {
        "version": config.version,
        "settings": {
            key: getattr(config.settings, key)
            for key, _label, _minimum, _maximum in SETTING_FIELDS
        },
        "clusters": [
            {
                "name": cluster.name,
                "mode": cluster.mode,
                "addresses": cluster.addresses,
                "user": cluster.user,
                "authentication": cluster.authentication,
                "control_persist_seconds": cluster.control_persist_seconds,
                "focus": cluster.focus,
                "hidden": cluster.hidden,
                "filesystems": cluster.filesystems,
                "slurm_bin_path": cluster.slurm_bin_path,
                "exclude_partitions": cluster.exclude_partitions,
            }
            for cluster in config.clusters
        ],
    }


def string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return [item.strip() for item in value if item.strip()]


def validate_config(config: AppConfig) -> None:
    if config.version != 1:
        raise ValueError(f"unsupported config version {config.version}")
    if not config.clusters:
        raise ValueError("at least one cluster is required")

    names: set[str] = set()
    for cluster in config.clusters:
        cluster.name = cluster.name.strip()
        cluster.mode = cluster.mode.strip().lower()
        cluster.authentication = cluster.authentication.strip().lower()
        cluster.focus = cluster.focus.strip().lower()
        cluster.addresses = [
            address.strip() for address in cluster.addresses if address.strip()
        ]
        cluster.filesystems = list(
            dict.fromkeys(path.strip() for path in cluster.filesystems if path.strip())
        )
        cluster.exclude_partitions = list(
            dict.fromkeys(
                partition.strip()
                for partition in cluster.exclude_partitions
                if partition.strip()
            )
        )
        cluster.slurm_bin_path = cluster.slurm_bin_path.strip()
        if cluster.user is not None:
            cluster.user = cluster.user.strip() or None

        if not cluster.name:
            raise ValueError("cluster names cannot be empty")
        folded_name = cluster.name.casefold()
        if folded_name in names:
            raise ValueError(f"duplicate cluster name: {cluster.name}")
        names.add(folded_name)
        if cluster.mode not in ("ssh", "local"):
            raise ValueError(f"{cluster.name}: mode must be 'ssh' or 'local'")
        if cluster.mode == "ssh" and not cluster.addresses:
            raise ValueError(f"{cluster.name}: SSH mode needs an address")
        if cluster.authentication not in ("batch", "interactive"):
            raise ValueError(
                f"{cluster.name}: authentication must be batch or interactive"
            )
        if (
            not isinstance(cluster.control_persist_seconds, int)
            or isinstance(cluster.control_persist_seconds, bool)
            or not 1 <= cluster.control_persist_seconds <= 86400
        ):
            raise ValueError(
                f"{cluster.name}: control_persist_seconds must be between 1 and 86400"
            )
        if cluster.focus not in ("auto", "cpu", "gpu"):
            raise ValueError(f"{cluster.name}: focus must be auto, cpu, or gpu")
        if not isinstance(cluster.hidden, bool):
            raise ValueError(f"{cluster.name}: hidden must be true or false")

    if not any(not cluster.hidden for cluster in config.clusters):
        raise ValueError("at least one cluster must remain shown")

    for key, (minimum, maximum) in SETTING_LIMITS.items():
        value = getattr(config.settings, key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"settings.{key} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"settings.{key} must be between {minimum} and {maximum}")


def app_config_from_dict(data: object) -> AppConfig:
    if not isinstance(data, dict):
        raise ValueError("config root must be a TOML table")
    version = data.get("version", 1)
    if not isinstance(version, int):
        raise ValueError("config version must be an integer")

    settings_data = data.get("settings", {})
    if not isinstance(settings_data, dict):
        raise ValueError("settings must be a TOML table")
    settings = DashboardSettings()
    for key, _label, _minimum, _maximum in SETTING_FIELDS:
        if key in settings_data:
            setattr(settings, key, settings_data[key])

    clusters_data = data.get("clusters", [])
    if not isinstance(clusters_data, list):
        raise ValueError("clusters must be an array of tables")
    clusters: list[ClusterConfig] = []
    for index, raw_cluster in enumerate(clusters_data):
        if not isinstance(raw_cluster, dict):
            raise ValueError(f"clusters[{index}] must be a TOML table")
        secret_fields = {
            key
            for key in raw_cluster
            if "password" in key.casefold() or "passphrase" in key.casefold()
        }
        if secret_fields:
            raise ValueError(
                f"clusters[{index}] must not contain passwords or passphrases; "
                'use authentication = "interactive"'
            )
        name = raw_cluster.get("name", "")
        if not isinstance(name, str):
            raise ValueError(f"clusters[{index}].name must be a string")
        user = raw_cluster.get("user")
        if user is not None and not isinstance(user, str):
            raise ValueError(f"clusters[{index}].user must be a string or null")
        mode = raw_cluster.get("mode", "ssh")
        authentication = raw_cluster.get("authentication", "batch")
        control_persist_seconds = raw_cluster.get("control_persist_seconds", 3600)
        focus = raw_cluster.get("focus", "auto")
        hidden = raw_cluster.get("hidden", False)
        slurm_bin_path = raw_cluster.get("slurm_bin_path", "")
        if not all(
            isinstance(value, str)
            for value in (mode, authentication, focus, slurm_bin_path)
        ):
            raise ValueError(
                f"clusters[{index}] mode, authentication, focus, and "
                "slurm_bin_path must be strings"
            )
        if not isinstance(control_persist_seconds, int) or isinstance(
            control_persist_seconds, bool
        ):
            raise ValueError(
                f"clusters[{index}].control_persist_seconds must be an integer"
            )
        if not isinstance(hidden, bool):
            raise ValueError(f"clusters[{index}].hidden must be true or false")
        clusters.append(
            ClusterConfig(
                name=name,
                mode=mode,
                addresses=string_list(
                    raw_cluster.get("addresses", []),
                    f"clusters[{index}].addresses",
                ),
                user=user,
                authentication=authentication,
                control_persist_seconds=control_persist_seconds,
                focus=focus,
                hidden=hidden,
                filesystems=string_list(
                    raw_cluster.get("filesystems", []),
                    f"clusters[{index}].filesystems",
                ),
                slurm_bin_path=slurm_bin_path,
                exclude_partitions=string_list(
                    raw_cluster.get("exclude_partitions", []),
                    f"clusters[{index}].exclude_partitions",
                ),
            )
        )
    config = AppConfig(version=version, settings=settings, clusters=clusters)
    validate_config(config)
    return config


def save_config(config: AppConfig, path: Path) -> None:
    validate_config(config)
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        lines = [f"version = {config.version}", "", "[settings]"]
        for key, _label, _minimum, _maximum in SETTING_FIELDS:
            lines.append(f"{key} = {getattr(config.settings, key)}")
        for cluster in config.clusters:
            lines.extend(
                [
                    "",
                    "[[clusters]]",
                    f"name = {json.dumps(cluster.name, ensure_ascii=False)}",
                    f"mode = {json.dumps(cluster.mode)}",
                    "addresses = ["
                    + ", ".join(
                        json.dumps(address, ensure_ascii=False)
                        for address in cluster.addresses
                    )
                    + "]",
                ]
            )
            if cluster.user is not None:
                lines.append(f"user = {json.dumps(cluster.user, ensure_ascii=False)}")
            lines.extend(
                [
                    f"authentication = {json.dumps(cluster.authentication)}",
                    f"control_persist_seconds = {cluster.control_persist_seconds}",
                    f"focus = {json.dumps(cluster.focus)}",
                    f"hidden = {str(cluster.hidden).lower()}",
                    "filesystems = ["
                    + ", ".join(
                        json.dumps(filesystem, ensure_ascii=False)
                        for filesystem in cluster.filesystems
                    )
                    + "]",
                    "slurm_bin_path = "
                    + json.dumps(cluster.slurm_bin_path, ensure_ascii=False),
                    "exclude_partitions = ["
                    + ", ".join(
                        json.dumps(partition, ensure_ascii=False)
                        for partition in cluster.exclude_partitions
                    )
                    + "]",
                ]
            )
        temporary_path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def load_config(path: Path, create: bool = True) -> AppConfig:
    path = path.expanduser()
    if not path.exists():
        config = default_app_config()
        if create:
            save_config(config, path)
        return config
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid TOML in {path}: {error}") from error
    return app_config_from_dict(data)


def active_cluster_configs(config: AppConfig) -> list[ClusterConfig]:
    """Return displayed clusters in their configured order."""
    return [cluster for cluster in config.clusters if not cluster.hidden]
