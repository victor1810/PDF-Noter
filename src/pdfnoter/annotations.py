"""Pure PDF annotation helpers built on top of PyMuPDF.

This module deliberately contains no Qt code so it can be unit-tested
without a GUI. Coordinates passed here are *PDF page coordinates*
(points, top-left origin); the Qt layer is responsible for converting
screen pixels to those coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

try:  # PyMuPDF >= 1.24 exposes the canonical module name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older releases
    import fitz


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def to_fitz(self) -> fitz.Rect:
        return fitz.Rect(self.x0, self.y0, self.x1, self.y1)


def rect_between(a: Point, b: Point) -> Rect:
    """Build a normalised rectangle from two dragged corners."""
    return Rect(
        min(a.x, b.x),
        min(a.y, b.y),
        max(a.x, b.x),
        max(a.y, b.y),
    )


def is_usable_rect(rect: Rect, minimum: float = 3.0) -> bool:
    return rect.x1 - rect.x0 >= minimum and rect.y1 - rect.y0 >= minimum


# ---------------------------------------------------------------------------
# Coordinate mapping (supports rotated pages)
# ---------------------------------------------------------------------------


def page_to_screen(page: fitz.Page, point: Point, zoom: float) -> Point:
    """PDF user space -> rendered pixmap pixels (top-left origin)."""
    p = fitz.Point(point.x, point.y) * page.rotation_matrix
    return Point(p.x * zoom, p.y * zoom)


def screen_to_page(page: fitz.Page, point: Point, zoom: float) -> Point:
    """Rendered pixmap pixels (top-left origin) -> PDF user space."""
    p = fitz.Point(point.x / zoom, point.y / zoom) * page.derotation_matrix
    return Point(p.x, p.y)


# ---------------------------------------------------------------------------
# Text markup: highlight / underline / strikeout
# ---------------------------------------------------------------------------

_MARKUP_BUILDERS = {
    "highlight": lambda page: page.add_highlight_annot,
    "underline": lambda page: page.add_underline_annot,
    "strikeout": lambda page: page.add_strikeout_annot,
}


def text_quads(page: fitz.Page, rect: Rect) -> list[fitz.Quad]:
    """Return quads of words touched by ``rect``.

    PyMuPDF stores text in unrotated page space, which is exactly what the
    markup annotation methods expect, so no rotation conversion is needed.
    """
    box = rect.to_fitz()
    quads: list[fitz.Quad] = []
    for word in page.get_text("words"):
        word_box = fitz.Rect(word[:4])
        if word_box.intersects(box):
            quads.append(word_box.quad)
    return quads


def add_markup(
    page: fitz.Page,
    rect: Rect,
    kind: str,
    color: tuple[float, float, float] = (1.0, 0.85, 0.2),
) -> fitz.Annot | None:
    """Add highlight/underline/strikeout over the text inside ``rect``.

    If no text is found, a small markup is created over the whole rectangle
    so the tool still gives visible feedback.
    """
    if kind not in _MARKUP_BUILDERS:
        raise ValueError(f"Unknown markup kind: {kind!r}")

    quads = text_quads(page, rect)
    if not quads:
        quads = [rect.to_fitz().quad]

    annot = _MARKUP_BUILDERS[kind](page)(quads)
    annot.set_colors(stroke=color)
    annot.set_info(title="PDF Noter")
    annot.update()
    return annot


# ---------------------------------------------------------------------------
# Other annotation types
# ---------------------------------------------------------------------------


def add_note(
    page: fitz.Page,
    point: Point,
    text: str,
    color: tuple[float, float, float] = (1.0, 0.8, 0.2),
) -> fitz.Annot:
    """Add a sticky note at ``point``."""
    annot = page.add_text_annot(
        fitz.Point(point.x, point.y), text or "Заметка", icon="Note"
    )
    annot.set_colors(stroke=color)
    annot.set_info(title="PDF Noter", content=text)
    annot.update()
    return annot


def add_freetext(
    page: fitz.Page,
    rect: Rect,
    text: str,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fill: tuple[float, float, float] = (1.0, 1.0, 1.0),
    fontsize: float = 12.0,
) -> fitz.Annot:
    """Add a text box inside ``rect``."""
    annot = page.add_freetext_annot(
        rect.to_fitz(),
        text,
        fontsize=fontsize,
        fontname="helv",
        text_color=color,
        fill_color=fill,
        border_width=0.8,
        align=fitz.TEXT_ALIGN_LEFT,
    )
    annot.set_info(title="PDF Noter")
    annot.update()
    return annot


def add_ink(
    page: fitz.Page,
    points: Sequence[Point],
    color: tuple[float, float, float] = (0.85, 0.25, 0.25),
    width: float = 2.0,
) -> fitz.Annot | None:
    """Add a freehand stroke. Returns ``None`` if fewer than two points."""
    if len(points) < 2:
        return None

    stroke = [[(p.x, p.y) for p in points]]
    annot = page.add_ink_annot(stroke)
    annot.set_colors(stroke=color)
    annot.set_border(width=width)
    annot.set_info(title="PDF Noter")
    annot.update()
    return annot


def annotations_at(page: fitz.Page, point: Point) -> list[fitz.Annot]:
    """Annotations whose bounding box contains ``point`` (topmost last)."""
    hits: list[fitz.Annot] = []
    for annot in page.annots() or ():
        if fitz.Point(point.x, point.y) in annot.rect:
            hits.append(annot)
    return hits


def delete_annotation(page: fitz.Page, annot: fitz.Annot) -> None:
    page.delete_annot(annot)


def save_document(doc: fitz.Document, path: str | None = None) -> None:
    """Save in place, or to ``path`` when a new name is requested.

    Never try an incremental save on a document PyMuPDF could not parse into
    pages: that would append a bogus update to an already damaged PDF.
    """
    if len(doc) == 0:
        raise ValueError("документ не содержит страниц — сохранение невозможно")
    if path is None:
        doc.saveIncr()
    else:
        doc.save(path, garbage=3, deflate=True)
