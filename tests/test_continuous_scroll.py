"""Offscreen tests for continuous scrolling and page insert/delete actions."""

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
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter import annotations as ann  # noqa: E402
from pdfnoter import ocr  # noqa: E402
from pdfnoter.main_window import MainWindow  # noqa: E402
from pdfnoter.theme import ThemeManager  # noqa: E402


def make_document(pages: int = 3) -> fitz.Document:
    doc = fitz.open()
    for number in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {number + 1}", fontsize=18)
    return doc


def make_scanned_document() -> tuple[fitz.Document, fitz.Page]:
    source = fitz.open()
    template = source.new_page(width=595, height=842)
    template.insert_text((72, 120), "Hello from scan", fontsize=26)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, pixmap=template.get_pixmap(dpi=150))
    source.close()
    return doc, page


class ContinuousScrollTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.theme = ThemeManager(self.app)
        self.window = MainWindow(self.theme)
        self.doc = make_document()
        self.viewer = self.window.viewer
        self.viewer.open_document(self.doc)

    def tearDown(self) -> None:
        # Avoid the "save changes?" modal dialog blocking an offscreen test.
        self.window._mark_clean()
        self.window.close()
        self.doc.close()

    def test_all_pages_are_stacked(self) -> None:
        self.assertEqual(self.viewer.page_count(), 3)
        self.assertEqual(len(self.viewer._page_items), 3)
        tops = self.viewer._page_tops
        self.assertLess(tops[0], tops[1])
        self.assertLess(tops[1], tops[2])
        self.assertGreater(tops[1] - tops[0], 800)

    def test_navigation_updates_current_page(self) -> None:
        self.viewer.goto_page(2)
        self.assertEqual(self.viewer.page_index, 2)
        self.viewer.previous_page()
        self.assertEqual(self.viewer.page_index, 1)
        self.assertEqual(self.window.page_spin.value(), 2)

    def test_coordinate_mapping_on_any_page(self) -> None:
        self.viewer.goto_page(1)
        point = ann.Point(120.5, 234.25)
        scene = self.viewer._to_scene(1, point)
        self.assertIsNotNone(self.viewer._page_at(scene))
        back = self.viewer._to_pdf(scene, 1)
        self.assertAlmostEqual(back.x, point.x, places=2)
        self.assertAlmostEqual(back.y, point.y, places=2)

    def test_insert_and_delete_page(self) -> None:
        self.viewer.goto_page(0)
        self.assertIsNotNone(self.viewer.insert_page(after_current=True))
        self.assertEqual(self.viewer.page_count(), 4)
        self.assertEqual(self.viewer.page_index, 1)

        self.assertTrue(self.viewer.delete_current_page())
        self.assertEqual(self.viewer.page_count(), 3)

    def test_last_page_cannot_be_deleted(self) -> None:
        single = make_document(1)
        self.addCleanup(single.close)
        self.viewer.open_document(single)
        self.assertFalse(self.viewer.delete_current_page())
        self.assertEqual(self.viewer.page_count(), 1)

    @unittest.skipUnless(
        ocr.find_tessdata(required={"eng"}) is not None,
        "English Tesseract data is not installed",
    )
    def test_window_ocr_action_makes_page_searchable(self) -> None:
        scanned, page = make_scanned_document()
        self.addCleanup(scanned.close)
        self.viewer.open_document(scanned)
        self.window._doc = scanned  # normally set by MainWindow.open_pdf()
        self.assertEqual(page.get_text().strip(), "")

        self.window.run_ocr(all_pages=True)

        self.assertIn("Hello", page.get_text())
        self.assertTrue(self.window._modified)


if __name__ == "__main__":
    unittest.main()
