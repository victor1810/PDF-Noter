"""Light / dark / system theme support for Qt 6 (PySide6)."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

SYSTEM = "system"
DARK = "dark"
LIGHT = "light"
MODES = (SYSTEM, DARK, LIGHT)


def _windows_system_is_dark() -> bool:
    """Read the Windows 'AppsUseLightTheme' setting (0 == dark)."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


class ThemeManager(QObject):
    """Applies a palette to the whole application and tracks changes.

    ``mode`` is one of ``system``, ``dark`` or ``light`` and is stored in
    QSettings between runs. On Windows 10/11 the system preference is read
    from the registry; on Qt >= 6.5 the native ``colorSchemeChanged`` signal
    is used as well.
    """

    themeChanged = Signal()

    def __init__(self, app: QApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._settings = QSettings("PDFNoter", "PDFNoter")
        mode = self._settings.value("theme", SYSTEM, str)
        self._mode = mode if mode in MODES else SYSTEM
        self._system_dark = _windows_system_is_dark()

        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_native_scheme_changed)

    # -- public API ---------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_dark(self) -> bool:
        if self._mode == DARK:
            return True
        if self._mode == LIGHT:
            return False
        return self._system_dark

    def set_mode(self, mode: str) -> None:
        if mode not in MODES or mode == self._mode:
            return
        self._mode = mode
        self._settings.setValue("theme", mode)
        self.apply()

    def apply(self) -> None:
        app = self._app
        app.setStyle("Fusion")
        palette = _dark_palette() if self.is_dark else _light_palette()
        app.setPalette(palette)

        if self.is_dark:
            stylesheet = """
            QGraphicsView { background: #1b1b1f; border: 1px solid #3a3a42; }
            QToolBar { border: 0; padding: 4px; spacing: 3px; }
            QStatusBar { border-top: 1px solid #3a3a42; }
            QDockWidget::title { background: #25252b; padding: 6px; }
            """
        else:
            stylesheet = """
            QGraphicsView { background: #e4e4e7; border: 1px solid #c9c9cf; }
            QToolBar { border: 0; padding: 4px; spacing: 3px; }
            QStatusBar { border-top: 1px solid #d0d0d6; }
            QDockWidget::title { background: #f0f0f3; padding: 6px; }
            """
        app.setStyleSheet(stylesheet)
        self.themeChanged.emit()

    # -- internal -----------------------------------------------------------
    def _on_native_scheme_changed(self, scheme) -> None:
        from PySide6.QtCore import Qt

        if scheme == Qt.ColorScheme.Dark:
            self._system_dark = True
        elif scheme == Qt.ColorScheme.Light:
            self._system_dark = False
        else:
            return
        if self._mode == SYSTEM:
            self.apply()


def _make_palette(base: dict[int, QColor]) -> QPalette:
    palette = QPalette()
    for role, color in base.items():
        palette.setColor(role, color)

    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.Text)
    disabled_text = QColor(text)
    disabled_text.setAlpha(130)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text
    )
    palette.setColor(
        QPalette.ColorRole.AlternateBase,
        QColor(window).lighter(110) if text.lightness() > 128 else QColor(window).lighter(115),
    )
    return palette


def _dark_palette() -> QPalette:
    return _make_palette(
        {
            QPalette.Window: QColor("#1f1f23"),
            QPalette.WindowText: QColor("#e8e8ec"),
            QPalette.Base: QColor("#25252b"),
            QPalette.AlternateBase: QColor("#2d2d34"),
            QPalette.ToolTipBase: QColor("#2d2d34"),
            QPalette.ToolTipText: QColor("#f0f0f4"),
            QPalette.Text: QColor("#e8e8ec"),
            QPalette.Button: QColor("#2d2d34"),
            QPalette.ButtonText: QColor("#e8e8ec"),
            QPalette.BrightText: QColor("#ffffff"),
            QPalette.Highlight: QColor("#3d7ce0"),
            QPalette.HighlightedText: QColor("#ffffff"),
            QPalette.Link: QColor("#69a8ff"),
            QPalette.LinkVisited: QColor("#b18cff"),
        }
    )


def _light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f3f3f6"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#202027"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#202027"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f0f0f4"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#202027"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fd0"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#1c5fc4"))
    return palette
