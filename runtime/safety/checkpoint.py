from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Checkpoint:
    project_root: Path
    git_available: bool
    is_dirty: bool
    head_commit: str
    can_rollback: bool
    mode: str
    message: str
    scoped_paths: list[str] = field(default_factory=list)
    dirty_paths: list[str] = field(default_factory=list)


@dataclass
class RollbackResult:
    rolled_back: bool
    message: str


def create_checkpoint(project_root: Path, scoped_paths: list[str] | None = None) -> Checkpoint:
    project_root = project_root.resolve()
    scoped = _normalize_scoped_paths(project_root, scoped_paths or [])
    git_dir = project_root / ".git"
    if not git_dir.exists():
        return Checkpoint(
            project_root=project_root,
            git_available=False,
            is_dirty=False,
            head_commit="",
            can_rollback=False,
            mode="none",
            message="当前目录不是 Git 仓库，暂不支持 checkpoint 回滚。",
            scoped_paths=scoped,
        )

    status = _run_git(project_root, ["status", "--short"])
    head = _run_git(project_root, ["rev-parse", "HEAD"])
    is_dirty = bool(status.stdout.strip())
    head_commit = head.stdout.strip() if head.returncode == 0 else ""
    dirty_paths = _parse_status_paths(status.stdout)

    if is_dirty:
        if scoped:
            conflicts = sorted(set(scoped) & set(dirty_paths))
            if conflicts:
                return Checkpoint(
                    project_root=project_root,
                    git_available=True,
                    is_dirty=True,
                    head_commit=head_commit,
                    can_rollback=False,
                    mode="scoped_conflict_protected",
                    message=(
                        "检测到用户已有未提交改动与 Agent 计划修改同一文件，"
                        f"已停止自动回滚保护：{', '.join(conflicts)}"
                    ),
                    scoped_paths=scoped,
                    dirty_paths=dirty_paths,
                )
            if head_commit:
                return Checkpoint(
                    project_root=project_root,
                    git_available=True,
                    is_dirty=True,
                    head_commit=head_commit,
                    can_rollback=True,
                    mode="scoped_patch_rollback",
                    message="检测到用户已有未提交改动；只允许回滚 Agent 声明触碰的文件。",
                    scoped_paths=scoped,
                    dirty_paths=dirty_paths,
                )

        return Checkpoint(
            project_root=project_root,
            git_available=True,
            is_dirty=True,
            head_commit=head_commit,
            can_rollback=False,
            mode="git_dirty_protected",
            message="检测到用户已有未提交改动，已启用保护模式，不自动回滚。",
            scoped_paths=scoped,
            dirty_paths=dirty_paths,
        )

    return Checkpoint(
        project_root=project_root,
        git_available=True,
        is_dirty=False,
        head_commit=head_commit,
        can_rollback=bool(head_commit),
        mode="git_clean_head",
        message="已记录干净工作区 checkpoint。",
        scoped_paths=scoped,
        dirty_paths=dirty_paths,
    )


def rollback_checkpoint(checkpoint: Checkpoint) -> RollbackResult:
    if not checkpoint.can_rollback:
        return RollbackResult(
            rolled_back=False,
            message=checkpoint.message or "当前 checkpoint 不允许自动回滚。",
        )

    if checkpoint.mode == "scoped_patch_rollback":
        return _rollback_scoped_checkpoint(checkpoint)

    if checkpoint.mode != "git_clean_head" or not checkpoint.head_commit:
        return RollbackResult(
            rolled_back=False,
            message="当前 checkpoint 模式不支持自动回滚。",
        )

    reset_result = _run_git(checkpoint.project_root, ["reset", "--hard", checkpoint.head_commit])
    clean_result = _run_git(checkpoint.project_root, ["clean", "-fd"])
    if reset_result.returncode != 0:
        return RollbackResult(
            rolled_back=False,
            message=f"git reset --hard 失败：{_stderr_or_stdout(reset_result)}",
        )
    if clean_result.returncode != 0:
        return RollbackResult(
            rolled_back=False,
            message=f"git clean -fd 失败：{_stderr_or_stdout(clean_result)}",
        )
    return RollbackResult(
        rolled_back=True,
        message=f"已回滚到执行前 checkpoint：{checkpoint.head_commit[:8]}",
    )


def _rollback_scoped_checkpoint(checkpoint: Checkpoint) -> RollbackResult:
    paths = list(checkpoint.scoped_paths)
    if not paths:
        return RollbackResult(False, "scoped rollback 缺少可回滚路径，已停止。")

    conflicts = sorted(set(paths) & set(checkpoint.dirty_paths))
    if conflicts:
        return RollbackResult(
            False,
            f"检测到用户已有未提交改动与 Agent 计划修改同一文件，拒绝自动回滚：{', '.join(conflicts)}",
        )

    tracked_paths = [path for path in paths if _path_tracked_at_checkpoint(checkpoint, path)]
    if tracked_paths:
        checkout = _run_git(checkpoint.project_root, ["checkout", checkpoint.head_commit, "--", *tracked_paths])
        if checkout.returncode != 0:
            return RollbackResult(False, f"scoped git checkout 失败：{_stderr_or_stdout(checkout)}")

    removed: list[str] = []
    for path in paths:
        target = (checkpoint.project_root / path).resolve()
        if not target.exists():
            continue

        if _path_tracked_at_checkpoint(checkpoint, path):
            continue

        if target.is_dir():
            return RollbackResult(False, f"scoped rollback 拒绝自动删除目录：{path}")
        try:
            target.unlink()
            removed.append(path)
        except OSError as exc:
            return RollbackResult(False, f"删除 Agent 新增文件失败：{path}：{exc}")

    detail = f"，并删除新增文件：{', '.join(removed)}" if removed else ""
    return RollbackResult(
        rolled_back=True,
        message=f"已按 scoped patch rollback 只回滚本轮 Agent 触碰路径：{', '.join(paths)}{detail}",
    )


def _path_tracked_at_checkpoint(checkpoint: Checkpoint, path: str) -> bool:
    tracked = _run_git(checkpoint.project_root, ["ls-tree", "-r", "--name-only", checkpoint.head_commit, "--", path])
    return tracked.returncode == 0 and bool(tracked.stdout.strip())


def _normalize_scoped_paths(project_root: Path, scoped_paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in scoped_paths:
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        resolved = (project_root / candidate).resolve()
        if not resolved.is_relative_to(project_root):
            continue
        relative = str(resolved.relative_to(project_root)).replace("\\", "/")
        if relative not in normalized:
            normalized.append(relative)
    return normalized


def _parse_status_paths(stdout: str) -> list[str]:
    paths: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        raw = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1].strip()
        raw = raw.strip('"').replace("\\", "/")
        if raw and raw not in paths:
            paths.append(raw)
    return paths


def _run_git(project_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        shell=False,
    )


def _stderr_or_stdout(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "无详细输出").strip()
