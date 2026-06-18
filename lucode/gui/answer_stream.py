from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AnswerStreamState:
    text: str = ""

    @property
    def has_streamed(self) -> bool:
        return bool(self.text)

    def append_delta(self, text: str) -> str:
        chunk = str(text or "")
        if not chunk:
            return self.text
        self.text += chunk
        return self.text

    def finalize(self, final_output: str) -> str:
        value = str(final_output or "")
        if value:
            self.text = value
        return self.text

    def reset(self) -> None:
        self.text = ""
