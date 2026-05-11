from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    """Resolved filesystem roots for an installed Lucode runtime."""

    app_home: Path
    user_home: Path
    workspace_root: Path
    project_config_dir: Path
    has_project_config: bool

    @property
    def backup_dir(self) -> Path:
        return self.workspace_root / ".agent_quarantine" / "backups"


def discover_workspace_context(
    app_home: Path,
    cwd: Path | None = None,
    user_home: Path | None = None,
) -> WorkspaceContext:
    """Resolve install, user, and workspace roots without changing process cwd."""

    resolved_app = Path(app_home).resolve()
    resolved_cwd = Path(cwd or Path.cwd()).resolve()
    resolved_user = (
        Path(user_home).resolve()
        if user_home is not None
        else Path(os.environ.get("LUCODE_USER_HOME") or Path.home().joinpath(".lucode")).resolve()
    )

    workspace_root = _nearest_lucode_workspace(resolved_cwd) or resolved_cwd
    project_config_dir = workspace_root / ".lucode"
    return WorkspaceContext(
        app_home=resolved_app,
        user_home=resolved_user,
        workspace_root=workspace_root,
        project_config_dir=project_config_dir,
        has_project_config=project_config_dir.is_dir(),
    )


def _nearest_lucode_workspace(start: Path) -> Path | None:
    current = start.resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / ".lucode").is_dir():
            return candidate
    return None
