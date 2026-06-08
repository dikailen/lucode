from __future__ import annotations

from runtime.safety.privacy import PrivacyPolicy, is_local_backend


BLOCKING_PROBE_STATUSES = {
    "chat_failed",
    "probe_failed",
    "service_unavailable",
    "model_missing",
    "capability_probe_failed",
    "config_incomplete",
}

RUNTIME_READY_PROBE_STATUSES = {
    "ok",
    "partial",
    "tools_unsupported",
}


def model_runtime_available(
    model_info: dict | None,
    *,
    require_probe_for_local: bool = True,
) -> bool:
    """Return whether a configured model can be considered runnable now."""

    if not model_info:
        return False
    probe = model_info.get("probe") or {}
    status = str(probe.get("status") or "").strip()
    if status in BLOCKING_PROBE_STATUSES:
        return False
    if _is_local_model(model_info) and require_probe_for_local:
        return status in RUNTIME_READY_PROBE_STATUSES
    return True


def model_usable_for_task(
    model_info: dict | None,
    policy: PrivacyPolicy | None = None,
    *,
    requires_tools: bool = False,
    require_probe_for_local: bool = True,
) -> bool:
    if not model_info:
        return False
    if not model_info.get("configured"):
        return False
    policy = policy or PrivacyPolicy()
    if not policy.model_allowed(model_info):
        return False
    if not model_runtime_available(model_info, require_probe_for_local=require_probe_for_local):
        return False
    if requires_tools and model_info.get("supports_tools") is False:
        return False
    return True


def select_model_for_skill(
    skill_id: str,
    catalog: dict,
    *,
    privacy_mode: str = "local_first",
    requires_tools: bool = False,
) -> str | None:
    policy = PrivacyPolicy(privacy_mode)
    models = [item for item in catalog.get("models", []) if isinstance(item, dict)]
    usable = [
        item
        for item in models
        if model_usable_for_task(item, policy, requires_tools=requires_tools)
    ]
    if not usable:
        return None

    model_infos = {str(item.get("id") or ""): item for item in usable if item.get("id")}
    sorted_ids = policy.sort_model_ids(
        [str(item.get("id") or "") for item in usable if item.get("id")],
        model_infos,
    )
    indexed = {model_id: index for index, model_id in enumerate(sorted_ids)}
    ranked = sorted(
        usable,
        key=lambda item: (
            -_skill_model_score(item, skill_id, requires_tools=requires_tools),
            indexed.get(str(item.get("id") or ""), 9999),
            str(item.get("id") or ""),
        ),
    )
    return str(ranked[0].get("id") or "") or None


def _skill_model_score(model_info: dict, skill_id: str, *, requires_tools: bool) -> int:
    skill = str(skill_id or "").strip()
    best_for = {str(value) for value in (model_info.get("best_for_skills") or [])}
    strengths = " ".join(str(value).lower() for value in (model_info.get("strengths") or []))
    reasoning = str(model_info.get("reasoning_level") or "").lower()
    cost = str(model_info.get("cost_level") or "").lower()
    tier = str(model_info.get("model_tier") or "").lower()
    score = 0

    if skill and skill in best_for:
        score += 40
    if requires_tools and model_info.get("supports_tools") is True:
        score += 12
    if model_info.get("supports_json_output") is True:
        score += 6
    if model_info.get("planner_suitable") is True:
        score += 6
    if model_info.get("execution_suitable") is True:
        score += 5

    score += {"high": 12, "medium": 6, "low": 2}.get(reasoning, 0)
    score += {"large": 8, "medium": 5, "small": 2}.get(tier, 0)

    if skill in {"jpc_now_skill", "code_engineer"}:
        if {"code", "python", "java", "cpp", "review"} & best_for:
            score += 16
        if any(marker in strengths for marker in ["code", "代码", "python", "java", "c++"]):
            score += 10
    elif skill == "skill_creator":
        if {"skill_creator", "orchestrator_planner"} & best_for:
            score += 16
        if reasoning == "high":
            score += 8
    elif skill in {"project_explorer", "humanizer_zh"}:
        if skill == "project_explorer" and "project_explorer" in best_for:
            score += 16
        if skill == "humanizer_zh" and "humanizer_zh" in best_for:
            score += 16
        score += {"low": 8, "medium": 4, "high": 1}.get(cost, 0)
    return score


def _is_local_model(model_info: dict) -> bool:
    return bool(model_info.get("is_local")) or is_local_backend(str(model_info.get("backend_type") or ""))
