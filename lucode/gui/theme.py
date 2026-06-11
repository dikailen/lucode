from __future__ import annotations

from string import Template


TOKENS = {
    "bg": "#0f1117",
    "surface": "#151922",
    "surface_raised": "#1b2030",
    "border": "#2a3142",
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
    "radius_small": "8px",
    "radius_bubble": "12px",
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
  border: 1px solid $border;
  border-radius: $radius_bubble;
  padding: $space_3;
}

QFrame#MessageBubble[userRole="true"] {
  background: $primary;
  border-color: $primary;
  color: $bg;
}

QFrame#MessageBubble[assistantRole="true"] {
  background: $surface_raised;
  border-color: $border;
}

QLabel#RoleLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QFrame#MessageBubble[userRole="true"] QLabel#RoleLabel {
  color: $bg;
}

QLabel#AssistantText {
  font-family: $font_mono;
  line-height: 150%;
}

QLabel#UserText {
  color: $bg;
  line-height: 150%;
}

QLabel#EmptyState {
  color: $text_muted;
}

QPlainTextEdit {
  background: $surface;
  border: 1px solid $border;
  border-radius: $radius_small;
  padding: $space_2;
  selection-background-color: $primary;
}

QPlainTextEdit:focus {
  border: 2px solid $primary;
}

QPushButton {
  background: $surface_raised;
  border: 1px solid $border;
  border-radius: $radius_small;
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
  background: $primary;
  border-color: $primary;
  color: $bg;
}

QPushButton#SendButton:disabled {
  background: $surface;
  border-color: $border;
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
  border: 1px solid $border;
  border-radius: $radius_small;
}

QFrame#ControlBar QLabel#FieldLabel {
  color: $text_muted;
  font-size: $font_size_small;
}

QPushButton#SegButton {
  background: $surface_raised;
  border: 1px solid $border;
  border-radius: 0;
  padding: 6px 16px;
  color: $text_muted;
}

QPushButton#SegButton:first-child {
  border-top-left-radius: $radius_small;
  border-bottom-left-radius: $radius_small;
}

QPushButton#SegButton:last-child {
  border-top-right-radius: $radius_small;
  border-bottom-right-radius: $radius_small;
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
  border: 1px solid $border;
  border-radius: $radius_small;
  padding: 6px 14px;
  color: $text_muted;
}

QPushButton#ToggleButton:checked {
  background: $success;
  border-color: $success;
  color: $bg;
}

QFrame#RolesHost {
  background: transparent;
}

QFrame#RoleRow {
  background: $surface_raised;
  border: 1px solid $border;
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
  border: 1px solid $border;
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
"""
)


def render_stylesheet(tokens: dict[str, str] | None = None) -> str:
    values = dict(TOKENS)
    if tokens:
        values.update(tokens)
    return QSS_TEMPLATE.safe_substitute(values)


def apply_theme(app, tokens: dict[str, str] | None = None) -> None:
    app.setStyleSheet(render_stylesheet(tokens))
