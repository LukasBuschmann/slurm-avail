"""Hypothetical Slurm request fields and validation."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from .models import EstimateRequest


@dataclass(frozen=True)
class EstimateField:
    key: str
    label: str
    prompt: str
    kind: str = "text"
    minimum: int = 0
    maximum: int = 0


ESTIMATE_FIELDS = (
    EstimateField("nodes", "Nodes", "Nodes", "integer", 1, 100_000),
    EstimateField(
        "tasks_per_node",
        "Tasks per node",
        "Tasks per node",
        "integer",
        1,
        100_000,
    ),
    EstimateField(
        "cpus_per_task",
        "CPUs per task",
        "CPUs per task",
        "integer",
        1,
        100_000,
    ),
    EstimateField(
        "memory_per_node",
        "Memory per node",
        "Memory per node, for example 64G",
        "memory",
    ),
    EstimateField(
        "gpus_per_node",
        "GPUs per node",
        "GPUs per node",
        "integer",
        0,
        1024,
    ),
    EstimateField(
        "gpu_type",
        "GPU type",
        "GPU type, blank for any",
        "gpu_type",
    ),
    EstimateField(
        "time_limit",
        "Time limit",
        "Slurm time limit, for example 08:00:00",
        "time",
    ),
    EstimateField("partition", "Partition", "Partition, blank for default"),
    EstimateField("account", "Account", "Account, blank for default"),
    EstimateField("qos", "QoS", "QoS, blank for default"),
    EstimateField(
        "constraint",
        "Constraint",
        "Node constraint, blank for none",
    ),
    EstimateField("exclusive", "Exclusive nodes", "Exclusive nodes", "boolean"),
)

MEMORY_RE = re.compile(r"[1-9]\d*[KMGTP]?", re.IGNORECASE)
GPU_TYPE_RE = re.compile(r"[A-Za-z0-9_.-]+")
TIME_RE = re.compile(r"[0-9:-]+")


def request_value_text(request: EstimateRequest, field: EstimateField) -> str:
    value = getattr(request, field.key)
    if field.kind == "boolean":
        return "yes" if value else "no"
    if value == "":
        return "default" if field.key in ("partition", "account", "qos") else "any"
    return str(value)


def validate_estimate_request(request: EstimateRequest) -> None:
    for field in ESTIMATE_FIELDS:
        value = getattr(request, field.key)
        if field.kind == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{field.label} must be an integer")
            if not field.minimum <= value <= field.maximum:
                raise ValueError(
                    f"{field.label} must be {field.minimum}-{field.maximum}"
                )
        elif field.kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{field.label} must be yes or no")
        elif not isinstance(value, str):
            raise ValueError(f"{field.label} must be text")

    if not MEMORY_RE.fullmatch(request.memory_per_node.strip()):
        raise ValueError("Memory per node needs a positive value such as 64G")
    if request.gpu_type and not GPU_TYPE_RE.fullmatch(request.gpu_type):
        raise ValueError(
            "GPU type may contain letters, numbers, dot, dash, or underscore"
        )
    if (
        not request.time_limit
        or not TIME_RE.fullmatch(request.time_limit)
        or not any(character.isdigit() for character in request.time_limit)
    ):
        raise ValueError("Time limit must use Slurm's numeric time format")
    try:
        parse_estimate_time(request.time_limit)
    except (IndexError, ValueError) as error:
        raise ValueError("Time limit must use Slurm's numeric time format") from error
    for field in ("partition", "account", "qos", "constraint"):
        value = getattr(request, field)
        if len(value) > 256 or any(character in value for character in "\r\n\0"):
            raise ValueError(f"{field.capitalize()} contains unsupported characters")


def with_estimate_value(
    request: EstimateRequest,
    field: EstimateField,
    raw_value: str,
) -> EstimateRequest:
    candidate = copy.deepcopy(request)
    value = raw_value.strip()
    if field.kind == "integer":
        try:
            parsed_value: object = int(value)
        except ValueError as error:
            raise ValueError(f"{field.label} must be an integer") from error
    elif field.kind == "boolean":
        if value.casefold() in ("yes", "y", "true", "1", "on"):
            parsed_value = True
        elif value.casefold() in ("no", "n", "false", "0", "off"):
            parsed_value = False
        else:
            raise ValueError(f"{field.label} must be yes or no")
    elif field.kind in ("memory", "gpu_type"):
        parsed_value = value.upper() if field.kind == "memory" else value
    else:
        parsed_value = value
    setattr(candidate, field.key, parsed_value)
    validate_estimate_request(candidate)
    return candidate


def toggle_estimate_boolean(
    request: EstimateRequest,
    field: EstimateField,
) -> EstimateRequest:
    if field.kind != "boolean":
        return request
    return with_estimate_value(
        request,
        field,
        "no" if getattr(request, field.key) else "yes",
    )


def estimate_field_is_adjustable(field: EstimateField) -> bool:
    return field.kind in ("integer", "memory", "time", "boolean")


def parse_estimate_time(value: str) -> int:
    day_text, separator, clock_text = value.partition("-")
    if separator:
        days = int(day_text)
        raw_parts = clock_text.split(":")
        if not 1 <= len(raw_parts) <= 3 or any(not part for part in raw_parts):
            raise ValueError("invalid Slurm time")
        parts = [int(part) for part in raw_parts]
        hours = parts[0]
        minutes = parts[1] if len(parts) > 1 else 0
        seconds = parts[2] if len(parts) > 2 else 0
        return ((days * 24 + hours) * 60 + minutes) * 60 + seconds

    raw_parts = value.split(":")
    if not 1 <= len(raw_parts) <= 3 or any(not part for part in raw_parts):
        raise ValueError("invalid Slurm time")
    parts = [int(part) for part in raw_parts]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = 0, parts[0], parts[1]
    else:
        hours, minutes, seconds = 0, parts[0], 0
    return (hours * 60 + minutes) * 60 + seconds


def format_estimate_time(seconds: int) -> str:
    hours, remainder = divmod(max(1, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def adjust_estimate_value(
    request: EstimateRequest,
    field: EstimateField,
    direction: int,
    time_component: int = 0,
) -> EstimateRequest:
    """Adjust one request value while keeping it valid."""
    if direction == 0 or not estimate_field_is_adjustable(field):
        return request
    if field.kind == "boolean":
        return with_estimate_value(
            request,
            field,
            "yes" if direction > 0 else "no",
        )
    if field.kind == "integer":
        current = int(getattr(request, field.key))
        value = min(field.maximum, max(field.minimum, current + direction))
        return with_estimate_value(request, field, str(value))
    if field.kind == "memory":
        match = MEMORY_RE.fullmatch(request.memory_per_node)
        if match is None:
            return request
        unit = request.memory_per_node[-1]
        has_unit = unit.upper() in "KMGTP"
        amount_text = (
            request.memory_per_node[:-1] if has_unit else request.memory_per_node
        )
        amount = max(1, int(amount_text) + direction)
        return with_estimate_value(
            request,
            field,
            f"{amount}{unit if has_unit else ''}",
        )
    if field.kind == "time":
        step = (3600, 60, 1)[min(2, max(0, time_component))]
        seconds = max(1, parse_estimate_time(request.time_limit) + direction * step)
        return with_estimate_value(request, field, format_estimate_time(seconds))
    return request
