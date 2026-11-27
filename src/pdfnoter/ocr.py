"""Tesseract-backed OCR helpers built on PyMuPDF.

PyMuPDF calls Tesseract internally (via ``Pixmap.pdfocr_tobytes``), so no
extra Python package is required — only the Tesseract binaries and its
``tessdata`` language files. Recognised words are written back into the PDF
as an *invisible* text layer, which makes scanned pages searchable and
allows text annotations to snap to words.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

try:  # PyMuPDF >= 1.24 exposes the canonical module name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older releases
    import fitz


DEFAULT_LANGUAGE = "rus+eng"


class OcrUnavailable(RuntimeError):
    """Raised when Tesseract or its language data cannot be found."""


def _bundled_tessdata() -> Path:
    """Folder shipped next to the app (also works inside PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path.cwd())) / "tessdata"
    return Path(__file__).resolve().parents[2] / "tessdata"


def _has_languages(folder: Path, required: set[str] | None) -> bool:
    if not folder.is_dir() or not any(folder.glob("*.traineddata")):
        return False
    if not required:
        return True
    installed = {path.stem for path in folder.glob("*.traineddata")}
    return required.issubset(installed)


def find_tessdata(
    explicit: str | None = None, required: set[str] | None = None
) -> str | None:
    """Locate a folder containing ``*.traineddata`` files.

    When ``required`` language codes are given, prefer a folder that has all
    of them (e.g. the Public folder with Russian data over the stock Windows
    installation that only ships English).
    """
    candidates: list[str | Path] = []
    if explicit:
        candidates.append(explicit)

    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        candidates += [env, Path(env) / "tessdata"]

    try:  # PyMuPDF's own lookup (honours TESSDATA_PREFIX / registry)
        found = fitz.get_tessdata()
        if found:
            candidates.append(found)
    except Exception:
        pass

    program_files = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for base in program_files:
        if base:
            candidates.append(Path(base) / "Tesseract-OCR" / "tessdata")

    executable = shutil.which("tesseract")
    if executable:
        candidates.append(Path(executable).parent / "tessdata")

    # Created by install_tesseract.bat: a short ASCII path that Tesseract's
    # C core can read even when the user profile contains Cyrillic letters.
    public_base = os.environ.get("PUBLIC")
    if public_base:
        candidates.append(Path(public_base) / "PdfNoter" / "tessdata")

    # Last resort: data shipped with the app. Tesseract cannot always open
    # non-ASCII paths, so prefer any of the locations above.
    candidates.append(_bundled_tessdata())

    for candidate in candidates:
        folder = Path(candidate)
        if _has_languages(folder, required):
            return str(folder)
    return None


def available_languages(tessdata: str | None = None) -> list[str]:
    """Return language codes installed in ``tessdata``."""
    folder = find_tessdata(tessdata)
    if folder is None:
        return []
    return sorted(path.stem for path in Path(folder).glob("*.traineddata"))


def check_language(language: str, tessdata: str | None = None) -> str:
    """Verify requested languages and return the tessdata folder to use."""
    requested = {code for code in language.split("+") if code}
    folder = find_tessdata(tessdata, required=requested)
    if folder is not None:
        return folder

    any_folder = find_tessdata(tessdata)
    if any_folder is None:
        raise OcrUnavailable(
            "Tesseract/tessdata не найден. Запустите install_tesseract.bat "
            "или установите Tesseract-OCR (UB Mannheim) с языками rus и eng."
        )
    installed = set(available_languages(any_folder))
    missing = sorted(requested - installed)
    raise OcrUnavailable(
        "Не найдены языковые данные: " + ", ".join(missing) + f"\nПапка: {any_folder}"
    )


def _unicode_font() -> str | None:
    """A Windows font that contains Cyrillic glyphs."""
    windows_dir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("segoeui.ttf", "arial.ttf", "calibri.ttf", "tahoma.ttf"):
        path = Path(windows_dir) / "Fonts" / name
        if path.is_file():
            return str(path)
    return None


def add_text_layer(page: fitz.Page, words, fontfile: str | None = None) -> int:
    """Insert invisible text at recognised word boxes. Returns word count.

    ``render_mode=3`` is PDF's "neither fill nor stroke" mode: the text is
    present for search/copy but not drawn, so the scan looks unchanged.
    """
    if not words:
        return 0

    fontfile = fontfile or _unicode_font()
    try:
        measuring_font = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font(fontname="helv")
    except Exception:
        measuring_font = fitz.Font(fontname="helv")

    inserted = 0
    for item in words:
        x0, y0, x1, y1, text = item[:5]
        text = str(text).strip()
        width = float(x1) - float(x0)
        if not text or width <= 0:
            continue

        base_size = 11.0
        measured = measuring_font.text_length(text, fontsize=base_size)
        fontsize = base_size * width / max(measured, 0.1)
        fontsize = max(3.0, min(72.0, fontsize))
        baseline = float(y1) - 0.12 * (float(y1) - float(y0))

        try:
            kwargs = dict(
                fontsize=fontsize,
                fontname="ocrhelv" if not fontfile else "ocrcyr",
                render_mode=3,
                overlay=True,
            )
            if fontfile:
                kwargs["fontfile"] = fontfile
            page.insert_text((float(x0), baseline), text, **kwargs)
            inserted += 1
        except Exception:
            # One bad glyph must not abort the whole page.
            continue
    return inserted


def ocr_page(
    page: fitz.Page,
    language: str = DEFAULT_LANGUAGE,
    tessdata: str | None = None,
    dpi: int = 300,
    force: bool = False,
) -> int:
    """OCR one page and add an invisible text layer. Returns inserted words.

    Pages that already contain legible digital text are skipped unless
    ``force=True`` (avoids duplicating the existing text layer).
    """
    if not force and page.get_text().strip():
        return 0

    folder = check_language(language, tessdata)
    textpage = page.get_textpage_ocr(
        language=language,
        tessdata=folder,
        dpi=dpi,
        full=True,
    )
    return add_text_layer(page, textpage.extractWORDS())
