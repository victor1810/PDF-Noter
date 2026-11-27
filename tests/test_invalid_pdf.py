"""A damaged PDF must be rejected with a clear message, not open as a blank window."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdfnoter.main_window import MainWindow  # noqa: E402
from pdfnoter.theme import ThemeManager  # noqa: E402


class InvalidPdfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_pdf_without_pages_is_rejected(self) -> None:
        window = MainWindow(ThemeManager(self.app))
        self.addCleanup(window.close)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.pdf"
            path.write_bytes(b"%PDF-1.7\n%%EOF\n")

            messages: list[tuple] = []
            original_warning = QMessageBox.warning
            original_critical = QMessageBox.critical
            handler = lambda *args, **kwargs: messages.append(args) or QMessageBox.StandardButton.Ok  # noqa: E731
            QMessageBox.warning = staticmethod(handler)  # type: ignore[assignment]
            QMessageBox.critical = staticmethod(handler)  # type: ignore[assignment]
            try:
                result = window.open_pdf(str(path))
            finally:
                QMessageBox.warning = original_warning  # type: ignore[assignment]
                QMessageBox.critical = original_critical  # type: ignore[assignment]

            self.assertFalse(result)
            self.assertIsNone(window._doc)
            self.assertEqual(len(messages), 1)
            self.assertIn("broken.pdf", messages[0][2])


if __name__ == "__main__":
    unittest.main()
