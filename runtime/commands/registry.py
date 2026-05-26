from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommandSpec:
    command: str
    description: str
    group: str
    argument_hint: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    writable: bool = False
    interactive_only: bool = False
    source: str = "builtin"
    path: str = ""
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    model: str = ""
    disable_model_invocation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        if not self.argument_hint:
            return self.command
        return f"{self.command} {self.argument_hint}"


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("/skill", "查看某个 Skill 的来源、触发词和建议工具", "扩展", argument_hint="<name>"),
    CommandSpec("/help", "查看命令菜单和常用操作", "基础", aliases=("/", "/?")),
    CommandSpec("/status", "查看当前运行状态、MCP、Git 和回滚点", "基础"),
    CommandSpec("/config", "查看当前模型、隐私和运行配置", "配置"),
    CommandSpec("/api show", "查看 API 与 base_url 配置，自动隐藏密钥", "配置", aliases=("/api",)),
    CommandSpec("/privacy", "查看隐私模式", "配置"),
    CommandSpec("/mode", "查看或切换 solo / serial / full", "配置", argument_hint="<solo|serial|full>", writable=True),
    CommandSpec("/refiner", "开启或关闭前置优化副脑", "配置", argument_hint="<on|off>", writable=True),
    CommandSpec("/model", "查看详细模型优先级和能力状态", "模型"),
    CommandSpec("/model available", "查看当前可运行模型", "模型", aliases=("/models available",)),
    CommandSpec("/models", "查看多脑模型调音台和 Provider 模型", "模型"),
    CommandSpec(
        "/models select",
        "选择主模型和 fallback",
        "模型",
        argument_hint="<provider/model> [fallback...]",
        aliases=("/model select",),
        writable=True,
    ),
    CommandSpec(
        "/models role",
        "配置四脑角色模型（兼容命令）",
        "模型",
        argument_hint="<role> <provider/model> [...]",
        aliases=("/model role",),
        writable=True,
    ),
    CommandSpec(
        "/models brain",
        "切换前置优化脑/主脑/执行脑/汇总脑模型",
        "模型",
        argument_hint="<脑位> <provider/model> [fallback...]",
        aliases=("/model brain",),
        writable=True,
    ),
    CommandSpec("/models brain reset", "重置项目多脑模型覆盖配置", "模型", aliases=("/model brain reset",), writable=True),
    CommandSpec("/models probe", "主动探测已配置模型的 key、接口和能力", "模型", argument_hint="[force]", aliases=("/model probe",), writable=True),
    CommandSpec("/connect", "进入 Provider 连接向导或添加模型 Provider", "模型", argument_hint="[provider] [--api-key ...]", writable=True),
    CommandSpec("/connect remove", "删除 Provider 配置、API key 和失效模型引用", "模型", argument_hint="<provider>", aliases=("/connect delete",), writable=True),
    CommandSpec("/plan", "只生成计划，不执行任务", "执行", argument_hint="<任务>"),
    CommandSpec("/context", "查看最近一轮共享上下文摘要", "会话"),
    CommandSpec("/history", "进入会话历史面板，查看、预览和恢复历史会话", "会话", argument_hint="[last|会话ID前缀]"),
    CommandSpec("/history search", "搜索本地历史会话内容和 Context 摘要", "会话", argument_hint="<关键词>"),
    CommandSpec("/history export", "导出历史会话为 Markdown 文件", "会话", argument_hint="[last|会话ID前缀]"),
    CommandSpec("/history remove", "删除不需要的历史会话，删除前会二次确认", "会话", argument_hint="<会话ID前缀>", writable=True),
    CommandSpec("/resume", "查看或恢复最近 JSONL 会话上下文", "会话", argument_hint="[last|with-context 会话ID前缀]"),
    CommandSpec("/diff", "查看当前 Git diff 摘要", "工作区", argument_hint="[路径]"),
    CommandSpec("/rollback", "回滚最近一轮修改", "工作区", writable=True),
    CommandSpec("/skills", "查看当前项目 Skills", "扩展"),
    CommandSpec("/skills_all", "查看全部 Skills", "扩展"),
    CommandSpec("/mcp", "查看当前项目 MCP", "扩展"),
    CommandSpec("/mcp_all", "查看全部 MCP", "扩展"),
    CommandSpec("/tools", "查看核心工具注册表", "扩展"),
    CommandSpec("/tools_all", "查看全部工具注册表", "扩展"),
    CommandSpec("/permissions", "查看项目权限策略", "安全"),
    CommandSpec("/audit", "查看最近工具审批审计记录", "安全", aliases=("/hooks",)),
    CommandSpec("/new", "清空对话上下文并重绘欢迎界面", "会话", writable=True, interactive_only=True),
    CommandSpec("/stop", "中止当前输入或运行中的任务", "会话", interactive_only=True),
    CommandSpec("/exit", "退出 Lucode", "会话", interactive_only=True),
)


def command_specs() -> tuple[CommandSpec, ...]:
    return COMMAND_SPECS


def all_command_specs(workspace_context=None) -> tuple[CommandSpec, ...]:
    return _dedupe_command_specs((*COMMAND_SPECS, *_external_command_specs(workspace_context)))


def search_command_specs(filter_text: str = "", workspace_context=None) -> list[CommandSpec]:
    query = _normalize_query(filter_text)
    specs = all_command_specs(workspace_context)
    if not query:
        return list(specs)
    return [spec for spec in specs if _matches(spec, query)]


def known_command_prefixes(workspace_context=None, *, include_dynamic: bool = False) -> set[str]:
    prefixes = {"/"}
    specs = all_command_specs(workspace_context) if include_dynamic else COMMAND_SPECS
    for spec in specs:
        prefixes.add(_root_command(spec.command))
        for alias in spec.aliases:
            prefixes.add(_root_command(alias))
    return prefixes


def _external_command_specs(workspace_context=None) -> tuple[CommandSpec, ...]:
    try:
        from runtime.commands.sources import discover_external_command_specs
    except Exception:
        return ()
    return tuple(discover_external_command_specs(CommandSpec, workspace_context))


def _dedupe_command_specs(specs: tuple[CommandSpec, ...]) -> tuple[CommandSpec, ...]:
    deduped: list[CommandSpec] = []
    seen: set[str] = set()
    for spec in specs:
        key = spec.command.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return tuple(deduped)


def _matches(spec: CommandSpec, query: str) -> bool:
    haystack = [
        spec.command,
        spec.display,
        spec.description,
        spec.group,
        spec.argument_hint,
        *spec.aliases,
    ]
    return any(query in item.lower().lstrip("/") for item in haystack)


def _normalize_query(value: str) -> str:
    query = str(value or "").strip().lower()
    if query.startswith("/"):
        query = query[1:]
    return query


def _root_command(value: str) -> str:
    stripped = str(value or "").strip().lower()
    if not stripped:
        return ""
    return stripped.split(maxsplit=1)[0]
