from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from runtime.agent.supervisor import WorkerReport
from runtime.execution.supervisor_scheduler import supervisor_normalize_resource


REWORKABLE_FINDING_KINDS = frozenset({"task_failed", "blocker", "missing_evidence", "unauthorized_write"})


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


@dataclass(frozen=True)
class LeadReworkAction:
    task_id: str
    attempt: int
    max_attempts: int
    finding_kinds: list[str]
    instruction: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeadReworkLimit:
    task_id: str
    max_attempts: int
    finding_kinds: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def review_worker_reports(tasks: list, reports: list[WorkerReport], *, readonly_hard_constraint: bool = False) -> list[LeadReviewFinding]:
    """Review worker reports before the supervisor decides whether to rework."""

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


def plan_lead_rework_actions(
    findings: list[LeadReviewFinding],
    attempts_by_task: dict[str, int] | None = None,
    *,
    max_attempts: int = 2,
) -> tuple[list[LeadReworkAction], list[LeadReworkLimit]]:
    attempts = {str(key): int(value or 0) for key, value in dict(attempts_by_task or {}).items()}
    grouped = _reworkable_findings_by_task(findings)
    actions: list[LeadReworkAction] = []
    limits: list[LeadReworkLimit] = []
    hard_limit = max(0, int(max_attempts or 0))
    for task_id, task_findings in sorted(grouped.items()):
        used = max(0, attempts.get(task_id, 0))
        kinds = _unique_finding_kinds(task_findings)
        if used >= hard_limit:
            limits.append(
                LeadReworkLimit(
                    task_id=task_id,
                    max_attempts=hard_limit,
                    finding_kinds=kinds,
                    reason="主管返工次数已达上限，本轮停止继续重跑并在最终答案中如实说明。",
                )
            )
            continue
        attempt = used + 1
        actions.append(
            LeadReworkAction(
                task_id=task_id,
                attempt=attempt,
                max_attempts=hard_limit,
                finding_kinds=kinds,
                instruction=render_lead_rework_instruction(task_id, task_findings, attempt=attempt, max_attempts=hard_limit),
            )
        )
    return actions, limits


def render_lead_rework_instruction(
    task_id: str,
    findings: list[LeadReviewFinding],
    *,
    attempt: int,
    max_attempts: int,
) -> str:
    lines = [
        "## 主管打回重做要求",
        f"- 任务：{task_id or 'unknown'}",
        f"- 返工轮次：{attempt}/{max_attempts}",
        "- 必须只修正本任务范围内的问题，不要自行扩大工具、文件或模型权限。",
        "- 完成后必须在 WorkerReport 中写清 evidence、files_read/files_written、验证或限制。",
        "- 本次打回原因：",
    ]
    for finding in findings:
        evidence = f" evidence={finding.evidence}" if finding.evidence else ""
        lines.append(f"  - {finding.severity} {finding.kind}: {finding.message}{evidence}")
    return "\n".join(lines)


def render_lead_rework_limits(limits: list[LeadReworkLimit]) -> str:
    if not limits:
        return ""
    lines = ["LeadReworkLimit", "- exhausted:"]
    for limit in limits:
        kinds = ", ".join(limit.finding_kinds) if limit.finding_kinds else "unknown"
        lines.append(
            f"  - task={limit.task_id or 'unknown'} max_attempts={limit.max_attempts} kinds={kinds}: {limit.reason}"
        )
    return "\n".join(lines)


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
    return f"主管审查完成，发现 {len(findings)} 条 WorkerReport 风险，已进入返工判断。"


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item).strip().replace("\\", "/") for item in list(value) if str(item).strip()]


def _reworkable_findings_by_task(findings: list[LeadReviewFinding]) -> dict[str, list[LeadReviewFinding]]:
    grouped: dict[str, list[LeadReviewFinding]] = {}
    for finding in list(findings or []):
        task_id = str(getattr(finding, "task_id", "") or "").strip()
        kind = str(getattr(finding, "kind", "") or "").strip()
        if not task_id or kind not in REWORKABLE_FINDING_KINDS:
            continue
        grouped.setdefault(task_id, []).append(finding)
    return grouped


def _unique_finding_kinds(findings: list[LeadReviewFinding]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        kind = str(getattr(finding, "kind", "") or "").strip()
        if not kind or kind in seen:
            continue
        seen.add(kind)
        result.append(kind)
    return result
