from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lucode.gui.control_panel import (
    MODEL_ROLES,
    ROLE_CONDITION_HINTS_ZH,
    _index_for_value,
    privacy_mode_options,
    query_refiner_available_for_mode,
    roles_for_mode,
    worker_pool_available_for_mode,
)
from runtime.config.execution_mode import normalize_execution_mode
from runtime.safety.privacy import normalize_privacy_mode


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
        hint = QLabel("主管只会从勾选模型中组建团队，空=不限制")
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


class SettingsDialog(QDialog):
    privacy_mode_changed = Signal(str)
    role_model_changed = Signal(str, str)
    query_refiner_toggled = Signal(bool)
    worker_pool_changed = Signal(list)
    provider_manager_requested = Signal()
    custom_provider_requested = Signal()
    language_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("设置")
        self.setModal(False)
        self.resize(760, 560)

        self._models: list[tuple[str, str]] = []
        self._role_models: dict[str, str] = {}
        self._worker_pool: list[str] = []
        self._mode = "solo"
        self._language = "zh"
        self._refiner_enabled = False
        self._building = True
        self._role_rows: dict[str, _RoleRow] = {}
        self._pool_row: _WorkerPoolRow | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("SettingsTitle")
        outer.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(14)
        outer.addLayout(body, 1)

        nav = QFrame()
        nav.setObjectName("SettingsNav")
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)
        body.addWidget(nav)

        self._tab_buttons: dict[str, QPushButton] = {}
        for key, text in (
            ("Models", "模型"),
            ("Privacy", "隐私"),
            ("Providers", "接入"),
            ("Language", "语言"),
            ("Shortcuts", "快捷键"),
            ("About", "关于"),
        ):
            button = QPushButton(text)
            button.setObjectName(f"SettingsTab{key}")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=key: self._select_page(page))
            nav_layout.addWidget(button)
            self._tab_buttons[key] = button
        nav_layout.addStretch(1)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("SettingsContentStack")
        body.addWidget(self.content_stack, 1)

        self._pages: dict[str, QWidget] = {}
        self._build_models_page()
        self._build_privacy_page()
        self._build_providers_page()
        self._build_language_page()
        self._build_shortcuts_page()
        self._build_about_page()

        self.privacy_combo = QComboBox()
        self.privacy_combo.setObjectName("PrivacyModeCombo")
        for key, text in privacy_mode_options():
            self.privacy_combo.addItem(text, key)
        self.privacy_combo.currentIndexChanged.connect(self._emit_privacy_mode)
        self._privacy_controls_layout.addWidget(self.privacy_combo)

        self.refiner_toggle = QPushButton("前置优化")
        self.refiner_toggle.setObjectName("QueryRefinerToggle")
        self.refiner_toggle.setCheckable(True)
        self.refiner_toggle.toggled.connect(self._on_refiner_toggled)
        self._models_actions_layout.addWidget(self.refiner_toggle)

        self.provider_manager_button = QPushButton("管理服务商")
        self.provider_manager_button.setObjectName("ProviderManagerButton")
        self.provider_manager_button.setToolTip("管理服务商、API 密钥和可用模型")
        self.provider_manager_button.clicked.connect(self.provider_manager_requested.emit)
        self._providers_actions_layout.addWidget(self.provider_manager_button)

        self.custom_provider_button = QPushButton("添加自定义中转")
        self.custom_provider_button.setObjectName("CustomProviderButton")
        self.custom_provider_button.setToolTip("创建 OpenAI 兼容的中转服务商")
        self.custom_provider_button.clicked.connect(self.custom_provider_requested.emit)
        self._providers_actions_layout.addWidget(self.custom_provider_button)
        self._providers_actions_layout.addStretch(1)

        self.roles_host = QFrame()
        self.roles_host.setObjectName("RolesHost")
        self._roles_layout = QVBoxLayout(self.roles_host)
        self._roles_layout.setContentsMargins(0, 0, 0, 0)
        self._roles_layout.setSpacing(6)
        self._models_page_layout.addWidget(self.roles_host, 1)

        self._select_page("Models")

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        outer.addLayout(footer)
        self._building = False

    def _build_models_page(self) -> None:
        page, layout = self._new_page("Models", "模型")
        description = QLabel("配置不同执行角色使用的模型。")
        description.setObjectName("SettingsDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        self._models_actions_layout = QHBoxLayout()
        self._models_actions_layout.setSpacing(8)
        layout.addLayout(self._models_actions_layout)
        self._models_page_layout = layout
        self._add_page("Models", page)

    def _build_privacy_page(self) -> None:
        page, layout = self._new_page("Privacy", "隐私")
        self.privacy_hint = QLabel("离线模式会阻止图形界面在发现服务商模型时请求上游接口。")
        self.privacy_hint.setObjectName("PrivacyModeHint")
        self.privacy_hint.setWordWrap(True)
        layout.addWidget(self.privacy_hint)
        self._privacy_controls_layout = QHBoxLayout()
        self._privacy_controls_layout.setSpacing(10)
        label = QLabel("隐私模式")
        label.setObjectName("FieldLabel")
        self._privacy_controls_layout.addWidget(label)
        layout.addLayout(self._privacy_controls_layout)
        layout.addStretch(1)
        self._add_page("Privacy", page)

    def _build_providers_page(self) -> None:
        page, layout = self._new_page("Providers", "接入")
        description = QLabel("连接 API 服务商、配置密钥，并添加 OpenAI 兼容中转。")
        description.setObjectName("SettingsDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        self._providers_actions_layout = QHBoxLayout()
        self._providers_actions_layout.setSpacing(8)
        layout.addLayout(self._providers_actions_layout)
        layout.addStretch(1)
        self._add_page("Providers", page)


    def _build_language_page(self) -> None:
        page, layout = self._new_page("Language", "语言")
        description = QLabel("选择界面显示语言。当前版本先以中文界面为主，英文界面将在后续版本补齐。")
        description.setObjectName("SettingsDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.language_group = QButtonGroup(self)
        self.language_group.setExclusive(True)
        self.language_zh_button = QPushButton("中文")
        self.language_zh_button.setObjectName("LanguageZhButton")
        self.language_zh_button.setCheckable(True)
        self.language_zh_button.setChecked(True)
        self.language_en_button = QPushButton("英文")
        self.language_en_button.setObjectName("LanguageEnButton")
        self.language_en_button.setCheckable(True)
        self.language_en_button.setToolTip("英文界面将在后续版本补齐")
        self.language_zh_button.clicked.connect(lambda _checked=False: self._set_language("zh"))
        self.language_en_button.clicked.connect(lambda _checked=False: self._set_language("en"))
        for button in (self.language_zh_button, self.language_en_button):
            self.language_group.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self._add_page("Language", page)

    def _build_shortcuts_page(self) -> None:
        page, layout = self._new_page("Shortcuts", "快捷键")
        for text in ("回车：发送", "Shift+回车：换行", "停止：取消当前轮"):
            item = QLabel(text)
            item.setObjectName("SettingsDescription")
            layout.addWidget(item)
        layout.addStretch(1)
        self._add_page("Shortcuts", page)

    def _build_about_page(self) -> None:
        page, layout = self._new_page("About", "关于")
        about = QLabel("Lucode 桌面工作台")
        about.setObjectName("SettingsDescription")
        about.setWordWrap(True)
        layout.addWidget(about)
        layout.addStretch(1)
        self._add_page("About", page)

    def _new_page(self, key: str, title_text: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName(f"SettingsPage{key}")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title = QLabel(title_text)
        title.setObjectName(f"SettingsPageTitle{key}")
        layout.addWidget(title)
        return page, layout

    def _add_page(self, key: str, page: QWidget) -> None:
        self._pages[key] = page
        self.content_stack.addWidget(page)

    def _select_page(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        self.content_stack.setCurrentWidget(page)
        for tab_key, button in self._tab_buttons.items():
            button.setChecked(tab_key == key)

    def select_page(self, key: str) -> None:
        self._select_page(str(key or ""))

    def current_language(self) -> str:
        return self._language

    def _set_language(self, language: str) -> None:
        normalized = "en" if str(language or "").lower().startswith("en") else "zh"
        self._language = normalized
        self.language_zh_button.setChecked(normalized == "zh")
        self.language_en_button.setChecked(normalized == "en")
        self.language_changed.emit(normalized)

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
        self.privacy_combo.setCurrentIndex(
            _index_for_value(privacy_mode_options(), normalize_privacy_mode(privacy_mode))
        )
        self.refiner_toggle.setChecked(self._refiner_enabled)
        self._building = False
        self._rebuild_role_rows()

    def set_execution_mode(self, mode: str) -> None:
        self._mode = normalize_execution_mode(mode)
        self._rebuild_role_rows()

    def set_enabled(self, enabled: bool) -> None:
        self.privacy_combo.setEnabled(enabled)
        self.refiner_toggle.setEnabled(enabled)
        self.provider_manager_button.setEnabled(enabled)
        self.custom_provider_button.setEnabled(enabled)
        for row in self._role_rows.values():
            row.combo.setEnabled(enabled)
        if self._pool_row is not None:
            self._pool_row.set_enabled(enabled)

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
            row.combo.setObjectName(f"RoleModelCombo_{role}")
            for model_id, text in self._models:
                row.combo.addItem(text, model_id)
            target = self._role_models.get(role) or ""
            idx = row.combo.findData(target)
            row.combo.setCurrentIndex(idx if idx >= 0 else 0)
            row.combo.currentIndexChanged.connect(lambda _i, r=role: self._emit_role_model(r))
            self._roles_layout.addWidget(row)
            self._role_rows[role] = row
        self._building = False

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
