from __future__ import annotations

import inspect
import re
from pathlib import Path

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agents.factory import AgentFactory
from runtime.config.settings import RuntimeSettings
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.inline_context import _inline_project_file_context
from runtime.execution.run_context import RunContextStore
from runtime.execution.skill_matcher import render_matching_user_skill_context
from runtime.safety.privacy import PrivacyPolicy
from runtime.ui.capabilities import detect_dynamic_ui_capability, normalize_dynamic_ui_mode
from runtime.ui.live_status import dynamic_status
from runtime.ui.rich_live_runtime import RichLiveRuntime


SOLO_READONLY_BUDGET_PROFILE = {
    "max_read_calls": "6",
    "max_files_per_call": "3",
    "max_chars_per_file": "4500",
    "max_total_chars": "18000",
    "max_tree_depth": "2",
    "max_tree_entries": "180",
}

READONLY_MCP_IDS = ["project_filesystem_readonly", "code_locator", "git_tools"]
EDIT_MCP_IDS = ["workspace_edit", "safe_backup"]
COMMAND_MCP_IDS = ["command_runner"]
WEB_MCP_IDS = ["web_search"]
CONTEXT7_MCP_IDS = ["context7_docs"]
GREP_MCP_IDS = ["grep_code_search"]

READ_MARKERS = [
    "分析",
    "查看",
    "读取",
    "检查",
    "解释",
    "项目",
    "代码",
    "文件",
    "目录",
    "函数",
    "类",
    "git",
    "diff",
    "status",
    ".py",
    ".md",
    ".json",
    "project",
    "code",
    "file",
    "directory",
    "function",
    "class",
]

EDIT_MARKERS = [
    "修改",
    "修复",
    "新增",
    "创建",
    "删除",
    "重构",
    "改造",
    "写入",
    "替换",
    "patch",
    "edit",
    "fix",
    "modify",
    "create",
    "delete",
    "refactor",
]

COMMAND_MARKERS = [
    "运行",
    "测试",
    "执行",
    "命令",
    "pytest",
    "unittest",
    "python ",
    "pip ",
    "command",
]
COMMAND_WORD_MARKERS = [
    "run",
    "test",
    "tests",
    "pytest",
    "unittest",
    "command",
]
NO_COMMAND_MARKERS = [
    "不要运行",
    "不运行",
    "无需运行",
    "不要执行",
    "不执行",
    "无需执行",
    "不要测试",
    "不测试",
    "无需测试",
    "do not run",
    "don't run",
    "no run",
    "without running",
    "do not test",
    "don't test",
    "no test",
]

WEB_MARKERS = [
    "联网",
    "搜索",
    "检索",
    "最新",
    "官方文档",
    "官方链接",
    "web search",
    "search the web",
    "latest",
    "official docs",
]
CONTEXT7_MARKERS = [
    "context7",
    "context 7",
]
GREP_MARKERS = [
    "grep by vercel",
    "mcp.grep.app",
    "github code",
    "github snippets",
    "code snippets",
    "公开代码",
]

NO_EDIT_MARKERS = [
    "不要修改",
    "不修改",
    "无需修改",
    "只读",
    "read only",
    "readonly",
]


async def run_solo_request(
    run_input: str,
    model_registry,
    mcp_manager,
    hooks,
    run_agent,
    settings: RuntimeSettings | None = None,
    project_root: Path | None = None,
) -> str:
    """Run one tool-capable Agent without planner/refiner/synthesizer."""

    settings = settings or RuntimeSettings.from_env()
    model_id = settings.select_model_id(model_registry, "executor")
    factory = AgentFactory(model_registry, mcp_manager=mcp_manager)
    mcp_ids = _solo_mcp_ids_for_input(run_input, settings)
    if "project_filesystem_readonly" in mcp_ids:
        mcp_manager.set_readonly_budget_profile("project_filesystem_readonly", SOLO_READONLY_BUDGET_PROFILE)
    run_context = RunContextStore(project_root) if project_root else None
    run_input = _solo_input_with_inline_context(run_input, model_id, project_root, run_context)
    servers = await mcp_manager.get_many(mcp_ids)
    agent = factory.create_solo_agent(model_id, mcp_servers=servers)
    rich_runtime, rich_state, rich_task = _start_solo_rich_live(
        run_input,
        model_registry=model_registry,
        model_id=model_id,
        mcp_ids=mcp_ids,
        settings=settings,
        project_root=project_root,
    )
    rich_started = rich_runtime is not None and rich_state is not None
    try:
        with dynamic_status(
            "solo agent",
            mode=settings.execution_mode,
            stage="worker",
            enabled=not rich_started,
        ):
            result = await run_agent(
                agent,
                run_input,
                hooks,
                **_solo_run_agent_kwargs(run_agent, rich_started=rich_started),
            )
        if rich_runtime is not None and rich_state is not None and rich_task is not None:
            rich_state.record_task_result(rich_task, str(result.final_output))
            rich_runtime.refresh(rich_state, mode=settings.execution_mode, attempt=1, active="Completed")
    except Exception:
        if rich_state is not None and rich_task is not None:
            rich_state.record_task_error(rich_task, "solo execution failed")
        raise
    finally:
        if rich_runtime is not None:
            rich_runtime.stop()
    summary = run_context.render_for_task() if run_context else ""
    return SoloExecutionResult(str(result.final_output), run_context_summary=summary)


class SoloExecutionResult(str):
    """String-compatible solo output with optional shared context metadata."""

    def __new__(cls, value: str, *, run_context_summary: str = ""):
        obj = str.__new__(cls, str(value or ""))
        obj.run_context_summary = str(run_context_summary or "")
        return obj


def _solo_input_with_inline_context(
    run_input: str,
    model_id: str,
    project_root: Path | None,
    run_context: RunContextStore | None,
) -> str:
    if project_root is None or run_context is None:
        return run_input
    task = PlannedTask(
        id="solo_context",
        title="solo inline context",
        instruction=run_input,
        skill_id="project_explorer",
        model=model_id,
        mcp=["project_filesystem_readonly"],
    )
    inline_context = _inline_project_file_context(project_root, task, run_input, run_context=run_context)
    skill_context = render_matching_user_skill_context(run_input, workspace_context=_solo_workspace_context(project_root))
    context_blocks = []
    if skill_context.strip():
        context_blocks.append(skill_context)
    if inline_context.strip():
        context_blocks.append(
            "下面是系统已经只读读取到的项目文件片段，请基于这些真实上下文回答：\n"
            f"{inline_context}"
        )
    if not context_blocks:
        return run_input
    joined_context = "\n\n".join(context_blocks)
    return (
        f"{run_input}\n\n"
        "下面是 Lucode 为本轮 solo 请求准备的可复用上下文：\n"
        f"{joined_context}"
    )


def _solo_mcp_ids_for_input(user_input: str, settings: RuntimeSettings) -> list[str]:
    text = str(user_input or "").lower()
    edit_blocked = _contains_any(text, NO_EDIT_MARKERS)
    command_blocked = _contains_any(text, NO_COMMAND_MARKERS)
    wants_edit = _contains_any(text, EDIT_MARKERS) and not edit_blocked
    wants_command = (
        _contains_any(text, COMMAND_MARKERS) or _contains_any_word(text, COMMAND_WORD_MARKERS)
    ) and not command_blocked
    wants_web = _contains_any(text, WEB_MARKERS)
    wants_context7 = _contains_any(text, CONTEXT7_MARKERS)
    wants_grep = _contains_any(text, GREP_MARKERS) or ("grep" in text and "github" in text)
    wants_read = wants_edit or wants_command or wants_web or wants_context7 or wants_grep or _contains_any(text, READ_MARKERS)

    mcp_ids: list[str] = []
    if wants_read:
        mcp_ids.extend(READONLY_MCP_IDS)
    if wants_edit:
        mcp_ids.extend(EDIT_MCP_IDS)
    if wants_command:
        mcp_ids.extend(COMMAND_MCP_IDS)
    if wants_web and settings.privacy_mode != "offline" and PrivacyPolicy(settings.privacy_mode).allows_network_tools:
        mcp_ids.extend(WEB_MCP_IDS)
    if wants_context7 and settings.privacy_mode != "offline" and PrivacyPolicy(settings.privacy_mode).allows_network_tools:
        mcp_ids.extend(CONTEXT7_MCP_IDS)
    if wants_grep and settings.privacy_mode != "offline" and PrivacyPolicy(settings.privacy_mode).allows_network_tools:
        mcp_ids.extend(GREP_MCP_IDS)

    return _dedupe(mcp_ids)


def _start_solo_rich_live(
    run_input: str,
    *,
    model_registry,
    model_id: str,
    mcp_ids: list[str],
    settings: RuntimeSettings,
    project_root: Path | None,
) -> tuple[RichLiveRuntime | None, PipelineRunState | None, PlannedTask | None]:
    if not _should_use_solo_rich_live():
        return None, None, None

    task = PlannedTask(
        id="solo_agent",
        title="Solo request",
        instruction=run_input,
        skill_id="solo",
        model=model_id,
        mcp=list(mcp_ids),
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="solo mode",
        refined_request=run_input,
        tasks=[task],
    )
    run_state = PipelineRunState.create(
        run_input,
        plan,
        project_root=project_root,
        mode=settings.execution_mode,
    )
    if project_root is not None:
        setattr(run_state, "project_root", Path(project_root))
    run_state.model_labels = _solo_model_label_map(model_registry, [model_id])
    run_state.record_task_started(task)

    runtime = RichLiveRuntime(enabled=True)
    if runtime.refresh(run_state, mode=settings.execution_mode, attempt=1, active="Answering request"):
        return runtime, run_state, task
    return None, run_state, task


def _should_use_solo_rich_live() -> bool:
    dynamic_mode = normalize_dynamic_ui_mode()
    if dynamic_mode == "off":
        return False
    if dynamic_mode == "on":
        return True
    return bool(detect_dynamic_ui_capability().enabled)


def _solo_model_label_map(model_registry, model_ids: list[str]) -> dict[str, str]:
    try:
        from runtime.execution.dynamic import _model_label_map

        return _model_label_map(model_registry, model_ids)
    except Exception:
        return {str(model_id): str(model_id) for model_id in model_ids if str(model_id or "").strip()}


def _solo_run_agent_kwargs(run_agent, *, rich_started: bool) -> dict:
    kwargs = {"max_turns": 20}
    if rich_started and _run_agent_accepts_stream_output(run_agent):
        kwargs["stream_output"] = False
    return kwargs


def _run_agent_accepts_stream_output(run_agent) -> bool:
    try:
        parameters = inspect.signature(run_agent).parameters
    except (TypeError, ValueError):
        return True
    if "stream_output" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


class _SoloWorkspaceContext:
    def __init__(self, project_root: Path):
        self.workspace_root = project_root
        self.app_home = Path(__file__).resolve().parents[2]
        self.user_home = None


def _solo_workspace_context(project_root: Path | None):
    if project_root is None:
        return None
    return _SoloWorkspaceContext(project_root)


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(marker.lower() in text for marker in markers)


def _contains_any_word(text: str, markers: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(marker.lower())}(?![a-z0-9_])", text) for marker in markers)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
