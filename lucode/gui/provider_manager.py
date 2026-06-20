from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from catalog_system.model_probe import fetch_upstream_models
from lucode.gui.i18n import Translator, normalize_language
from runtime.config.model_config import (
    connect_provider,
    load_auth,
    load_lucode_config,
    load_provider_catalog,
    normalize_provider_id,
    provider_has_api_key,
    remove_provider_config,
    set_provider_models,
)


CUSTOM_PROVIDER_SENTINEL = "__custom_provider__"


class ProviderManagerDialog(QDialog):
    providers_changed = Signal()

    def __init__(
        self,
        *,
        workspace_root: Path | str,
        user_home: Path | str,
        privacy_mode: str = "local_first",
        language: str = "zh",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace_root = Path(workspace_root)
        self.user_home = Path(user_home)
        self.privacy_mode = str(privacy_mode or "local_first")
        self._language = normalize_language(language)
        self._t = Translator(self._language)
        self.catalog = load_provider_catalog()
        self._mode = "list"
        self._editing_provider_id = ""
        self._model_checks: dict[str, QCheckBox] = {}

        self.setObjectName("ProviderManagerDialog")
        self.setWindowTitle(self._t('provider.window'))
        self.resize(720, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self._build_list_page()
        self._build_form_page()
        self._set_custom_provider_visible(False)
        self.refresh_provider_list()

    def _build_list_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(self._t('provider.list_title'))
        title.setObjectName("ProviderManagerTitle")
        header.addWidget(title)
        header.addStretch(1)
        add_button = QPushButton(self._t('provider.add'))
        add_button.setObjectName("ProviderPrimaryButton")
        add_button.clicked.connect(self.start_add)
        header.addWidget(add_button)
        layout.addLayout(header)

        self.provider_list = QFrame()
        self.provider_list.setObjectName("ProviderList")
        self.provider_list_layout = QVBoxLayout(self.provider_list)
        self.provider_list_layout.setContentsMargins(0, 0, 0, 0)
        self.provider_list_layout.setSpacing(8)
        layout.addWidget(self.provider_list, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton(self._t('provider.close'))
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self.stack.addWidget(page)

    def _build_form_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.form_title = QLabel(self._t('provider.form.add'))
        self.form_title.setObjectName("ProviderManagerTitle")
        layout.addWidget(self.form_title)

        self.provider_combo = QComboBox()
        self._populate_provider_combo()
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addLayout(self._field_row(self._t('provider.field.provider'), self.provider_combo))

        self.custom_provider_id_edit = QLineEdit()
        self.custom_provider_id_edit.setPlaceholderText("my_proxy")
        self.custom_provider_id_edit.textChanged.connect(lambda _text: self._on_provider_changed())
        layout.addLayout(self._field_row(self._t('provider.field.provider_id'), self.custom_provider_id_edit))

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        layout.addLayout(self._field_row(self._t('provider.field.base_url'), self.base_url_edit))

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(self._t('provider.placeholder.keep_key'))
        layout.addLayout(self._field_row(self._t('provider.field.api_key'), self.api_key_edit))

        self.homepage_edit = QLineEdit()
        self.homepage_edit.setPlaceholderText("https://example.com")
        layout.addLayout(self._field_row(self._t('provider.field.homepage'), self.homepage_edit))

        action_row = QHBoxLayout()
        self.fetch_button = QPushButton(self._t('provider.fetch'))
        self.fetch_button.setObjectName("ProviderSecondaryButton")
        self.fetch_button.clicked.connect(self.fetch_models)
        action_row.addWidget(self.fetch_button)
        self.select_all_models = QCheckBox(self._t('provider.select_all'))
        self.select_all_models.toggled.connect(self._toggle_all_models)
        action_row.addWidget(self.select_all_models)
        action_row.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self._t('provider.search'))
        self.search_edit.textChanged.connect(self._filter_models)
        action_row.addWidget(self.search_edit)
        layout.addLayout(action_row)

        self.models_host = QFrame()
        self.models_host.setObjectName("ProviderModelsHost")
        models_host_layout = QVBoxLayout(self.models_host)
        models_host_layout.setContentsMargins(10, 10, 10, 10)
        models_host_layout.setSpacing(8)
        self.models_layout = QVBoxLayout()
        self.models_layout.setContentsMargins(0, 0, 0, 0)
        self.models_layout.setSpacing(6)
        models_host_layout.addLayout(self.models_layout)
        models_host_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("ProviderModelsScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.models_host)
        layout.addWidget(scroll, 1)

        self.manual_models_edit = QPlainTextEdit()
        self.manual_models_edit.setObjectName("ProviderManualModels")
        self.manual_models_edit.setPlaceholderText(self._t('provider.manual'))
        self.manual_models_edit.setFixedHeight(72)
        layout.addWidget(self.manual_models_edit)

        self.status_label = QLabel("")
        self.status_label.setObjectName("ProviderStatus")
        layout.addWidget(self.status_label)

        footer = QHBoxLayout()
        back_button = QPushButton(self._t('provider.back'))
        back_button.clicked.connect(self.show_list)
        footer.addWidget(back_button)
        footer.addStretch(1)
        self.save_button = QPushButton(self._t('provider.save'))
        self.save_button.setObjectName("ProviderPrimaryButton")
        self.save_button.clicked.connect(self.save_current_provider)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self.stack.addWidget(page)

    def _field_row(self, label: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        field_label = QLabel(label)
        field_label.setObjectName("ProviderFieldLabel")
        field_label.setMinimumWidth(76)
        row.addWidget(field_label)
        row.addWidget(widget, 1)
        return row

    def _populate_provider_combo(self) -> None:
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        seen: set[str] = set()
        self.provider_combo.addItem(self._t('provider.custom_combo'), CUSTOM_PROVIDER_SENTINEL)
        for provider_id, item in sorted(self.catalog.items()):
            display_name = str(item.get("display_name") or provider_id)
            self.provider_combo.addItem(display_name, provider_id)
            seen.add(provider_id)
        for provider_id, provider_config in sorted((load_lucode_config(workspace_root=self.workspace_root).get("provider") or {}).items()):
            provider_id = normalize_provider_id(provider_id)
            if provider_id in seen:
                continue
            display_name = str((provider_config or {}).get("display_name") or provider_id)
            self.provider_combo.addItem(display_name, provider_id)
        self.provider_combo.blockSignals(False)

    def _set_custom_provider_visible(self, visible: bool) -> None:
        self.custom_provider_id_edit.setVisible(bool(visible))

    def refresh_provider_list(self) -> None:
        while self.provider_list_layout.count():
            item = self.provider_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        config = load_lucode_config(workspace_root=self.workspace_root)
        providers = config.get("provider") or {}
        if not providers:
            empty = QLabel(self._t('provider.empty'))
            empty.setObjectName("ProviderEmpty")
            self.provider_list_layout.addWidget(empty)
            self.provider_list_layout.addStretch(1)
            return

        for provider_id, provider_config in sorted(providers.items()):
            if not isinstance(provider_config, dict):
                continue
            self.provider_list_layout.addWidget(self._provider_row(provider_id, provider_config))
        self.provider_list_layout.addStretch(1)

    def _provider_row(self, provider_id: str, provider_config: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("ProviderRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        name = QLabel(str(provider_config.get("display_name") or provider_id))
        name.setObjectName("ProviderName")
        layout.addWidget(name)

        models = [str(item) for item in provider_config.get("models") or [] if str(item).strip()]
        key_state = self._t("provider.local") if provider_config.get("local") else (self._t("provider.key_configured") if provider_has_api_key(provider_id, self.user_home) else self._t("provider.key_missing"))
        detail = QLabel(f"{key_state} · {self._t('provider.models_count', count=len(models))}")
        detail.setObjectName("ProviderDetail")
        layout.addWidget(detail)
        layout.addStretch(1)

        edit_button = QPushButton(self._t('provider.edit'))
        edit_button.clicked.connect(lambda _checked=False, pid=provider_id: self.start_edit(pid))
        layout.addWidget(edit_button)
        delete_button = QPushButton(self._t('provider.delete'))
        delete_button.setObjectName("ProviderDangerButton")
        delete_button.clicked.connect(lambda _checked=False, pid=provider_id: self.delete_provider(pid))
        layout.addWidget(delete_button)
        return row

    def start_add(self) -> None:
        self._mode = "add"
        self._editing_provider_id = ""
        self.form_title.setText(self._t('provider.form.add'))
        self._populate_provider_combo()
        if self.provider_combo.currentData() == CUSTOM_PROVIDER_SENTINEL and self.provider_combo.count() > 1:
            self.provider_combo.setCurrentIndex(1)
        self.provider_combo.setEnabled(True)
        self._set_custom_provider_visible(False)
        self.custom_provider_id_edit.clear()
        self.api_key_edit.clear()
        self.status_label.clear()
        self.manual_models_edit.clear()
        self._set_models([], checked=[])
        self._on_provider_changed()
        self.stack.setCurrentIndex(1)

    def start_custom_provider(self, provider_id: str = "") -> None:
        self._mode = "add"
        self._editing_provider_id = ""
        self.form_title.setText(self._t('provider.form.custom'))
        self._populate_provider_combo()
        idx = self.provider_combo.findData(CUSTOM_PROVIDER_SENTINEL)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.setEnabled(True)
        self._set_custom_provider_visible(True)
        self.custom_provider_id_edit.setText(str(provider_id or "").strip())
        self.api_key_edit.clear()
        self.homepage_edit.clear()
        self.base_url_edit.clear()
        self.manual_models_edit.clear()
        self.status_label.clear()
        self._set_models([], checked=[])
        self.stack.setCurrentIndex(1)

    def start_edit(self, provider_id: str) -> None:
        provider_id = normalize_provider_id(provider_id)
        config = load_lucode_config(workspace_root=self.workspace_root)
        provider_config = config.get("provider", {}).get(provider_id)
        if not isinstance(provider_config, dict):
            self.status_label.setText(self._t('provider.not_found', provider_id=provider_id))
            return

        self._mode = "edit"
        self._editing_provider_id = provider_id
        self.form_title.setText(self._t('provider.form.edit'))
        self._populate_provider_combo()
        idx = self.provider_combo.findData(provider_id)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.setEnabled(False)
        self._set_custom_provider_visible(False)
        self.custom_provider_id_edit.clear()
        self.base_url_edit.setText(str(provider_config.get("base_url") or ""))
        self.homepage_edit.setText(str(provider_config.get("homepage") or ""))
        self.api_key_edit.clear()
        models = [str(item).strip() for item in provider_config.get("models") or [] if str(item).strip()]
        self.manual_models_edit.setPlainText("\n".join(models))
        self.status_label.clear()
        self._set_models(models, checked=models)
        self.stack.setCurrentIndex(1)

    def show_list(self) -> None:
        self._mode = "list"
        self._editing_provider_id = ""
        self.refresh_provider_list()
        self.stack.setCurrentIndex(0)

    def fetch_models(self) -> None:
        try:
            provider_id = self._current_provider_id()
        except ValueError:
            self.status_label.setText(self._t('provider.error.provider_id'))
            return
        base_url = self.base_url_edit.text().strip()
        api_key = self.api_key_edit.text().strip()
        provider_info = self._provider_info(provider_id)
        backend_type = str(provider_info.get("compatible_type") or provider_info.get("backend_type") or "openai_compatible")
        if str(self.privacy_mode or "").lower() == "offline":
            self.status_label.setText(self._t('provider.offline'))
            return
        if not base_url:
            self.status_label.setText(self._t('provider.error.base_url'))
            return
        if backend_type != "ollama" and not api_key and not provider_info.get("local"):
            self.status_label.setText(self._t('provider.error.api_key'))
            return

        result = fetch_upstream_models(base_url, api_key, backend_type=backend_type)
        if not result.get("ok"):
            self.status_label.setText(str(result.get('error') or self._t('provider.fetch_failed')))
            return

        models = [str(item).strip() for item in result.get("models") or [] if str(item).strip()]
        self._set_models(models, checked=[])
        self.manual_models_edit.setPlainText("\n".join(models))
        self.status_label.setText(self._t('provider.fetch_ok', count=len(models)))

    def save_current_provider(self) -> bool:
        try:
            provider_id = self._current_provider_id()
        except ValueError:
            self.status_label.setText(self._t('provider.error.provider_id'))
            return False
        selected = self.selected_models() if self._model_checks else self._manual_models()
        if not selected:
            self.status_label.setText(self._t('provider.error.no_models'))
            return False

        provider_info = self._provider_info(provider_id)
        base_url = self.base_url_edit.text().strip()
        homepage = self.homepage_edit.text().strip()
        api_key = self.api_key_edit.text().strip()
        try:
            if self._mode == "edit":
                key_for_save = api_key or self._existing_api_key(provider_id)
                connect_provider(
                    provider_id,
                    api_key=key_for_save or None,
                    workspace_root=self.workspace_root,
                    user_home=self.user_home,
                    homepage=homepage,
                    base_url=base_url,
                    models=selected,
                    display_name=str(provider_info.get("display_name") or provider_id),
                    compatible_type=str(provider_info.get("compatible_type") or "openai_compatible"),
                    local=bool(provider_info.get("local", False)),
                    supports_tools=provider_info.get("supports_tools"),
                    custom=self._is_custom_provider(provider_id),
                )
                set_provider_models(provider_id, selected, workspace_root=self.workspace_root)
            else:
                connect_provider(
                    provider_id,
                    api_key=api_key or None,
                    workspace_root=self.workspace_root,
                    user_home=self.user_home,
                    homepage=homepage,
                    base_url=base_url,
                    models=selected,
                    display_name=str(provider_info.get("display_name") or provider_id),
                    compatible_type=str(provider_info.get("compatible_type") or "openai_compatible"),
                    local=bool(provider_info.get("local", False)),
                    supports_tools=provider_info.get("supports_tools"),
                    custom=self._is_custom_provider(provider_id),
                )
        except Exception as exc:
            self.status_label.setText(str(exc))
            return False

        self.providers_changed.emit()
        self.show_list()
        return True

    def delete_provider(self, provider_id: str, *, confirm: bool = False) -> bool:
        provider_id = normalize_provider_id(provider_id)
        if not confirm:
            result = QMessageBox.question(
                self,
                self._t('provider.delete_title'),
                self._t('provider.delete_prompt'),
            )
            if result != QMessageBox.Yes:
                return False
        remove_provider_config(
            provider_id,
            workspace_root=self.workspace_root,
            user_home=self.user_home,
            remove_auth=True,
        )
        self.providers_changed.emit()
        self.refresh_provider_list()
        return True

    def model_names(self) -> list[str]:
        return list(self._model_checks)

    def selected_models(self) -> list[str]:
        return [name for name, box in self._model_checks.items() if box.isChecked()]

    def set_model_checked(self, model_name: str, checked: bool) -> None:
        box = self._model_checks.get(model_name)
        if box is not None:
            box.setChecked(bool(checked))
            self._sync_select_all_state()

    def _set_models(self, models: list[str], *, checked: list[str]) -> None:
        while self.models_layout.count():
            item = self.models_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._model_checks = {}
        checked_set = set(checked or [])
        for model_name in models:
            if model_name in self._model_checks:
                continue
            box = QCheckBox(model_name)
            box.setObjectName("ProviderModelCheck")
            box.setChecked(model_name in checked_set)
            box.toggled.connect(lambda _checked=False: self._sync_select_all_state())
            self.models_layout.addWidget(box)
            self._model_checks[model_name] = box
        self.models_layout.addStretch(1)
        self._filter_models(self.search_edit.text())
        self._sync_select_all_state()

    def _toggle_all_models(self, checked: bool) -> None:
        for box in self._model_checks.values():
            box.setChecked(bool(checked))
        self._sync_select_all_state()

    def _sync_select_all_state(self) -> None:
        self.select_all_models.blockSignals(True)
        self.select_all_models.setChecked(bool(self._model_checks) and all(box.isChecked() for box in self._model_checks.values()))
        self.select_all_models.blockSignals(False)

    def _filter_models(self, text: str) -> None:
        needle = str(text or "").strip().lower()
        for model_name, box in self._model_checks.items():
            box.setVisible(not needle or needle in model_name.lower())

    def _manual_models(self) -> list[str]:
        raw = self.manual_models_edit.toPlainText().replace(",", "\n")
        models: list[str] = []
        for line in raw.splitlines():
            model_name = line.strip()
            if model_name and model_name not in models:
                models.append(model_name)
        return models

    def _existing_api_key(self, provider_id: str) -> str:
        provider_auth = (load_auth(user_home=self.user_home).get("providers") or {}).get(provider_id) or {}
        return str(provider_auth.get("api_key") or "").strip()

    def _current_provider_id(self) -> str:
        if self.provider_combo.currentData() == CUSTOM_PROVIDER_SENTINEL:
            provider_id = self.custom_provider_id_edit.text()
        else:
            provider_id = str(self.provider_combo.currentData() or self.provider_combo.currentText() or "")
        return normalize_provider_id(provider_id)

    def _is_custom_provider(self, provider_id: str) -> bool:
        return self.provider_combo.currentData() == CUSTOM_PROVIDER_SENTINEL or provider_id not in self.catalog

    def _provider_info(self, provider_id: str) -> dict:
        config = load_lucode_config(workspace_root=self.workspace_root)
        provider_config = config.get("provider", {}).get(provider_id)
        if isinstance(provider_config, dict):
            merged = dict(self.catalog.get(provider_id) or {})
            merged.update(provider_config)
            return merged
        if self._is_custom_provider(provider_id):
            return {
                "id": provider_id,
                "display_name": provider_id,
                "compatible_type": "openai_compatible",
                "supports_tools": "probe",
            }
        return dict(self.catalog.get(provider_id) or {"id": provider_id, "display_name": provider_id})

    def _on_provider_changed(self) -> None:
        if self._mode == "edit":
            return
        is_custom = self.provider_combo.currentData() == CUSTOM_PROVIDER_SENTINEL
        self._set_custom_provider_visible(is_custom)
        if is_custom:
            if not self.custom_provider_id_edit.text().strip():
                self.base_url_edit.clear()
                self.homepage_edit.clear()
                return
            if not self.base_url_edit.text().strip():
                self.base_url_edit.clear()
            if not self.homepage_edit.text().strip():
                self.homepage_edit.clear()
            return
        provider_id = self._current_provider_id()
        provider_info = self._provider_info(provider_id)
        self.base_url_edit.setText(str(provider_info.get("base_url") or ""))
        self.homepage_edit.setText(str(provider_info.get("homepage") or ""))
        default_models = [str(item).strip() for item in provider_info.get("models") or [] if str(item).strip()]
        self.manual_models_edit.setPlainText("\n".join(default_models))

