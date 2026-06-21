from __future__ import annotations

from runtime.config.execution_mode import EXECUTION_MODES, normalize_execution_mode
from runtime.config.model_config import ROLE_ORDER
from runtime.safety.privacy import PRIVACY_MODES

from lucode.gui.i18n import Translator, normalize_language


EXECUTION_MODE_ORDER = ("solo", "serial", "full")
PRIVACY_MODE_ORDER = ("offline", "local_first", "cloud_allowed")

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


def execution_mode_label(mode: str, language: str = "zh") -> str:
    normalized = normalize_execution_mode(mode)
    return Translator(language)(f"control.mode.{normalized}")


def execution_mode_options(language: str = "zh") -> list[tuple[str, str]]:
    return [
        (mode, execution_mode_label(mode, language))
        for mode in EXECUTION_MODE_ORDER
        if mode in EXECUTION_MODES
    ]


def privacy_mode_options(language: str = "zh") -> list[tuple[str, str]]:
    t = Translator(language)
    return [
        (mode, t(f"privacy.{mode}"))
        for mode in PRIVACY_MODE_ORDER
        if mode in PRIVACY_MODES
    ]


def role_label(role: str, language: str = "zh") -> str:
    return Translator(language)(f"role.{role}")


def role_condition_hint(role: str, language: str = "zh") -> str:
    t = Translator(language)
    if role == "final_synthesizer":
        return t("settings.role.final_synthesizer_hint")
    return t("settings.role.conditional")


def role_options(language: str = "zh") -> list[tuple[str, str]]:
    return [(role, role_label(role, language)) for role in ROLE_ORDER]


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
        QSizePolicy,
        QWidget,
    )

    _PYSIDE_AVAILABLE = True
except ModuleNotFoundError:
    _PYSIDE_AVAILABLE = False


if _PYSIDE_AVAILABLE:

    class ControlBar(QFrame):
        execution_mode_changed = Signal(str)
        settings_requested = Signal()

        def __init__(self, parent: QWidget | None = None, *, language: str = "zh"):
            super().__init__(parent)
            self.setObjectName("ControlBar")
            self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            self._language = normalize_language(language)
            self._t = Translator(self._language)
            self._mode = "solo"
            self._building = True

            outer = QHBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(8)

            self._build_row(outer)
            self._building = False

        def _build_row(self, outer) -> None:
            self.mode_label = QLabel(self._t("control.execution_mode"))
            self.mode_label.setObjectName("FieldLabel")
            outer.addWidget(self.mode_label)

            self._mode_group = QButtonGroup(self)
            self._mode_group.setExclusive(True)
            self._mode_buttons: dict[str, QPushButton] = {}
            seg = QHBoxLayout()
            seg.setSpacing(0)
            for mode, label in execution_mode_options(self._language):
                btn = QPushButton(label)
                btn.setText(self._compact_mode_label(mode))
                btn.setCheckable(True)
                btn.setObjectName("SegButton")
                btn.setProperty("mode_id", mode)
                btn.setToolTip(self._t(f"control.mode_tip.{mode}"))
                btn.clicked.connect(lambda _checked, m=mode: self._on_mode_clicked(m))
                self._mode_group.addButton(btn)
                self._mode_buttons[mode] = btn
                seg.addWidget(btn)
            outer.addLayout(seg)

            self.summary_label = QLabel("")
            self.summary_label.setObjectName("ControlSummary")
            self.summary_label.hide()
            outer.addWidget(self.summary_label, 1)

            self.settings_button = QPushButton(self._t("settings.title"))
            self.settings_button.setObjectName("SettingsButton")
            self.settings_button.setToolTip(self._t("control.settings_tip"))
            self.settings_button.clicked.connect(self.settings_requested.emit)
            outer.addWidget(self.settings_button)

        def set_language(self, language: str) -> None:
            self._language = normalize_language(language)
            self._t = Translator(self._language)
            self._refresh_language()

        def _refresh_language(self) -> None:
            self.mode_label.setText(self._t("control.execution_mode"))
            for mode, button in self._mode_buttons.items():
                button.setText(self._compact_mode_label(mode))
                button.setToolTip(self._t(f"control.mode_tip.{mode}"))
            self.settings_button.setText(self._t("settings.title"))
            self.settings_button.setToolTip(self._t("control.settings_tip"))
            self._refresh_summary()

        def _compact_mode_label(self, mode: str) -> str:
            labels = {
                "zh": {"solo": "单人", "serial": "串行", "full": "完全"},
                "en": {"solo": "Solo", "serial": "Serial", "full": "Full"},
            }
            return labels.get(self._language, labels["zh"]).get(mode, execution_mode_label(mode, self._language))

        def _refresh_summary(self) -> None:
            self.summary_label.setText(
                self._t("control.summary", mode=execution_mode_label(self._mode, self._language))
            )

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
            self._refresh_summary()
            self._building = False

        def set_enabled(self, enabled: bool) -> None:
            for btn in self._mode_buttons.values():
                btn.setEnabled(enabled)
            self.settings_button.setEnabled(enabled)

        def _on_mode_clicked(self, mode: str) -> None:
            self._mode = normalize_execution_mode(mode)
            self._refresh_summary()
            if not self._building:
                self.execution_mode_changed.emit(self._mode)
