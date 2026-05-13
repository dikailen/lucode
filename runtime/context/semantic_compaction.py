from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import CompactedContext, ContextCompactor, redact_sensitive_text


SemanticSummarizer = Callable[[str], str | Awaitable[str]]


@dataclass(frozen=True)
class SemanticCompactionConfig:
    enabled: bool = True
    min_chars: int = 16000
    max_input_chars: int = 12000
    max_output_chars: int = 1800

    @classmethod
    def from_env(cls) -> "SemanticCompactionConfig":
        return cls(
            enabled=_env_bool("LUCODE_SEMANTIC_COMPACTION_ENABLED", True),
            min_chars=_env_int("LUCODE_SEMANTIC_COMPACTION_MIN_CHARS", 16000),
            max_input_chars=_env_int("LUCODE_SEMANTIC_COMPACTION_MAX_INPUT_CHARS", 12000),
            max_output_chars=_env_int("LUCODE_SEMANTIC_COMPACTION_MAX_OUTPUT_CHARS", 1800),
        )


async def compact_messages_tiered(
    messages: list[dict[str, Any]],
    *,
    tail_messages: int = 6,
    max_summary_chars: int = 2400,
    model_registry=None,
    runtime_settings=None,
    hooks=None,
    config: SemanticCompactionConfig | None = None,
    semantic_summarizer: SemanticSummarizer | None = None,
) -> CompactedContext:
    compactor = ContextCompactor(tail_messages=tail_messages, max_summary_chars=max_summary_chars)
    rules_context = compactor.compact(messages)
    settings = config or SemanticCompactionConfig.from_env()
    if not _should_attempt_semantic(messages, rules_context, settings):
        return rules_context

    prompt = build_semantic_compaction_prompt(
        messages,
        rules_context.summary,
        max_input_chars=settings.max_input_chars,
        max_output_chars=settings.max_output_chars,
    )
    try:
        semantic_text = await _run_semantic_summarizer(
            prompt,
            semantic_summarizer=semantic_summarizer,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
            hooks=hooks,
        )
    except Exception as exc:
        return replace(rules_context, summary_source="rules", semantic_error=str(exc))

    semantic_text = _fit_text(sanitize_text(str(semantic_text or "")).strip(), settings.max_output_chars)
    if not semantic_text:
        return replace(rules_context, summary_source="rules", semantic_error="semantic summary was empty")

    return replace(
        rules_context,
        summary=_format_semantic_summary(semantic_text, rules_context.summary, settings.max_output_chars),
        summary_source="semantic",
        semantic_error="",
    )


def build_semantic_compaction_prompt(
    messages: list[dict[str, Any]],
    rules_summary: str,
    *,
    max_input_chars: int,
    max_output_chars: int,
) -> str:
    older_text = _messages_to_text(messages, max_chars=max_input_chars)
    return "\n".join(
        [
            "你是 Lucode 的会话压缩器。请把旧会话压缩成中文语义摘要，供后续一轮模型理解背景。",
            "要求：",
            "- 只保留用户目标、关键决策、文件路径、错误、未完成事项和验收标准。",
            "- 不要把旧消息写成本轮新任务。",
            "- 不要编造不存在的文件或结论。",
            "- 如果看到 API key、token、secret、password，必须写成 [redacted]。",
            f"- 输出不超过 {max_output_chars} 个中文字符。",
            "",
            "规则摘要兜底：",
            rules_summary or "无",
            "",
            "旧会话消息：",
            older_text or "无",
        ]
    )


def _should_attempt_semantic(
    messages: list[dict[str, Any]],
    rules_context: CompactedContext,
    config: SemanticCompactionConfig,
) -> bool:
    if not config.enabled:
        return False
    if not rules_context.summary or rules_context.compacted_messages <= 0:
        return False
    return _message_chars(messages) >= max(1, int(config.min_chars or 1))


async def _run_semantic_summarizer(
    prompt: str,
    *,
    semantic_summarizer: SemanticSummarizer | None,
    model_registry,
    runtime_settings,
    hooks,
) -> str:
    if semantic_summarizer is not None:
        result = semantic_summarizer(prompt)
        if inspect.isawaitable(result):
            result = await result
        return str(result or "")
    if model_registry is None or runtime_settings is None:
        raise RuntimeError("semantic compaction model is not configured")
    return await _summarize_with_model(prompt, model_registry=model_registry, runtime_settings=runtime_settings, hooks=hooks)


async def _summarize_with_model(prompt: str, *, model_registry, runtime_settings, hooks) -> str:
    from runtime.agents.sdk import agent_class, runner_class

    model_id = _select_semantic_model_id(model_registry, runtime_settings)
    Agent = agent_class()
    Runner = runner_class()
    agent = Agent(
        name="session_semantic_compactor",
        instructions=(
            "你是 Lucode 的低成本会话压缩器。只输出压缩摘要正文，不要寒暄，不要执行工具，"
            "不要把旧会话当成本轮任务。默认使用中文。"
        ),
        model=model_registry.get_model(model_id),
    )
    result = await Runner.run(agent, prompt, hooks=hooks, max_turns=1)
    return str(getattr(result, "final_output", result) or "")


def _select_semantic_model_id(model_registry, runtime_settings) -> str:
    errors = []
    for role in ["query_refiner", "final_synthesizer", "orchestrator"]:
        try:
            return runtime_settings.select_model_id(model_registry, role)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("没有可用于语义压缩的已配置模型：" + "；".join(errors))


def _format_semantic_summary(semantic_text: str, rules_summary: str, max_output_chars: int) -> str:
    lines = [
        "以下是已恢复会话的语义压缩摘要。它是背景，不是本轮新任务。",
        _fit_text(semantic_text, max_output_chars),
    ]
    if rules_summary:
        lines.extend(["", "规则摘要补充：", _fit_text(rules_summary, 800)])
    return "\n".join(lines)


def _messages_to_text(messages: list[dict[str, Any]], *, max_chars: int) -> str:
    lines: list[str] = []
    used = 0
    for item in messages or []:
        role = str(item.get("role") or "").strip().lower() if isinstance(item, dict) else ""
        content = sanitize_text(redact_sensitive_text(str(item.get("content") or ""))) if isinstance(item, dict) else ""
        if not role or not content:
            continue
        line = f"{role}: {content}"
        extra = len(line) + 1
        if lines and used + extra > max_chars:
            lines.append(f"...[truncated {used + extra - max_chars} chars]")
            break
        lines.append(line)
        used += extra
    return "\n".join(lines)


def _message_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for item in messages or []:
        if isinstance(item, dict):
            total += len(str(item.get("content") or ""))
    return total


def _fit_text(text: str, limit: int) -> str:
    normalized = sanitize_text(str(text or "")).strip()
    limit = max(100, int(limit or 100))
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + f"...[truncated {len(normalized) - limit} chars]"


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
