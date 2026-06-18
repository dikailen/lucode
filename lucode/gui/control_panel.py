from __future__ import annotations

from runtime.config.execution_mode import EXECUTION_MODES, execution_mode_label_zh, normalize_execution_mode
from runtime.config.model_config import MODEL_ROLES, ROLE_ORDER
from runtime.safety.privacy import PRIVACY_MODES, normalize_privacy_mode


EXECUTION_MODE_ORDER = ("solo", "serial", "full")
PRIVACY_MODE_ORDER = ("offline", "local_first", "cloud_allowed")

PRIVACY_LABELS_ZH = {
    "offline": "离线",
    "local_first": "本地优先",
    "cloud_allowed": "允许云端",
}

EXECUTION_MODE_HINTS_ZH = {
    "solo": "单执行脑直接完成任务",
    "serial": "主脑规划，专家串行流水线",
    "full": "主脑按复杂度组队，多员工并行",
}

# 每个执行模式实际会用到的角色脑（依据 runtime 实现核实）。
# value: (role_id, "always" | "conditional")
MODE_ROLE_USAGE: dict[str, list[tuple[str, str]]] = {
    "solo": [("executor", "always")],
    "serial": [
        ("orchestrator", "always"),
        ("executor", "always"),
        ("final_synthesizer", "conditional"),
    ],
    "full": [
        ("orchestrator", "always"),
        ("executor", "always"),
        ("final_synthesizer", "conditional"),
    ],
}

ROLE_CONDITION_HINTS_ZH = {
    "final_synthesizer": "多 agent 路线时启用",
}


def execution_mode_options() -> list[tuple[str, str]]:
    return [(mode, execution_mode_label_zh(mode)) for mode in EXECUTION_MODE_ORDER if mode in EXECUTION_MODES]


def privacy_mode_options() -> list[tuple[str, str]]:
    return [(mode, PRIVACY_LABELS_ZH.get(mode, mode)) for mode in PRIVACY_MODE_ORDER if mode in PRIVACY_MODES]


def role_options() -> list[tuple[str, str]]:
    return [(role, MODEL_ROLES[role]["label"]) for role in ROLE_ORDER]


def roles_for_mode(mode: str) -> list[tuple[str, str]]:
    """Return (role_id, usage) the given execution mode actually uses."""

    return list(MODE_ROLE_USAGE.get(normalize_execution_mode(mode), MODE_ROLE_USAGE["solo"]))


def query_refiner_available_for_mode(mode: str) -> bool:
    """solo never runs the query refiner; serial/full do when enabled."""

    return normalize_execution_mode(mode) != "solo"


def worker_pool_available_for_mode(mode: str) -> bool:
    """Only full team mode lets the supervisor build a multi-model worker team."""

    return normalize_execution_mode(mode) == "full"


def _index_for_value(options: list[tuple[str, str]], value: str) -> int:
    for idx, (key, _label) in enumerate(options):
        if key == value:
            return idx
    return 0


try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtWidgets import (
        QButtonGroup,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QWidget,
    )

    _PYSIDE_AVAILABLE = True
except ModuleNotFoundError:
    _PYSIDE_AVAILABLE = False


if _PYSIDE_AVAILABLE:

    class ControlBar(QFrame):
        execution_mode_changed = Signal(str)
        settings_requested = Signal()

        def __init__(self, parent: QWidget | None = None):
            super().__init__(parent)
            self.setObjectName("ControlBar")
            self._mode = "solo"
            self._building = True

            outer = QHBoxLayout(self)
            outer.setContentsMargins(14, 12, 14, 12)
            outer.setSpacing(12)

            self._build_row(outer)
            self._building = False

        def _build_row(self, outer) -> None:
            seg_label = QLabel("执行模式")
            seg_label.setObjectName("FieldLabel")
            outer.addWidget(seg_label)

            self._mode_group = QButtonGroup(self)
            self._mode_group.setExclusive(True)
            self._mode_buttons: dict[str, QPushButton] = {}
            seg = QHBoxLayout()
            seg.setSpacing(0)
            for mode, label in execution_mode_options():
                btn = QPushButton(label)
                btn.setCheckable(True)
                btn.setObjectName("SegButton")
                btn.setToolTip(EXECUTION_MODE_HINTS_ZH.get(mode, ""))
                btn.clicked.connect(lambda _checked, m=mode: self._on_mode_clicked(m))
                self._mode_group.addButton(btn)
                self._mode_buttons[mode] = btn
                seg.addWidget(btn)
            outer.addLayout(seg)

            self.summary_label = QLabel("")
            self.summary_label.setObjectName("ControlSummary")
            outer.addWidget(self.summary_label, 1)

            self.settings_button = QPushButton("⚙")
            self.settings_button.setObjectName("SettingsButton")
            self.settings_button.setToolTip("设置")
            self.settings_button.clicked.connect(self.settings_requested.emit)
            outer.addWidget(self.settings_button)

        def set_models(self, models: list[tuple[str, str]]) -> None:
            del models

        def set_initial(
            self,
            *,
            execution_mode: str,
            privacy_mode: str,
            role_models: dict[str, str],
            query_refiner_enabled: bool = False,
            worker_pool: list[str] | None = None,
        ) -> None:
            del privacy_mode, role_models, query_refiner_enabled, worker_pool
            self._building = True
            self._mode = normalize_execution_mode(execution_mode)
            btn = self._mode_buttons.get(self._mode)
            if btn is not None:
                btn.setChecked(True)
            self.summary_label.setText(f"模式 {execution_mode_label_zh(self._mode)}")
            self._building = False

        def set_enabled(self, enabled: bool) -> None:
            for btn in self._mode_buttons.values():
                btn.setEnabled(enabled)
            self.settings_button.setEnabled(enabled)

        def _on_mode_clicked(self, mode: str) -> None:
            self._mode = normalize_execution_mode(mode)
            self.summary_label.setText(f"模式 {execution_mode_label_zh(self._mode)}")
            if not self._building:
                self.execution_mode_changed.emit(self._mode)
