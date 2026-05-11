from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


_TOOL_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


class MessageTransformer:
    """Provider-aware message cleanup before model requests.

    MVP scope: avoid empty content, sanitize tool ids, and drop orphan tool
    results. Provider-specific adapters can extend this without changing callers.
    """

    def transform(self, messages: list[dict[str, Any]], provider_type: str = "openai_compatible") -> list[dict[str, Any]]:
        provider = str(provider_type or "openai_compatible").strip().lower()
        transformed = [self._clean_message(item) for item in deepcopy(messages or []) if isinstance(item, dict)]
        transformed = [item for item in transformed if not self._is_empty_message(item)]
        transformed = self._drop_orphan_tool_results(transformed)
        if provider in {"anthropic", "bedrock"}:
            transformed = [self._clean_anthropic_message(item) for item in transformed]
            transformed = [item for item in transformed if not self._is_empty_message(item)]
        return transformed

    def _clean_message(self, message: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(message)
        if "tool_call_id" in cleaned:
            cleaned["tool_call_id"] = sanitize_tool_call_id(cleaned.get("tool_call_id"))
        if isinstance(cleaned.get("tool_calls"), list):
            cleaned["tool_calls"] = [self._clean_tool_call(item) for item in cleaned["tool_calls"] if isinstance(item, dict)]
        if cleaned.get("content") is None:
            cleaned["content"] = ""
        return cleaned

    def _clean_tool_call(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(tool_call)
        if "id" in cleaned:
            cleaned["id"] = sanitize_tool_call_id(cleaned.get("id"))
        return cleaned

    def _clean_anthropic_message(self, message: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(message)
        content = cleaned.get("content")
        if isinstance(content, list):
            cleaned["content"] = [block for block in content if not self._is_empty_content(block)]
        return cleaned

    def _drop_orphan_tool_results(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        known_tool_ids: set[str] = set()
        output: list[dict[str, Any]] = []
        for message in messages:
            for tool_call in message.get("tool_calls") or []:
                tool_id = sanitize_tool_call_id(tool_call.get("id"))
                if tool_id:
                    known_tool_ids.add(tool_id)
            if str(message.get("role") or "").lower() == "tool":
                tool_id = sanitize_tool_call_id(message.get("tool_call_id"))
                if tool_id and tool_id not in known_tool_ids:
                    continue
            output.append(message)
        return output

    def _is_empty_message(self, message: dict[str, Any]) -> bool:
        role = str(message.get("role") or "").lower()
        if role == "assistant" and message.get("tool_calls"):
            return False
        if role == "tool":
            return False
        return self._is_empty_content(message.get("content"))

    def _is_empty_content(self, content: Any) -> bool:
        if content is None:
            return True
        if isinstance(content, str):
            return not content.strip()
        if isinstance(content, list):
            return not any(not self._is_empty_content(item) for item in content)
        if isinstance(content, dict):
            if "text" in content:
                return self._is_empty_content(content.get("text"))
            return False
        return False


def sanitize_tool_call_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    sanitized = _TOOL_ID_PATTERN.sub("_", raw)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:64] or "tool_call"
