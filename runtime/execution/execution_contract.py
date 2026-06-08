from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planning.planner_schema import PlannerResult
from runtime.config.execution_mode import normalize_execution_mode


MUTATING_OR_TERMINAL_TOOLS = {
    "workspace_edit",
    "command_runner",
    "safe_backup",
}

READONLY_TOOLS = {
    "project_filesystem_readonly",
    "code_locator",
    "git_tools",
}


@dataclass(frozen=True)
class ExecutionContractDecision:
    """Deterministic execution contract applied after planner output."""

    readonly_hard_constraint: bool
    full_supervisor_route: str
    summary_helper_enabled: bool
    reason: str
    normalized_task_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "readonly_hard_constraint": self.readonly_hard_constraint,
            "supervisor_route": self.full_supervisor_route,
            "summary_helper": {
                "enabled": self.summary_helper_enabled,
                "reason": "summary_helper_requested" if self.summary_helper_enabled else "lead_supervisor_final_answer",
            },
            "reason": self.reason,
            "normalized_task_ids": list(self.normalized_task_ids),
        }


def normalize_execution_contract(
    plan: PlannerResult,
    user_request: str = "",
    *,
    mode: str = "",
) -> ExecutionContractDecision:
    """Harden planner output before any runtime path consumes it.

    This is intentionally small and deterministic: the planner may suggest a
    broad plan, but explicit user constraints such as read-only mode become hard
    runtime facts here.
    """

    normalized_mode = normalize_execution_mode(mode)
    text = _combined_contract_text(plan, user_request)
    readonly = _has_readonly_hard_constraint(text)
    normalized_task_ids: list[str] = []

    if readonly:
        for task in list(plan.tasks or []):
            if _force_task_readonly(task):
                normalized_task_ids.append(str(getattr(task, "id", "") or ""))
    else:
        for task in list(plan.tasks or []):
            if _constrain_code_repair_scope(task):
                normalized_task_ids.append(str(getattr(task, "id", "") or ""))

    supervisor_route = _supervisor_route(plan, normalized_mode)
    summary_helper_enabled = _should_enable_summary_helper(plan, normalized_mode, supervisor_route)
    if normalized_mode == "full" and supervisor_route == "team" and not summary_helper_enabled:
        plan.needs_synthesis = False
        plan.synthesis_instruction = ""

    _store_contract(plan, ExecutionContractDecision(
        readonly_hard_constraint=readonly,
        full_supervisor_route=supervisor_route,
        summary_helper_enabled=summary_helper_enabled,
        reason=_decision_reason(readonly, normalized_mode, supervisor_route),
        normalized_task_ids=[item for item in normalized_task_ids if item],
    ))
    return _decision_from_plan(plan)


def _force_task_readonly(task) -> bool:
    before_mcp = list(getattr(task, "mcp", []) or [])
    before_write = list(getattr(task, "write_intent", []) or [])
    task.mcp = [mcp_id for mcp_id in before_mcp if str(mcp_id) not in MUTATING_OR_TERMINAL_TOOLS]
    task.write_intent = []
    if "project_filesystem_readonly" not in task.mcp:
        task.mcp.append("project_filesystem_readonly")
    if "code_locator" not in task.mcp and _looks_like_project_read_task(task):
        task.mcp.append("code_locator")
    note = "ExecutionContract: 用户声明只读/不修改，已移除写入、删除和命令工具。"
    risk_notes = str(getattr(task, "risk_notes", "") or "")
    if note not in risk_notes:
        task.risk_notes = (risk_notes.rstrip() + " " + note).strip()
    instruction = str(getattr(task, "instruction", "") or "")
    guard = (
        "\n\n## Execution Contract 只读硬约束\n"
        "- 用户已经声明只读或不要修改文件，本任务禁止写入、删除、运行命令和测试。\n"
        "- 如信息不足，请说明限制，不要申请写入或命令工具绕过约束。"
    )
    if "## Execution Contract 只读硬约束" not in instruction:
        task.instruction = instruction.rstrip() + guard
    return before_mcp != task.mcp or bool(before_write)


def _constrain_code_repair_scope(task) -> bool:
    if not _is_code_repair_task(task):
        return False

    changed = False
    original_acceptance = list(getattr(task, "acceptance_criteria", []) or [])
    filtered_acceptance = [
        item for item in original_acceptance if not _is_opportunistic_acceptance(str(item or ""))
    ]
    if filtered_acceptance != original_acceptance:
        task.acceptance_criteria = filtered_acceptance
        changed = True

    instruction = str(getattr(task, "instruction", "") or "")
    guard = (
        "\n\n## Execution Contract 修复范围约束\n"
        "- 只修复用户请求或验证失败直接相关的问题，不要顺手优化、重构或修改额外游戏逻辑。\n"
        "- 如果发现无关潜在问题，只在最终说明中列为建议，不要直接改动。"
    )
    if "## Execution Contract 修复范围约束" not in instruction:
        task.instruction = instruction.rstrip() + guard
        changed = True
    return changed


def _supervisor_route(plan: PlannerResult, mode: str) -> str:
    if mode != "full":
        return normalize_execution_mode(mode)
    if plan.route_type in {"direct_answer", "clarify"}:
        return "direct"
    if plan.route_type == "single_agent" or len(plan.tasks or []) <= 1:
        return "single"
    return "team"


def _should_enable_summary_helper(plan: PlannerResult, mode: str, supervisor_route: str) -> bool:
    if mode != "full" or supervisor_route != "team":
        return bool(plan.needs_synthesis)
    raw = dict(getattr(plan, "memory_interface", {}) or {})
    contract = dict(raw.get("execution_contract") or {})
    helper = dict(contract.get("summary_helper") or {})
    if helper.get("enabled") is True:
        return True
    return False


def _is_code_repair_task(task) -> bool:
    text = "\n".join(
        [
            str(getattr(task, "title", "") or ""),
            str(getattr(task, "instruction", "") or ""),
            " ".join(list(getattr(task, "acceptance_criteria", []) or [])),
        ]
    ).lower()
    has_write = bool(list(getattr(task, "write_intent", []) or [])) or "workspace_edit" in list(
        getattr(task, "mcp", []) or []
    )
    has_code_file = any(suffix in text for suffix in [".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml"])
    has_repair_marker = any(marker in text for marker in ["fix", "repair", "修复", "修改", "语法错误", "syntax"])
    return has_write and has_code_file and has_repair_marker


def _is_opportunistic_acceptance(value: str) -> bool:
    lowered = str(value or "").lower()
    markers = [
        "potential bug",
        "style issue",
        "code style",
        "if any",
        "顺手",
        "潜在 bug",
        "潜在问题",
        "风格问题",
        "代码风格",
        "优化",
        "重构",
    ]
    return any(marker in lowered for marker in markers)


def summary_helper_enabled(plan: PlannerResult) -> bool:
    contract = _contract_dict(plan)
    helper = dict(contract.get("summary_helper") or {})
    return bool(helper.get("enabled"))


def supervisor_route(plan: PlannerResult) -> str:
    return str(_contract_dict(plan).get("supervisor_route") or "")


def _store_contract(plan: PlannerResult, decision: ExecutionContractDecision) -> None:
    memory_interface = dict(getattr(plan, "memory_interface", {}) or {})
    memory_interface["execution_contract"] = decision.to_dict()
    plan.memory_interface = memory_interface


def _decision_from_plan(plan: PlannerResult) -> ExecutionContractDecision:
    contract = _contract_dict(plan)
    helper = dict(contract.get("summary_helper") or {})
    return ExecutionContractDecision(
        readonly_hard_constraint=bool(contract.get("readonly_hard_constraint")),
        full_supervisor_route=str(contract.get("supervisor_route") or ""),
        summary_helper_enabled=bool(helper.get("enabled")),
        reason=str(contract.get("reason") or ""),
        normalized_task_ids=[str(item) for item in list(contract.get("normalized_task_ids") or [])],
    )


def _contract_dict(plan: PlannerResult) -> dict[str, Any]:
    memory_interface = dict(getattr(plan, "memory_interface", {}) or {})
    return dict(memory_interface.get("execution_contract") or {})


def _decision_reason(readonly: bool, mode: str, route: str) -> str:
    parts = []
    if readonly:
        parts.append("readonly_hard_constraint")
    if mode == "full":
        parts.append(f"full_supervisor_{route}")
    return ", ".join(parts) if parts else "no_contract_changes"


def _combined_contract_text(plan: PlannerResult, user_request: str) -> str:
    """Text that can express user constraints.

    Keep runtime metadata such as MCP ids and path lists out of this string:
    names like ``project_filesystem_readonly`` or ``mcp_servers/readonly`` are
    implementation details, not proof that the user requested read-only mode.
    """

    parts = [
        user_request,
        getattr(plan, "refined_request", ""),
        getattr(plan, "reason", ""),
        getattr(plan, "direct_answer_instruction", ""),
        getattr(plan, "synthesis_instruction", ""),
    ]
    for task in list(getattr(plan, "tasks", []) or []):
        parts.extend(
            [
                getattr(task, "title", ""),
                getattr(task, "instruction", ""),
                " ".join(list(getattr(task, "acceptance_criteria", []) or [])),
                " ".join(list(getattr(task, "expected_outputs", []) or [])),
            ]
        )
    return "\n".join(str(item or "").lower() for item in parts if str(item or "").strip())


def _has_readonly_hard_constraint(text: str) -> bool:
    lowered = str(text or "").lower()
    readonly_markers = [
        "\u53ea\u8bfb",
        "\u4e0d\u8981\u4fee\u6539",
        "\u4e0d\u4fee\u6539",
        "\u4e0d\u8981\u6539\u6587\u4ef6",
        "\u4e0d\u8981\u5199\u5165",
        "\u4e0d\u8981\u8fd0\u884c\u6d4b\u8bd5",
        "\u4e0d\u8981\u8fd0\u884c",
        "read-only",
        "readonly",
        "do not modify",
        "do not edit",
        "do not write",
        "do not run",
    ]
    edit_override_markers = [
        "\u8bf7\u4fee\u6539",
        "\u8bf7\u4fee\u590d",
        "\u53ef\u4ee5\u4fee\u6539",
        "\u5141\u8bb8\u4fee\u6539",
        "\u9700\u8981\u4fee\u590d",
        "\u5b9e\u9645\u4fee\u6539",
        "\u4fee\u590d\u4ee3\u7801",
        "\u5199\u5165\u6587\u4ef6",
        "\u521b\u5efa\u6587\u4ef6",
        "\u5220\u9664\u6587\u4ef6",
        "fix",
        "repair",
        "please modify",
        "please edit",
    ]
    return any(marker in lowered for marker in readonly_markers) and not any(
        marker in lowered for marker in edit_override_markers
    )


def _looks_like_project_read_task(task) -> bool:
    text = "\n".join(
        [
            str(getattr(task, "title", "") or ""),
            str(getattr(task, "instruction", "") or ""),
            " ".join(list(getattr(task, "read_set", []) or [])),
        ]
    ).lower()
    if any(suffix in text for suffix in [".py", ".js", ".ts", ".md", ".json", ".toml", ".yaml", ".yml"]):
        return True
    return any(marker in text for marker in ["runtime", "src", "代码", "文件", "project", "code", "inspect", "analyze"])
