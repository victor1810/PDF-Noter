"""Image sources: files, clipboard data and Windows WIA scanners.

Everything is converted to :class:`PySide6.QtGui.QImage` first, then into PDF
pages at a sane physical size. PyMuPDF is used for the final embedding so no
extra imaging dependency is required.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

try:  # PyMuPDF >= 1.24 exposes the canonical module name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older releases
    import fitz

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
    ".webp",
}
IMAGE_FILTER = (
    "Изображения (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp);;"
    "Все файлы (*.*)"
)
DEFAULT_DPI = 150.0


class ImageSourceError(RuntimeError):
    """Raised when an image source cannot provide usable data."""


class ImageSourceCancelled(ImageSourceError):
    """Raised when the user cancels a scanner/camera dialog."""


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def read_images(path: str | Path) -> list[QImage]:
    """Read one or more frames (e.g. a multipage TIFF) from ``path``."""
    reader = QImageReader(str(path))
    images: list[QImage] = []
    while True:
        image = reader.read()
        if image.isNull():
            break
        images.append(image.copy())
        if not reader.jumpToNextImage():
            break
    if not images:
        raise ImageSourceError(
            f"Не удалось прочитать изображение «{Path(path).name}»: "
            f"{reader.errorString() or 'неизвестный формат'}"
        )
    return images


def image_dpi(image: QImage) -> float:
    """Use embedded DPI when present, otherwise assume 150 dpi."""
    x = image.dotsPerMeterX()
    y = image.dotsPerMeterY()
    if x <= 0 or y <= 0:
        return DEFAULT_DPI
    dpi = (x * 0.0254 + y * 0.0254) / 2.0
    return max(36.0, min(1200.0, dpi))


def qimage_to_pixmap(image: QImage) -> fitz.Pixmap:
    """Convert a Qt image to a PyMuPDF pixmap via an in-memory PNG buffer."""
    if image.isNull():
        raise ImageSourceError("Пустое изображение")
    if image.format() != QImage.Format.Format_RGB888:
        image = image.convertToFormat(QImage.Format.Format_RGB888)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        buffer.close()
        raise ImageSourceError("Не удалось закодировать изображение в PNG")
    data = bytes(buffer.data())
    buffer.close()
    # PyMuPDF 1.28 accepts image bytes only as a positional argument.
    return fitz.Pixmap(data)


def insert_images(
    doc: fitz.Document,
    images: list[QImage],
    index: int,
    dpi: float | None = None,
) -> int:
    """Insert ``images`` starting at zero-based ``index``; return page count."""
    inserted = 0
    for offset, image in enumerate(images):
        pixmap = qimage_to_pixmap(image)
        resolution = dpi if dpi and dpi > 0 else image_dpi(image)
        width = max(1.0, pixmap.width * 72.0 / resolution)
        height = max(1.0, pixmap.height * 72.0 / resolution)
        page = doc.new_page(pno=index + offset, width=width, height=height)
        page.insert_image(page.rect, pixmap=pixmap, keep_proportion=True)
        inserted += 1
    return inserted


_WIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
# Path comes from the environment: -Command does not reliably bind trailing
# arguments to $args, and this also handles spaces/Cyrillic in the path.
$outDir = $env:PDFNOTER_SCAN_OUTDIR
$jpeg = '{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}'
try {
    $dialog = New-Object -ComObject WIA.CommonDialog
    # WiaDeviceType.ScannerDevice=1, WiaImageIntent.ColorIntent=2,
    # WiaImageBias.MaximizeQuality=131072 (0x20000),
    # AutoSelect=$false, ShowUI=$true, Connect=$false
    $result = $dialog.ShowAcquireImage(1, 2, 131072, $jpeg, $false, $true, $false)
    if ($null -eq $result) {
        Write-Output 'CANCELLED'
        exit 2
    }
    # WIA returns one ImageFile or a Vector of ImageFiles for multi-page scans.
    $items = @()
    if ($result.PSObject.Properties.Name -contains 'Count') {
        foreach ($image in $result) { $items += $image }
    } else {
        $items = @($result)
    }
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $i = 0
    foreach ($image in $items) {
        $suffix = if ($i -eq 0) { '' } else { '_' + $i }
        $file = Join-Path $outDir ("scan_$stamp$suffix.jpg")
        $image.SaveFile($file)
        Write-Output $file
        $i++
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def scan_via_wia(output_dir: str | Path, timeout: int = 300) -> list[Path]:
    """Show the Windows scanner dialog and save acquired images to ``output_dir``."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    env["PDFNOTER_SCAN_OUTDIR"] = str(output_dir)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _WIA_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=flags,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImageSourceError("Сканер не ответил вовремя") from exc
    except OSError as exc:
        raise ImageSourceError(f"Не удалось запустить сканирование: {exc}") from exc

    output = (completed.stdout or "").strip()
    if completed.returncode == 2 or output == "CANCELLED":
        raise ImageSourceCancelled("Сканирование отменено")
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or f"код ошибки {completed.returncode}"
        raise ImageSourceError(f"Ошибка сканирования: {detail}")

    paths = [
        Path(line.strip())
        for line in output.splitlines()
        if line.strip().lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"))
    ]
    paths = [path for path in paths if path.exists()]
    if not paths:
        raise ImageSourceError("Сканер не вернул изображения")
    return paths
