from __future__ import annotations

from types import SimpleNamespace

_TRACING_DISABLED = False


def ensure_tracing_disabled() -> None:
    """Disable Agents SDK tracing the first time the SDK is actually needed."""

    global _TRACING_DISABLED
    if _TRACING_DISABLED:
        return
    try:
        from agents import set_tracing_disabled
    except ModuleNotFoundError:
        _TRACING_DISABLED = True
        return

    set_tracing_disabled(True)
    _TRACING_DISABLED = True


def agent_class():
    ensure_tracing_disabled()
    try:
        from agents import Agent
    except ModuleNotFoundError:
        return _FallbackAgent

    return Agent


def runner_class():
    ensure_tracing_disabled()
    try:
        from agents import Runner
    except ModuleNotFoundError:
        return _FallbackRunner

    return Runner


def run_hooks_class():
    ensure_tracing_disabled()
    try:
        from agents import RunHooks
    except ModuleNotFoundError:
        return _FallbackRunHooks

    return RunHooks


def async_openai_class():
    ensure_tracing_disabled()
    try:
        from agents import AsyncOpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError(_missing_sdk_message()) from exc

    return AsyncOpenAI


def openai_chat_completions_model_class():
    ensure_tracing_disabled()
    try:
        from agents import OpenAIChatCompletionsModel
    except ModuleNotFoundError as exc:
        raise RuntimeError(_missing_sdk_message()) from exc

    return OpenAIChatCompletionsModel


def mcp_stdio_class():
    ensure_tracing_disabled()
    try:
        from agents.mcp import MCPServerStdio
    except ModuleNotFoundError:
        return _FallbackMCPServerStdio

    return MCPServerStdio


def mcp_streamable_http_class():
    ensure_tracing_disabled()
    try:
        from agents.mcp import MCPServerStreamableHttp
    except ModuleNotFoundError:
        return _FallbackMCPServerStreamableHttp

    return MCPServerStreamableHttp


def static_tool_filter_factory():
    ensure_tracing_disabled()
    try:
        from agents.mcp import create_static_tool_filter
    except ModuleNotFoundError:
        return _fallback_static_tool_filter

    return create_static_tool_filter


class _FallbackAgent:
    """Small construction-only Agent replacement for tests when Agents SDK is absent."""

    def __init__(self, name: str, instructions: str, model=None, mcp_servers=None, **kwargs):
        self.name = name
        self.instructions = instructions
        self.model = model
        self.mcp_servers = list(mcp_servers or [])
        self.kwargs = dict(kwargs)


class _FallbackRunHooks:
    """No-op hook base used only before real model execution starts."""

    pass


class _FallbackRunner:
    @staticmethod
    async def run(*args, **kwargs):
        raise RuntimeError(_missing_sdk_message())

    @staticmethod
    def run_streamed(*args, **kwargs):
        raise RuntimeError(_missing_sdk_message())


class _FallbackMCPServerStdio:
    """Construction-only MCP server replacement for catalog and budget tests."""

    def __init__(self, name: str, params: dict, **kwargs):
        self.name = name
        self.params = SimpleNamespace(**dict(params or {}))
        self.kwargs = dict(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FallbackMCPServerStreamableHttp(_FallbackMCPServerStdio):
    """Construction-only remote MCP replacement for tests when Agents SDK is absent."""

    pass


def _fallback_static_tool_filter(**kwargs):
    return dict(kwargs)


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def _missing_sdk_message() -> str:
    return (
        "OpenAI Agents SDK 未安装，当前只能执行启动、配置和本地结构测试。"
        "请安装 openai-agents-python 相关依赖后再运行模型任务。"
    )
