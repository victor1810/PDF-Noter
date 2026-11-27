"""Offscreen smoke test: build the window, switch themes, render a PDF."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # noqa: E402
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter.main_window import MainWindow  # noqa: E402
from pdfnoter.theme import DARK, LIGHT, SYSTEM, ThemeManager  # noqa: E402
from pdfnoter.viewer import Tool  # noqa: E402


class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_window_themes_and_tools(self) -> None:
        theme = ThemeManager(self.app)
        theme.apply()
        window = MainWindow(theme)

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Smoke test", fontsize=14)
        window.viewer.open_document(doc)

        self.assertEqual(window.viewer.page_count(), 1)
        for mode in (DARK, LIGHT, SYSTEM):
            window._set_theme(mode)
            self.assertIsNotNone(self.app.palette())

        for tool in Tool:
            window.viewer.set_tool(tool)
        window.viewer.fit_width()
        window.viewer.zoom_in()
        window.viewer.zoom_out()
        window.viewer.next_page()
        window.viewer.previous_page()

        window.viewer.set_tool(Tool.HIGHLIGHT)
        self.assertEqual(window.viewer.tool, Tool.HIGHLIGHT)
        window.close()
        doc.close()


if __name__ == "__main__":
    unittest.main()
