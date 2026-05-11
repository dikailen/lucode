import os
import hashlib
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    from mcp_servers.core.operation_log import append_operation_log
    from runtime.safety.permissions import evaluate_permission, load_effective_permissions
except ModuleNotFoundError:
    from operation_log import append_operation_log
    from runtime.safety.permissions import evaluate_permission, load_effective_permissions


mcp = FastMCP("workspace_edit", log_level="ERROR")

PROTECTED_TOP_LEVEL = {".git", ".agent_quarantine", ".agent_cache"}
PROTECTED_FILES = {".env"}
DEFAULT_MAX_BACKUP_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_BACKUP_FILES = 5000


def _project_root() -> Path:
    return Path(os.environ["WORKSPACE_EDIT_PROJECT_ROOT"]).resolve()


def _quarantine_dir() -> Path:
    return Path(os.environ["WORKSPACE_EDIT_QUARANTINE_DIR"]).resolve()


def _backup_dir() -> Path:
    return _quarantine_dir() / "backups"


def _operation_log() -> Path:
    return _quarantine_dir() / "operations.jsonl"


def _resolve_target(target_path: str, *, must_exist: bool | None = None) -> Path:
    if not target_path or not target_path.strip():
        raise ValueError("target_path must not be empty")

    project_root = _project_root()
    raw_path = Path(target_path)
    if not raw_path.is_absolute():
        raw_path = project_root / raw_path

    resolved = raw_path.resolve()
    if resolved == project_root:
        raise ValueError("Refusing to modify the project root itself")
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"Refusing to access outside project root: {resolved}")

    relative = resolved.relative_to(project_root)
    parts = relative.parts
    if not parts:
        raise ValueError("Refusing to modify the project root itself")
    if parts[0] in PROTECTED_TOP_LEVEL:
        raise ValueError(f"Refusing to modify protected path: {relative}")
    if str(relative).replace("\\", "/") in PROTECTED_FILES:
        raise ValueError(f"Refusing to modify protected file: {relative}")

    if must_exist is True and not resolved.exists():
        raise FileNotFoundError(f"Target does not exist: {resolved}")
    if must_exist is False and resolved.exists():
        raise FileExistsError(f"Target already exists: {resolved}")

    return resolved


def _safe_archive_name(path: Path) -> str:
    relative = path.relative_to(_project_root())
    return str(relative).replace("\\", "__").replace("/", "__").replace(":", "_")


def _zip_target(target: Path, action: str) -> Path | None:
    if not target.exists():
        return None

    _check_backup_budget(target)
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{timestamp}__{action}__{_safe_archive_name(target)}.zip"

    project_root = _project_root()
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if target.is_file():
            archive.write(target, target.relative_to(project_root))
        else:
            for child in target.rglob("*"):
                archive.write(child, child.relative_to(project_root))
    return backup_path


def _backup_limits() -> tuple[int, int]:
    return (
        _env_int("WORKSPACE_EDIT_MAX_BACKUP_BYTES", DEFAULT_MAX_BACKUP_BYTES),
        _env_int("WORKSPACE_EDIT_MAX_BACKUP_FILES", DEFAULT_MAX_BACKUP_FILES),
    )


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, 0)


def _check_backup_budget(target: Path) -> None:
    max_bytes, max_files = _backup_limits()
    total_bytes = 0
    file_count = 0

    if target.is_file():
        total_bytes = target.stat().st_size
        file_count = 1
    elif target.is_dir():
        for child in target.rglob("*"):
            if not child.is_file():
                continue
            file_count += 1
            total_bytes += child.stat().st_size
            if max_files and file_count > max_files:
                raise ValueError(
                    "backup file count limit exceeded for "
                    f"{_relative_target(target)}: {file_count} files > {max_files} files"
                )
            if max_bytes and total_bytes > max_bytes:
                raise ValueError(
                    "backup size limit exceeded for "
                    f"{_relative_target(target)}: {total_bytes} bytes > {max_bytes} bytes"
                )

    if max_files and file_count > max_files:
        raise ValueError(
            "backup file count limit exceeded for "
            f"{_relative_target(target)}: {file_count} files > {max_files} files"
        )
    if max_bytes and total_bytes > max_bytes:
        raise ValueError(
            "backup size limit exceeded for "
            f"{_relative_target(target)}: {total_bytes} bytes > {max_bytes} bytes"
        )


def _relative_target(target: Path) -> str:
    return str(target.relative_to(_project_root())).replace("\\", "/")


def _enforce_permission(action: str, target: Path) -> None:
    policy = load_effective_permissions(_project_root())
    decision = evaluate_permission(policy, action, target=_relative_target(target))
    if decision.decision == "deny":
        raise ValueError(f"{action} denied by permissions.toml: {decision.reason}")


def _log_operation(
    action: str,
    target: Path,
    reason: str,
    backup_path: Path | None,
    *,
    status: str = "success",
    result_summary: str = "",
    error: str = "",
) -> None:
    append_operation_log(
        _operation_log(),
        tool=f"workspace_edit.{action}",
        action=action,
        reason=reason,
        status=status,
        params_summary={"target_path": _relative_target(target)},
        approval_required=True,
        approval_note="MCP server requires approval for workspace edits.",
        backup_path=backup_path,
        result_summary=result_summary,
        error=error,
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_sha256_enabled() -> bool:
    raw = str(os.environ.get("WORKSPACE_EDIT_STRICT_SHA256") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def _verify_expected_sha256(target: Path, expected_sha256: str | None, *, require_for_existing: bool = False) -> None:
    expected = str(expected_sha256 or "").strip().lower()
    if not expected:
        if require_for_existing and _strict_sha256_enabled() and target.exists() and target.is_file():
            raise ValueError(
                f"expected_sha256 is required for existing file {_relative_target(target)}. "
                "Read the file first and pass its current SHA-256 digest before editing."
            )
        return
    if not target.exists():
        raise ValueError(
            f"expected_sha256 was provided for {_relative_target(target)}, but the target does not exist"
        )
    if not target.is_file():
        raise ValueError(f"expected_sha256 can only verify files: {_relative_target(target)}")
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("expected_sha256 must be a 64-character lowercase hex SHA-256 digest")
    current = _sha256_file(target)
    if current != expected:
        raise ValueError(
            "expected_sha256 mismatch for "
            f"{_relative_target(target)}: expected {expected}, current {current}. "
            "The file changed after it was read; re-read the file before editing."
        )


def _parse_patch_paths(patch: str) -> list[Path]:
    project_root = _project_root()
    paths = []
    for line in patch.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        raw = line[4:].strip().split("\t", 1)[0]
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"Unsafe patch path: {raw}")
        resolved = (project_root / candidate).resolve()
        if not resolved.is_relative_to(project_root):
            raise ValueError(f"Patch path escapes project root: {raw}")
        if resolved.relative_to(project_root).parts[0] in PROTECTED_TOP_LEVEL:
            raise ValueError(f"Patch touches protected path: {raw}")
        if str(resolved.relative_to(project_root)).replace("\\", "/") in PROTECTED_FILES:
            raise ValueError(f"Patch touches protected file: {raw}")
        paths.append(resolved)
    return sorted(set(paths))


@mcp.tool(
    name="create_file",
    description="Create a new UTF-8 text file inside the project. Requires user approval.",
)
def create_file(target_path: str, content: str, reason: str) -> str:
    target = _resolve_target(target_path, must_exist=False)
    _enforce_permission("write", target)
    _write_text(target, content)
    _log_operation("create_file", target, reason, None, result_summary="created UTF-8 file")
    return f"已创建文件：{target}\n原因：{reason}"


@mcp.tool(
    name="write_file",
    description=(
        "Create or overwrite a UTF-8 text file inside the project. Existing files are backed up first. "
        "Requires user approval."
    ),
)
def write_file(target_path: str, content: str, reason: str, expected_sha256: str = "") -> str:
    target = _resolve_target(target_path, must_exist=None)
    _enforce_permission("write", target)
    _verify_expected_sha256(target, expected_sha256, require_for_existing=True)
    backup_path = _zip_target(target, "write_file")
    _write_text(target, content)
    _log_operation("write_file", target, reason, backup_path, result_summary="wrote UTF-8 file")
    backup_text = f"\n备份：{backup_path}" if backup_path else "\n备份：无，目标原本不存在"
    return f"已写入文件：{target}{backup_text}\n原因：{reason}"


@mcp.tool(
    name="replace_in_file",
    description=(
        "Replace exact text inside a UTF-8 file. The file is backed up first. "
        "Use for small surgical edits. Requires user approval."
    ),
)
def replace_in_file(
    target_path: str,
    old_text: str,
    new_text: str,
    reason: str,
    expected_replacements: int = 1,
    expected_sha256: str = "",
) -> str:
    target = _resolve_target(target_path, must_exist=True)
    _enforce_permission("write", target)
    _verify_expected_sha256(target, expected_sha256, require_for_existing=True)
    if not target.is_file():
        raise ValueError(f"Target is not a file: {target}")
    if not old_text:
        raise ValueError("old_text must not be empty")

    original = target.read_text(encoding="utf-8")
    count = original.count(old_text)
    if count != int(expected_replacements):
        raise ValueError(
            f"Expected {expected_replacements} replacement(s), found {count}; refusing ambiguous edit"
        )

    backup_path = _zip_target(target, "replace_in_file")
    target.write_text(original.replace(old_text, new_text, count), encoding="utf-8")
    _log_operation(
        "replace_in_file",
        target,
        reason,
        backup_path,
        result_summary=f"replaced {count} occurrence(s)",
    )
    return (
        f"已替换文件内容：{target}\n"
        f"替换次数：{count}\n"
        f"备份：{backup_path}\n"
        f"原因：{reason}"
    )


@mcp.tool(
    name="apply_unified_patch",
    description=(
        "Apply a unified diff patch inside the project using git apply. Touched existing files are backed up first. "
        "Requires user approval."
    ),
)
def apply_unified_patch(patch: str, reason: str, expected_sha256_by_path: dict | None = None) -> str:
    if not patch.strip():
        raise ValueError("patch must not be empty")

    targets = _parse_patch_paths(patch)
    expected_hashes = expected_sha256_by_path or {}
    for target in targets:
        _enforce_permission("write", target)
        relative = _relative_target(target)
        expected = expected_hashes.get(relative) or expected_hashes.get(str(target)) or ""
        _verify_expected_sha256(target, str(expected), require_for_existing=True)
    backup_records = [(path, _zip_target(path, "apply_patch")) for path in targets]

    result = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        input=patch,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=_project_root(),
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "git apply failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    for target, backup_path in backup_records:
        _log_operation("apply_unified_patch", target, reason, backup_path, result_summary="applied patch")

    return (
        "已应用 unified diff patch。\n"
        f"影响路径：{', '.join(str(path.relative_to(_project_root())) for path in targets) or '未检测到'}\n"
        f"备份数量：{sum(1 for _, item in backup_records if item)}\n"
        f"原因：{reason}"
    )


@mcp.tool(
    name="delete_file",
    description=(
        "Delete a file or directory inside the project after creating a zip backup. "
        "Protected paths are denied. Requires user approval."
    ),
)
def delete_file(target_path: str, reason: str, expected_sha256: str = "") -> str:
    target = _resolve_target(target_path, must_exist=True)
    _enforce_permission("delete", target)
    _verify_expected_sha256(target, expected_sha256, require_for_existing=True)
    backup_path = _zip_target(target, "delete_file")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    _log_operation("delete_file", target, reason, backup_path, result_summary="deleted target after backup")
    return (
        "已删除目标，并已先创建压缩备份。\n"
        f"目标：{target}\n"
        f"备份：{backup_path}\n"
        f"原因：{reason}"
    )


if __name__ == "__main__":
    mcp.run("stdio")
