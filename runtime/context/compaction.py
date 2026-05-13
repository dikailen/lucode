from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from runtime.common.conversation import append_recent_turn
from runtime.common.text_utils import sanitize_text


SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[^,\s;，。；]+"),
)
PATH_PATTERN = re.compile(
    r"(?:(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|[A-Za-z0-9_.-]+[\\/])"
    r"[^\s，。；：、,;:\"'<>|]+)"
)
RISK_PATTERN = re.compile(r"(错误|失败|异常|拒绝|缺失|未完成|TODO|Traceback|Exception|Error)", re.IGNORECASE)


@dataclass(frozen=True)
class CompactedContext:
    summary: str
    recent_turns: list[dict[str, str]]
    total_messages: int
    compacted_messages: int
    summary_source: str = "rules"
    semantic_error: str = ""


class ContextCompactor:
    """Deterministic context compactor for resumed JSONL sessions.

    This is intentionally model-free. It keeps recent messages verbatim and
    folds older messages into a bounded, structured background summary.
    """

    def __init__(
        self,
        *,
        tail_messages: int = 6,
        max_summary_chars: int = 2400,
        max_recent_chars: int = 800,
    ):
        self.tail_messages = max(1, int(tail_messages or 6))
        self.max_summary_chars = max(600, int(max_summary_chars or 2400))
        self.max_recent_chars = max(200, int(max_recent_chars or 800))

    def compact(self, messages: list[dict[str, Any]]) -> CompactedContext:
        normalized = self._normalize_messages(messages)
        total = len(normalized)
        if not normalized:
            return CompactedContext(summary="", recent_turns=[], total_messages=0, compacted_messages=0)

        recent_messages = normalized[-self.tail_messages :]
        older_messages = normalized[: max(0, total - len(recent_messages))]
        recent_turns: list[dict[str, str]] = []
        for message in recent_messages:
            append_recent_turn(
                recent_turns,
                message["role"],
                message["content"],
                max_chars=self.max_recent_chars,
            )

        summary = self._build_summary(older_messages, total_messages=total, recent_count=len(recent_messages))
        return CompactedContext(
            summary=summary,
            recent_turns=recent_turns,
            total_messages=total,
            compacted_messages=len(older_messages),
            summary_source="rules",
        )

    def _normalize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = self._redact(str(item.get("content") or ""))
            content = sanitize_text(content).strip()
            if role in {"user", "assistant", "system", "tool"} and content:
                normalized.append({"role": role, "content": content})
        return normalized

    def _build_summary(self, older_messages: list[dict[str, str]], *, total_messages: int, recent_count: int) -> str:
        if not older_messages:
            return ""

        user_notes = self._latest_snippets(older_messages, role="user", limit=6)
        assistant_notes = self._latest_snippets(older_messages, role="assistant", limit=4)
        paths = self._extract_paths(older_messages, limit=8)
        risks = self._extract_risk_notes(older_messages, limit=4)

        lines = [
            "以下是已恢复会话的压缩摘要。它是背景，不是本轮新任务。",
            f"会话统计：共 {total_messages} 条消息，已折叠 {len(older_messages)} 条旧消息，最近 {recent_count} 条保留原文。",
        ]
        self._append_section(lines, "较早用户目标", user_notes)
        self._append_section(lines, "较早助手结论", assistant_notes)
        self._append_section(lines, "涉及文件/路径", paths)
        self._append_section(lines, "风险或未完成线索", risks)
        return self._fit_budget(lines)

    def _latest_snippets(self, messages: list[dict[str, str]], *, role: str, limit: int) -> list[str]:
        snippets = [
            self._one_line(message["content"], 180)
            for message in messages
            if message["role"] == role and message["content"].strip()
        ]
        return snippets[-limit:]

    def _extract_paths(self, messages: list[dict[str, str]], *, limit: int) -> list[str]:
        seen: set[str] = set()
        paths: list[str] = []
        for message in messages:
            for match in PATH_PATTERN.findall(message["content"]):
                value = match.strip().rstrip(".")
                if value and value not in seen:
                    seen.add(value)
                    paths.append(value)
        return paths[-limit:]

    def _extract_risk_notes(self, messages: list[dict[str, str]], *, limit: int) -> list[str]:
        notes = [
            self._one_line(message["content"], 180)
            for message in messages
            if RISK_PATTERN.search(message["content"])
        ]
        return notes[-limit:]

    def _append_section(self, lines: list[str], title: str, items: list[str]) -> None:
        if not items:
            return
        lines.append(f"{title}：")
        for item in items:
            lines.append(f"- {item}")

    def _fit_budget(self, lines: list[str]) -> str:
        output: list[str] = []
        used = 0
        for line in lines:
            extra = len(line) + 1
            if output and used + extra > self.max_summary_chars:
                remaining = len(lines) - len(output)
                output.append(f"...[compacted {remaining} lines]")
                break
            output.append(line)
            used += extra
        return "\n".join(output)

    def _redact(self, text: str) -> str:
        return redact_sensitive_text(text)

    def _one_line(self, text: str, limit: int) -> str:
        normalized = sanitize_text(str(text or "")).replace("\n", " ").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit] + f"...[truncated {len(normalized) - limit} chars]"


def _redact_match(match: re.Match) -> str:
    if match.lastindex:
        return f"{match.group(1)}=[redacted]"
    return "[redacted]"


def redact_sensitive_text(text: str) -> str:
    redacted = str(text or "")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: _redact_match(match), redacted)
    return redacted
