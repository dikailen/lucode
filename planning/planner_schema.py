from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from runtime.common.text_utils import sanitize_text


RouteType = Literal["direct_answer", "single_agent", "multi_agent", "clarify"]
ACTION_INTENT_MARKERS = (
    "检查",
    "修复",
    "修改",
    "创建",
    "删除",
    "实现",
    "重构",
    "运行",
    "测试",
    "接入",
    "优化",
    "改造",
    "评审",
    "写入",
    "编辑",
    "fix",
    "modify",
    "create",
    "delete",
    "implement",
    "refactor",
    "run",
    "test",
    "debug",
    "review",
    "edit",
    "optimize",
)


@dataclass
class RefinedRequest:
    raw_user_input: str
    refined_request: str
    explicit_constraints: list[str] = field(default_factory=list)
    possible_ambiguities: list[str] = field(default_factory=list)
    likely_intent: str = "mixed"


@dataclass
class PlannedTask:
    id: str
    title: str
    instruction: str
    skill_id: str
    model: str
    mcp: list[str] = field(default_factory=list)
    parallel_group: int = 1
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    read_set: list[str] = field(default_factory=list)
    write_intent: list[str] = field(default_factory=list)
    requires_unimplemented_mcp: bool = False
    risk_notes: str = ""


@dataclass
class PlannerResult:
    route_type: RouteType
    reason: str
    refined_request: str
    direct_answer_instruction: str = ""
    clarifying_question: str = ""
    tasks: list[PlannedTask] = field(default_factory=list)
    needs_synthesis: bool = False
    synthesis_instruction: str = ""
    memory_interface: dict[str, Any] = field(default_factory=dict)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, allowing accidental fenced blocks."""

    text = _strip_model_reasoning_noise(sanitize_text(text)).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def parse_refined_request(text: str, raw_user_input: str) -> RefinedRequest:
    try:
        data = parse_json_object(text)
    except json.JSONDecodeError:
        return RefinedRequest(
            raw_user_input=sanitize_text(raw_user_input),
            refined_request=sanitize_text(raw_user_input),
            explicit_constraints=[],
            possible_ambiguities=["query_refiner 未返回合法 JSON，已使用原始问题继续规划。"],
            likely_intent="mixed",
        )
    raw = sanitize_text(data.get("raw_user_input") or raw_user_input)
    refined = sanitize_text(data.get("refined_request") or raw_user_input)
    ambiguities = [sanitize_text(item) for item in list(data.get("possible_ambiguities") or [])]
    likely_intent = str(data.get("likely_intent") or "mixed")

    if _lost_action_intent(raw, refined):
        refined = f"{refined}\n\n保留原始执行意图：{raw}"
        ambiguities.append("query_refiner 的优化结果可能弱化了原始执行动作，已把原始请求并入优化问题。")
        if likely_intent == "explanation":
            likely_intent = "mixed"

    return RefinedRequest(
        raw_user_input=raw,
        refined_request=refined,
        explicit_constraints=[sanitize_text(item) for item in list(data.get("explicit_constraints") or [])],
        possible_ambiguities=ambiguities,
        likely_intent=likely_intent,
    )


def _lost_action_intent(raw_user_input: str, refined_request: str) -> bool:
    raw = raw_user_input.lower()
    refined = refined_request.lower()
    raw_has_action = any(marker in raw for marker in ACTION_INTENT_MARKERS)
    refined_has_action = any(marker in refined for marker in ACTION_INTENT_MARKERS)
    return raw_has_action and not refined_has_action


def parse_planner_result(text: str, fallback_user_input: str = "") -> PlannerResult:
    try:
        data = parse_json_object(text)
    except json.JSONDecodeError:
        return build_fallback_planner_result(fallback_user_input, text)
    tasks = [
        PlannedTask(
            id=str(item.get("id") or f"task_{index + 1}"),
            title=str(item.get("title") or item.get("id") or f"任务 {index + 1}"),
            instruction=str(item.get("instruction") or ""),
            skill_id=str(item.get("skill_id") or ""),
            model=str(item.get("model") or ""),
            mcp=list(item.get("mcp") or []),
            parallel_group=int(item.get("parallel_group") or 1),
            depends_on=[str(value) for value in list(item.get("depends_on") or []) if str(value).strip()],
            acceptance_criteria=[
                str(value) for value in list(item.get("acceptance_criteria") or []) if str(value).strip()
            ],
            expected_outputs=[str(value) for value in list(item.get("expected_outputs") or []) if str(value).strip()],
            read_set=[str(value) for value in list(item.get("read_set") or []) if str(value).strip()],
            write_intent=[str(value) for value in list(item.get("write_intent") or []) if str(value).strip()],
            requires_unimplemented_mcp=bool(item.get("requires_unimplemented_mcp") or False),
            risk_notes=str(item.get("risk_notes") or ""),
        )
        for index, item in enumerate(data.get("tasks") or [])
    ]

    route_type = data.get("route_type") or "clarify"
    if route_type not in {"direct_answer", "single_agent", "multi_agent", "clarify"}:
        route_type = "clarify"

    result = PlannerResult(
        route_type=route_type,
        reason=str(data.get("reason") or ""),
        refined_request=str(data.get("refined_request") or ""),
        direct_answer_instruction=str(data.get("direct_answer_instruction") or ""),
        clarifying_question=str(data.get("clarifying_question") or ""),
        tasks=tasks,
        needs_synthesis=bool(data.get("needs_synthesis") or False),
        synthesis_instruction=str(data.get("synthesis_instruction") or ""),
        memory_interface=dict(data.get("memory_interface") or {}),
    )
    return _normalize_planner_result(result, fallback_user_input=fallback_user_input)


def build_fallback_planner_result(raw_user_input: str, model_output: str = "") -> PlannerResult:
    """Build a conservative plan when a weaker/local planner ignores JSON instructions."""

    raw = _extract_current_turn_input(sanitize_text(raw_user_input)).strip()
    output = _strip_model_reasoning_noise(sanitize_text(model_output)).strip()
    text = f"{raw}\n{output}".lower()
    reason = "本地模型未返回合法 JSON，已启用兼容兜底规划。"

    if not raw:
        return PlannerResult(
            route_type="clarify",
            reason=reason,
            refined_request="",
            clarifying_question="主脑输出格式异常，而且缺少原始问题。请把问题再说具体一点。",
        )

    if _looks_like_simple_chat(raw, output):
        return PlannerResult(
            route_type="direct_answer",
            reason=reason,
            refined_request=raw,
            direct_answer_instruction=(
                "主脑规划模型输出了自然语言而不是 JSON。请直接用简洁中文回答用户；"
                "如果用户在询问能力范围，就简单介绍当前系统能做的事情。"
            ),
            tasks=[],
            needs_synthesis=False,
        )

    if _looks_like_skill_creation(text):
        mcp = ["skills_filesystem_readonly"]
        model = _fallback_model_for_skill("skill_creator", requires_tools=bool(mcp))
        if not model:
            return _fallback_no_tool_model_direct_answer(raw, "skill_creator")
        return _fallback_single_agent(
            raw,
            reason,
            skill_id="skill_creator",
            model=model,
            mcp=mcp,
            title="处理 Skill 创建或优化请求",
        )

    if _looks_like_project_or_code_readonly(text):
        skill_id = "jpc_now_skill" if _looks_like_code_edit_intent(raw, output) else "project_explorer"
        mcp = ["code_locator", "project_filesystem_readonly"]
        if skill_id == "jpc_now_skill":
            mcp.append("workspace_edit")
        model = _fallback_model_for_skill(skill_id, requires_tools=bool(mcp))
        if not model:
            return _fallback_no_tool_model_direct_answer(raw, skill_id)
        return _fallback_single_agent(
            raw,
            reason,
            skill_id=skill_id,
            model=model,
            mcp=mcp,
            title="处理项目或代码相关请求",
        )

    if _looks_like_writing(text):
        return _fallback_single_agent(
            raw,
            reason,
            skill_id="humanizer_zh",
            model=_fallback_model_for_skill("humanizer_zh", requires_tools=False) or _default_model_for_skill("humanizer_zh"),
            mcp=[],
            title="处理中文文本润色请求",
        )

    return PlannerResult(
        route_type="direct_answer",
        reason=reason,
        refined_request=raw,
        direct_answer_instruction=(
            "主脑规划模型输出了自然语言而不是 JSON。请直接用中文回答用户本轮问题；"
            "如果用户只是问候或询问能力，请简洁介绍当前系统能做的事。"
        ),
        tasks=[],
        needs_synthesis=False,
    )


def _fallback_single_agent(
    raw: str,
    reason: str,
    skill_id: str,
    model: str,
    mcp: list[str],
    title: str,
) -> PlannerResult:
    return PlannerResult(
        route_type="single_agent",
        reason=reason,
        refined_request=raw,
        tasks=[
            PlannedTask(
                id="fallback_task_1",
                title=title,
                instruction=(
                    "主脑规划模型没有返回合法 JSON。请按用户原始问题谨慎执行："
                    f"{raw}"
                ),
                skill_id=skill_id,
                model=model,
                mcp=mcp,
                parallel_group=1,
                risk_notes="兼容兜底规划：因为本地/弱模型没有严格遵守 JSON 输出格式，已采用保守单 Agent 路线。",
            )
        ],
        needs_synthesis=False,
    )


def _fallback_no_tool_model_direct_answer(raw: str, skill_id: str) -> PlannerResult:
    return PlannerResult(
        route_type="direct_answer",
        reason="本地模型未返回合法 JSON，且当前可用模型不支持工具调用，已降级为直接回答。",
        refined_request=raw,
        direct_answer_instruction=(
            "当前可用本地模型不支持工具调用，无法直接使用 MCP/Skill 工具执行该任务。"
            f"请基于用户问题给出可执行建议，并说明如果要让 {skill_id} 使用 MCP，"
            "需要换用支持 tools/function calling 的模型，或在隐私模式允许时配置云端模型。"
        ),
        tasks=[],
        needs_synthesis=False,
    )


def _normalize_planner_result(result: PlannerResult, fallback_user_input: str = "") -> PlannerResult:
    for task in result.tasks:
        task.model = _normalize_model_reference(task.model, task.skill_id)

    web_search_text = "\n".join(
        [
            result.refined_request,
            fallback_user_input,
        ]
    )
    if result.route_type == "direct_answer" and _needs_web_search(web_search_text):
        model = _fallback_model_for_skill("project_explorer", requires_tools=True) or _default_model_for_skill("project_explorer")
        result.route_type = "single_agent"
        result.reason = (result.reason + " 已根据用户明确联网/搜索需求改为 web_search 路线。").strip()
        result.direct_answer_instruction = ""
        result.tasks = [
            PlannedTask(
                id="web_search_task",
                title="联网检索外部资料",
                instruction=(
                    "用户明确要求联网、搜索、官方链接或最新外部资料。"
                    "请优先使用 web_search；如果用户要求只返回链接，就不要调用 web_fetch，也不要扩写摘要。"
                    f"用户请求：{result.refined_request}"
                ),
                skill_id="project_explorer",
                model=model,
                mcp=["web_search"],
                parallel_group=1,
                acceptance_criteria=["返回与用户请求相关的搜索结果或说明搜索失败原因"],
                expected_outputs=["搜索结果、官方链接或结构化失败原因"],
                risk_notes="联网搜索会向搜索 Provider 发送查询词；隐私模式会在执行前校验是否允许。",
            )
        ]
        result.needs_synthesis = False
        result.synthesis_instruction = ""

    if result.route_type == "single_agent" and _needs_web_search(web_search_text):
        _preserve_web_search_constraints(result, fallback_user_input)

    if result.route_type == "multi_agent":
        if not result.needs_synthesis:
            result.needs_synthesis = True
        if not result.synthesis_instruction.strip():
            result.synthesis_instruction = (
                "请读取所有任务输出，按任务顺序整合结论；"
                "如果存在依赖关系，优先采用后续任务基于前序结果形成的最终表述；"
                "保留关键信息，不要编造未出现的事实。"
            )
    return result


def _preserve_web_search_constraints(result: PlannerResult, fallback_user_input: str) -> None:
    raw = str(fallback_user_input or "")
    lowered = raw.lower()
    for task in result.tasks:
        if "web_search" not in task.mcp:
            continue
        if any(marker in lowered for marker in ["只返回链接", "只要链接", "return only links", "only links"]):
            addition = "用户原始请求明确要求只返回链接；只调用 web_search，不要调用 web_fetch，不要输出解释、摘要或额外文字。"
            if addition not in task.instruction:
                task.instruction = task.instruction.rstrip() + "\n" + addition
            if "只返回链接" not in task.acceptance_criteria:
                task.acceptance_criteria.append("只返回链接")
            if "纯链接文本" not in task.expected_outputs:
                task.expected_outputs.append("纯链接文本")
        if any(marker in lowered for marker in ["不改文件", "不要改文件", "不要修改", "do not edit", "do not modify"]):
            task.write_intent = []
            task.mcp = [mcp_id for mcp_id in task.mcp if mcp_id != "workspace_edit"]
            note = "用户原始请求明确要求不修改文件。"
            if note not in task.risk_notes:
                task.risk_notes = (task.risk_notes + " " + note).strip()


def _needs_web_search(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False

    if _looks_like_capability_question(lowered) and not _has_explicit_web_search_command(lowered):
        return False

    if _has_explicit_web_search_command(lowered):
        return True

    weak_markers = ["最新", "官方", "文档", "资料"]
    if any(marker in lowered for marker in weak_markers) and any(
        marker in lowered for marker in ["查", "找", "搜索", "检索", "链接"]
    ):
        return True

    return bool(
        re.search(r"\b(latest|urls?|links?)\b", lowered)
        and re.search(r"\b(search|find|look up|browse|official|docs?|documentation|return|only)\b", lowered)
    )


def _looks_like_capability_question(text: str) -> bool:
    capability_markers = [
        "你有什么技能",
        "你具备的功能",
        "你有哪些功能",
        "你会什么",
        "你能做什么",
        "能帮我做什么",
        "介绍你",
        "介绍一下你",
        "what can you do",
        "your capabilities",
        "what are your skills",
    ]
    return any(marker in text for marker in capability_markers)


def _has_explicit_web_search_command(text: str) -> bool:
    strong_markers = [
        "请联网",
        "帮我联网",
        "使用联网",
        "用联网",
        "联网查",
        "联网找",
        "联网搜索一下",
        "联网搜一下",
        "上网搜",
        "网上搜",
        "搜索一下",
        "检索",
        "查一下",
        "找一下",
        "官方链接",
        "官方文档",
        "只返回链接",
        "只要链接",
        "web search",
        "search the web",
        "browse the web",
        "official link",
        "official docs",
        "return only links",
        "only links",
    ]
    if any(marker in text for marker in strong_markers):
        return True
    return bool(
        re.search(r"\b(search|find|look up|browse)\b.{0,60}\b(web|internet|official|docs?|documentation|links?|urls?|latest)\b", text)
    )


def _normalize_model_reference(model_id: str, skill_id: str = "") -> str:
    model_id = str(model_id or "").strip()
    if not model_id:
        return model_id

    try:
        from catalog_system.model_catalog import load_model_catalog
        from runtime.safety.privacy import PrivacyPolicy
    except Exception:
        return model_id

    try:
        catalog = load_model_catalog()
    except Exception:
        return model_id

    models = {item.get("id"): item for item in catalog.get("models", []) if item.get("id")}
    if model_id in models:
        return model_id

    policy = PrivacyPolicy.from_env()
    aliases = _legacy_model_alias_candidates(model_id, skill_id)
    for candidate in policy.sort_model_ids(aliases, models):
        info = models.get(candidate)
        if _model_usable_for_task(info, policy, requires_tools=False):
            return candidate

    return model_id


def _legacy_model_alias_candidates(model_id: str, skill_id: str = "") -> list[str]:
    aliases = {
        "mimo_model": [
            "mimo_v25_pro_model",
            "mimo_v25_model",
        ],
        "deepseek_V4_flash_model": [
            "deepseek_v4_flash_model",
            "deepseek_v4_pro_model",
        ],
        "deepseek_V4_pro_model": [
            "deepseek_v4_pro_model",
            "deepseek_v4_flash_model",
        ],
    }
    candidates = list(aliases.get(model_id, []))
    candidates.extend(_preferred_models_for_skill(skill_id))
    return _dedupe(candidates)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _strip_model_reasoning_noise(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _extract_current_turn_input(raw_user_input: str) -> str:
    marker = "本轮用户问题："
    if marker in raw_user_input:
        return raw_user_input.rsplit(marker, 1)[1].strip()
    return raw_user_input


def _looks_like_project_or_code_readonly(text: str) -> bool:
    markers = [
        "项目",
        "代码",
        "文件",
        "目录",
        "定位",
        "查找",
        "分析",
        "检查",
        "函数",
        "类",
        "mcp",
        "git",
        "project",
        "code",
        "file",
        "locate",
        "find",
        "analyze",
        "review",
    ]
    return any(marker in text for marker in markers)


def _looks_like_code_edit_intent(raw: str, output: str) -> bool:
    combined = f"{raw}\n{output}".lower()
    if any(marker in combined for marker in ["先不要修改", "不要修改", "只分析", "先分析", "readonly", "read only"]):
        return False
    return any(marker in raw.lower() for marker in ACTION_INTENT_MARKERS)


def _looks_like_skill_creation(text: str) -> bool:
    return any(marker in text for marker in ["skill", "技能", "skil"])


def _looks_like_writing(text: str) -> bool:
    return any(marker in text for marker in ["润色", "改写", "人味", "中文表达", "humanizer", "rewrite"])


def _looks_like_simple_chat(raw: str, output: str) -> bool:
    raw_lower = raw.lower()
    output_lower = output.lower()
    chat_markers = ["你好", "hello", "hi", "介绍一下", "你能帮我", "你会什么", "能做什么"]
    code_markers = ["代码", "项目", "文件", "mcp", "skill", "修复", "实现", "定位", "测试", "code", "project"]
    return any(marker in raw_lower for marker in chat_markers) and not any(marker in raw_lower for marker in code_markers) and (
        not output_lower or any(marker in output_lower for marker in ["可以帮你", "回答", "分析", "运行测试"])
    )


def _fallback_model_for_skill(skill_id: str, requires_tools: bool = False) -> str | None:
    try:
        from catalog_system.model_catalog import load_model_catalog
        from runtime.safety.privacy import PrivacyPolicy
    except Exception:
        return _default_model_for_skill(skill_id)

    policy = PrivacyPolicy.from_env()
    catalog = load_model_catalog()
    models = {item["id"]: item for item in catalog.get("models", [])}
    preferred = _preferred_models_for_skill(skill_id)
    for model_id in policy.sort_model_ids(preferred, models):
        info = models.get(model_id)
        if _model_usable_for_task(info, policy, requires_tools):
            return model_id
    for model_id, info in sorted(models.items()):
        if _model_usable_for_task(info, policy, requires_tools):
            return model_id
    return None if requires_tools else _default_model_for_skill(skill_id)


def _preferred_models_for_skill(skill_id: str) -> list[str]:
    env_value = os.environ.get("AGENTS_FALLBACK_MODEL_PRIORITY") or ""
    env_models = [item.strip() for item in env_value.split(",") if item.strip()]
    if skill_id == "jpc_now_skill":
        return env_models + ["mimo_model", "cloud_model", "deepseek_V4_pro_model", "deepseek_V4_flash_model", "local_model"]
    if skill_id == "skill_creator":
        return env_models + ["deepseek_V4_pro_model", "cloud_model", "mimo_model", "deepseek_V4_flash_model", "local_model"]
    return env_models + ["cloud_model", "deepseek_V4_flash_model", "deepseek_V4_pro_model", "mimo_model", "local_model"]


def _model_usable_for_task(info: dict | None, policy, requires_tools: bool) -> bool:
    if not info:
        return False
    if not info.get("configured"):
        return False
    if not policy.model_allowed(info):
        return False
    if requires_tools and not info.get("supports_tools", True):
        return False
    return True


def _default_model_for_skill(skill_id: str) -> str:
    if skill_id == "jpc_now_skill":
        return "mimo_model"
    if skill_id == "skill_creator":
        return "deepseek_V4_pro_model"
    return "deepseek_V4_flash_model"
