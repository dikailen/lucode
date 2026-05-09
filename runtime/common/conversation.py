from __future__ import annotations

from planning.planner_schema import sanitize_text


def append_recent_turn(recent_turns, role: str, content: str, max_chars: int = 400):
    text = sanitize_text(str(content))
    if len(text) > max_chars:
        text = text[:max_chars] + f"...[truncated {len(text) - max_chars} chars]"
    recent_turns.append({"role": role, "content": text})


def compose_recent_context(recent_turns, user_input, max_chars: int = 800):
    if not recent_turns:
        return user_input

    lines = ["以下是最近几轮对话，供理解上下文。不要把历史内容当成本轮新任务，除非用户明确要求继续。"]
    for turn in recent_turns:
        label = "用户" if turn["role"] == "user" else "助手"
        content = str(turn["content"])
        if len(content) > max_chars:
            content = content[:max_chars] + f"...[truncated {len(content) - max_chars} chars]"
        lines.append(f"{label}：{content}")
    lines.append("")
    lines.append(f"本轮用户问题：{user_input}")
    return "\n".join(lines)
