from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalog_system.model_catalog import load_model_catalog
from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agents.model_capability import ModelExecutionStrategy, strategy_for_model_info
from runtime.events import ExecutionEventBus
from runtime.execution.run_context import RunContextStore
from runtime.safety.verification_commands import (
    extract_explicit_verification_commands,
    format_verification_command_lock,
)
from runtime.ui.output_controller import OutputController


CODE_MARKERS = {
    "代码",
    "函数",
    "类",
    "接口",
    "bug",
    "报错",
    "修复",
    "实现",
    "重构",
    "评审",
    "python",
    "java",
    "c++",
    "mcpservermanager",
    "code",
    "function",
    "class",
    "fix",
    "bug",
    "debug",
    "implement",
    "refactor",
    "review",
}
CODE_FILE_PATTERN = re.compile(
    r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|json|toml|yaml|yml|ini|cfg|txt)\b",
    re.IGNORECASE,
)
EDIT_MARKERS = {
    "修复",
    "修改",
    "实现",
    "重构",
    "创建",
    "写入",
    "编辑",
    "fix",
    "modify",
    "implement",
    "refactor",
    "create",
    "write",
    "edit",
}
TEST_MARKERS = {
    "测试",
    "验证",
    "运行测试",
    "执行测试",
    "运行验证",
    "执行验证",
    "node --check",
    "pytest",
    "unittest",
    "npm test",
    "pnpm test",
    "yarn test",
    "bun test",
    "test",
    "verify",
}


@dataclass
class GateDecision:
    """Deterministic B-pipeline gate result used to harden model plans."""

    needs_code_pipeline: bool
    edit_intent: bool
    test_intent: bool
    should_verify: bool
    risk_level: str
    reason: str
    applied_tasks: list[str] = field(default_factory=list)


@dataclass
class TaskRunRecord:
    """Serializable execution state for one planned task."""

    id: str
    title: str
    skill_id: str
    model: str
    mcp: list[str]
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    read_set: list[str] = field(default_factory=list)
    write_intent: list[str] = field(default_factory=list)
    status: str = "pending"
    output_preview: str = ""
    verification: str = ""
    error: str = ""


@dataclass
class PipelineRunState:
    """Minimal B-pipeline state object for recovery and observability."""

    user_request: str
    route_type: str
    reason: str
    tasks: list[TaskRunRecord]
    gate: GateDecision | None = None
    errors: list[str] = field(default_factory=list)
    run_context: RunContextStore | None = None
    event_bus: ExecutionEventBus = field(default_factory=ExecutionEventBus)
    output_controller: OutputController = field(default_factory=OutputController)
    model_labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        user_request: str,
        plan: PlannerResult,
        project_root: Path | None = None,
        mode: str = "",
        output_controller: OutputController | None = None,
        event_bus: ExecutionEventBus | None = None,
    ) -> "PipelineRunState":
        controller = output_controller or OutputController(mode=mode, route=plan.route_type)
        controller.configure(mode=mode, route=plan.route_type)
        return cls(
            user_request=user_request,
            route_type=plan.route_type,
            reason=plan.reason,
            tasks=[
                TaskRunRecord(
                    id=task.id,
                    title=task.title,
                    skill_id=task.skill_id,
                    model=task.model,
                    mcp=list(task.mcp),
                    depends_on=list(task.depends_on),
                    acceptance_criteria=list(task.acceptance_criteria),
                    expected_outputs=list(task.expected_outputs),
                    read_set=list(task.read_set),
                    write_intent=list(task.write_intent),
                )
                for task in plan.tasks
            ],
            run_context=RunContextStore(project_root) if project_root else None,
            event_bus=event_bus or ExecutionEventBus(),
            output_controller=controller,
        )

    def record_gate(self, decision: GateDecision) -> None:
        self.gate = decision
        for record in self.tasks:
            if record.id in decision.applied_tasks:
                record.mcp = sorted(set(record.mcp) | {"code_locator", "project_filesystem_readonly"})

    def record_task_started(self, task: PlannedTask) -> None:
        record = self._find_task(task.id)
        if record:
            record.status = "running"
        self.output_controller.enter_running(
            task_id=str(getattr(task, "id", "") or ""),
            reason=str(getattr(task, "title", "") or ""),
        )
        self.emit_event(
            "TaskStarted",
            str(getattr(task, "title", "") or getattr(task, "id", "") or "任务开始"),
            task_id=str(getattr(task, "id", "") or ""),
            status="running",
        )

    def record_task_result(self, task: PlannedTask, output: str) -> None:
        record = self._find_task(task.id)
        if not record:
            return
        record.status = "completed"
        record.output_preview = _preview(output)
        if self._all_tasks_terminal():
            if self.errors or any(str(getattr(item, "status", "")) == "failed" for item in self.tasks):
                self.output_controller.enter_failed("task failed")
            else:
                self.output_controller.enter_completed("tasks completed")
        self.emit_event(
            "TaskCompleted",
            str(getattr(task, "title", "") or getattr(task, "id", "") or "任务完成"),
            task_id=str(getattr(task, "id", "") or ""),
            status="completed",
        )

    def record_task_error(self, task: PlannedTask, error: Exception | str) -> None:
        record = self._find_task(task.id)
        message = str(error)
        if record:
            record.status = "failed"
            record.error = message
        self.errors.append(f"{task.id}: {message}")
        self.output_controller.enter_failed(message)
        self.emit_event(
            "TaskFailed",
            message,
            task_id=str(getattr(task, "id", "") or ""),
            status="failed",
            payload={"reason": message[:200]},
        )

    def record_fast_path_used(self, task: PlannedTask, *, tool: str, action: str) -> None:
        label = f"{tool} {action}".strip() or "只读快速路径"
        self.emit_event(
            "FastPathUsed",
            f"命中只读快速路径：{label}",
            task_id=str(getattr(task, "id", "") or ""),
            status="completed",
            payload={"tool": tool, "action": action},
        )

    def record_verification(self, task_id: str, report: str) -> None:
        record = self._find_task(task_id)
        if record:
            record.verification = report

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "route_type": self.route_type,
            "reason": self.reason,
            "gate": _gate_to_dict(self.gate) if self.gate else None,
            "tasks": [record.__dict__ for record in self.tasks],
            "errors": list(self.errors),
            "run_context": self.run_context.render_for_task() if self.run_context else "",
            "events": [event.to_dict() for event in self.event_bus.snapshot()],
            "output": self.output_controller.snapshot().to_dict(),
        }

    def _find_task(self, task_id: str) -> TaskRunRecord | None:
        for record in self.tasks:
            if record.id == task_id:
                return record
        return None

    def _all_tasks_terminal(self) -> bool:
        if not self.tasks:
            return False
        return all(str(getattr(record, "status", "")) in {"completed", "failed"} for record in self.tasks)

    def emit_event(self, event_type: str, message: str = "", **kwargs: Any):
        try:
            return self.event_bus.emit(event_type, message, **kwargs)
        except Exception:
            return None


def apply_pipeline_gate(plan: PlannerResult, refined_request: str) -> GateDecision:
    """Apply a small KWCode-style Gate pass to code tasks before validation."""

    text = _combined_plan_text(plan, refined_request)
    if _is_explicit_readonly_analysis(text):
        _ensure_readonly_locator_tools(plan)
        return GateDecision(
            needs_code_pipeline=False,
            edit_intent=False,
            test_intent=False,
            should_verify=False,
            risk_level="low",
            reason="用户明确要求只读分析或不要运行测试，无需代码流水线。",
        )

    code_tasks = [task for task in plan.tasks if _is_code_task(task)]
    is_code = bool(code_tasks) or _contains_any(text, CODE_MARKERS)
    code_text = "\n".join([refined_request, *(_task_text(task) for task in code_tasks)]).lower()
    declared_write_intent = any(getattr(task, "write_intent", []) for task in code_tasks)
    edit_intent = bool(code_tasks) and (_contains_any(code_text, EDIT_MARKERS) or declared_write_intent)
    test_intent = bool(code_tasks) and _contains_any(code_text, TEST_MARKERS)
    needs_code_pipeline = plan.route_type in {"single_agent", "multi_agent"} and is_code
    should_verify = needs_code_pipeline and (edit_intent or test_intent)

    decision = GateDecision(
        needs_code_pipeline=needs_code_pipeline,
        edit_intent=edit_intent,
        test_intent=test_intent,
        should_verify=should_verify,
        risk_level="medium" if edit_intent else "low",
        reason="代码任务需要 Gate/Locator/Verifier 骨架兜底。" if needs_code_pipeline else "无需代码流水线。",
    )

    if not needs_code_pipeline:
        return decision

    explicit_verification_commands = extract_explicit_verification_commands(text)

    for task in plan.tasks:
        if not _is_code_task(task):
            continue

        strategy = _strategy_for_task(task)
        task_text = _task_text(task).lower()
        task_edit_intent = bool(
            getattr(task, "write_intent", [])
            or (task.skill_id == "code_engineer" and _contains_any(task_text, EDIT_MARKERS))
        )
        task_test_intent = _contains_any(task_text, TEST_MARKERS)
        _append_unique(task.mcp, "code_locator")
        _append_unique(task.mcp, "project_filesystem_readonly")
        if edit_intent and task_edit_intent:
            _append_unique(task.mcp, "workspace_edit")

        if test_intent and (task_test_intent or task_edit_intent):
            _append_unique(task.mcp, "command_runner")

        task.instruction = _append_gate_instruction(
            task.instruction,
            decision,
            strategy,
            task_edit_intent=task_edit_intent,
            task_test_intent=task_test_intent,
            explicit_verification_commands=explicit_verification_commands,
        )
        task.risk_notes = _append_note(
            task.risk_notes,
            "Gate 已启用代码流水线兜底：先定位，再少量读取，修改后由 Verifier 做只读核验。",
        )
        task.risk_notes = _append_note(task.risk_notes, strategy.note_zh)
        decision.applied_tasks.append(task.id)

    return decision


def _ensure_readonly_locator_tools(plan: PlannerResult) -> None:
    for task in plan.tasks:
        if "project_filesystem_readonly" not in task.mcp or "code_locator" in task.mcp:
            continue
        if not _readonly_task_mentions_file_or_code(task):
            continue
        _append_unique(task.mcp, "code_locator")
        task.risk_notes = _append_note(
            task.risk_notes,
            "只读代码/文件分析已自动加入 code_locator，用于先定位后少量读取；未启用写入或命令工具。",
        )


def format_gate_decision(decision: GateDecision) -> str:
    if not decision.needs_code_pipeline:
        return "Gate：无需代码流水线。"
    tasks = ", ".join(decision.applied_tasks) if decision.applied_tasks else "无可应用任务"
    return (
        "Gate：已启用代码流水线兜底\n"
        f"- 风险等级：{decision.risk_level}\n"
        f"- 修改意图：{decision.edit_intent}\n"
        f"- 验证建议：{decision.should_verify}\n"
        f"- 应用任务：{tasks}"
    )


def should_verify_task(task: PlannedTask) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    if _is_explicit_readonly_analysis(text):
        return False
    return (
        task.skill_id == "code_engineer"
        and ("workspace_edit" in task.mcp or "command_runner" in task.mcp or _contains_any(text, EDIT_MARKERS))
    )


def build_verification_report(project_root: Path, task: PlannedTask) -> str:
    """Run a read-only Verifier pass after likely code modifications."""

    if not should_verify_task(task):
        return ""

    status = _run_git(project_root, ["status", "--short"])
    diff_stat = _run_git(project_root, ["diff", "--stat"])
    changed_files = _parse_status_files(status["stdout"]) if status["returncode"] == 0 else []

    lines = [
        "## Verifier 校验摘要",
        "- 已执行只读 git status / diff --stat 核验。",
    ]
    if status["returncode"] != 0:
        lines.append(f"- git status 失败：{status['stderr'] or status['stdout'] or '无详细输出'}")
    elif changed_files:
        lines.append("- 当前工作区改动文件：")
        lines.extend(f"  - {item}" for item in changed_files[:30])
        if len(changed_files) > 30:
            lines.append(f"  - ...另有 {len(changed_files) - 30} 个文件")
    else:
        lines.append("- 当前工作区没有检测到文件改动。")

    if diff_stat["returncode"] == 0 and diff_stat["stdout"].strip():
        lines.append("- diff --stat：")
        for line in diff_stat["stdout"].strip().splitlines()[:20]:
            lines.append(f"  {line}")
    elif diff_stat["returncode"] != 0:
        lines.append(f"- git diff --stat 失败：{diff_stat['stderr'] or diff_stat['stdout'] or '无详细输出'}")

    command_reports = _run_configured_verification_commands(project_root)
    if command_reports:
        lines.append("- Configured verification commands:")
        for report in command_reports:
            lines.append(f"  - command={report['command']}")
            lines.append(f"    returncode={report['returncode']}")
            if report["stdout"]:
                lines.append(f"    stdout={report['stdout']}")
            if report["stderr"]:
                lines.append(f"    stderr={report['stderr']}")
    elif "command_runner" not in task.mcp:
        lines.append("- 未自动运行测试命令；如需执行测试，请在任务中明确要求或让主脑加入 command_runner。")
    return "\n".join(lines)


def _run_configured_verification_commands(project_root: Path) -> list[dict[str, str | int]]:
    raw = str(os.environ.get("AGENTS_VERIFY_COMMANDS") or "").strip()
    if not raw:
        return []

    reports: list[dict[str, str | int]] = []
    commands = [item.strip() for item in raw.splitlines() if item.strip()]
    for command in commands:
        try:
            args = shlex.split(command, posix=True)
        except ValueError as exc:
            reports.append({"command": command, "returncode": 2, "stdout": "", "stderr": str(exc)})
            continue
        if not args:
            continue
        try:
            result = subprocess.run(
                args,
                cwd=project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
                shell=False,
            )
        except FileNotFoundError:
            reports.append({"command": command, "returncode": 127, "stdout": "", "stderr": "command not found"})
            continue
        except subprocess.TimeoutExpired:
            reports.append(
                {"command": command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
            )
            continue

        reports.append(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout": _preview(result.stdout.strip(), 400),
                "stderr": _preview(result.stderr.strip(), 400),
            }
        )
    return reports


def _gate_to_dict(decision: GateDecision) -> dict[str, Any]:
    return {
        "needs_code_pipeline": decision.needs_code_pipeline,
        "edit_intent": decision.edit_intent,
        "test_intent": decision.test_intent,
        "should_verify": decision.should_verify,
        "risk_level": decision.risk_level,
        "reason": decision.reason,
        "applied_tasks": list(decision.applied_tasks),
    }


def _preview(value: str, limit: int = 800) -> str:
    value = str(value)
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"


def _append_gate_instruction(
    instruction: str,
    decision: GateDecision,
    strategy: ModelExecutionStrategy | None = None,
    *,
    task_edit_intent: bool = False,
    task_test_intent: bool = False,
    explicit_verification_commands: list[str] | None = None,
) -> str:
    addition = (
        "\n\n## Gate 兜底要求\n"
        "- 这是代码流水线任务：先用 code_locator 定位，再少量读取目标文件。\n"
        "- 如果需要修改，优先小范围 patch/replace，不要整文件重写。\n"
        "- 完成后输出修改点、风险和验证建议。"
    )
    if strategy is not None:
        addition += (
            "\n"
            f"- 当前模型能力档位：{strategy.tier.value}；"
            f"最多读取 {strategy.max_files_per_task} 个核心文件，"
            f"单文件建议不超过 {strategy.max_read_chars_per_file} 字符，"
            f"总读取建议不超过 {strategy.max_total_read_chars} 字符。"
        )
        if strategy.force_plan_before_edit:
            addition += "\n- 该模型需要先列出简短修改计划，再执行文件修改。"
    if decision.edit_intent and not task_edit_intent:
        addition += "\n- 本任务未声明写入意图：保持只读定位，不要申请写入工具。"
    if decision.test_intent and (task_test_intent or task_edit_intent):
        addition += "\n- 用户表达了测试/验证意图，可在获得审批后运行必要测试命令。"
        command_lock = format_verification_command_lock(explicit_verification_commands or [])
        if command_lock:
            addition += f"\n- {command_lock}"
    if "## Gate 兜底要求" in instruction:
        return instruction
    return instruction + addition


def _strategy_for_task(task: PlannedTask) -> ModelExecutionStrategy:
    try:
        catalog = load_model_catalog()
    except Exception:
        return strategy_for_model_info({"model_name": task.model})
    model_infos = {item["id"]: item for item in catalog.get("models", [])}
    return strategy_for_model_info(model_infos.get(task.model) or {"model_name": task.model})


def _combined_plan_text(plan: PlannerResult, refined_request: str) -> str:
    parts = [refined_request, plan.refined_request, plan.reason]
    for task in plan.tasks:
        parts.extend([task.title, task.instruction])
    return "\n".join(str(item).lower() for item in parts if item)


def _is_code_task(task: PlannedTask) -> bool:
    if task.skill_id == "code_engineer":
        return True
    return "code_locator" in task.mcp or "workspace_edit" in task.mcp or "command_runner" in task.mcp


def _readonly_task_mentions_file_or_code(task: PlannedTask) -> bool:
    text = _task_text(task).lower()
    if CODE_FILE_PATTERN.search(text):
        return True
    return _contains_any(text, CODE_MARKERS)


def _task_text(task: PlannedTask) -> str:
    return "\n".join(
        [
            task.title,
            task.instruction,
            " ".join(task.write_intent),
            " ".join(getattr(task, "acceptance_criteria", []) or []),
            " ".join(getattr(task, "expected_outputs", []) or []),
        ]
    )


def _contains_any(text: str, markers: set[str]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _is_explicit_readonly_analysis(text: str) -> bool:
    lowered = text.lower()
    readonly_markers = [
        "不要修改",
        "不修改",
        "不要改文件",
        "不要运行测试",
        "不要运行",
        "只读",
        "read-only",
        "readonly",
        "do not modify",
        "do not edit",
        "do not run",
    ]
    analysis_markers = [
        "分析",
        "检查",
        "查看",
        "覆盖",
        "总结",
        "analyze",
        "inspect",
        "review",
        "coverage",
        "summary",
    ]
    edit_markers = [
        "需要修改",
        "请修改",
        "请修复",
        "可以修改",
        "允许修改",
        "需要修复",
        "实际修改",
        "修复",
        "修复代码",
        "写入文件",
        "创建文件",
        "删除文件",
        "fix",
        "repair",
        "please modify",
        "please edit",
        "fix the code",
    ]
    return (
        any(marker in lowered for marker in readonly_markers)
        and any(marker in lowered for marker in analysis_markers)
        and not any(marker in lowered for marker in edit_markers)
    )


def _append_unique(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return existing.rstrip() + " " + note


def _run_git(project_root: Path, args: list[str]) -> dict[str, str | int]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            shell=False,
        )
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "git executable was not found in PATH."}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "git command timed out."}
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _parse_status_files(stdout: str) -> list[str]:
    files = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        files.append(path)
    return files
