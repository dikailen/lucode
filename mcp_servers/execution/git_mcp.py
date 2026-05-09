import json
import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    from mcp_servers.core.operation_log import append_operation_log
except ModuleNotFoundError:
    from operation_log import append_operation_log


mcp = FastMCP("git_tools", log_level="ERROR")


def _project_root() -> Path:
    return Path(os.environ["GIT_TOOLS_PROJECT_ROOT"]).resolve()


def _quarantine_dir() -> Path:
    return Path(os.environ["GIT_TOOLS_QUARANTINE_DIR"]).resolve()


def _operation_log() -> Path:
    return _quarantine_dir() / "operations.jsonl"


def _run_git(args: list[str], timeout_seconds: int = 30) -> dict:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_project_root(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": "git executable was not found in PATH.",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"git command timed out after {timeout_seconds} seconds.",
        }

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def _log_operation(action: str, details: dict) -> None:
    returncode = int(details.get("returncode", 0))
    append_operation_log(
        _operation_log(),
        tool=f"git_tools.{action}",
        action=action,
        reason=str(details.get("reason", "")),
        status="success" if returncode == 0 else "failed",
        params_summary={key: value for key, value in details.items() if key != "reason"},
        approval_required=action == "git_commit",
        approval_note="Only git_commit requires approval; read-only git tools are allowed.",
        result_summary=f"returncode={returncode}",
        error=str(details.get("stderr", "")),
    )


def _parse_status_short(stdout: str) -> list[dict]:
    status_names = {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "U": "unmerged",
        "?": "untracked",
        "!": "ignored",
    }
    items = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        status_key = code.strip()[:1] or "?"
        items.append(
            {
                "status_code": code,
                "status": status_names.get(status_key, code.strip() or "unknown"),
                "path": path,
            }
        )
    return items


@mcp.tool(name="git_status", description="Show git status for the current project repository.")
def git_status(short: bool = True) -> str:
    result = _run_git(["status", "--short" if short else "--branch"])
    changed_files = _parse_status_short(result["stdout"]) if short and result["returncode"] == 0 else []
    return json.dumps(
        {
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "changed_files": changed_files,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(name="git_diff", description="Show git diff for the current project repository.")
def git_diff(path: str = "", max_chars: int = 12000) -> str:
    args = ["diff", "--"]
    if path:
        target = Path(path)
        if target.is_absolute() or ".." in target.parts:
            raise ValueError("path must be relative to the project root")
        args.append(path)
    result = _run_git(args)
    limit = max(1000, min(int(max_chars or 12000), 50000))
    return json.dumps(
        {
            "returncode": result["returncode"],
            "stdout": _truncate(result["stdout"], limit),
            "stderr": _truncate(result["stderr"], limit),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(name="git_log", description="Show recent git commits for the current project repository.")
def git_log(max_count: int = 5) -> str:
    max_count = max(1, min(int(max_count or 5), 20))
    result = _run_git(["log", f"--max-count={max_count}", "--oneline"])
    return json.dumps(
        {
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="git_commit",
    description="Create a local git commit. Requires user approval. Does not push.",
)
def git_commit(message: str, reason: str) -> str:
    if not message.strip():
        raise ValueError("message must not be empty")
    if "\n" in message:
        raise ValueError("message must be a single-line commit summary")

    result = _run_git(["commit", "-m", message], timeout_seconds=60)
    _log_operation(
        "git_commit",
        {
            "message": message,
            "reason": reason,
            "returncode": result["returncode"],
        },
    )
    return json.dumps(
        {
            "message": message,
            "reason": reason,
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run("stdio")
