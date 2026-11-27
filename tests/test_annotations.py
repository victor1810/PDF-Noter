"""Headless checks for the annotation core (no Qt needed)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter import annotations as ann  # noqa: E402


class AnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=595, height=842)
        self.page.insert_text((72, 100), "Hello PDF Noter", fontsize=16)

    def tearDown(self) -> None:
        self.doc.close()

    def count(self) -> int:
        return len(list(self.page.annots() or ()))

    def test_markup_added_over_text(self) -> None:
        annot = ann.add_markup(
            self.page, ann.Rect(60, 80, 330, 110), "highlight", (1.0, 0.8, 0.1)
        )
        self.assertIsNotNone(annot)
        self.assertEqual(self.count(), 1)
        self.assertEqual(annot.type[1], "Highlight")

    def test_all_markup_kinds(self) -> None:
        for kind, expected in (
            ("highlight", "Highlight"),
            ("underline", "Underline"),
            ("strikeout", "StrikeOut"),
        ):
            with self.subTest(kind=kind):
                annot = ann.add_markup(self.page, ann.Rect(60, 80, 330, 110), kind)
                self.assertEqual(annot.type[1], expected)
        self.assertEqual(self.count(), 3)

    def test_note_freetext_and_ink(self) -> None:
        self.assertIsNotNone(ann.add_note(self.page, ann.Point(100, 200), "Заметка"))
        self.assertIsNotNone(
            ann.add_freetext(self.page, ann.Rect(100, 250, 300, 320), "Текст")
        )
        self.assertIsNotNone(
            ann.add_ink(
                self.page,
                [ann.Point(100, 400), ann.Point(140, 420), ann.Point(180, 400)],
            )
        )
        self.assertEqual(self.count(), 3)

    def test_hit_test_and_delete(self) -> None:
        annot = ann.add_note(self.page, ann.Point(120, 300), "Заметка")
        hits = ann.annotations_at(self.page, ann.Point(125, 305))
        self.assertEqual(len(hits), 1)
        ann.delete_annotation(self.page, annot)
        self.assertEqual(self.count(), 0)

    def test_save_reload(self) -> None:
        ann.add_markup(self.page, ann.Rect(60, 80, 330, 110), "highlight")
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "out.pdf")
            ann.save_document(self.doc, path)
            with fitz.open(path) as reopened:
                self.assertEqual(len(list(reopened[0].annots() or ())), 1)

    def test_coordinate_roundtrip(self) -> None:
        for rotation in (0, 90, 180, 270):
            self.page.set_rotation(rotation)
            for zoom in (0.5, 1.0, 2.0):
                point = ann.Point(120.5, 215.25)
                screen = ann.page_to_screen(self.page, point, zoom)
                restored = ann.screen_to_page(self.page, screen, zoom)
                with self.subTest(rotation=rotation, zoom=zoom):
                    self.assertAlmostEqual(restored.x, point.x, places=4)
                    self.assertAlmostEqual(restored.y, point.y, places=4)

    def test_rect_between(self) -> None:
        rect = ann.rect_between(ann.Point(30, 40), ann.Point(10, 20))
        self.assertEqual((rect.x0, rect.y0, rect.x1, rect.y1), (10, 20, 30, 40))
        self.assertTrue(ann.is_usable_rect(rect))
        self.assertFalse(ann.is_usable_rect(ann.Rect(0, 0, 1, 1)))


if __name__ == "__main__":
    unittest.main()
