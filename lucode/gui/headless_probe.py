from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from runtime.config.settings import RuntimeSettings
from runtime.events import ExecutionEventBus
from runtime.kernel import KernelFacade


class StaticApprovalSession:
    """Headless approval adapter used only for GUI event-flow verification."""

    def __init__(self, decision: str):
        self.decision = decision

    async def request_approval(self, prompt: str) -> str:
        del prompt
        return self.decision


class WorkspaceContext:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root


async def collect_headless_events(
    prompt: str,
    *,
    workspace: Path,
    mode: str = "",
    auto_approve: bool = False,
    show_plan: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    bus = ExecutionEventBus()
    bus.subscribe(lambda event: events.append(event.to_dict()))

    settings = RuntimeSettings.from_env()
    if mode:
        settings.execution_mode = mode

    approval_session = StaticApprovalSession("y" if auto_approve else "n")
    response = await KernelFacade(WorkspaceContext(workspace)).run_once(
        prompt,
        show_plan=show_plan,
        approval_session=approval_session,
        settings=settings,
        event_bus=bus,
    )
    return events, response.final_output


async def _amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Lucode once and print execution events without a GUI.")
    parser.add_argument("--prompt", default="Say hello in one short sentence.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--mode", default="")
    parser.add_argument("--show-plan", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--jsonl", action="store_true", help="Print one event JSON object per line.")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    events, final_output = await collect_headless_events(
        args.prompt,
        workspace=workspace,
        mode=args.mode,
        auto_approve=args.auto_approve,
        show_plan=args.show_plan,
    )

    if args.jsonl:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
    else:
        print(json.dumps({"events": events, "final_output": final_output}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
