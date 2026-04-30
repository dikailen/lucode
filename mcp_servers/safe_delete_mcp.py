import os
import zipfile
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("safe_delete", log_level="ERROR")


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

    if not resolved.exists():
        raise FileNotFoundError(f"Target does not exist: {resolved}")

    return resolved


def _zip_target(target: Path, zip_path: Path) -> None:
    project_root = _project_root()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if target.is_file():
            archive.write(target, target.relative_to(project_root))
            return

        for path in target.rglob("*"):
            archive.write(path, path.relative_to(project_root))


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

    return (
        "压缩备份完成，原文件未移动、未删除。\n"
        f"目标：{target}\n"
        f"原因：{reason}\n"
        f"压缩备份：{backup_path}\n"
        "如仍需删除，请在确认备份无误后手动处理原文件。"
    )


if __name__ == "__main__":
    mcp.run("stdio")
