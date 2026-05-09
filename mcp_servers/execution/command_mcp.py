import json
import os
import shlex
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    from mcp_servers.core.operation_log import append_operation_log
except ModuleNotFoundError:
    from operation_log import append_operation_log


mcp = FastMCP("command_runner", log_level="ERROR")

DENIED_TOKENS = {
    "rm",
    "del",
    "erase",
    "rmdir",
    "remove-item",
    "format",
    "shutdown",
    "reboot",
    "reg",
    "schtasks",
}
DENIED_GIT_PATTERNS = [
    ["git", "push"],
    ["git", "reset", "--hard"],
    ["git", "clean"],
    ["git", "checkout", "--"],
]
DENIED_SHELL_OPERATORS = {"&&", "||", "|", ">", ">>", "<", ";", "`"}


def _project_root() -> Path:
    return Path(os.environ["COMMAND_RUNNER_PROJECT_ROOT"]).resolve()


def _quarantine_dir() -> Path:
    return Path(os.environ["COMMAND_RUNNER_QUARANTINE_DIR"]).resolve()


def _operation_log() -> Path:
    return _quarantine_dir() / "operations.jsonl"


def _parse_command(command: str) -> list[str]:
    if not command or not command.strip():
        raise ValueError("command must not be empty")
    try:
        args = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ValueError(f"Unable to parse command: {exc}") from exc
    if not args:
        raise ValueError("command must not be empty")
    return [arg.strip('"') for arg in args]


def _validate_command(args: list[str]) -> None:
    lowered = [arg.lower() for arg in args]
    if any(item in DENIED_SHELL_OPERATORS for item in lowered):
        raise ValueError("Shell chaining, pipes, and redirection are not allowed")

    executable = Path(lowered[0]).name
    if executable in DENIED_TOKENS:
        raise ValueError(f"Command is denied: {args[0]}")

    for pattern in DENIED_GIT_PATTERNS:
        if lowered[: len(pattern)] == pattern:
            raise ValueError(f"Git command is denied: {' '.join(args)}")

    if any(".." in Path(arg).parts for arg in args[1:] if not arg.startswith("-")):
        raise ValueError("Arguments containing parent-directory traversal are not allowed")


def _truncate(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def _log_operation(command: str, reason: str, returncode: int, *, status: str = "success", error: str = "") -> None:
    append_operation_log(
        _operation_log(),
        tool="command_runner.run_command",
        action="run_command",
        reason=reason,
        status=status,
        params_summary={"command": command, "returncode": returncode},
        approval_required=True,
        approval_note="MCP server requires approval for command execution.",
        result_summary=f"returncode={returncode}",
        error=error,
    )


@mcp.tool(
    name="run_command",
    description=(
        "Run a local project command without shell expansion. Dangerous commands are denied before execution. "
        "Requires user approval."
    ),
)
def run_command(command: str, reason: str, timeout_seconds: int = 60) -> str:
    args = _parse_command(command)
    try:
        _validate_command(args)
    except ValueError as exc:
        _log_operation(command, reason, -1, status="failed", error=str(exc))
        raise
    timeout_seconds = max(1, min(int(timeout_seconds or 60), 300))

    try:
        result = subprocess.run(
            args,
            cwd=_project_root(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except FileNotFoundError:
        returncode = 127
        stdout = ""
        stderr = f"Executable not found: {args[0]}"
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout or ""
        stderr = f"Command timed out after {timeout_seconds} seconds."

    status = "success" if returncode == 0 else "failed"
    _log_operation(command, reason, returncode, status=status, error=stderr if returncode else "")
    return json.dumps(
        {
            "command": command,
            "reason": reason,
            "returncode": returncode,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run("stdio")
