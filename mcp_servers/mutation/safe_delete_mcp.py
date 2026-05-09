import os
import zipfile
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    from mcp_servers.core.operation_log import append_operation_log
except ModuleNotFoundError:
    from operation_log import append_operation_log


mcp = FastMCP("safe_delete", log_level="ERROR")

PROTECTED_TOP_LEVEL = {".git", ".agent_quarantine", ".agent_cache"}
PROTECTED_FILES = {".env"}
DEFAULT_MAX_BACKUP_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_BACKUP_FILES = 5000


def _project_root() -> Path:
    return Path(os.environ["SAFE_DELETE_PROJECT_ROOT"]).resolve()


def _quarantine_dir() -> Path:
    return Path(os.environ["SAFE_DELETE_QUARANTINE_DIR"]).resolve()


def _resolve_target(target_path: str) -> Path:
    project_root = _project_root()
    quarantine_dir = _quarantine_dir()
    raw_path = Path(target_path)
    if not raw_path.is_absolute():
        raw_path = project_root / raw_path

    resolved = raw_path.resolve()

    if not resolved.is_relative_to(project_root):
        raise ValueError(f"Refusing to delete outside project root: {resolved}")

    if resolved == quarantine_dir or resolved.is_relative_to(quarantine_dir):
        raise ValueError(f"Refusing to delete quarantine files: {resolved}")

    relative = resolved.relative_to(project_root)
    if relative.parts and relative.parts[0] in PROTECTED_TOP_LEVEL:
        raise ValueError(f"Refusing to access protected path: {relative}")
    if str(relative).replace("\\", "/") in PROTECTED_FILES:
        raise ValueError(f"Refusing to access protected file: {relative}")

    if not resolved.exists():
        raise FileNotFoundError(f"Target does not exist: {resolved}")

    return resolved


def _operation_log() -> Path:
    return _quarantine_dir() / "operations.jsonl"


def _zip_target(target: Path, zip_path: Path) -> None:
    _check_backup_budget(target)
    project_root = _project_root()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if target.is_file():
            archive.write(target, target.relative_to(project_root))
            return

        for path in target.rglob("*"):
            archive.write(path, path.relative_to(project_root))


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, 0)


def _backup_limits() -> tuple[int, int]:
    return (
        _env_int("SAFE_DELETE_MAX_BACKUP_BYTES", DEFAULT_MAX_BACKUP_BYTES),
        _env_int("SAFE_DELETE_MAX_BACKUP_FILES", DEFAULT_MAX_BACKUP_FILES),
    )


def _check_backup_budget(target: Path) -> None:
    max_bytes, max_files = _backup_limits()
    total_bytes = 0
    file_count = 0

    if target.is_file():
        total_bytes = target.stat().st_size
        file_count = 1
    else:
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            file_count += 1
            total_bytes += path.stat().st_size
            if max_files and file_count > max_files:
                raise ValueError(
                    f"backup file count limit exceeded for {target}: {file_count} files > {max_files} files"
                )
            if max_bytes and total_bytes > max_bytes:
                raise ValueError(
                    f"backup size limit exceeded for {target}: {total_bytes} bytes > {max_bytes} bytes"
                )

    if max_files and file_count > max_files:
        raise ValueError(f"backup file count limit exceeded for {target}: {file_count} files > {max_files} files")
    if max_bytes and total_bytes > max_bytes:
        raise ValueError(f"backup size limit exceeded for {target}: {total_bytes} bytes > {max_bytes} bytes")


@mcp.tool(
    name="safe_delete_file",
    description=(
        "Create a zip backup for a file or directory inside the project before a user decides "
        "whether to delete it manually. This tool does not move or delete the original file. "
        "Use only after explaining why the backup is needed."
    ),
)
def safe_delete_file(target_path: str, reason: str) -> str:
    target = _resolve_target(target_path)
    project_root = _project_root()
    backup_dir = _quarantine_dir() / "backups"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    relative = target.relative_to(project_root)
    safe_name = str(relative).replace("\\", "__").replace("/", "__").replace(":", "_")

    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_path = backup_dir / f"{timestamp}__{safe_name}.zip"

    _zip_target(target, backup_path)
    append_operation_log(
        _operation_log(),
        tool="safe_delete.safe_delete_file",
        action="safe_delete_file",
        reason=reason,
        status="success",
        params_summary={"target_path": str(relative).replace("\\", "/")},
        approval_required=True,
        approval_note="MCP server requires approval before creating delete backups.",
        backup_path=backup_path,
        result_summary="created backup; original target was not moved or deleted",
    )

    return (
        "压缩备份完成，原文件未移动、未删除。\n"
        f"目标：{target}\n"
        f"原因：{reason}\n"
        f"压缩备份：{backup_path}\n"
        "如仍需删除，请在确认备份无误后手动处理原文件。"
    )


if __name__ == "__main__":
    mcp.run("stdio")
