"""Main window: menus, toolbar, file handling and theme switching."""

from __future__ import annotations

from pathlib import Path

try:  # PyMuPDF >= 1.24 exposes the canonical module name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older releases
    import fitz

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSpinBox,
    QStatusBar,
    QToolBar,
)

from . import annotations as ann
from . import ocr as ocr_module
from .theme import DARK, LIGHT, SYSTEM, ThemeManager
from .viewer import PdfViewer, Tool

UNDO_LIMIT = 20


def _history_icon(symbol: str, color: str) -> QIcon:
    """Small toolbar icon drawn locally (no external theme dependency)."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(color))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    return QIcon(pixmap)


class DocumentSnapshotCommand(QUndoCommand):
    """Undo/redo a document edit by restoring whole-PDF snapshots.

    Annotations, page operations and OCR all change the PDF in ways that are
    awkward to invert individually. Snapshots are simple and reliable; the
    stack is capped by ``UNDO_LIMIT`` to keep memory usage predictable.
    """

    def __init__(
        self, window: "MainWindow", label: str, before: bytes, after: bytes
    ) -> None:
        super().__init__(label)
        self._window = window
        self._before = before
        self._after = after
        self._first_redo = True

    def undo(self) -> None:
        self._window._restore_snapshot(self._before)

    def redo(self) -> None:
        # QUndoStack.redo() is called by push(); the change is already applied.
        if self._first_redo:
            self._first_redo = False
            return
        self._window._restore_snapshot(self._after)


class MainWindow(QMainWindow):
    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self._theme = theme
        self._settings = QSettings("PDFNoter", "PDFNoter")
        self._doc: fitz.Document | None = None
        self._path: str | None = None
        self._modified = False
        self._ocr_language = self._settings.value(
            "ocrLanguage", ocr_module.DEFAULT_LANGUAGE, str
        )
        self._updating_page_spin = False
        self._password = ""
        self._pending_snapshot: bytes | None = None
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(UNDO_LIMIT)
        self._undo_stack.setClean()
        self._undo_stack.cleanChanged.connect(self._on_clean_changed)

        self.setWindowTitle("PDF Noter")
        self.resize(1100, 780)

        self.viewer = PdfViewer(self)
        self.setCentralWidget(self.viewer)

        self._create_actions()
        self.act_undo.triggered.connect(
            lambda: self.statusBar().showMessage("Действие отменено", 2000)
        )
        self.act_redo.triggered.connect(
            lambda: self.statusBar().showMessage("Действие повторено", 2000)
        )
        self._create_menus()
        self._create_toolbar()
        self._create_status_bar()

        self.setAcceptDrops(True)
        self.viewer.annotationAdded.connect(self._on_modified)
        self.viewer.annotationDeleted.connect(self._on_modified)
        self.viewer.documentChanged.connect(self._on_modified)
        self.viewer.operationStarted.connect(self._on_operation_started)
        self.viewer.operationFinished.connect(self._on_operation_finished)
        self.viewer.pageChanged.connect(self._on_page_changed)
        self.viewer.zoomChanged.connect(self._on_zoom_changed)
        self.viewer.statusMessage.connect(self.statusBar().showMessage)
        theme.themeChanged.connect(self._refresh_color_icon)

        self.viewer.set_tool(Tool.HIGHLIGHT)
        self.viewer.set_color(QColor("#ffd633"))
        self._update_actions()

    # -- UI construction ----------------------------------------------------
    def _action(
        self,
        text: str,
        slot,
        shortcut: str | None = None,
        icon: QIcon | None = None,
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if icon is not None:
            action.setIcon(icon)
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        return action

    def _create_actions(self) -> None:
        self.act_open = self._action("Открыть...", self.open_dialog, "Ctrl+O")
        self.act_save = self._action("Сохранить", self.save, "Ctrl+S")
        self.act_save_as = self._action("Сохранить как...", self.save_as, "Ctrl+Shift+S")
        self.act_undo = self._undo_stack.createUndoAction(self, "Отменить")
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.setIcon(_history_icon("↶", "#69b7ff"))
        self.act_redo = self._undo_stack.createRedoAction(self, "Повторить")
        self.act_redo.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        self.act_redo.setIcon(_history_icon("↷", "#ffb26b"))
        self.act_close = self._action("Закрыть файл", self.close_document, "Ctrl+W")
        self.act_quit = self._action("Выход", self.close, "Alt+F4")

        self.act_zoom_in = self._action("Увеличить", self.viewer.zoom_in, "Ctrl++")
        self.act_zoom_out = self._action("Уменьшить", self.viewer.zoom_out, "Ctrl+-")
        self.act_fit_page = self._action("По странице", self.viewer.fit_page, "Ctrl+0")
        self.act_fit_width = self._action("По ширине", self.viewer.fit_width, "Ctrl+1")
        self.act_next_page = self._action("Следующая страница", self.viewer.next_page, "PgDown")
        self.act_prev_page = self._action("Предыдущая страница", self.viewer.previous_page, "PgUp")

        self.act_delete = self._action("Удалить аннотацию", self.viewer.delete_selected, "Del")
        self.act_color = self._action("Цвет аннотаций...", self.choose_color)
        self._refresh_color_icon()

        self.act_select = self._action("Выбор", lambda: self.viewer.set_tool(Tool.SELECT), "Esc")
        self.act_highlight = self._action("Выделение", lambda: self.viewer.set_tool(Tool.HIGHLIGHT))
        self.act_underline = self._action("Подчёркивание", lambda: self.viewer.set_tool(Tool.UNDERLINE))
        self.act_strikeout = self._action("Зачёркивание", lambda: self.viewer.set_tool(Tool.STRIKEOUT))
        self.act_note = self._action("Заметка", lambda: self.viewer.set_tool(Tool.NOTE))
        self.act_freetext = self._action("Текст", lambda: self.viewer.set_tool(Tool.FREETEXT))
        self.act_ink = self._action("Карандаш", lambda: self.viewer.set_tool(Tool.INK))

        self._tool_actions = {
            Tool.SELECT: self.act_select,
            Tool.HIGHLIGHT: self.act_highlight,
            Tool.UNDERLINE: self.act_underline,
            Tool.STRIKEOUT: self.act_strikeout,
            Tool.NOTE: self.act_note,
            Tool.FREETEXT: self.act_freetext,
            Tool.INK: self.act_ink,
        }
        self._tool_group = QActionGroup(self)
        for action in self._tool_actions.values():
            action.setCheckable(True)
            self._tool_group.addAction(action)
        self.act_highlight.setChecked(True)

        self.act_system_theme = self._action("Системная", lambda: self._set_theme(SYSTEM), checkable=True)
        self.act_dark_theme = self._action("Тёмная", lambda: self._set_theme(DARK), checkable=True)
        self.act_light_theme = self._action("Светлая", lambda: self._set_theme(LIGHT), checkable=True)
        self._theme_group = QActionGroup(self)
        for action in (self.act_system_theme, self.act_dark_theme, self.act_light_theme):
            self._theme_group.addAction(action)
        getattr(self, f"act_{self._theme.mode}_theme").setChecked(True)

        self.act_insert_page = self._action(
            "Вставить страницу после текущей", self.insert_page, "Ctrl+Shift+Return"
        )
        self.act_append_page = self._action(
            "Добавить страницу в конец", self.append_page
        )
        self.act_delete_page = self._action(
            "Удалить текущую страницу", self.delete_page, "Ctrl+Del"
        )

        self.act_ocr_page = self._action(
            "Распознать текущую страницу (OCR)", lambda: self.run_ocr(False), "Ctrl+Shift+R"
        )
        self.act_ocr_all = self._action(
            "Распознать весь документ (OCR)", lambda: self.run_ocr(True)
        )
        self.act_ocr_language = self._action("Язык OCR...", self.choose_ocr_language)

        self.act_about = self._action("О программе", self.show_about)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction(self.act_open)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save)
        file_menu.addAction(self.act_save_as)
        file_menu.addAction(self.act_close)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = self.menuBar().addMenu("Правка")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_delete)

        view_menu = self.menuBar().addMenu("Вид")
        view_menu.addAction(self.act_zoom_in)
        view_menu.addAction(self.act_zoom_out)
        view_menu.addAction(self.act_fit_page)
        view_menu.addAction(self.act_fit_width)
        view_menu.addSeparator()
        view_menu.addAction(self.act_prev_page)
        view_menu.addAction(self.act_next_page)

        pages_menu = self.menuBar().addMenu("Страницы")
        pages_menu.addAction(self.act_insert_page)
        pages_menu.addAction(self.act_append_page)
        pages_menu.addAction(self.act_delete_page)

        annot_menu = self.menuBar().addMenu("Аннотации")
        for action in self._tool_actions.values():
            annot_menu.addAction(action)
        annot_menu.addSeparator()
        annot_menu.addAction(self.act_color)
        annot_menu.addAction(self.act_delete)
        annot_menu.addSeparator()
        annot_menu.addAction(self.act_ocr_page)
        annot_menu.addAction(self.act_ocr_all)
        annot_menu.addAction(self.act_ocr_language)

        theme_menu = self.menuBar().addMenu("Тема")
        theme_menu.addAction(self.act_system_theme)
        theme_menu.addAction(self.act_dark_theme)
        theme_menu.addAction(self.act_light_theme)

        help_menu = self.menuBar().addMenu("Справка")
        help_menu.addAction(self.act_about)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Панель инструментов", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        toolbar.addAction(self.act_open)
        toolbar.addAction(self.act_save)
        toolbar.addSeparator()
        toolbar.addAction(self.act_undo)
        toolbar.addAction(self.act_redo)
        toolbar.addSeparator()
        for tool, action in self._tool_actions.items():
            action.setText(self._tool_label(tool))
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.act_color)
        toolbar.addAction(self.act_delete)
        toolbar.addSeparator()
        toolbar.addAction(self.act_zoom_out)
        toolbar.addAction(self.act_zoom_in)
        toolbar.addAction(self.act_fit_page)
        toolbar.addSeparator()

        self.page_spin = QSpinBox(self)
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setToolTip("Номер страницы")
        self.page_spin.valueChanged.connect(self._on_spin_page)
        toolbar.addWidget(self.page_spin)
        toolbar.addAction(self.act_insert_page)
        toolbar.addAction(self.act_delete_page)
        toolbar.addSeparator()
        toolbar.addAction(self.act_ocr_page)

    def _on_spin_page(self, value: int) -> None:
        if not self._updating_page_spin:
            self.viewer.goto_page(value - 1)

    @staticmethod
    def _tool_label(tool: Tool) -> str:
        return {
            Tool.SELECT: "▣ Выбор",
            Tool.HIGHLIGHT: "▨ Выделение",
            Tool.UNDERLINE: "▂ Подчёркивание",
            Tool.STRIKEOUT: "▿ Зачёркивание",
            Tool.NOTE: "● Заметка",
            Tool.FREETEXT: "T Текст",
            Tool.INK: "✎ Карандаш",
        }[tool]

    def _create_status_bar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)
        self._page_label = QLabel("Страница: —")
        self._zoom_label = QLabel("Масштаб: —")
        self._file_label = QLabel("Файл не открыт")
        status.addPermanentWidget(self._page_label)
        status.addPermanentWidget(self._zoom_label)
        status.addPermanentWidget(self._file_label, 1)
        status.showMessage("Откройте PDF-файл (Ctrl+O) или перетащите его в окно")

    # -- file handling ------------------------------------------------------
    def open_dialog(self) -> None:
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть PDF", start, "PDF-файлы (*.pdf);;Все файлы (*.*)"
        )
        if path:
            self.open_pdf(path)

    def open_pdf(self, path: str) -> bool:
        if not self._confirm_discard():
            return False
        self._password = ""
        try:
            doc = fitz.open(path)
            if doc.needs_pass:
                password, ok = QInputDialog.getText(
                    self,
                    "Пароль",
                    "PDF защищён паролем:",
                    QLineEdit.EchoMode.Password,
                )
                if not ok or not doc.authenticate(password):
                    doc.close()
                    QMessageBox.warning(self, "Ошибка", "Неверный пароль или файл не удалось открыть.")
                    return False
                self._password = password
            if len(doc) == 0:
                try:
                    doc.repair()
                except Exception:
                    pass
            if len(doc) == 0:
                doc.close()
                message = (
                    f"Файл «{Path(path).name}» не содержит страниц или повреждён.\n\n"
                    "PyMuPDF не смог восстановить структуру PDF (отсутствует "
                    "каталог страниц). Попробуйте открыть другую копию файла "
                    "или скачать документ заново."
                )
                QMessageBox.warning(self, "Файл не открыт", message)
                self.statusBar().showMessage("Не удалось открыть файл: повреждённый PDF", 8000)
                return False
            self.viewer.open_document(doc)
        except Exception as exc:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось открыть файл «{Path(path).name}»:\n{exc}"
            )
            self.statusBar().showMessage("Не удалось открыть файл", 8000)
            return False

        if self._doc is not None:
            self._doc.close()
        self._doc = doc
        self._path = path
        self._pending_snapshot = None
        self._undo_stack.clear()
        self._mark_clean()
        self._settings.setValue("lastDir", str(Path(path).parent))
        self._update_actions()
        self._file_label.setText(Path(path).name)
        return True

    def close_document(self) -> None:
        if not self._confirm_discard():
            return
        if self._doc is not None:
            self._doc.close()
        self._doc = None
        self._path = None
        self._password = ""
        self._pending_snapshot = None
        self._undo_stack.clear()
        self._modified = False
        self.viewer.close_document()
        self._update_title()
        self._update_actions()
        self._file_label.setText("Файл не открыт")

    def save(self) -> bool:
        if self._doc is None:
            return False
        if len(self._doc) == 0:
            QMessageBox.warning(self, "Ошибка сохранения", "Документ не содержит страниц.")
            return False
        if self._path is None:
            return self.save_as()
        try:
            ann.save_document(self._doc)
        except Exception:
            return self.save_as()
        self._mark_clean()
        self.statusBar().showMessage("Файл сохранён", 3000)
        return True

    def save_as(self) -> bool:
        if self._doc is None:
            return False
        default = self._path or str(Path.home() / "document.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить PDF как", default, "PDF-файлы (*.pdf)"
        )
        if not path:
            return False
        try:
            ann.save_document(self._doc, path)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{exc}")
            return False
        self._path = path
        self._mark_clean()
        self._update_title()
        self._file_label.setText(Path(path).name)
        self.statusBar().showMessage("Файл сохранён", 3000)
        return True

    def _mark_clean(self) -> None:
        self._undo_stack.setClean()
        self._modified = False
        self._update_title()

    def _confirm_discard(self) -> bool:
        if not self._modified:
            return True
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "Сохранить изменения перед продолжением?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def _update_title(self) -> None:
        name = Path(self._path).name if self._path else "без файла"
        mark = " *" if self._modified else ""
        self.setWindowTitle(f"PDF Noter — {name}{mark}")

    def _on_modified(self, *_args) -> None:
        self._modified = True
        self._update_title()

    def _on_clean_changed(self, clean: bool) -> None:
        self._modified = not clean
        self._update_title()

    def _on_operation_started(self, label: str) -> None:
        if self._doc is None:
            self._pending_snapshot = None
            return
        try:
            self._pending_snapshot = self._doc.tobytes()
        except Exception:
            self._pending_snapshot = None

    def _on_operation_finished(self, label: str) -> None:
        before = self._pending_snapshot
        self._pending_snapshot = None
        if before is None or self._doc is None:
            return
        try:
            after = self._doc.tobytes()
        except Exception:
            return
        if before == after:
            return
        self._undo_stack.push(DocumentSnapshotCommand(self, label, before, after))

    def _restore_snapshot(self, data: bytes) -> None:
        old = self._doc
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            if doc.needs_pass and self._password:
                doc.authenticate(self._password)
        except Exception as exc:
            QMessageBox.warning(self, "Отмена", f"Не удалось восстановить документ:\n{exc}")
            return
        self._pending_snapshot = None
        self.viewer.replace_document(doc)
        self._doc = doc
        if old is not None:
            old.close()
        self._update_actions()

    def _on_page_changed(self, index: int, count: int) -> None:
        self._updating_page_spin = True
        self.page_spin.setMaximum(max(1, count))
        self.page_spin.setValue(index + 1)
        self._updating_page_spin = False
        self._page_label.setText(f"Страница: {index + 1} / {count}" if count else "Страница: —")

    def _on_zoom_changed(self, zoom: float) -> None:
        self._zoom_label.setText(f"Масштаб: {zoom * 100:.0f}%")

    def _update_actions(self) -> None:
        enabled = self._doc is not None
        for action in (
            self.act_save,
            self.act_save_as,
            self.act_close,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_fit_page,
            self.act_fit_width,
            self.act_next_page,
            self.act_prev_page,
            self.act_insert_page,
            self.act_append_page,
            self.act_delete_page,
            self.act_ocr_page,
            self.act_ocr_all,
            self.act_color,
            self.act_delete,
        ):
            action.setEnabled(enabled)
        self.page_spin.setEnabled(enabled)

    # -- pages --------------------------------------------------------------
    def insert_page(self) -> None:
        self.viewer.insert_page(after_current=True)

    def append_page(self) -> None:
        self.viewer.insert_page(after_current=False)

    def delete_page(self) -> None:
        if self._doc is None:
            return
        if len(self._doc) <= 1:
            self.statusBar().showMessage(
                "В документе должна остаться хотя бы одна страница", 3000
            )
            return
        answer = QMessageBox.question(
            self,
            "Удаление страницы",
            f"Удалить страницу {self.viewer.page_index + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.viewer.delete_current_page()

    # -- OCR ----------------------------------------------------------------
    def run_ocr(self, all_pages: bool) -> None:
        if self._doc is None:
            return
        try:
            ocr_module.check_language(self._ocr_language)
        except ocr_module.OcrUnavailable as exc:
            QMessageBox.warning(self, "OCR недоступен", str(exc))
            return

        total = len(self._doc) if all_pages else 1
        progress = QProgressDialog("Распознавание страниц...", "Отмена", 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(500)
        progress.setValue(0)

        def on_progress(done: int, _total: int) -> None:
            progress.setValue(done)
            self.application_process_events()

        def cancelled() -> bool:
            self.application_process_events()
            return progress.wasCanceled()

        try:
            pages, words = self.viewer.run_ocr(
                self._ocr_language, all_pages=all_pages, progress=on_progress, cancelled=cancelled
            )
        finally:
            progress.setValue(total)
        if progress.wasCanceled():
            self.statusBar().showMessage("OCR прерван", 3000)
        elif pages:
            self.statusBar().showMessage(
                f"OCR: распознано страниц: {pages}, слов добавлено: {words}", 5000
            )
        else:
            self.statusBar().showMessage(
                "OCR: на выбранных страницах уже есть распознанный текст", 5000
            )

    @staticmethod
    def application_process_events() -> None:
        QApplication.processEvents()

    def choose_ocr_language(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Язык OCR",
            "Коды языков Tesseract (например, rus+eng):",
            text=self._ocr_language,
        )
        if not ok:
            return
        language = text.strip() or "eng"
        try:
            ocr_module.check_language(language)
        except ocr_module.OcrUnavailable as exc:
            QMessageBox.warning(self, "Язык OCR недоступен", str(exc))
            return
        self._ocr_language = language
        self._settings.setValue("ocrLanguage", language)
        installed = ocr_module.available_languages()
        self.statusBar().showMessage(
            f"Язык OCR: {language} (установлены: {', '.join(installed) or 'нет'})", 5000
        )

    # -- theme / color ------------------------------------------------------
    def _set_theme(self, mode: str) -> None:
        self._theme.set_mode(mode)
        getattr(self, f"act_{mode}_theme").setChecked(True)
        self.viewer.render()
        self.statusBar().showMessage("Тема изменена", 2000)

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.viewer.color(), self, "Цвет аннотаций")
        if color.isValid():
            self.viewer.set_color(color)
            self._refresh_color_icon()

    def _refresh_color_icon(self) -> None:
        pixmap = QPixmap(20, 20)
        pixmap.fill(self.viewer.color() if hasattr(self, "viewer") else QColor("#ffd633"))
        self.act_color.setIcon(QIcon(pixmap))

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "О программе",
            "<b>PDF Noter</b> — свободный просмотрщик PDF с аннотациями.<br><br>"
            "Возможности: непрерывная прокрутка страниц, масштабирование, "
            "выделение текста, заметки, текстовые поля, карандаш, удаление "
            "аннотаций, отмена/повтор действий, добавление/удаление страниц, "
            "OCR через Tesseract, сохранение в PDF.<br><br>"
            "Основан на PySide6 (LGPL v3), PyMuPDF (AGPL v3) и Tesseract OCR (Apache 2.0).<br>"
            "Исходный код распространяется по лицензии AGPL-3.0-or-later.<br><br>"
            "Это независимая разработка, не связанная с PDF-XChange.",
        )

    # -- drag and drop ------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
                event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).suffix.lower() == ".pdf":
                self.open_pdf(path)
        event.acceptProposedAction()
