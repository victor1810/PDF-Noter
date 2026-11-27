"""Tests for the Tesseract-backed OCR layer (skipped if Tesseract is absent)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:  # noqa: E402
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter import ocr  # noqa: E402


def make_scanned_page(text: str = "Hello OCR") -> tuple[fitz.Document, fitz.Page]:
    """Render text into an image, then place only the image in a new PDF."""
    source = fitz.open()
    template = source.new_page(width=595, height=842)
    template.insert_text((72, 120), text, fontsize=26)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pix = template.get_pixmap(dpi=150)
    page.insert_image(page.rect, pixmap=pix)
    source.close()
    return doc, page


class OcrTest(unittest.TestCase):
    def test_language_lookup(self) -> None:
        folder = ocr.find_tessdata()
        if folder is None:
            self.skipTest("Tesseract/tessdata is not installed")
        self.assertTrue(Path(folder).is_dir())
        self.assertTrue(list(Path(folder).glob("*.traineddata")))

    @unittest.skipUnless(
        ocr.find_tessdata(required={"eng"}) is not None,
        "English Tesseract data is not installed",
    )
    def test_scanned_page_becomes_searchable(self) -> None:
        doc, page = make_scanned_page()
        self.addCleanup(doc.close)
        self.assertEqual(page.get_text().strip(), "")

        inserted = ocr.ocr_page(page, language="eng", dpi=150)
        self.assertGreater(inserted, 0)
        self.assertIn("Hello", page.get_text())

    @unittest.skipUnless(
        ocr.find_tessdata(required={"eng"}) is not None,
        "English Tesseract data is not installed",
    )
    def test_page_with_text_is_not_ocrd_twice(self) -> None:
        doc = fitz.open()
        self.addCleanup(doc.close)
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 120), "Already searchable", fontsize=14)
        self.assertEqual(ocr.ocr_page(page, language="eng"), 0)


if __name__ == "__main__":
    unittest.main()
