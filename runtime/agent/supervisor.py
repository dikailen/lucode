from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.agent.spec import TaskSpec


@dataclass(frozen=True)
class ResourceLease:
    """A declarative read/write lease for supervised full-mode tasks."""

    resource_id: str
    lease_type: str
    owner_task_id: str
    parallel_group: int = 1
    status: str = "planned"
    reason: str = ""

    @classmethod
    def read(cls, resource_id: str, *, owner_task_id: str, parallel_group: int = 1, reason: str = "") -> "ResourceLease":
        return cls(
            resource_id=str(resource_id or ""),
            lease_type="read",
            owner_task_id=str(owner_task_id or ""),
            parallel_group=int(parallel_group or 1),
            reason=str(reason or ""),
        )

    @classmethod
    def write(
        cls, resource_id: str, *, owner_task_id: str, parallel_group: int = 1, reason: str = ""
    ) -> "ResourceLease":
        return cls(
            resource_id=str(resource_id or ""),
            lease_type="write",
            owner_task_id=str(owner_task_id or ""),
            parallel_group=int(parallel_group or 1),
            reason=str(reason or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceLease":
        return cls(
            resource_id=str(data.get("resource_id") or ""),
            lease_type=str(data.get("lease_type") or "read"),
            owner_task_id=str(data.get("owner_task_id") or ""),
            parallel_group=int(data.get("parallel_group") or 1),
            status=str(data.get("status") or "planned"),
            reason=str(data.get("reason") or ""),
        )


@dataclass(frozen=True)
class WorkerReport:
    """Structured report submitted by a worker.

    `files_written` is actual observed tool evidence. PlannedTask.write_intent
    remains the declared write scope used for authorization comparisons.
    """

    task_id: str
    status: str = "pending"
    summary: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerReport":
        return cls(
            task_id=str(data.get("task_id") or ""),
            status=str(data.get("status") or "pending"),
            summary=str(data.get("summary") or ""),
            evidence_refs=_string_list(data.get("evidence_refs")),
            files_read=_string_list(data.get("files_read")),
            files_written=_string_list(data.get("files_written")),
            tool_calls=_dict_list(data.get("tool_calls")),
            blockers=_string_list(data.get("blockers")),
            artifacts=_string_list(data.get("artifacts")),
        )


@dataclass(frozen=True)
class SupervisorDecision:
    """Deterministic supervisor decision captured without changing execution."""

    action: str
    reason: str = ""
    affected_task_ids: list[str] = field(default_factory=list)
    resource_conflicts: list[dict[str, Any]] = field(default_factory=list)
    context_pack_refs: list[str] = field(default_factory=list)
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorDecision":
        return cls(
            action=str(data.get("action") or "observe"),
            reason=str(data.get("reason") or ""),
            affected_task_ids=_string_list(data.get("affected_task_ids")),
            resource_conflicts=_dict_list(data.get("resource_conflicts")),
            context_pack_refs=_string_list(data.get("context_pack_refs")),
            severity=str(data.get("severity") or "info"),
        )


@dataclass(frozen=True)
class ContextPack:
    """Shared context packet that can be handed to supervised workers later."""

    pack_id: str
    summary: str = ""
    shared_files: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    source_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPack":
        return cls(
            pack_id=str(data.get("pack_id") or ""),
            summary=str(data.get("summary") or ""),
            shared_files=_dict_list(data.get("shared_files")),
            tool_outputs=_dict_list(data.get("tool_outputs")),
            artifact_refs=_string_list(data.get("artifact_refs")),
            source_task_ids=_string_list(data.get("source_task_ids")),
        )


@dataclass(frozen=True)
class SupervisorPlanView:
    """Read-only supervisor view over an existing full-mode plan."""

    mode: str
    route_type: str
    task_specs: list[TaskSpec] = field(default_factory=list)
    resource_leases: list[ResourceLease] = field(default_factory=list)
    context_packs: list[ContextPack] = field(default_factory=list)
    worker_reports: list[WorkerReport] = field(default_factory=list)
    decisions: list[SupervisorDecision] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "route_type": self.route_type,
            "task_specs": [item.to_dict() for item in self.task_specs],
            "resource_leases": [item.to_dict() for item in self.resource_leases],
            "context_packs": [item.to_dict() for item in self.context_packs],
            "worker_reports": [item.to_dict() for item in self.worker_reports],
            "decisions": [item.to_dict() for item in self.decisions],
            "conflicts": [dict(item) for item in self.conflicts],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorPlanView":
        return cls(
            mode=str(data.get("mode") or ""),
            route_type=str(data.get("route_type") or ""),
            task_specs=[TaskSpec.from_dict(item) for item in _dict_list(data.get("task_specs"))],
            resource_leases=[ResourceLease.from_dict(item) for item in _dict_list(data.get("resource_leases"))],
            context_packs=[ContextPack.from_dict(item) for item in _dict_list(data.get("context_packs"))],
            worker_reports=[WorkerReport.from_dict(item) for item in _dict_list(data.get("worker_reports"))],
            decisions=[SupervisorDecision.from_dict(item) for item in _dict_list(data.get("decisions"))],
            conflicts=_dict_list(data.get("conflicts")),
            notes=_string_list(data.get("notes")),
        )


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in list(value) if str(item).strip()]


def _dict_list(value) -> list[dict[str, Any]]:
    if value is None:
        return []
    return [dict(item) for item in list(value) if isinstance(item, dict)]
