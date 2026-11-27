"""Images from files, clipboard, WIA and camera must become real PDF pages."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter.main_window import MainWindow  # noqa: E402
from pdfnoter.theme import ThemeManager  # noqa: E402


class ImageImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _sample_image() -> QImage:
        image = QImage(240, 120, QImage.Format.Format_RGB888)
        image.fill(QColor("#4da3ff"))
        return image

    def _window(self) -> MainWindow:
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Original", fontsize=18)
        doc.save(path)
        doc.close()

        window = MainWindow(ThemeManager(self.app))
        self.assertTrue(window.open_pdf(path))

        def cleanup() -> None:
            window._mark_clean()
            window.close()
            try:
                os.remove(path)
            except OSError:
                pass

        self.addCleanup(cleanup)
        return window

    def test_image_becomes_page_and_is_undoable(self) -> None:
        window = self._window()
        self.assertEqual(window.viewer.page_count(), 1)

        window.viewer.insert_images([self._sample_image()])

        self.assertEqual(window.viewer.page_count(), 2)
        self.assertTrue(window._doc.load_page(1).get_images())

        window.act_undo.trigger()
        self.assertEqual(window.viewer.page_count(), 1)

        window.act_redo.trigger()
        self.assertEqual(window.viewer.page_count(), 2)

    def test_clipboard_image_action(self) -> None:
        window = self._window()
        QApplication.clipboard().setImage(self._sample_image())

        window.import_image_from_clipboard()

        self.assertEqual(window.viewer.page_count(), 2)
        self.assertTrue(window._doc.load_page(1).get_images())

    def test_scanner_action_uses_wia_result(self) -> None:
        window = self._window()
        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        self._sample_image().save(path)

        scanners = [{"id": "{TEST}", "label": "HP LaserJet MFP M132fn (NPI211153)"}]
        with patch(
            "pdfnoter.main_window.image_import.list_scanners", return_value=scanners
        ), patch(
            "pdfnoter.main_window.image_import.scan_via_wia", return_value=[Path(path)]
        ) as scan:
            window.import_images_from_scanner()

        scan.assert_called_once()
        self.assertEqual(scan.call_args.kwargs.get("device_id"), "{TEST}")
        self.assertEqual(window.viewer.page_count(), 2)
        self.assertTrue(window._doc.load_page(1).get_images())
        try:
            os.remove(path)
        except OSError:
            pass

    def test_camera_action_uses_captured_frame(self) -> None:
        window = self._window()
        image = self._sample_image()

        class FakeCameraDialog:
            def __init__(self, parent=None) -> None:
                pass

            def exec(self) -> int:
                return int(QDialog.DialogCode.Accepted)

            def image(self) -> QImage:
                return image

        with patch("pdfnoter.main_window.CameraDialog", FakeCameraDialog):
            window.import_image_from_camera()

        self.assertEqual(window.viewer.page_count(), 2)
        self.assertTrue(window._doc.load_page(1).get_images())


if __name__ == "__main__":
    unittest.main()
