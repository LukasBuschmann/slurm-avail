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
    gpu_type: str = ""

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
    mine: bool = False


@dataclass
class RunningInterval:
    job_id: str
    end: float
    nodes: tuple[str, ...]
    gpu_per_node: int
    cpu_per_node: int
    mem_per_node: int = 0
    mine: bool = False


@dataclass
class PriorityFactors:
    """Weighted priority contributions reported by ``sprio``."""

    total: int = 0
    site: int = 0
    age: int = 0
    association: int = 0
    fairshare: int = 0
    job_size: int = 0
    partition: int = 0
    qos: int = 0
    nice: int = 0
    tres: str = ""


@dataclass(frozen=True)
class PendingJobPriority:
    """Raw scheduler priority for a pending job visible to ``squeue``."""

    job_id: str
    priority: int
    partition: str


@dataclass
class UserJob:
    job_id: str
    partition: str
    name: str
    state: str
    reason: str
    priority: str
    start: float | None
    end: float | None
    scheduled_nodes: tuple[str, ...]
    time_limit: str
    node_count: int
    cpu_count: int
    memory: str
    tres_per_node: str
    allocated_tres: str
    account: str
    qos: str
    submit: float | None = None
    elapsed: str = ""
    exit_code: str = ""
    historical: bool = False
    priority_factors: PriorityFactors | None = None

    @property
    def is_running(self) -> bool:
        return self.state.upper() in ("R", "RUNNING")

    @property
    def placement(self) -> str:
        if self.historical:
            return "past"
        if self.is_running:
            return "running"
        if self.start is not None and self.scheduled_nodes:
            return "planned"
        if self.start is not None:
            return "estimate"
        return "none"


@dataclass
class FairshareAssociation:
    account: str
    user: str
    raw_shares: float | None
    normalized_shares: float | None
    raw_usage: float | None
    normalized_usage: float | None
    effective_usage: float | None
    fairshare: float | None
    level_fairshare: float | None


@dataclass
class SchedulerData:
    reservations: list[ReservationInterval] = field(default_factory=list)
    running_jobs: list[RunningInterval] = field(default_factory=list)
    jobs: list[UserJob] = field(default_factory=list)
    past_jobs: list[UserJob] = field(default_factory=list)
    pending_priorities: list[PendingJobPriority] = field(default_factory=list)
    fairshare: list[FairshareAssociation] = field(default_factory=list)
    priority_config: dict[str, str] = field(default_factory=dict)
    jobs_error: str | None = None
    history_error: str | None = None
    priority_error: str | None = None
    pending_priorities_error: str | None = None
    fairshare_error: str | None = None


@dataclass
class EstimateRequest:
    nodes: int = 1
    tasks_per_node: int = 1
    cpus_per_task: int = 1
    memory_per_node: str = "4G"
    gpus_per_node: int = 0
    gpu_type: str = ""
    time_limit: str = "01:00:00"
    partition: str = ""
    account: str = ""
    qos: str = ""
    constraint: str = ""
    exclusive: bool = False


@dataclass
class EstimateResult:
    cluster_name: str
    status: str
    endpoint: str = ""
    start: float | None = None
    job_id: str = ""
    processors: int = 0
    nodes: str = ""
    partition: str = ""
    message: str = ""
    checked_at: float = 0.0


@dataclass
class LoginNode:
    hostname: str
    checked: bool = False
    reachable: bool = False
    used: bool = False
    auth_required: bool = False
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
    jobs: list[UserJob] = field(default_factory=list)
    past_jobs: list[UserJob] = field(default_factory=list)
    pending_priorities: list[PendingJobPriority] = field(default_factory=list)
    fairshare: list[FairshareAssociation] = field(default_factory=list)
    priority_config: dict[str, str] = field(default_factory=dict)
    jobs_error: str | None = None
    history_error: str | None = None
    priority_error: str | None = None
    pending_priorities_error: str | None = None
    fairshare_error: str | None = None
    loading: bool = False
    auth_required: bool = False
    error: str | None = None

    @property
    def has_gpus(self) -> bool:
        if self.focus == "gpu":
            return True
        if self.focus == "cpu":
            return False
        return any(node.gpu_total for node in self.nodes)
