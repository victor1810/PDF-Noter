"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import ThemeManager


def main() -> int:
    QApplication.setOrganizationName("PDFNoter")
    QApplication.setApplicationName("PDF Noter")
    QApplication.setApplicationVersion("0.3.0")

    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    theme = ThemeManager(app)
    theme.apply()

    window = MainWindow(theme)
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".pdf"):
        window.open_pdf(sys.argv[1])
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
