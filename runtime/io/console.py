from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from lucode.shell.input_adapter import ConsoleChoice, ConsoleFormField, ConsoleFormResult


@runtime_checkable
class Console(Protocol):
    """Input surface expected by interactive shell and future GUI adapters."""

    interactive: bool

    async def read_line(self, prompt: str = "") -> str:
        ...

    async def read_runtime_line(self) -> str:
        ...

    async def read_runtime_control_line(self) -> str:
        ...

    async def read_choice_line(self, prompt: str, choices: Sequence[ConsoleChoice], **kwargs) -> str:
        ...

    async def read_secret_line(self, prompt: str) -> str:
        ...

    async def read_form(
        self,
        *,
        title: str,
        fields: Sequence[ConsoleFormField],
        actions: Sequence[ConsoleChoice],
        message: str = "",
        footer: str = "",
    ) -> ConsoleFormResult | None:
        ...

    def defer(self, line: str) -> None:
        ...


@runtime_checkable
class ApprovalSession(Protocol):
    """Approval surface used by runtime.agent.approval.run_with_approval."""

    async def request_approval(self, prompt: str) -> str:
        ...
