from __future__ import annotations

import argparse
import os
from pathlib import Path


def resolve_workspace_context(workspace_arg: str | None = None):
    from runtime.config.app_home import get_app_home
    from runtime.config.workspace import discover_workspace_context

    explicit_workspace = bool(workspace_arg)
    env_workspace = os.environ.get("LUCODE_WORKSPACE_ROOT")
    if explicit_workspace:
        cwd = Path(workspace_arg).expanduser().resolve()
    elif env_workspace:
        cwd = Path(env_workspace).expanduser().resolve()
        explicit_workspace = True
    else:
        cwd = Path.cwd()
    return discover_workspace_context(get_app_home(), cwd=cwd, explicit_workspace=explicit_workspace)


def export_workspace_context(context) -> None:
    os.environ["LUCODE_APP_HOME"] = str(context.app_home)
    os.environ["LUCODE_USER_HOME"] = str(context.user_home)
    os.environ["LUCODE_WORKSPACE_ROOT"] = str(context.workspace_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the Lucode desktop GUI.")
    parser.add_argument(
        "--workspace",
        default="",
        help="Workspace path for engine turns; defaults to .lucode discovery from the current directory.",
    )
    parser.add_argument("--mode", default="", help="Execution mode label shown in the UI.")
    args = parser.parse_args(argv)
    context = resolve_workspace_context(str(args.workspace or "").strip() or None)
    export_workspace_context(context)
    try:
        from lucode.gui.app import run_gui
    except ModuleNotFoundError as exc:
        if exc.name in {"PySide6", "qasync"}:
            print('Lucode GUI dependencies are missing. Install with: pip install -e ".[gui]"')
            return 2
        raise
    return run_gui(workspace=context.workspace_root, mode=str(args.mode or ""))


if __name__ == "__main__":
    raise SystemExit(main())
