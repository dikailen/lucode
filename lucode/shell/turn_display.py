from __future__ import annotations

from runtime.common.text_utils import sanitize_text
from runtime.ui.output_visibility import streamed_output_is_sufficient


def should_print_final_output(hooks, final_output) -> bool:
    if not streamed_output_is_sufficient(hooks):
        return True
    text = str(final_output or "")
    important_prefixes = (
        "本轮执行失败",
        "本轮任务超过",
        "主脑规划未通过校验",
        "计划审查未通过",
        "已收到 /stop",
        "已拒绝工具调用",
    )
    return text.startswith(important_prefixes)


def turn_status_label(final_output, stopped: bool = False) -> str:
    if stopped:
        return "已中断"
    text = str(final_output or "").strip()
    failed_prefixes = (
        "本轮执行失败",
        "本轮任务超过",
        "主脑规划未通过校验",
        "计划审查未通过",
    )
    if text.startswith(failed_prefixes):
        return "失败"
    if text.startswith("已拒绝工具调用"):
        return "已拒绝"
    if "最终审核未通过" in text:
        return "待修复"
    return "完成"


def format_turn_error(exc: Exception) -> str:
    """Format a recoverable per-turn error without crashing the chat loop."""

    message = sanitize_text(str(exc)).strip() or "无详细错误信息"
    class_name = exc.__class__.__name__
    friendly = friendly_error_hint(message)
    if friendly:
        return (
            "本轮执行失败，但程序没有退出，你可以继续输入下一条问题。\n"
            f"错误类型：{class_name}\n"
            f"{friendly}"
        )
    return (
        "本轮执行失败，但程序没有退出，你可以继续输入下一条问题。\n"
        f"错误类型：{class_name}\n"
        f"错误信息：{message}\n"
        "如果是 APIConnectionError / ConnectError，通常是模型 API 网络连接临时失败，"
        "稍后重试即可。"
    )


def is_exit_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/exit"


def is_stop_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/stop"


def is_new_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/new"


def friendly_error_hint(message: str) -> str:
    normalized = message.lower()
    if "tool " in normalized and " not found in agent " in normalized:
        tool_name = extract_missing_tool_name(message)
        return (
            "原因：模型尝试调用未分配的工具"
            f"{f'：{tool_name}' if tool_name else ''}。\n"
            "这通常是模型把其它任务的工具规则误认为当前任务也可用，或计划没有给该 Agent 分配对应 MCP。\n"
            "解决办法：系统已限制后续任务提示只展示已分配工具；你也可以用 /plan 查看规划，"
            "必要时把需求说得更具体后重新规划。"
        )
    if "no configured models allowed by privacy mode: offline" in normalized:
        return (
            "原因：当前是隐私模式 offline，但没有可用的本地模型配置。\n"
            "offline 模式会禁止 DeepSeek、SiliconFlow、MiMo 等云端模型，也会禁止联网搜索。\n"
            "解决办法：\n"
            "1. 如果你要严格本地运行，请用 /connect 配置本地 Ollama Provider，或在 .lucode/config.toml 中配置本地模型。\n"
            "2. 如果你想继续使用 DeepSeek/MiMo 这类云端 API，请把隐私模式切到 local_first 或 cloud_allowed；"
            "Provider 配置写入 .lucode/config.toml，密钥写入用户级 auth.json。"
        )
    return ""


def extract_missing_tool_name(message: str) -> str:
    marker = "Tool "
    if marker not in message:
        return ""
    rest = message.split(marker, 1)[1]
    return rest.split(" not found in agent ", 1)[0].strip()
