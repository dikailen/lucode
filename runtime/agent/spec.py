from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.config.model_config import iter_model_roles, model_role_label, normalize_model_role


ROLE_CAPABILITY_DEFAULTS = {
    "query_refiner": ["chat", "stream"],
    "orchestrator": ["chat", "json", "planner"],
    "executor": ["chat", "tools", "stream"],
    "final_synthesizer": ["chat", "long_context"],
}

ROLE_CONTEXT_DEFAULTS = {
    "query_refiner": "short",
    "orchestrator": "large",
    "executor": "medium",
    "final_synthesizer": "large",
}


@dataclass(frozen=True)
class BrainSpec:
    """Contract for one Lucode brain role without changing runtime behavior."""

    role: str
    display_name: str
    model_priority: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    context_budget: str = "medium"
    fallback_policy: list[str] = field(default_factory=list)
    ui_badges: list[str] = field(default_factory=list)

    @classmethod
    def from_runtime_settings(cls, settings, role: str) -> "BrainSpec":
        role_id = normalize_model_role(role)
        priority = list(settings.model_priority_for(role_id))
        required = list(ROLE_CAPABILITY_DEFAULTS.get(role_id, ["chat"]))
        return cls(
            role=role_id,
            display_name=model_role_label(role_id),
            model_priority=priority,
            required_capabilities=required,
            context_budget=ROLE_CONTEXT_DEFAULTS.get(role_id, "medium"),
            fallback_policy=priority[1:],
            ui_badges=_badges_for_capabilities(required),
        )

    @classmethod
    def all_from_runtime_settings(cls, settings) -> list["BrainSpec"]:
        return [cls.from_runtime_settings(settings, role_id) for role_id, _ in iter_model_roles()]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrainSpec":
        return cls(
            role=normalize_model_role(str(data.get("role") or "")),
            display_name=str(data.get("display_name") or ""),
            model_priority=_string_list(data.get("model_priority")),
            required_capabilities=_string_list(data.get("required_capabilities")),
            context_budget=str(data.get("context_budget") or "medium"),
            fallback_policy=_string_list(data.get("fallback_policy")),
            ui_badges=_string_list(data.get("ui_badges")),
        )


@dataclass(frozen=True)
class TaskSpec:
    """Structured task contract that can be mapped from existing PlannedTask."""

    task_id: str
    goal: str
    mode_hint: str = "solo"
    read_intent: list[str] = field(default_factory=list)
    write_intent: list[str] = field(default_factory=list)
    toolset_id: str = "general_agent"
    required_context: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    risk_level: str = "low"
    expected_outputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    skill_id: str = ""
    model: str = ""
    mcp: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_planned_task(cls, task, *, mode_hint: str = "solo") -> "TaskSpec":
        read_intent = _string_list(getattr(task, "read_set", []))
        write_intent = _string_list(getattr(task, "write_intent", []))
        mcp = _string_list(getattr(task, "mcp", []))
        toolset_id = _infer_toolset_id(read_intent=read_intent, write_intent=write_intent, mcp=mcp)
        risk_level = _infer_risk_level(write_intent=write_intent, mcp=mcp)
        return cls(
            task_id=str(getattr(task, "id", "") or ""),
            goal=str(getattr(task, "instruction", "") or getattr(task, "title", "") or ""),
            mode_hint=str(mode_hint or "solo"),
            read_intent=read_intent,
            write_intent=write_intent,
            toolset_id=toolset_id,
            required_context=[],
            acceptance_criteria=_string_list(getattr(task, "acceptance_criteria", [])),
            risk_level=risk_level,
            expected_outputs=_string_list(getattr(task, "expected_outputs", [])),
            dependencies=_string_list(getattr(task, "depends_on", [])),
            skill_id=str(getattr(task, "skill_id", "") or ""),
            model=str(getattr(task, "model", "") or ""),
            mcp=mcp,
            notes=str(getattr(task, "risk_notes", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        return cls(
            task_id=str(data.get("task_id") or ""),
            goal=str(data.get("goal") or ""),
            mode_hint=str(data.get("mode_hint") or "solo"),
            read_intent=_string_list(data.get("read_intent")),
            write_intent=_string_list(data.get("write_intent")),
            toolset_id=str(data.get("toolset_id") or "general_agent"),
            required_context=_string_list(data.get("required_context")),
            acceptance_criteria=_string_list(data.get("acceptance_criteria")),
            risk_level=str(data.get("risk_level") or "low"),
            expected_outputs=_string_list(data.get("expected_outputs")),
            dependencies=_string_list(data.get("dependencies")),
            skill_id=str(data.get("skill_id") or ""),
            model=str(data.get("model") or ""),
            mcp=_string_list(data.get("mcp")),
            notes=str(data.get("notes") or ""),
        )


@dataclass(frozen=True)
class ToolsetPolicy:
    """Declarative tool routing and approval policy."""

    toolset_id: str
    read_route: str = "native_preferred"
    read_approval: str = "none"
    edit_route: str = "workspace_tools"
    edit_approval: str = "required"
    terminal_route: str = "command_analyzer"
    terminal_approval: str = "ask_or_sandbox"
    git_route: str = "readonly_native_mutating_ask"
    web_route: str = "mcp_with_privacy_notice"
    mcp_fallback: str = "external_protocol_or_native_unavailable"

    @classmethod
    def readonly_project_analysis(cls) -> "ToolsetPolicy":
        return cls(toolset_id="readonly_project_analysis")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolsetPolicy":
        return cls(
            toolset_id=str(data.get("toolset_id") or "general_agent"),
            read_route=str(data.get("read_route") or "native_preferred"),
            read_approval=str(data.get("read_approval") or "none"),
            edit_route=str(data.get("edit_route") or "workspace_tools"),
            edit_approval=str(data.get("edit_approval") or "required"),
            terminal_route=str(data.get("terminal_route") or "command_analyzer"),
            terminal_approval=str(data.get("terminal_approval") or "ask_or_sandbox"),
            git_route=str(data.get("git_route") or "readonly_native_mutating_ask"),
            web_route=str(data.get("web_route") or "mcp_with_privacy_notice"),
            mcp_fallback=str(data.get("mcp_fallback") or "external_protocol_or_native_unavailable"),
        )


@dataclass(frozen=True)
class ContextContract:
    """Context layer contract shared by session, history, and multi-agent execution."""

    hot_context: list[str] = field(default_factory=list)
    evidence_context: list[str] = field(default_factory=list)
    rule_context: list[str] = field(default_factory=list)
    cold_context: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextContract":
        return cls(
            hot_context=_string_list(data.get("hot_context")),
            evidence_context=_string_list(data.get("evidence_context")),
            rule_context=_string_list(data.get("rule_context")),
            cold_context=_string_list(data.get("cold_context")),
            artifact_refs=_string_list(data.get("artifact_refs")),
        )


@dataclass(frozen=True)
class ProviderRuntimeSpec:
    """Resolved provider/model runtime contract without exposing secrets."""

    provider_id: str
    homepage: str = ""
    base_url: str = ""
    api_key_ref: str = ""
    model_name: str = ""
    api_mode: str = "openai_compatible"
    capability_fingerprint: dict[str, Any] = field(default_factory=dict)
    fallback_models: list[str] = field(default_factory=list)
    auxiliary_models: dict[str, str] = field(default_factory=dict)
    source: str = ""
    model_id: str = ""
    provider_ref: str = ""

    @classmethod
    def from_model_info(
        cls,
        model_info: dict[str, Any],
        *,
        fallback_models: list[str] | None = None,
        auxiliary_models: dict[str, str] | None = None,
    ) -> "ProviderRuntimeSpec":
        provider_id = str(model_info.get("provider") or model_info.get("provider_id") or "").strip()
        provider_ref = str(model_info.get("provider_ref") or "").strip()
        model_name = str(
            model_info.get("model_name")
            or model_info.get("model_name_value")
            or model_info.get("model")
            or ""
        ).strip()
        if not provider_id and provider_ref and "/" in provider_ref:
            provider_id = provider_ref.split("/", 1)[0]
        if not model_name and provider_ref and "/" in provider_ref:
            model_name = provider_ref.split("/", 1)[1]
        return cls(
            provider_id=provider_id,
            homepage=str(model_info.get("homepage") or ""),
            base_url=str(model_info.get("base_url_value") or model_info.get("base_url") or ""),
            api_key_ref=str(model_info.get("api_key_ref") or ""),
            model_name=model_name,
            api_mode=str(model_info.get("api_mode") or model_info.get("backend_type") or "openai_compatible"),
            capability_fingerprint=dict(model_info.get("probe") or {}),
            fallback_models=list(fallback_models or []),
            auxiliary_models=dict(auxiliary_models or {}),
            source=str(model_info.get("source") or ""),
            model_id=str(model_info.get("id") or ""),
            provider_ref=provider_ref,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderRuntimeSpec":
        return cls(
            provider_id=str(data.get("provider_id") or ""),
            homepage=str(data.get("homepage") or ""),
            base_url=str(data.get("base_url") or ""),
            api_key_ref=str(data.get("api_key_ref") or ""),
            model_name=str(data.get("model_name") or ""),
            api_mode=str(data.get("api_mode") or "openai_compatible"),
            capability_fingerprint=dict(data.get("capability_fingerprint") or {}),
            fallback_models=_string_list(data.get("fallback_models")),
            auxiliary_models={str(key): str(value) for key, value in dict(data.get("auxiliary_models") or {}).items()},
            source=str(data.get("source") or ""),
            model_id=str(data.get("model_id") or ""),
            provider_ref=str(data.get("provider_ref") or ""),
        )


def _badges_for_capabilities(capabilities: list[str]) -> list[str]:
    labels = {
        "chat": "对话",
        "json": "JSON",
        "planner": "规划",
        "tools": "工具",
        "stream": "流式",
        "long_context": "长上下文",
    }
    return [labels[item] for item in capabilities if item in labels]


def _infer_toolset_id(*, read_intent: list[str], write_intent: list[str], mcp: list[str]) -> str:
    if write_intent or "workspace_edit" in mcp:
        return "workspace_edit"
    if any(item in mcp for item in ["context7_docs", "grep_code_search", "web_search"]):
        return "remote_lookup"
    if read_intent or any(item in mcp for item in ["project_filesystem_readonly", "code_locator", "git_tools"]):
        return "readonly_project_analysis"
    return "general_agent"


def _infer_risk_level(*, write_intent: list[str], mcp: list[str]) -> str:
    if write_intent or "workspace_edit" in mcp:
        return "medium"
    if any(item in mcp for item in ["web_search", "context7_docs", "grep_code_search"]):
        return "low"
    return "low"


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in list(value) if str(item).strip()]
