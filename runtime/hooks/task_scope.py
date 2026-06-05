from __future__ import annotations

import inspect

from runtime.agents.sdk import run_hooks_class
from runtime.hooks.event_bridge import emit_tool_event_bridge, emit_tool_invoked_event
from runtime.hooks.tool_events import build_tool_event


class TaskScopedHooks(run_hooks_class()):
    """Bind tool hook events to a planned task while preserving the wrapped hooks object."""

    def __init__(self, base_hooks, *, task_id: str, event_bus=None):
        object.__setattr__(self, "_base_hooks", base_hooks)
        object.__setattr__(self, "_task_id", str(task_id or ""))
        object.__setattr__(self, "_event_bus", event_bus)

    async def on_llm_start(self, *args, **kwargs):
        await self._call_base_hook("on_llm_start", *args, **kwargs)

    async def on_llm_end(self, *args, **kwargs):
        await self._call_base_hook("on_llm_end", *args, **kwargs)

    async def on_agent_start(self, *args, **kwargs):
        await self._call_base_hook("on_agent_start", *args, **kwargs)

    async def on_agent_end(self, *args, **kwargs):
        await self._call_base_hook("on_agent_end", *args, **kwargs)

    async def on_handoff(self, *args, **kwargs):
        await self._call_base_hook("on_handoff", *args, **kwargs)

    def record_tool_event(self, event) -> None:
        base_hooks = object.__getattribute__(self, "_base_hooks")
        recorder = getattr(base_hooks, "record_tool_event", None)
        if callable(recorder):
            recorder(event)
        else:
            events = getattr(base_hooks, "tool_events", None)
            if isinstance(events, list):
                events.append(event)
        emit_tool_event_bridge(
            object.__getattribute__(self, "_event_bus"),
            event,
            task_id=object.__getattribute__(self, "_task_id"),
        )

    async def on_tool_start(self, context, agent, tool):
        await self._call_base_hook("on_tool_start", context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        await self._call_base_hook("on_tool_end", context, agent, tool, result)
        event = build_tool_event(
            "sdk_tool_end",
            _tool_name_from_context_or_tool(context, tool),
            _tool_arguments_from_context_or_tool(context, tool),
            status="completed",
            decision="completed",
            reason="sdk_tool_callback",
        )
        emit_tool_invoked_event(
            object.__getattribute__(self, "_event_bus"),
            event,
            task_id=object.__getattribute__(self, "_task_id"),
        )

    async def _call_base_hook(self, name: str, *args, **kwargs):
        callback = getattr(object.__getattribute__(self, "_base_hooks"), name, None)
        if not callable(callback):
            return None
        value = callback(*args, **kwargs)
        if inspect.isawaitable(value):
            return await value
        return value

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_base_hooks"), name)

    def __setattr__(self, name: str, value) -> None:
        if name in {"_base_hooks", "_task_id", "_event_bus"}:
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_base_hooks"), name, value)


def _tool_name(tool) -> str:
    return str(getattr(tool, "name", "") or getattr(tool, "qualified_name", "") or tool or "")


def _tool_arguments(tool) -> str:
    for name in ("arguments", "args", "input", "input_json"):
        value = getattr(tool, name, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if value:
            return str(value)
    return ""


def _tool_name_from_context_or_tool(context, tool) -> str:
    for source in (context, tool):
        value = _first_tool_attr(source, ("qualified_tool_name", "tool_name", "name", "qualified_name"))
        if value:
            return value
    return _tool_name(tool)


def _tool_arguments_from_context_or_tool(context, tool) -> str:
    for source in (context, tool):
        value = _first_tool_attr(source, ("tool_arguments", "arguments", "args", "input", "input_json"))
        if value:
            return value
    return ""


def _first_tool_attr(source, names: tuple[str, ...]) -> str:
    if source is None:
        return ""
    for name in names:
        value = getattr(source, name, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if value:
            return str(value)
    return ""
