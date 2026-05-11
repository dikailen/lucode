from __future__ import annotations


COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "查看命令菜单和常用操作"),
    ("/status", "查看当前运行状态、MCP、Git 和回滚点"),
    ("/config", "查看当前模型、隐私和运行配置"),
    ("/model", "查看三脑模型优先级"),
    ("/model available", "查看当前可运行模型"),
    ("/models", "查看 Provider 模型和选择说明"),
    ("/models select", "选择主模型和 fallback"),
    ("/models role", "配置 query_refiner / orchestrator / final_synthesizer 角色模型"),
    ("/connect", "查看或添加模型 Provider"),
    ("/privacy", "查看隐私模式"),
    ("/mode", "查看或切换 solo / serial / full"),
    ("/plan", "只生成计划，不执行任务"),
    ("/diff", "查看当前 Git diff 摘要"),
    ("/rollback", "回滚最近一轮修改"),
    ("/skills", "查看当前项目 Skills"),
    ("/skills_all", "查看全部 Skills"),
    ("/mcp", "查看当前项目 MCP"),
    ("/mcp_all", "查看全部 MCP"),
    ("/tools", "查看核心工具注册表"),
    ("/tools_all", "查看全部工具注册表"),
    ("/permissions", "查看项目权限策略"),
    ("/new", "清空对话上下文并重绘欢迎界面"),
    ("/stop", "中止当前输入或运行中的任务"),
    ("/exit", "退出 Lucode"),
)


def render_command_palette(filter_text: str = "") -> str:
    query = str(filter_text or "").strip().lower()
    if query.startswith("/"):
        query = query[1:]
    items = [
        (command, description)
        for command, description in COMMANDS
        if not query or query in command.lower() or query in description.lower()
    ]
    lines = [
        "命令菜单",
        "提示：每条命令带中文说明；后续接入 prompt_toolkit 后可支持上下键、鼠标和模糊选择。",
        "",
    ]
    if not items:
        lines.append("- 没有匹配命令")
        return "\n".join(lines)
    for command, description in items:
        lines.append(f"{command:<18} {description}")
    return "\n".join(lines)
