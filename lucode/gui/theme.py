from __future__ import annotations

from string import Template


TOKENS = {
    "bg": "#0f1117",
    "surface": "#151922",
    "surface_raised": "#1b2030",
    "border": "#2a3142",
    "border_subtle": "#20262f",
    "user_surface": "#1d2433",
    "text": "#e6eaf2",
    "text_muted": "#9aa4b2",
    "primary": "#6aa6ff",
    "primary_hover": "#8bbcff",
    "danger": "#ff6b7a",
    "success": "#62d68f",
    "warning": "#f2c14e",
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

QLabel {
  background: transparent;
}

QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {
  background: $bg;
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

QFrame#MessageBubble[assistantRole="true"] {
  background: $surface_raised;
  border-color: $border_subtle;
}

QLabel#RoleLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QLabel#AssistantText {
  line-height: 150%;
}

QLabel#UserText {
  color: $text;
  line-height: 150%;
}

QLabel#EmptyState {
  color: $text_muted;
}

QPlainTextEdit {
  background: $surface;
  border: 2px solid $border_subtle;
  border-radius: $radius_small;
  padding: $space_2;
  selection-background-color: $primary;
}

QPlainTextEdit:focus {
  border: 2px solid $primary;
}

QPlainTextEdit:disabled {
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
  border-radius: $radius_small;
}

QFrame#SessionSidebar {
  background: $surface;
  border-right: 1px solid $border_subtle;
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
  border-radius: $radius_small;
}

QFrame#SessionRow[selected="true"] {
  border-left: 3px solid $primary;
}

QPushButton#SessionRowButton {
  background: transparent;
  border: none;
  border-radius: $radius_small;
  padding: 4px 6px;
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
  border-radius: $radius_small;
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
  background: transparent;
  border: none;
}

QLabel#SessionTitleLabel {
  color: $text;
  font-weight: 600;
}

QPushButton#SidebarToggleButton {
  background: transparent;
  border: none;
  border-radius: $radius_small;
  padding: 4px 8px;
  color: $text_muted;
}

QPushButton#SidebarToggleButton:hover {
  color: $text;
  background: $surface_raised;
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
  background: transparent;
  border: none;
  border-left: 2px solid $border_subtle;
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

QDialog#SettingsDialog QLabel#FieldLabel {
  color: $text_muted;
  font-size: $font_size_small;
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

QPushButton#ApprovalAllow {
  background: $primary;
  border-color: $primary;
  color: $bg;
}

QPushButton#ApprovalAllow:hover {
  background: $primary_hover;
  border-color: $primary_hover;
}

QPushButton#ApprovalDeny {
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
