"""Undo/redo must restore real document state, not just toggle toolbar icons."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter import annotations as ann  # noqa: E402
from pdfnoter.main_window import MainWindow  # noqa: E402
from pdfnoter.theme import ThemeManager  # noqa: E402


class UndoRedoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_pdf(self, pages: int = 1) -> str:
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        doc = fitz.open()
        for number in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100 + number * 30), f"Page {number + 1}", fontsize=18)
        doc.save(path)
        doc.close()
        def cleanup() -> None:
            try:
                os.remove(path)
            except OSError:
                pass

        self.addCleanup(cleanup)
        return path

    def _window(self, pages: int = 1) -> MainWindow:
        window = MainWindow(ThemeManager(self.app))
        self.assertTrue(window.open_pdf(self._make_pdf(pages)))

        def cleanup() -> None:
            window._mark_clean()
            window.close()

        self.addCleanup(cleanup)
        return window

    @staticmethod
    def _annotation_count(window: MainWindow) -> int:
        return sum(1 for _ in (window._doc.load_page(0).annots() or ()))

    def test_insert_page_undo_redo(self) -> None:
        window = self._window()
        self.assertEqual(window.viewer.page_count(), 1)
        self.assertFalse(window.act_undo.isEnabled())

        window.insert_page()
        self.assertEqual(window.viewer.page_count(), 2)
        self.assertTrue(window.act_undo.isEnabled())

        window.act_undo.trigger()
        self.assertEqual(window.viewer.page_count(), 1)
        self.assertFalse(window.act_undo.isEnabled())
        self.assertTrue(window.act_redo.isEnabled())

        window.act_redo.trigger()
        self.assertEqual(window.viewer.page_count(), 2)

    def test_delete_page_undo_redo(self) -> None:
        window = self._window(pages=2)
        self.assertEqual(window.viewer.page_count(), 2)

        window.viewer.delete_current_page()
        self.assertEqual(window.viewer.page_count(), 1)

        window.act_undo.trigger()
        self.assertEqual(window.viewer.page_count(), 2)

        window.act_redo.trigger()
        self.assertEqual(window.viewer.page_count(), 1)

    def test_annotation_delete_undo_redo(self) -> None:
        window = self._window()
        page = window.viewer._doc.load_page(0)
        annot = ann.add_note(page, ann.Point(120, 140), "Отменяемая заметка")
        window.viewer._rendered.discard(0)
        window.viewer.refresh()
        window.viewer._selected = (0, page, annot)
        self.assertEqual(self._annotation_count(window), 1)

        window.viewer.delete_selected()
        self.assertEqual(self._annotation_count(window), 0)

        window.act_undo.trigger()
        self.assertEqual(self._annotation_count(window), 1)

        window.act_redo.trigger()
        self.assertEqual(self._annotation_count(window), 0)

    def test_history_is_cleared_when_opening_another_file(self) -> None:
        window = self._window()
        window.insert_page()
        self.assertTrue(window.act_undo.isEnabled())

        window._mark_clean()
        self.assertTrue(window.open_pdf(self._make_pdf()))
        self.assertFalse(window.act_undo.isEnabled())
        self.assertFalse(window.act_redo.isEnabled())


if __name__ == "__main__":
    unittest.main()
