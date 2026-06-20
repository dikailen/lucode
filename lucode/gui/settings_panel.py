from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from lucode.gui.i18n import Translator, normalize_language
from lucode.gui.provider_manager import ProviderManagerDialog
from lucode.gui.settings_dialog import SettingsContent


class SettingsSidePanel(QFrame):
    providers_changed = Signal()

    def __init__(
        self,
        *,
        settings_content: SettingsContent,
        workspace_root: Path | str,
        user_home: Path | str,
        privacy_mode: str = "local_first",
        language: str = "zh",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsSidePanel")
        self.setMinimumWidth(436)
        self.setMaximumWidth(436)
        self.workspace_root = Path(workspace_root)
        self.user_home = Path(user_home)
        self.privacy_mode = str(privacy_mode or "local_first")
        self._language = normalize_language(language)
        self._t = Translator(self._language)
        self.settings_content = settings_content

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 24, 18, 18)
        root.setSpacing(18)

        header = QFrame(self)
        header.setObjectName("SettingsPanelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.back_button = QPushButton(self._t("provider.back"))
        self.back_button.setObjectName("SettingsPanelBackButton")
        self.back_button.clicked.connect(self.show_settings)
        self.back_button.hide()
        header_layout.addWidget(self.back_button)

        self.title_label = QLabel(self._t("settings.title"))
        self.title_label.setObjectName("SettingsPanelTitle")
        header_layout.addWidget(self.title_label, 1)

        self.close_button = QPushButton("X")
        self.close_button.setObjectName("SettingsPanelCloseButton")
        self.close_button.setToolTip(self._t("settings.close"))
        self.close_button.clicked.connect(self.hide)
        header_layout.addWidget(self.close_button)
        root.addWidget(header)

        self.stack = QStackedWidget(self)
        self.stack.setObjectName("SettingsPanelStack")
        root.addWidget(self.stack, 1)

        self.settings_content.setParent(self.stack)
        self.stack.addWidget(self.settings_content)

        self.provider_content = self._create_provider_content()
        self.stack.addWidget(self.provider_content)
        self.stack.setCurrentWidget(self.settings_content)
        self.hide()

    def _create_provider_content(self) -> ProviderManagerDialog:
        content = ProviderManagerDialog(
            workspace_root=self.workspace_root,
            user_home=self.user_home,
            privacy_mode=self.privacy_mode,
            language=self._language,
            parent=self,
            embedded=True,
        )
        content.setWindowFlags(Qt.Widget)
        content.setModal(False)
        content.providers_changed.connect(self.providers_changed.emit)
        content.close_requested.connect(self.show_settings)
        return content

    def current_view(self) -> str:
        return "providers" if self.stack.currentWidget() is self.provider_content else "settings"

    def show_settings(self, page: str = "") -> None:
        if page:
            self.settings_content.select_page(page)
        self.stack.setCurrentWidget(self.settings_content)
        self.back_button.hide()
        self.title_label.setText(self._t("settings.title"))
        self.show()
        self._ensure_panel_width()
        self.raise_()

    def show_provider_manager(self, *, custom: bool = False) -> None:
        self.provider_content.privacy_mode = self.privacy_mode
        if custom:
            self.provider_content.start_custom_provider()
        else:
            self.provider_content.show_list()
        self.stack.setCurrentWidget(self.provider_content)
        self.back_button.show()
        self.title_label.setText(self._t("provider.window"))
        self.show()
        self._ensure_panel_width()
        self.raise_()

    def set_language(self, language: str) -> None:
        normalized = normalize_language(language)
        if normalized != self._language:
            provider_visible = self.current_view() == "providers"
            self._language = normalized
            self._t = Translator(self._language)
            self._replace_provider_content()
            if provider_visible:
                self.stack.setCurrentWidget(self.provider_content)
        else:
            self._language = normalized
            self._t = Translator(self._language)
        self.back_button.setText(self._t("provider.back"))
        self.close_button.setToolTip(self._t("settings.close"))
        self.title_label.setText(self._t("provider.window") if self.current_view() == "providers" else self._t("settings.title"))

    def _replace_provider_content(self) -> None:
        old = self.provider_content
        old.blockSignals(True)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.provider_content = self._create_provider_content()
        self.stack.addWidget(self.provider_content)

    def _ensure_panel_width(self) -> None:
        parent = self.parentWidget()
        if not isinstance(parent, QSplitter):
            return
        sizes = parent.sizes()
        index = parent.indexOf(self)
        if index < 0 or len(sizes) <= index:
            return
        target = self.maximumWidth()
        if sizes[index] >= self.minimumWidth():
            return
        available = sum(sizes)
        if available <= 0:
            return
        sizes[index] = target
        largest = max((i for i in range(len(sizes)) if i != index), key=lambda i: sizes[i], default=-1)
        if largest >= 0:
            sizes[largest] = max(120, sizes[largest] - target)
        parent.setSizes(sizes)

    def set_privacy_mode(self, privacy_mode: str) -> None:
        self.privacy_mode = str(privacy_mode or "local_first")
        self.provider_content.privacy_mode = self.privacy_mode

    def set_enabled(self, enabled: bool) -> None:
        self.settings_content.set_enabled(enabled)
        self.provider_content.setEnabled(bool(enabled))
