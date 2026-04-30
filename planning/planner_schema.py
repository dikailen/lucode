from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal


RouteType = Literal["direct_answer", "single_agent", "multi_agent", "clarify"]


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

    text = text.strip()
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
            raw_user_input=raw_user_input,
            refined_request=raw_user_input,
            explicit_constraints=[],
            possible_ambiguities=["query_refiner 未返回合法 JSON，已使用原始问题继续规划。"],
            likely_intent="mixed",
        )
    return RefinedRequest(
        raw_user_input=str(data.get("raw_user_input") or raw_user_input),
        refined_request=str(data.get("refined_request") or raw_user_input),
        explicit_constraints=list(data.get("explicit_constraints") or []),
        possible_ambiguities=list(data.get("possible_ambiguities") or []),
        likely_intent=str(data.get("likely_intent") or "mixed"),
    )


def parse_planner_result(text: str) -> PlannerResult:
    try:
        data = parse_json_object(text)
    except json.JSONDecodeError:
        return PlannerResult(
            route_type="clarify",
            reason="orchestrator_planner 未返回合法 JSON，无法安全执行。",
            refined_request="",
            clarifying_question="主脑规划输出格式异常。请把问题再说具体一点，或稍后重试。",
        )
    tasks = [
        PlannedTask(
            id=str(item.get("id") or f"task_{index + 1}"),
            title=str(item.get("title") or item.get("id") or f"任务 {index + 1}"),
            instruction=str(item.get("instruction") or ""),
            skill_id=str(item.get("skill_id") or ""),
            model=str(item.get("model") or ""),
            mcp=list(item.get("mcp") or []),
            parallel_group=int(item.get("parallel_group") or 1),
            requires_unimplemented_mcp=bool(item.get("requires_unimplemented_mcp") or False),
            risk_notes=str(item.get("risk_notes") or ""),
        )
        for index, item in enumerate(data.get("tasks") or [])
    ]

    route_type = data.get("route_type") or "clarify"
    if route_type not in {"direct_answer", "single_agent", "multi_agent", "clarify"}:
        route_type = "clarify"

    return PlannerResult(
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
