from __future__ import annotations

from runtime.execution.pipeline import PipelineRunState
from runtime.memory.flywheel import FlywheelStore
from runtime.safety.checkpoint import RollbackResult


def _record_flywheel_safely(flywheel: FlywheelStore, run_state: PipelineRunState) -> None:
    try:
        flywheel.record_pipeline_state(run_state)
    except Exception as exc:
        print(f"Flywheel 记录失败，已跳过：{exc}")


def _record_failure_case_safely(
    flywheel: FlywheelStore,
    raw_user_input: str,
    attempt_count: int,
    audit,
    rollback: RollbackResult,
) -> None:
    try:
        flywheel.record_failure_case(
            user_request=raw_user_input,
            attempt_count=attempt_count,
            models_used=list(getattr(audit, "models_used", []) or []),
            files_touched=list(getattr(audit, "files_touched", []) or []),
            failure_reasons=list(audit.remaining_issues),
            rollback_status="rolled_back" if rollback.rolled_back else "not_rolled_back",
            lesson=(
                "自动修复达到上限后仍未通过 Final Auditor。"
                "后续规划应缩小任务范围、补充验收标准或换用更强模型。"
            ),
        )
    except Exception as exc:
        print(f"Failure Memory 记录失败，已跳过：{exc}")


def _audit_files_touched(audit) -> list[str]:
    values = []
    for raw in list(getattr(audit, "files_touched", []) or []):
        path = str(raw or "").strip().replace("\\", "/")
        if path and path not in values:
            values.append(path)
    return values
