from __future__ import annotations

from typing import Any

from runtime.agents.sdk import async_openai_class, openai_chat_completions_model_class

_REPLAY_FAMILY_MARKERS = ("mimo",)


class OpenAICompatibleProvider:
    """Create OpenAI-compatible model objects using the existing Agents SDK wrapper."""

    sdk_type = "openai_compatible"

    def create_model(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        provider_id: str | None = None,
        options: dict[str, Any] | None = None,
    ):
        AsyncOpenAI = async_openai_class()
        OpenAIChatCompletionsModel = openai_chat_completions_model_class()
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return OpenAIChatCompletionsModel(
            model=model_name,
            openai_client=client,
            should_replay_reasoning_content=_reasoning_replay_hook(
                provider_id=provider_id,
                model_name=model_name,
                base_url=base_url,
                options=options or {},
            ),
        )


def _reasoning_replay_hook(
    *,
    provider_id: str | None,
    model_name: str,
    base_url: str,
    options: dict[str, Any],
):
    explicit = options.get("replay_reasoning_content")
    if explicit is False:
        return _never_replay_reasoning_content
    if explicit is True:
        return _replay_reasoning_content_for_compatible_families
    if _needs_reasoning_replay(provider_id=provider_id, model_name=model_name, base_url=base_url):
        return _replay_reasoning_content_for_compatible_families
    return None


def _replay_reasoning_content_for_compatible_families(context) -> bool:
    try:
        from agents.models.reasoning_content_replay import default_should_replay_reasoning_content
    except Exception:
        default_should_replay_reasoning_content = None

    if callable(default_should_replay_reasoning_content) and default_should_replay_reasoning_content(context):
        return True

    current_family = _reasoning_family(
        str(getattr(context, "model", "") or ""),
        str(getattr(context, "base_url", "") or ""),
    )
    reasoning = getattr(context, "reasoning", None)
    origin_model = str(getattr(reasoning, "origin_model", "") or "")
    provider_data = getattr(reasoning, "provider_data", {}) or {}
    origin_family = _reasoning_family(origin_model, "")
    if current_family not in _REPLAY_FAMILY_MARKERS:
        return False
    return origin_family == current_family or provider_data == {}


def _never_replay_reasoning_content(context) -> bool:
    del context
    return False


def _needs_reasoning_replay(*, provider_id: str | None, model_name: str, base_url: str) -> bool:
    return _reasoning_family(provider_id or model_name, base_url) in _REPLAY_FAMILY_MARKERS


def _reasoning_family(value: str, base_url: str) -> str:
    text = f"{value} {base_url}".lower()
    if "deepseek" in text:
        return "deepseek"
    if "mimo" in text or "xiaomimimo" in text:
        return "mimo"
    return ""
