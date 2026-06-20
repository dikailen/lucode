from __future__ import annotations

from string import Template


TOKENS = {
    "bg": "#f7f8fb",
    "surface": "#ffffff",
    "surface_raised": "#f8fafc",
    "border": "#d8dee8",
    "border_subtle": "#e6ebf2",
    "user_surface": "#eef4ff",
    "text": "#111827",
    "text_muted": "#64748b",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "danger": "#dc2626",
    "success": "#16a34a",
    "warning": "#d97706",
    "font_ui": '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
    "font_mono": '"Cascadia Code", Consolas, "Courier New", monospace',
    "font_size": "14px",
    "font_size_small": "12px",
    "radius_small": "10px",
    "radius_bubble": "16px",
    "radius_pill": "999px",
    "space_1": "4px",
    "space_2": "8px",
    "space_3": "12px",
    "space_4": "16px",
}


QSS_TEMPLATE = Template(
    """
QWidget {
  background: $bg;
  color: $text;
  font-family: $font_ui;
  font-size: $font_size;
}

QMainWindow {
  background: $bg;
}

QWidget#ChatPane {
  background: $surface;
  border-left: 1px solid $border_subtle;
  border-right: 1px solid $border_subtle;
}

QLabel {
  background: transparent;
}

QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {
  background: $surface;
  border: none;
}

QFrame#MessageBubble {
  border: 1px solid $border_subtle;
  border-radius: $radius_bubble;
  padding: 0;
}

QFrame#MessageBubble[userRole="true"] {
  background: $user_surface;
  border: none;
  color: $text;
}

QLabel#RoleLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#UserText {
  color: $text;
  line-height: 150%;
}

QLabel#EmptyState {
  color: $text_muted;
}

QFrame#ComposerShell {
  background: $surface;
  border: 1px solid $border;
  border-radius: 20px;
}

QFrame#ComposerInputRow {
  background: transparent;
  border: none;
}

QFrame#ComposerShell QPlainTextEdit {
  background: $surface;
  border: 1px solid $border_subtle;
  border-radius: 14px;
  padding: $space_2;
  selection-background-color: $primary;
}

QFrame#ComposerShell QPlainTextEdit:focus {
  border: 1px solid $primary;
}

QFrame#ComposerShell QPlainTextEdit:disabled {
  background: $surface;
  border-color: $border;
  color: $text_muted;
}

QPushButton {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 7px 14px;
}

QPushButton:hover {
  border-color: $primary_hover;
}

QPushButton:disabled {
  color: $text_muted;
  background: $surface;
}

QPushButton#SendButton {
  background: $surface_raised;
  border-color: $border_subtle;
  color: $text;
  border-radius: $radius_pill;
}

QPushButton#SendButton:hover {
  background: $user_surface;
  border-color: $border;
}

QPushButton#SendButton:disabled {
  background: $surface;
  border-color: $border_subtle;
  color: $text_muted;
}

QPushButton#StopButton {
  color: $danger;
}

QPushButton#StopButton:disabled {
  color: $text_muted;
}

QStatusBar {
  background: $surface;
  border-top: 1px solid $border;
  color: $text_muted;
}

QFrame#ControlBar {
  background: $surface;
  border: 1px solid $border_subtle;
  border-radius: 14px;
}

QFrame#SessionSidebar {
  background: $bg;
  border-right: none;
}
QFrame#SessionSidebar[collapsed="true"] {
  background: $surface;
  border-right: 1px solid $border;
}

QFrame#SidebarFullContent {
  background: transparent;
  border: none;
}

QFrame#SidebarIconRail {
  background: $surface;
  border: none;
}

QLabel#SidebarRailLogo {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: 14px;
  color: $primary;
  font-weight: 700;
  min-height: 38px;
  max-height: 38px;
}

QPushButton#SidebarRailChats,
QPushButton#SidebarRailSkills,
QPushButton#SidebarRailMcp {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 14px;
  color: $text_muted;
  min-height: 38px;
  max-height: 38px;
  padding: 0;
}

QPushButton#SidebarRailChats:hover,
QPushButton#SidebarRailSkills:hover,
QPushButton#SidebarRailMcp:hover {
  background: $surface_raised;
  border-color: $border_subtle;
  color: $text;
}

QPushButton#SidebarRailChats:checked,
QPushButton#SidebarRailSkills:checked,
QPushButton#SidebarRailMcp:checked {
  background: $user_surface;
  border-color: $border;
  color: $primary;
}

QLabel#SidebarTitle {
  color: $text;
  font-weight: 600;
}

QLabel#SidebarEmpty {
  color: $text_muted;
  font-size: $font_size_small;
}

QLineEdit#SessionSearchBox {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
  padding: 6px 10px;
  color: $text;
}

QLineEdit#SessionSearchBox:focus {
  border-color: $primary;
}

QScrollArea#SessionListScroll {
  background: transparent;
  border: none;
}

QFrame#SessionRow {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: 12px;
}

QFrame#SessionRow[selected="true"] {
  border-left: 3px solid $primary;
}

QPushButton#SessionRowButton {
  background: transparent;
  border: none;
  border-radius: 10px;
  padding: 6px 8px;
  text-align: left;
  color: $text;
}

QPushButton#SessionDeleteButton {
  background: transparent;
  border: none;
  color: $text_muted;
  padding: 4px 6px;
}

QPushButton#SessionDeleteButton:hover {
  color: $danger;
}

QPushButton#SidebarNewSessionButton {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
  padding: 7px 12px;
  text-align: left;
}

QPushButton#SidebarNewSessionButton:hover {
  border-color: $primary_hover;
}

QPushButton#SidebarTabChats,
QPushButton#SidebarTabSkills,
QPushButton#SidebarTabMcp {
  background: transparent;
  border: 1px solid transparent;
  border-radius: $radius_pill;
  padding: 5px 10px;
  color: $text_muted;
}

QPushButton#SidebarTabChats:hover,
QPushButton#SidebarTabSkills:hover,
QPushButton#SidebarTabMcp:hover {
  color: $text;
  background: $surface_raised;
  border-color: $border_subtle;
}

QPushButton#SidebarTabChats:checked,
QPushButton#SidebarTabSkills:checked,
QPushButton#SidebarTabMcp:checked {
  color: $text;
  background: $user_surface;
  border-color: $border;
}

QLabel#SkillPanelTitle,
QLabel#McpPanelTitle {
  color: $text_muted;
  font-size: $font_size_small;
  font-weight: 600;
  padding: 2px 4px 4px 4px;
}

QFrame#SkillCardRow,
QFrame#McpStatusRow {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: 12px;
}

QPushButton#SkillCardButton {
  background: transparent;
  border: none;
  border-radius: $radius_small;
  padding: 8px;
  text-align: left;
  color: $text;
}

QPushButton#SkillCardButton:hover {
  background: $user_surface;
}

QLabel#McpName {
  color: $text;
}

QLabel#McpStatus,
QLabel#McpDetail {
  color: $text_muted;
  font-size: $font_size_small;
}

QFrame#ChatHeader {
  background: $surface;
  border-bottom: 1px solid $border_subtle;
}

QLabel#SessionTitleLabel {
  color: $text;
  font-size: 18px;
  font-weight: 600;
}

QLabel#TopStatusChip {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: 10px;
  padding: 8px 14px;
  color: $success;
  font-size: $font_size_small;
}

QLabel#TopStatusChip[state="running"] {
  color: $success;
}

QLabel#TopStatusChip[state="stopped"] {
  color: $warning;
}

QLabel#TopStatusChip[state="failed"] {
  color: $danger;
}

QWidget#TopModeHost {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: 10px;
}

QPushButton#TopModeButton {
  background: transparent;
  border: none;
  border-radius: 8px;
  padding: 8px 14px;
  color: $text;
}

QPushButton#TopModeButton:checked {
  background: $surface;
  color: $primary;
}

QPushButton#TopSettingsButton {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: 10px;
  padding: 8px 13px;
  color: $text;
}

QFrame#SidebarUtilityBar {
  background: transparent;
  border: none;
}

QPushButton#SidebarSettingsButton,
QPushButton#SidebarToggleButton {
  background: transparent;
  border: none;
  border-radius: $radius_small;
  padding: 4px 8px;
  color: $text_muted;
}

QPushButton#SidebarSettingsButton {
  text-align: left;
}

QPushButton#SidebarSettingsButton:hover,
QPushButton#SidebarToggleButton:hover {
  color: $text;
  background: $surface_raised;
}

QPushButton#SidebarRailSettingsButton,
QPushButton#SidebarRailToggleButton {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 14px;
  color: $text_muted;
  min-height: 38px;
  max-height: 38px;
  padding: 0;
}

QPushButton#SidebarRailSettingsButton:hover,
QPushButton#SidebarRailToggleButton:hover {
  background: $surface_raised;
  border-color: $border_subtle;
  color: $text;
}

QFrame#ComposerToolbar {
  background: transparent;
  border: none;
}

QFrame#ControlBar QLabel#FieldLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QPushButton#SegButton {
  background: $surface;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 6px 16px;
  color: $text_muted;
}

QPushButton#SegButton:first-child {
  border-top-left-radius: $radius_pill;
  border-bottom-left-radius: $radius_pill;
}

QPushButton#SegButton:last-child {
  border-top-right-radius: $radius_pill;
  border-bottom-right-radius: $radius_pill;
}

QPushButton#SegButton:hover {
  border-color: $primary_hover;
}

QPushButton#SegButton:checked {
  background: $primary;
  border-color: $primary;
  color: $bg;
}

QPushButton#SegButton:disabled {
  color: $text_muted;
  background: $surface;
}

QPushButton#ToggleButton {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 6px 14px;
  color: $text_muted;
}

QPushButton#ToggleButton:checked {
  background: $user_surface;
  border-color: $border;
  color: $text;
}

QFrame#RolesHost {
  background: transparent;
}

QFrame#RoleRow {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
}

QFrame#RoleRow QLabel#RoleName {
  color: $text;
}

QFrame#RoleRow QLabel#RoleHint {
  color: $warning;
  font-size: $font_size_small;
}

QComboBox {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
  padding: 5px 10px;
}

QComboBox:hover {
  border-color: $primary_hover;
}

QComboBox QAbstractItemView {
  background: $surface_raised;
  border: 1px solid $border;
  selection-background-color: $primary;
  selection-color: $bg;
}

QFrame#WorkArea {
  background: transparent;
  border: none;
}

QPushButton#WorkAreaHeader {
  background: transparent;
  border: none;
  padding: 2px 0;
  text-align: left;
  color: $primary;
  font-weight: 600;
}

QPushButton#WorkAreaHeader:hover {
  color: $primary_hover;
}

QLabel#SupervisorActivity {
  color: $text_muted;
  font-family: $font_mono;
  font-size: $font_size_small;
}

QFrame#WorkerNode {
  background: $surface;
  border: 1px solid $border_subtle;
  border-left: 2px solid $border_subtle;
  border-radius: 12px;
}

QPushButton#NodeToggle {
  background: transparent;
  border: none;
  padding: 0;
  color: $text_muted;
  text-align: left;
}

QPushButton#NodeToggle:hover {
  color: $primary;
}

QPushButton#NodeToggle:checked {
  color: $primary;
}

QLabel#NodeLatest {
  color: $text_muted;
  font-family: $font_mono;
  font-size: $font_size_small;
}

QFrame#AnswerBlock {
  background: transparent;
  border: none;
}

QLabel#AnswerText {
  color: $text;
  line-height: 150%;
}

QFrame#ErrorRecoveryPanel {
  background: transparent;
  border: none;
}

QLabel#RunFailedTitle {
  color: $danger;
  font-weight: 600;
}

QLabel#RunFailedReason {
  color: $text_muted;
  font-size: $font_size_small;
}

QPushButton#RunFailedRetryButton {
  background: $surface_raised;
  border-color: $primary;
  color: $primary;
  border-radius: 14px;
  padding: 7px 14px;
}

QPushButton#RunFailedSwitchModelButton,
QPushButton#RunFailedProviderDoctorButton {
  background: $surface;
  border-color: $border_subtle;
  color: $text;
  border-radius: 14px;
  padding: 7px 14px;
}

QFrame#ThinkingIndicator {
  background: $surface;
  border: 1px solid $border;
  border-radius: $radius_bubble;
}

QLabel#ThinkingText {
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#PlanDot {
  font-size: 13px;
}

QLabel#PlanTaskTitle {
  color: $text;
}

QLabel#PlanStatus {
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#PlanGroupLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#PlanActivity {
  color: $text_muted;
  font-family: $font_mono;
  font-size: $font_size_small;
}

QLabel#PlanEmpty {
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#PlanChip {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
  padding: 2px 8px;
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#PlanChipModel {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
  padding: 2px 8px;
  color: $text_muted;
  font-size: $font_size_small;
}

QDialog#ApprovalDialog {
  background: $surface;
  border: 1px solid $border;
  border-radius: $radius_small;
}

QDialog#ApprovalDialog QLabel {
  color: $text;
}

QDialog#ApprovalDialog QLabel#ApprovalTitle {
  color: $text_muted;
  font-size: $font_size_small;
}

QDialog#ApprovalDialog QLabel#ApprovalPrompt {
  color: $text;
}

QDialog#ApprovalDialog QPlainTextEdit#ApprovalDetails {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
  padding: $space_2;
  color: $text;
  font-family: $font_mono;
  font-size: $font_size_small;
}

QDialog#ProviderManagerDialog {
  background: $surface;
  border: 1px solid $border;
  border-radius: $radius_small;
}

QDialog#ProviderManagerDialog QLabel#ProviderManagerTitle {
  color: $text;
  font-weight: 600;
}

QDialog#ProviderManagerDialog QLabel#ProviderFieldLabel,
QDialog#ProviderManagerDialog QLabel#ProviderDetail,
QDialog#ProviderManagerDialog QLabel#ProviderStatus,
QDialog#ProviderManagerDialog QLabel#ProviderEmpty {
  color: $text_muted;
  font-size: $font_size_small;
}

QDialog#ProviderManagerDialog QFrame#ProviderRow,
QDialog#ProviderManagerDialog QFrame#ProviderModelsHost {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
}

QDialog#ProviderManagerDialog QLabel#ProviderName {
  color: $text;
}

QDialog#ProviderManagerDialog QPushButton#ProviderPrimaryButton {
  background: $primary;
  border-color: $primary;
  color: $bg;
}

QDialog#ProviderManagerDialog QPushButton#ProviderPrimaryButton:hover {
  background: $primary_hover;
  border-color: $primary_hover;
}

QDialog#ProviderManagerDialog QPushButton#ProviderDangerButton {
  color: $danger;
}

QDialog#SettingsDialog {
  background: $surface;
  border: 1px solid $border;
  border-radius: $radius_small;
}

QDialog#SettingsDialog QLabel#SettingsTitle {
  color: $text;
  font-weight: 600;
}

QDialog#SettingsDialog QFrame#SettingsNav {
  background: transparent;
  border: none;
  min-width: 140px;
}

QDialog#SettingsDialog QPushButton#SettingsTabModels,
QDialog#SettingsDialog QPushButton#SettingsTabPrivacy,
QDialog#SettingsDialog QPushButton#SettingsTabProviders,
QDialog#SettingsDialog QPushButton#SettingsTabLanguage,
QDialog#SettingsDialog QPushButton#SettingsTabShortcuts,
QDialog#SettingsDialog QPushButton#SettingsTabAbout {
  background: transparent;
  border: 1px solid transparent;
  border-radius: $radius_small;
  padding: 8px 10px;
  text-align: left;
  color: $text_muted;
}

QDialog#SettingsDialog QPushButton#SettingsTabModels:hover,
QDialog#SettingsDialog QPushButton#SettingsTabPrivacy:hover,
QDialog#SettingsDialog QPushButton#SettingsTabProviders:hover,
QDialog#SettingsDialog QPushButton#SettingsTabLanguage:hover,
QDialog#SettingsDialog QPushButton#SettingsTabShortcuts:hover,
QDialog#SettingsDialog QPushButton#SettingsTabAbout:hover {
  background: $surface_raised;
  border-color: $border_subtle;
  color: $text;
}

QDialog#SettingsDialog QPushButton#SettingsTabModels:checked,
QDialog#SettingsDialog QPushButton#SettingsTabPrivacy:checked,
QDialog#SettingsDialog QPushButton#SettingsTabProviders:checked,
QDialog#SettingsDialog QPushButton#SettingsTabLanguage:checked,
QDialog#SettingsDialog QPushButton#SettingsTabShortcuts:checked,
QDialog#SettingsDialog QPushButton#SettingsTabAbout:checked {
  background: $user_surface;
  border-color: $border;
  color: $text;
}

QDialog#SettingsDialog QLabel#SettingsPageTitleModels,
QDialog#SettingsDialog QLabel#SettingsPageTitlePrivacy,
QDialog#SettingsDialog QLabel#SettingsPageTitleProviders,
QDialog#SettingsDialog QLabel#SettingsPageTitleLanguage,
QDialog#SettingsDialog QLabel#SettingsPageTitleShortcuts,
QDialog#SettingsDialog QLabel#SettingsPageTitleAbout {
  color: $text;
  font-weight: 600;
}

QDialog#SettingsDialog QLabel#SettingsDescription,
QDialog#SettingsDialog QLabel#PrivacyModeHint {
  color: $text_muted;
  font-size: $font_size_small;
}

QDialog#SettingsDialog QLabel#FieldLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QDialog#SettingsDialog QPushButton#LanguageZhButton,
QDialog#SettingsDialog QPushButton#LanguageEnButton {
  background: $surface;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 7px 18px;
  color: $text_muted;
}

QDialog#SettingsDialog QPushButton#LanguageZhButton:hover,
QDialog#SettingsDialog QPushButton#LanguageEnButton:hover {
  border-color: $primary_hover;
  color: $text;
}

QDialog#SettingsDialog QPushButton#LanguageZhButton:checked,
QDialog#SettingsDialog QPushButton#LanguageEnButton:checked {
  background: $user_surface;
  border-color: $primary;
  color: $primary;
}

QDialog#SettingsDialog QFrame#RolesHost {
  background: transparent;
}

QDialog#SettingsDialog QFrame#RoleRow {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_small;
}

QDialog#SettingsDialog QLabel#RoleName {
  color: $text;
}

QDialog#SettingsDialog QLabel#RoleHint {
  color: $warning;
  font-size: $font_size_small;
}

QDialog#ProviderManagerDialog QScrollArea#ProviderModelsScroll {
  background: transparent;
  border: none;
}

QDialog#ProviderManagerDialog QCheckBox#ProviderModelCheck {
  color: $text;
  background: transparent;
}

QPushButton#ApprovalAllowOnce {
  background: $primary;
  border-color: $primary;
  color: $bg;
}

QPushButton#ApprovalAllowOnce:hover {
  background: $primary_hover;
  border-color: $primary_hover;
}

QPushButton#ApprovalAllowSession {
  background: $user_surface;
  border-color: $border;
  color: $text;
}

QPushButton#ApprovalReject {
  color: $danger;
}

QScrollBar:vertical {
  background: transparent;
  width: 10px;
  margin: 0;
}

QScrollBar::handle:vertical {
  background: $border;
  border-radius: 5px;
  min-height: 32px;
}

QScrollBar::handle:vertical:hover {
  background: $text_muted;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
  height: 0;
  background: transparent;
}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
  background: transparent;
}

QScrollBar:horizontal {
  background: transparent;
  height: 10px;
  margin: 0;
}

QScrollBar::handle:horizontal {
  background: $border;
  border-radius: 5px;
  min-width: 32px;
}

QScrollBar::handle:horizontal:hover {
  background: $text_muted;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
  width: 0;
  background: transparent;
}

QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
  background: transparent;
}

QLabel#ControlSummary {
  color: $text_muted;
  font-size: $font_size_small;
}

QPushButton#GearButton {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 6px 12px;
  color: $text_muted;
}

QPushButton#GearButton:hover {
  border-color: $primary_hover;
}

QPushButton#GearButton:checked {
  background: $user_surface;
  border-color: $border;
  color: $text;
}

QFrame#SettingsSidePanel {
  background: $surface;
  border-left: 1px solid $border_subtle;
}

QFrame#SettingsPanelHeader {
  background: transparent;
  border: none;
}

QLabel#SettingsPanelTitle {
  color: $text;
  font-weight: 600;
}

QPushButton#SettingsPanelBackButton,
QPushButton#SettingsPanelCloseButton {
  background: $surface_raised;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 6px 12px;
  color: $text_muted;
}

QPushButton#SettingsPanelBackButton:hover,
QPushButton#SettingsPanelCloseButton:hover {
  border-color: $primary_hover;
  color: $text;
}

QWidget#SettingsContent QLabel#SettingsTitle {
  color: $text;
  font-weight: 600;
}

QWidget#SettingsContent QFrame#SettingsNav {
  background: transparent;
  border: none;
  min-width: 116px;
}

QWidget#SettingsContent QPushButton#SettingsTabModels,
QWidget#SettingsContent QPushButton#SettingsTabPrivacy,
QWidget#SettingsContent QPushButton#SettingsTabProviders,
QWidget#SettingsContent QPushButton#SettingsTabLanguage,
QWidget#SettingsContent QPushButton#SettingsTabShortcuts,
QWidget#SettingsContent QPushButton#SettingsTabAbout {
  background: transparent;
  border: 1px solid transparent;
  border-radius: $radius_small;
  padding: 8px 10px;
  text-align: left;
  color: $text_muted;
}

QWidget#SettingsContent QPushButton#SettingsTabModels:hover,
QWidget#SettingsContent QPushButton#SettingsTabPrivacy:hover,
QWidget#SettingsContent QPushButton#SettingsTabProviders:hover,
QWidget#SettingsContent QPushButton#SettingsTabLanguage:hover,
QWidget#SettingsContent QPushButton#SettingsTabShortcuts:hover,
QWidget#SettingsContent QPushButton#SettingsTabAbout:hover {
  background: $surface_raised;
  border-color: $border_subtle;
  color: $text;
}

QWidget#SettingsContent QPushButton#SettingsTabModels:checked,
QWidget#SettingsContent QPushButton#SettingsTabPrivacy:checked,
QWidget#SettingsContent QPushButton#SettingsTabProviders:checked,
QWidget#SettingsContent QPushButton#SettingsTabLanguage:checked,
QWidget#SettingsContent QPushButton#SettingsTabShortcuts:checked,
QWidget#SettingsContent QPushButton#SettingsTabAbout:checked {
  background: $user_surface;
  border-color: $border;
  color: $text;
}

QWidget#SettingsContent QLabel#SettingsPageTitleModels,
QWidget#SettingsContent QLabel#SettingsPageTitlePrivacy,
QWidget#SettingsContent QLabel#SettingsPageTitleProviders,
QWidget#SettingsContent QLabel#SettingsPageTitleLanguage,
QWidget#SettingsContent QLabel#SettingsPageTitleShortcuts,
QWidget#SettingsContent QLabel#SettingsPageTitleAbout {
  color: $text;
  font-weight: 600;
}

QWidget#SettingsContent QLabel#SettingsDescription,
QWidget#SettingsContent QLabel#PrivacyModeHint,
QWidget#SettingsContent QLabel#FieldLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QWidget#SettingsContent QPushButton#LanguageZhButton,
QWidget#SettingsContent QPushButton#LanguageEnButton {
  background: $surface;
  border: 1px solid $border_subtle;
  border-radius: $radius_pill;
  padding: 7px 18px;
  color: $text_muted;
}

QWidget#SettingsContent QPushButton#LanguageZhButton:checked,
QWidget#SettingsContent QPushButton#LanguageEnButton:checked {
  background: $user_surface;
  border-color: $primary;
  color: $primary;
}

QFrame#SettingsDrawer {
  background: transparent;
  border: none;
  border-top: 1px solid $border_subtle;
}
"""
)


def render_stylesheet(tokens: dict[str, str] | None = None) -> str:
    values = dict(TOKENS)
    if tokens:
        values.update(tokens)
    return QSS_TEMPLATE.safe_substitute(values)


def apply_theme(app, tokens: dict[str, str] | None = None) -> None:
    app.setStyleSheet(render_stylesheet(tokens))
