"""Launcher for running PDF Noter directly from source."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdfnoter.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
