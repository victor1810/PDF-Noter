"""Optional webcam capture dialog (requires PySide6-Addons / QtMultimedia)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

try:
    from PySide6.QtMultimedia import (
        QCamera,
        QImageCapture,
        QMediaDevices,
        QMediaCaptureSession,
    )
    from PySide6.QtMultimediaWidgets import QVideoWidget
except ImportError:  # pragma: no cover - QtMultimedia is an optional add-on
    QCamera = QImageCapture = QMediaDevices = QMediaCaptureSession = None  # type: ignore[assignment]
    QVideoWidget = None  # type: ignore[assignment]


class CameraDialog(QDialog):
    """Show a live camera preview and return the captured frame as QImage."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Камера — PDF Noter")
        self.resize(720, 560)

        self._image: QImage | None = None
        self._devices: list = []
        self._camera = None
        self._session = None
        self._capture = None

        layout = QVBoxLayout(self)
        self._status = QLabel(self)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._device_combo = QComboBox(self)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        layout.addWidget(self._device_combo)

        self._view = QVideoWidget(self) if QVideoWidget is not None else QLabel(self)
        self._view.setMinimumSize(480, 320)
        layout.addWidget(self._view, 1)

        buttons = QDialogButtonBox(self)
        self._capture_button = QPushButton("Снять кадр", self)
        self._capture_button.setEnabled(False)
        self._capture_button.clicked.connect(self.capture_frame)
        buttons.addButton(self._capture_button, QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(
            "Отмена", QDialogButtonBox.ButtonRole.RejectRole
        )
        close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

        if QCamera is None:
            self._status.setText(
                "QtMultimedia не установлен. Установите PySide6-Addons, "
                "чтобы использовать камеру."
            )
            return

        self._devices = list(QMediaDevices.videoInputs())
        for number, device in enumerate(self._devices):
            self._device_combo.addItem(device.description() or f"Камера {number + 1}", number)

        if not self._devices:
            self._status.setText("Камера не найдена. Подключите веб-камеру и повторите.")
            return

        self._session = QMediaCaptureSession(self)
        if self._view is not None:
            self._session.setVideoOutput(self._view)
        self._capture = QImageCapture(self)
        self._session.setImageCapture(self._capture)
        self._capture.imageCaptured.connect(self._on_image_captured)
        self._capture.errorOccurred.connect(self._on_capture_error)
        self._on_device_changed(0)

    @staticmethod
    def available() -> bool:
        return QMediaDevices is not None and bool(QMediaDevices.videoInputs())

    def image(self) -> QImage | None:
        return self._image

    def _on_device_changed(self, index: int) -> None:
        if self._session is None or not self._devices:
            return
        index = max(0, min(index, len(self._devices) - 1))
        if self._camera is not None:
            self._camera.stop()
            self._camera.deleteLater()
        device = self._devices[index]
        self._camera = QCamera(device, self)
        self._camera.errorOccurred.connect(self._on_camera_error)
        self._session.setCamera(self._camera)
        self._camera.start()
        self._capture_button.setEnabled(True)
        self._status.setText(f"Камера: {device.description() or 'по умолчанию'}")

    def capture_frame(self) -> None:
        if self._capture is None:
            QMessageBox.warning(self, "Камера", "Камера недоступна.")
            return
        if not self._capture.capture():
            QMessageBox.warning(self, "Камера", "Не удалось сделать снимок.")

    def _on_image_captured(self, _id: int, image: QImage) -> None:
        self._image = image.copy()
        self.accept()

    def _on_capture_error(self, *_args) -> None:
        QMessageBox.warning(self, "Камера", "Ошибка захвата кадра.")

    def _on_camera_error(self, *_args) -> None:
        QMessageBox.warning(self, "Камера", "Не удалось открыть камеру.")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._camera is not None:
            self._camera.stop()
        super().closeEvent(event)
