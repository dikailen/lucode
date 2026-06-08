from __future__ import annotations

import re


_NODE_CHECK_RE = re.compile(
    r"\bnode\s+--check\s+(?P<path>[\w./\\:-]+\.js)\b",
    re.IGNORECASE,
)


def extract_explicit_verification_commands(text: str) -> list[str]:
    """Return verification commands explicitly requested by the user.

    This intentionally starts narrow. A syntax check such as `node --check`
    is a validator, while `node src/app.js` can start an interactive process
    and must not be inferred as equivalent.
    """

    commands: list[str] = []
    seen: set[str] = set()
    for match in _NODE_CHECK_RE.finditer(text or ""):
        path = match.group("path").rstrip(".,;，。；")
        command = f"node --check {path}"
        key = command.lower()
        if key in seen:
            continue
        commands.append(command)
        seen.add(key)
    return commands


def format_verification_command_lock(commands: list[str]) -> str:
    if not commands:
        return ""
    joined = "；".join(commands)
    return (
        f"只能运行明确指定的验证命令：{joined}；"
        "不要扩大为其它运行命令，不要改成 node src/game.js 或其它启动命令。"
    )
