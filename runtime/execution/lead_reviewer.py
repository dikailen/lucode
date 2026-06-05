from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from runtime.agent.supervisor import WorkerReport
from runtime.execution.supervisor_scheduler import supervisor_normalize_resource


@dataclass(frozen=True)
class LeadReviewFinding:
    """Deterministic supervisor review note for completed worker reports."""

    task_id: str
    severity: str
    kind: str
    message: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def review_worker_reports(tasks: list, reports: list[WorkerReport], *, readonly_hard_constraint: bool = False) -> list[LeadReviewFinding]:
    """Review worker reports without triggering retry or rework."""

    task_by_id = {str(getattr(task, "id", "") or ""): task for task in list(tasks or [])}
    findings: list[LeadReviewFinding] = []
    for report in list(reports or []):
        task_id = str(getattr(report, "task_id", "") or "")
        task = task_by_id.get(task_id)
        if _report_failed(report):
            findings.append(
                LeadReviewFinding(
                    task_id=task_id,
                    severity="error",
                    kind="task_failed",
                    message="Worker task ended in a failed state.",
                    evidence=str(getattr(report, "summary", "") or ""),
                )
            )
        for blocker in _string_list(getattr(report, "blockers", [])):
            findings.append(
                LeadReviewFinding(
                    task_id=task_id,
                    severity="warning",
                    kind="blocker",
                    message=blocker,
                    evidence="blockers",
                )
            )
        if _missing_effective_evidence(report):
            findings.append(
                LeadReviewFinding(
                    task_id=task_id,
                    severity="warning",
                    kind="missing_evidence",
                    message="Worker report did not include files, tool calls, artifacts, or concrete evidence.",
                    evidence=str(getattr(report, "summary", "") or ""),
                )
            )
        unauthorized = _unauthorized_writes(task, report, readonly_hard_constraint=readonly_hard_constraint)
        if unauthorized:
            findings.append(
                LeadReviewFinding(
                    task_id=task_id,
                    severity="error" if readonly_hard_constraint else "warning",
                    kind="unauthorized_write",
                    message="Worker reported writes outside the declared task contract.",
                    evidence=", ".join(unauthorized),
                )
            )
    return findings


def render_lead_review_findings(findings: list[LeadReviewFinding]) -> str:
    if not findings:
        return "LeadReview\n- findings: none"
    lines = ["LeadReview", "- findings:"]
    for finding in findings:
        evidence = f" evidence={finding.evidence}" if finding.evidence else ""
        lines.append(
            f"  - {finding.severity} {finding.kind} task={finding.task_id or 'unknown'}: {finding.message}{evidence}"
        )
    return "\n".join(lines)


def emit_lead_review_events(run_state, findings: list[LeadReviewFinding], *, mode: str = "full") -> None:
    if run_state is None or not hasattr(run_state, "emit_event"):
        return
    for finding in findings:
        run_state.emit_event(
            "LeadReviewFinding",
            finding.message,
            mode=mode,
            agent="supervisor",
            task_id=finding.task_id,
            status=finding.severity,
            payload=finding.to_dict(),
        )
    run_state.emit_event(
        "LeadReviewCompleted",
        _completed_message(findings),
        mode=mode,
        agent="supervisor",
        status="warning" if findings else "completed",
        payload={
            "finding_count": len(findings),
            "error_count": sum(1 for finding in findings if finding.severity == "error"),
            "warning_count": sum(1 for finding in findings if finding.severity == "warning"),
        },
    )


def readonly_hard_constraint_from_plan(plan) -> bool:
    memory_interface = dict(getattr(plan, "memory_interface", {}) or {})
    contract = dict(memory_interface.get("execution_contract") or {})
    return bool(contract.get("readonly_hard_constraint"))


def _report_failed(report: WorkerReport) -> bool:
    status = str(getattr(report, "status", "") or "").strip().lower()
    return status in {"failed", "error", "cancelled", "timeout"}


def _missing_effective_evidence(report: WorkerReport) -> bool:
    if _string_list(getattr(report, "files_read", [])):
        return False
    if _string_list(getattr(report, "files_written", [])):
        return False
    if list(getattr(report, "tool_calls", []) or []):
        return False
    if _string_list(getattr(report, "artifacts", [])):
        return False
    refs = [
        value
        for value in _string_list(getattr(report, "evidence_refs", []))
        if not value.startswith("task:")
    ]
    return not refs


def _unauthorized_writes(task, report: WorkerReport, *, readonly_hard_constraint: bool) -> list[str]:
    reported = [supervisor_normalize_resource(item) for item in _string_list(getattr(report, "files_written", []))]
    reported = [item for item in reported if item]
    if not reported:
        return []
    if readonly_hard_constraint:
        return reported
    declared = [supervisor_normalize_resource(item) for item in _string_list(getattr(task, "write_intent", []))]
    declared = [item for item in declared if item]
    if not declared:
        return reported
    return [path for path in reported if not any(_resource_within(path, allowed) for allowed in declared)]


def _resource_within(path: str, allowed: str) -> bool:
    path = supervisor_normalize_resource(path)
    allowed = supervisor_normalize_resource(allowed)
    if not path or not allowed:
        return False
    return path == allowed or path.startswith(allowed.rstrip("/") + "/")


def _completed_message(findings: list[LeadReviewFinding]) -> str:
    if not findings:
        return "主管审查完成，未发现 WorkerReport 风险。"
    return f"主管审查完成，发现 {len(findings)} 条 WorkerReport 风险，已记录但不自动返工。"


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item).strip().replace("\\", "/") for item in list(value) if str(item).strip()]
