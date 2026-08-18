"""Runtime data models shared by collection and presentation code."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    name: str
    state: str
    status: str
    owner: str | None
    unavailable: bool
    gpu_total: int
    gpu_alloc: int
    gpu_busy: int
    cpu_alloc: int
    cpu_total: int
    mem_alloc: int
    mem_total: int

    @property
    def cpu_free(self) -> int:
        if self.unavailable:
            return 0
        return max(0, self.cpu_total - self.cpu_alloc)

    @property
    def mem_free(self) -> int:
        if self.unavailable:
            return 0
        return max(0, self.mem_total - self.mem_alloc)

    @property
    def gpu_free(self) -> int:
        return max(0, self.gpu_total - self.gpu_busy)


@dataclass
class Filesystem:
    label: str
    total: int
    used: int
    available: int
    percent_used: int
    mountpoint: str


@dataclass
class ReservationInterval:
    name: str
    start: float
    end: float
    nodes: tuple[str, ...]


@dataclass
class RunningInterval:
    job_id: str
    end: float
    nodes: tuple[str, ...]
    gpu_per_node: int
    cpu_per_node: int


@dataclass
class LoginNode:
    hostname: str
    checked: bool = False
    reachable: bool = False
    used: bool = False
    error: str | None = None


@dataclass
class Cluster:
    name: str
    host: str
    user: str = ""
    mode: str = "ssh"
    focus: str = "auto"
    filesystem_paths: tuple[str, ...] = ()
    nodes: list[Node] = field(default_factory=list)
    filesystems: dict[str, Filesystem] = field(default_factory=dict)
    login_nodes: list[LoginNode] = field(default_factory=list)
    reservations: list[ReservationInterval] = field(default_factory=list)
    running_jobs: list[RunningInterval] = field(default_factory=list)
    loading: bool = False
    error: str | None = None

    @property
    def has_gpus(self) -> bool:
        if self.focus == "gpu":
            return True
        if self.focus == "cpu":
            return False
        return any(node.gpu_total for node in self.nodes)
