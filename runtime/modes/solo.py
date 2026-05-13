"""Compatibility wrapper; SoloStrategy calls solo execution directly."""

from __future__ import annotations

from runtime.execution.solo_runner import (
    COMMAND_MARKERS,
    COMMAND_MCP_IDS,
    COMMAND_WORD_MARKERS,
    EDIT_MARKERS,
    EDIT_MCP_IDS,
    NO_COMMAND_MARKERS,
    NO_EDIT_MARKERS,
    READ_MARKERS,
    READONLY_MCP_IDS,
    SOLO_READONLY_BUDGET_PROFILE,
    WEB_MARKERS,
    WEB_MCP_IDS,
    _contains_any,
    _contains_any_word,
    _dedupe,
    _solo_mcp_ids_for_input,
    run_solo_request,
)


__all__ = [
    "COMMAND_MARKERS",
    "COMMAND_MCP_IDS",
    "COMMAND_WORD_MARKERS",
    "EDIT_MARKERS",
    "EDIT_MCP_IDS",
    "NO_COMMAND_MARKERS",
    "NO_EDIT_MARKERS",
    "READ_MARKERS",
    "READONLY_MCP_IDS",
    "SOLO_READONLY_BUDGET_PROFILE",
    "WEB_MARKERS",
    "WEB_MCP_IDS",
    "_contains_any",
    "_contains_any_word",
    "_dedupe",
    "_solo_mcp_ids_for_input",
    "run_solo_request",
]
