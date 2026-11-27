"""Qt widget: continuous PDF scrolling, annotation tools, page/OCR actions."""

from __future__ import annotations

import math
from enum import Enum

try:  # PyMuPDF >= 1.24 exposes the canonical module name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older releases
    import fitz

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMessageBox,
)

from . import annotations as ann
from . import image_import
from . import ocr as ocr_module


class Tool(str, Enum):
    SELECT = "select"
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"
    NOTE = "note"
    FREETEXT = "freetext"
    INK = "ink"


RECT_TOOLS = {Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKEOUT, Tool.FREETEXT}
MARKUP_TOOLS = {
    Tool.HIGHLIGHT: "highlight",
    Tool.UNDERLINE: "underline",
    Tool.STRIKEOUT: "strikeout",
}

PAGE_SPACING = 16
RENDER_BUFFER = 1


class PdfViewer(QGraphicsView):
    """All pages stacked vertically; only pages near the viewport are rasterised."""

    annotationAdded = Signal(object)
    annotationDeleted = Signal()
    documentChanged = Signal()
    pageChanged = Signal(int, int)  # page index (0-based), total pages
    zoomChanged = Signal(float)
    statusMessage = Signal(str)
    modifiedChanged = Signal(bool)
    operationStarted = Signal(str)
    operationFinished = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: fitz.Document | None = None
        self._current_page = 0
        self._zoom = 1.0
        self._tool = Tool.SELECT
        self._color: tuple[float, float, float] = (1.0, 0.85, 0.2)
        self._auto_fit = True
        self._updating_scroll = False

        # Per-page scene items and layout.
        self._page_items: list[QGraphicsPixmapItem] = []
        self._page_tops: list[float] = []
        self._page_sizes: list[tuple[int, int]] = []
        self._rendered: set[int] = set()

        # Keep the page alive while an annotation from it is selected:
        # PyMuPDF invalidates annotations when their page is garbage-collected.
        self._selected: tuple[int, fitz.Page, fitz.Annot] | None = None
        self._dragging = False
        self._drag_page: int | None = None
        self._start_scene = QPointF()
        self._start_pdf = ann.Point(0, 0)
        self._ink_points: list[ann.Point] = []

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._rubber = QGraphicsRectItem()
        self._rubber.setPen(QPen(QColor("#4da3ff"), 1.5, Qt.PenStyle.DashLine))
        self._rubber.setBrush(QBrush(QColor(77, 163, 255, 30)))
        self._rubber.hide()
        self._scene.addItem(self._rubber)

        self._ink_path_item = QGraphicsPathItem()
        self._ink_path_item.hide()
        self._scene.addItem(self._ink_path_item)

        self._selection_item = QGraphicsRectItem()
        self._selection_item.setPen(QPen(QColor("#4da3ff"), 1.5, Qt.PenStyle.DashLine))
        self._selection_item.setBrush(QBrush(QColor(77, 163, 255, 20)))
        self._selection_item.hide()
        self._scene.addItem(self._selection_item)

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # -- document / layout --------------------------------------------------
    def open_document(self, doc: fitz.Document) -> None:
        self._doc = doc
        self._current_page = 0
        self._auto_fit = True
        self._selected = None
        self._selection_item.hide()
        self.refresh()

    def close_document(self) -> None:
        self._doc = None
        self._clear_page_items()
        self._scene.setSceneRect(QRectF())
        self._selected = None
        self._rubber.hide()
        self._ink_path_item.hide()
        self._selection_item.hide()
        self._emit_page()

    def replace_document(self, doc: fitz.Document) -> None:
        """Swap in a new document object (used by undo/redo snapshots).

        The old object is deliberately *not* closed here: the caller owns it
        and must close it after this widget stops referencing it.
        """
        self._selected = None
        self._selection_item.hide()
        self._doc = doc
        self.refresh()

    def _clear_page_items(self) -> None:
        for item in self._page_items:
            self._scene.removeItem(item)
        self._page_items.clear()
        self._page_tops.clear()
        self._page_sizes.clear()
        self._rendered.clear()

    def _placeholder(self, width: int, height: int) -> QPixmap:
        pixmap = QPixmap(max(1, width), max(1, height))
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        pixmap.fill(QColor("#2a2a30") if dark else QColor("#d9d9df"))
        return pixmap

    def rebuild_layout(self, preserve_page: bool = True) -> None:
        """Rebuild the vertical page stack at the current zoom."""
        if self._doc is None:
            self._clear_page_items()
            self._scene.setSceneRect(QRectF())
            return

        self._clear_page_items()
        y = 0.0
        max_width = 0
        for idx in range(len(self._doc)):
            page = self._doc.load_page(idx)
            width = max(1, math.ceil(page.rect.width * self._zoom))
            height = max(1, math.ceil(page.rect.height * self._zoom))
            item = QGraphicsPixmapItem(self._placeholder(width, height))
            item.setPos(0, y)
            item.setData(0, idx)
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._scene.addItem(item)
            self._page_items.append(item)
            self._page_tops.append(y)
            self._page_sizes.append((width, height))
            max_width = max(max_width, width)
            y += height + PAGE_SPACING

        total_height = max(0, y - PAGE_SPACING)
        self._scene.setSceneRect(0, 0, max_width, total_height)
        self._selected = None
        self._selection_item.hide()
        self.update_visible_pages()
        if preserve_page:
            self._scroll_to_page(self._current_page)

    def refresh(self) -> None:
        self.rebuild_layout(preserve_page=True)

    # Kept as a convenient alias used by the theme switch.
    def render(self) -> None:
        self.refresh()

    def update_visible_pages(self) -> None:
        """Rasterise visible pages plus a small buffer, leave the rest as placeholders."""
        if not self._page_items:
            return
        bar = self.verticalScrollBar()
        top = bar.value() - self.viewport().height() * RENDER_BUFFER
        bottom = bar.value() + self.viewport().height() * (1 + RENDER_BUFFER)
        for idx, (page_top, (_, height)) in enumerate(
            zip(self._page_tops, self._page_sizes)
        ):
            if page_top < bottom and page_top + height > top:
                for neighbour in range(idx - RENDER_BUFFER, idx + RENDER_BUFFER + 1):
                    if 0 <= neighbour < len(self._page_items) and neighbour not in self._rendered:
                        self._render_page(neighbour)

    def _render_page(self, idx: int) -> None:
        assert self._doc is not None
        # A fresh Page object from the document already sees annotations and
        # OCR text added through other Page objects; reload_page() would fail
        # when several Python references to the page are alive.
        page = self._doc.load_page(idx)
        pix = page.get_pixmap(
            matrix=fitz.Matrix(self._zoom, self._zoom),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        image = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,
        ).copy()
        self._page_items[idx].setPixmap(QPixmap.fromImage(image))
        self._rendered.add(idx)

    def _scroll_to_page(self, idx: int) -> None:
        if not self._page_tops:
            return
        idx = max(0, min(len(self._page_tops) - 1, idx))
        self._current_page = idx
        bar = self.verticalScrollBar()
        self._updating_scroll = True
        bar.setValue(max(0, int(self._page_tops[idx] - 8)))
        self._updating_scroll = False
        self._emit_page()
        self.update_visible_pages()

    def _on_scroll(self, *_args) -> None:
        if self._updating_scroll or not self._page_tops:
            return
        top = self.verticalScrollBar().value() + 12
        idx = 0
        for number, page_top in enumerate(self._page_tops):
            if page_top <= top:
                idx = number
            else:
                break
        if idx != self._current_page:
            self._current_page = idx
            self._emit_page()
        self.update_visible_pages()

    # -- properties / navigation -------------------------------------------
    @property
    def document(self) -> fitz.Document | None:
        return self._doc

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def page_index(self) -> int:
        return self._current_page

    @property
    def tool(self) -> Tool:
        return self._tool

    def page_count(self) -> int:
        return len(self._doc) if self._doc is not None else 0

    def _page(self, idx: int | None = None) -> fitz.Page | None:
        if self._doc is None or not self._page_items:
            return None
        idx = self._current_page if idx is None else idx
        if not (0 <= idx < len(self._doc)):
            return None
        return self._doc.load_page(idx)

    def set_zoom(self, zoom: float) -> None:
        zoom = max(0.2, min(8.0, zoom))
        if abs(zoom - self._zoom) < 1e-4:
            return
        self._zoom = zoom
        self._auto_fit = False
        self.refresh()
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.2)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.2)

    def fit_page(self) -> None:
        page = self._page()
        if page is None:
            return
        width = self.viewport().width() - 24
        height = self.viewport().height() - 24
        if width <= 0 or height <= 0:
            return
        self._auto_fit = False
        self.set_zoom(min(width / page.rect.width, height / page.rect.height))

    def fit_width(self) -> None:
        page = self._page()
        if page is None:
            return
        width = self.viewport().width() - 24
        if width <= 0:
            return
        self._auto_fit = False
        self.set_zoom(width / page.rect.width)

    def goto_page(self, index: int) -> None:
        if self._doc is None or not self._page_items:
            return
        index = max(0, min(len(self._page_items) - 1, index))
        self._scroll_to_page(index)

    def next_page(self) -> None:
        self.goto_page(self._current_page + 1)

    def previous_page(self) -> None:
        self.goto_page(self._current_page - 1)

    def _emit_page(self) -> None:
        self.pageChanged.emit(self._current_page, self.page_count())
        self.zoomChanged.emit(self._zoom)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self._auto_fit and self._doc is not None:
            self.fit_page()
        else:
            self.update_visible_pages()

    # -- page operations ----------------------------------------------------
    def insert_page(self, after_current: bool = True) -> int | None:
        if self._doc is None:
            return None
        reference = self._page()
        width = reference.rect.width if reference is not None else 595.0
        height = reference.rect.height if reference is not None else 842.0
        index = min(len(self._doc), self._current_page + 1) if after_current else len(self._doc)
        label = "Вставка страницы" if after_current else "Добавление страницы"
        self.operationStarted.emit(label)
        try:
            self._doc.new_page(pno=index, width=width, height=height)
            self._current_page = index
            self.refresh()
            self.documentChanged.emit()
            self.statusMessage.emit(f"Добавлена страница {index + 1}")
        finally:
            self.operationFinished.emit(label)
        return index

    def insert_images(
        self,
        images: list[QImage],
        after_current: bool = True,
        dpi: float | None = None,
    ) -> list[int]:
        """Turn Qt images into PDF pages and insert them after the current page."""
        if self._doc is None or not images:
            return []
        index = (
            min(len(self._doc), self._current_page + 1)
            if after_current
            else len(self._doc)
        )
        label = "Добавление изображений"
        self.operationStarted.emit(label)
        try:
            count = image_import.insert_images(self._doc, images, index, dpi)
            self._current_page = index
            self.refresh()
            self.documentChanged.emit()
            self.statusMessage.emit(f"Добавлено страниц из изображений: {count}")
        finally:
            self.operationFinished.emit(label)
        return list(range(index, index + count))

    def delete_current_page(self) -> bool:
        if self._doc is None or len(self._doc) <= 1:
            self.statusMessage.emit("В документе должна остаться хотя бы одна страница")
            return False
        index = self._current_page
        label = "Удаление страницы"
        self.operationStarted.emit(label)
        try:
            self._doc.delete_page(index)
            self._current_page = max(0, min(index, len(self._doc) - 1))
            self.refresh()
            self.documentChanged.emit()
            self.statusMessage.emit("Страница удалена")
        finally:
            self.operationFinished.emit(label)
        return True

    # -- OCR ----------------------------------------------------------------
    def run_ocr(
        self,
        language: str,
        all_pages: bool = False,
        force: bool = False,
        progress=None,
        cancelled=None,
    ) -> tuple[int, int]:
        """Return (number of processed pages, number of inserted words)."""
        if self._doc is None:
            return 0, 0
        indices = list(range(len(self._doc))) if all_pages else [self._current_page]
        pages_done = 0
        words_done = 0
        label = "OCR"
        self.operationStarted.emit(label)
        try:
            for done, idx in enumerate(indices, start=1):
                if cancelled is not None and cancelled():
                    break
                page = self._doc.load_page(idx)
                count = ocr_module.ocr_page(page, language=language, force=force)
                if count:
                    pages_done += 1
                    words_done += count
                    self._rendered.discard(idx)
                if progress is not None:
                    progress(done, len(indices))
            if pages_done:
                self.refresh()
                self.documentChanged.emit()
        finally:
            self.operationFinished.emit(label)
        return pages_done, words_done

    # -- tools --------------------------------------------------------------
    def set_tool(self, tool: Tool) -> None:
        self._tool = tool
        self._clear_preview()
        if tool != Tool.SELECT:
            self._selected = None
            self._selection_item.hide()
        hints = {
            Tool.SELECT: "Выбор: щёлкните по аннотации, затем Delete для удаления",
            Tool.HIGHLIGHT: "Выделение: протяните рамку по тексту",
            Tool.UNDERLINE: "Подчёркивание: протяните рамку по тексту",
            Tool.STRIKEOUT: "Зачёркивание: протяните рамку по тексту",
            Tool.NOTE: "Заметка: щёлкните в месте размещения",
            Tool.FREETEXT: "Текстовое поле: протяните рамку",
            Tool.INK: "Карандаш: рисуйте мышью",
        }
        self.statusMessage.emit(hints.get(tool, ""))

    def set_color(self, color: QColor) -> None:
        self._color = (color.redF(), color.greenF(), color.blueF())

    def color(self) -> QColor:
        r, g, b = self._color
        return QColor.fromRgbF(r, g, b)

    def delete_selected(self) -> None:
        if self._selected is None:
            self.statusMessage.emit("Аннотация не выбрана")
            return
        idx, page, annot = self._selected
        label = "Удаление аннотации"
        self.operationStarted.emit(label)
        try:
            try:
                ann.delete_annotation(page, annot)
            except Exception as exc:  # pragma: no cover - PDF internals
                QMessageBox.warning(self, "Ошибка", f"Не удалось удалить аннотацию:\n{exc}")
                return
            self._selected = None
            self._selection_item.hide()
            self._rendered.discard(idx)
            self.refresh()
            self.annotationDeleted.emit()
        finally:
            self.operationFinished.emit(label)

    # -- mouse handling -----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._page() is None:
            return super().mousePressEvent(event)

        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            return super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        scene_pos = self.mapToScene(event.position().toPoint())
        idx = self._page_at(scene_pos)
        if idx is None:
            return super().mousePressEvent(event)

        self._start_scene = scene_pos
        self._start_pdf = self._to_pdf(scene_pos, idx)
        self._drag_page = idx
        self._dragging = True
        self._ink_points = []

        if self._tool in RECT_TOOLS:
            self._rubber.setRect(QRectF(scene_pos, scene_pos))
            self._rubber.show()
        elif self._tool is Tool.INK:
            self._ink_points.append(self._start_pdf)
            path = QPainterPath()
            path.moveTo(scene_pos)
            self._ink_path_item.setPath(path)
            self._ink_path_item.setPen(
                QPen(self.color(), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            self._ink_path_item.show()
        elif self._tool is Tool.SELECT:
            self._select_at(idx, self._start_pdf)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._dragging or self._page() is None:
            return super().mouseMoveEvent(event)
        current_scene = self.mapToScene(event.position().toPoint())
        if self._tool in RECT_TOOLS:
            self._rubber.setRect(QRectF(self._start_scene, current_scene).normalized())
        elif self._tool is Tool.INK:
            self._ink_points.append(self._to_pdf(current_scene, self._drag_page))
            path = self._ink_path_item.path()
            path.lineTo(current_scene)
            self._ink_path_item.setPath(path)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
            super().mouseReleaseEvent(event)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            return
        if not self._dragging or self._page() is None or self._drag_page is None:
            return super().mouseReleaseEvent(event)

        self._dragging = False
        idx = self._drag_page
        current_scene = self.mapToScene(event.position().toPoint())
        current_pdf = self._to_pdf(current_scene, idx)
        page = self._doc.load_page(idx)
        annot_obj = None
        label = None if self._tool is Tool.SELECT else f"Аннотация: {self._tool.value}"

        if label is not None:
            self.operationStarted.emit(label)
        try:
            try:
                if self._tool in MARKUP_TOOLS:
                    rect = ann.rect_between(self._start_pdf, current_pdf)
                    if ann.is_usable_rect(rect):
                        annot_obj = ann.add_markup(
                            page, rect, MARKUP_TOOLS[self._tool], self._color
                        )
                elif self._tool is Tool.FREETEXT:
                    rect = ann.rect_between(self._start_pdf, current_pdf)
                    if ann.is_usable_rect(rect):
                        text, ok = QInputDialog.getMultiLineText(
                            self, "Текстовое поле", "Введите текст:"
                        )
                        if ok and text.strip():
                            annot_obj = ann.add_freetext(page, rect, text.strip())
                elif self._tool is Tool.NOTE:
                    text, ok = QInputDialog.getMultiLineText(
                        self, "Заметка", "Введите текст заметки:"
                    )
                    if ok:
                        annot_obj = ann.add_note(page, current_pdf, text.strip(), self._color)
                elif self._tool is Tool.INK and len(self._ink_points) >= 2:
                    annot_obj = ann.add_ink(page, self._ink_points, self._color)
            except Exception as exc:
                QMessageBox.warning(self, "Ошибка", f"Не удалось добавить аннотацию:\n{exc}")
            finally:
                self._clear_preview()

            if annot_obj is not None:
                self._rendered.discard(idx)
                self.refresh()
                self.annotationAdded.emit(annot_obj)
                self.statusMessage.emit("Аннотация добавлена. Ctrl+S — сохранить.")
        finally:
            if label is not None:
                self.operationFinished.emit(label)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
            self.set_zoom(self._zoom * factor)
            event.accept()
        else:
            super().wheelEvent(event)

    # -- coordinate helpers -------------------------------------------------
    def _page_at(self, scene_pos: QPointF) -> int | None:
        for idx, item in enumerate(self._page_items):
            if item.sceneBoundingRect().contains(scene_pos):
                return idx
        return None

    def _to_pdf(self, scene_pos: QPointF, idx: int) -> ann.Point:
        page = self._page(idx)
        assert page is not None
        item = self._page_items[idx]
        local_x = scene_pos.x() - item.pos().x()
        local_y = scene_pos.y() - item.pos().y()
        return ann.screen_to_page(page, ann.Point(local_x, local_y), self._zoom)

    def _to_scene(self, idx: int, point: ann.Point) -> QPointF:
        page = self._page(idx)
        assert page is not None
        screen = ann.page_to_screen(page, point, self._zoom)
        offset = self._page_items[idx].pos()
        return QPointF(screen.x + offset.x(), screen.y + offset.y())

    def _select_at(self, idx: int, point: ann.Point) -> None:
        page = self._page(idx)
        assert page is not None
        hits = ann.annotations_at(page, point)
        if hits:
            self._selected = (idx, page, hits[-1])
            self._refresh_selection_overlay()
            content = hits[-1].info.get("content", "")
            self.statusMessage.emit(f"Аннотация выбрана: {content[:60] or 'без текста'}")
        else:
            self._selected = None
            self._selection_item.hide()

    def _refresh_selection_overlay(self) -> None:
        if self._selected is None:
            self._selection_item.hide()
            return
        idx, _page, annot = self._selected
        rect = annot.rect
        top_left = self._to_scene(idx, ann.Point(rect.x0, rect.y0))
        bottom_right = self._to_scene(idx, ann.Point(rect.x1, rect.y1))
        self._selection_item.setRect(
            QRectF(
                QPointF(min(top_left.x(), bottom_right.x()), min(top_left.y(), bottom_right.y())),
                QPointF(max(top_left.x(), bottom_right.x()), max(top_left.y(), bottom_right.y())),
            )
        )
        self._selection_item.show()

    def _clear_preview(self) -> None:
        self._dragging = False
        self._drag_page = None
        self._rubber.hide()
        self._ink_path_item.hide()
        self._ink_points = []
