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
        QCheckBox,
        QComboBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    _PYSIDE_AVAILABLE = True
except ModuleNotFoundError:
    _PYSIDE_AVAILABLE = False


if _PYSIDE_AVAILABLE:

    class _RoleRow(QFrame):
        def __init__(self, role: str, label: str, usage: str, parent=None):
            super().__init__(parent)
            self.role = role
            self.setObjectName("RoleRow")
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(10)

            name = QLabel(label)
            name.setObjectName("RoleName")
            layout.addWidget(name)

            if usage == "conditional":
                hint = QLabel(ROLE_CONDITION_HINTS_ZH.get(role, "条件启用"))
                hint.setObjectName("RoleHint")
                layout.addWidget(hint)

            layout.addStretch(1)
            self.combo = QComboBox()
            self.combo.setMinimumWidth(220)
            layout.addWidget(self.combo)

    class _WorkerPoolRow(QFrame):
        """Full team mode: pick which models the supervisor may assign to workers."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("RoleRow")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(6)

            header = QHBoxLayout()
            name = QLabel(MODEL_ROLES["executor"]["label"] + "（员工可用模型池）")
            name.setObjectName("RoleName")
            header.addWidget(name)
            hint = QLabel("主管只能从勾选模型中组建团队，空=不限制")
            hint.setObjectName("RoleHint")
            header.addWidget(hint)
            header.addStretch(1)
            layout.addLayout(header)

            self._checks_host = QFrame()
            self._checks_layout = QHBoxLayout(self._checks_host)
            self._checks_layout.setContentsMargins(0, 0, 0, 0)
            self._checks_layout.setSpacing(12)
            layout.addWidget(self._checks_host)
            self._checks: list[QCheckBox] = []

        def set_models(self, models, selected) -> None:
            while self._checks_layout.count():
                item = self._checks_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._checks = []
            selected_set = {str(item) for item in (selected or [])}
            for model_id, text in models:
                box = QCheckBox(text)
                box.setProperty("model_id", model_id)
                box.setChecked(model_id in selected_set)
                self._checks_layout.addWidget(box)
                self._checks.append(box)
            self._checks_layout.addStretch(1)

        def selected_models(self) -> list[str]:
            return [str(box.property("model_id")) for box in self._checks if box.isChecked()]

        def connect_changed(self, callback) -> None:
            for box in self._checks:
                box.toggled.connect(lambda _checked: callback())

        def set_enabled(self, enabled: bool) -> None:
            for box in self._checks:
                box.setEnabled(enabled)

    class ControlBar(QFrame):
        execution_mode_changed = Signal(str)
        privacy_mode_changed = Signal(str)
        role_model_changed = Signal(str, str)
        query_refiner_toggled = Signal(bool)
        worker_pool_changed = Signal(list)

        def __init__(self, parent: QWidget | None = None):
            super().__init__(parent)
            self.setObjectName("ControlBar")
            self._models: list[tuple[str, str]] = []
            self._role_models: dict[str, str] = {}
            self._worker_pool: list[str] = []
            self._mode = "solo"
            self._refiner_enabled = False
            self._building = True
            self._role_rows: dict[str, _RoleRow] = {}
            self._pool_row: _WorkerPoolRow | None = None

            outer = QVBoxLayout(self)
            outer.setContentsMargins(14, 12, 14, 12)
            outer.setSpacing(10)

            self._build_top_row(outer)
            self._build_roles_area(outer)
            self._building = False

        def _build_top_row(self, outer) -> None:
            top = QHBoxLayout()
            top.setSpacing(12)
            outer.addLayout(top)

            seg_label = QLabel("执行模式")
            seg_label.setObjectName("FieldLabel")
            top.addWidget(seg_label)

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
            top.addLayout(seg)

            priv_label = QLabel("隐私模式")
            priv_label.setObjectName("FieldLabel")
            top.addWidget(priv_label)
            self.privacy_combo = QComboBox()
            for key, text in privacy_mode_options():
                self.privacy_combo.addItem(text, key)
            self.privacy_combo.currentIndexChanged.connect(self._emit_privacy_mode)
            top.addWidget(self.privacy_combo)

            self.refiner_toggle = QPushButton("前置优化")
            self.refiner_toggle.setObjectName("ToggleButton")
            self.refiner_toggle.setCheckable(True)
            self.refiner_toggle.toggled.connect(self._on_refiner_toggled)
            top.addWidget(self.refiner_toggle)
            top.addStretch(1)

        def _build_roles_area(self, outer) -> None:
            self.roles_host = QFrame()
            self.roles_host.setObjectName("RolesHost")
            self._roles_layout = QVBoxLayout(self.roles_host)
            self._roles_layout.setContentsMargins(0, 0, 0, 0)
            self._roles_layout.setSpacing(6)
            outer.addWidget(self.roles_host)

        def _rebuild_role_rows(self) -> None:
            self._building = True
            while self._roles_layout.count():
                item = self._roles_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._role_rows.clear()
            self._pool_row = None

            rows: list[tuple[str, str]] = list(roles_for_mode(self._mode))
            if self._refiner_enabled and query_refiner_available_for_mode(self._mode):
                rows = [("query_refiner", "always")] + rows

            use_pool = worker_pool_available_for_mode(self._mode)
            for role, usage in rows:
                if role == "executor" and use_pool:
                    pool_row = _WorkerPoolRow()
                    pool_row.set_models(self._models, self._worker_pool)
                    pool_row.connect_changed(self._emit_worker_pool)
                    self._roles_layout.addWidget(pool_row)
                    self._pool_row = pool_row
                    continue
                label = MODEL_ROLES[role]["label"]
                row = _RoleRow(role, label, usage)
                for model_id, text in self._models:
                    row.combo.addItem(text, model_id)
                target = self._role_models.get(role) or ""
                idx = row.combo.findData(target)
                row.combo.setCurrentIndex(idx if idx >= 0 else 0)
                row.combo.currentIndexChanged.connect(lambda _i, r=role: self._emit_role_model(r))
                self._roles_layout.addWidget(row)
                self._role_rows[role] = row
            self._building = False

        def set_models(self, models: list[tuple[str, str]]) -> None:
            self._models = list(models)
            self._rebuild_role_rows()

        def set_initial(
            self,
            *,
            execution_mode: str,
            privacy_mode: str,
            role_models: dict[str, str],
            query_refiner_enabled: bool = False,
            worker_pool: list[str] | None = None,
        ) -> None:
            self._building = True
            self._mode = normalize_execution_mode(execution_mode)
            self._role_models = dict(role_models)
            self._worker_pool = list(worker_pool or [])
            self._refiner_enabled = bool(query_refiner_enabled)
            btn = self._mode_buttons.get(self._mode)
            if btn is not None:
                btn.setChecked(True)
            self.privacy_combo.setCurrentIndex(
                _index_for_value(privacy_mode_options(), normalize_privacy_mode(privacy_mode))
            )
            self.refiner_toggle.setChecked(self._refiner_enabled)
            self._building = False
            self._rebuild_role_rows()

        def set_enabled(self, enabled: bool) -> None:
            for btn in self._mode_buttons.values():
                btn.setEnabled(enabled)
            self.privacy_combo.setEnabled(enabled)
            self.refiner_toggle.setEnabled(enabled)
            for row in self._role_rows.values():
                row.combo.setEnabled(enabled)
            if self._pool_row is not None:
                self._pool_row.set_enabled(enabled)

        def _on_mode_clicked(self, mode: str) -> None:
            self._mode = normalize_execution_mode(mode)
            self._rebuild_role_rows()
            if not self._building:
                self.execution_mode_changed.emit(self._mode)

        def _on_refiner_toggled(self, checked: bool) -> None:
            self._refiner_enabled = bool(checked)
            self._rebuild_role_rows()
            if not self._building:
                self.query_refiner_toggled.emit(self._refiner_enabled)

        def _emit_privacy_mode(self) -> None:
            if not self._building:
                self.privacy_mode_changed.emit(str(self.privacy_combo.currentData() or ""))

        def _emit_role_model(self, role: str) -> None:
            if self._building:
                return
            row = self._role_rows.get(role)
            if row is not None:
                model_id = str(row.combo.currentData() or "")
                self._role_models[role] = model_id
                self.role_model_changed.emit(role, model_id)

        def _emit_worker_pool(self) -> None:
            if self._building or self._pool_row is None:
                return
            self._worker_pool = self._pool_row.selected_models()
            self.worker_pool_changed.emit(list(self._worker_pool))
