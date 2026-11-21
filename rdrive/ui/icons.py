"""Tạo icon vector sắc nét bằng QPainter (không phụ thuộc file ảnh ngoài)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _make_canvas(size: int = 24) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


@lru_cache(maxsize=64)
def app_icon(size: int = 64) -> QIcon:
    pm, p = _make_canvas(size)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#202634"))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.22, size * 0.22)
    # Viền tam giác Google Drive cách điệu + chữ R
    s = size
    p.setBrush(QColor("#4285F4"))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.68, s * 0.72, s * 0.15), 4, 4)
    p.setBrush(QColor("#34A853"))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.18, s * 0.16, s * 0.56), 4, 4)
    p.setBrush(QColor("#FBBC05"))
    p.drawRoundedRect(QRectF(s * 0.70, s * 0.18, s * 0.16, s * 0.56), 4, 4)
    f = QFont("Segoe UI", int(size * 0.42))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 0, s, s * 0.86), Qt.AlignCenter, "R")
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=32)
def gdrive_icon(size: int = 18) -> QIcon:
    pm, p = _make_canvas(size)
    p.setPen(Qt.NoPen)
    s = float(size)
    # Cạnh xanh lá (trái)
    p.setBrush(QColor("#0F9D58"))
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.34, s * 0.12),
        QPointF(s * 0.52, s * 0.42),
        QPointF(s * 0.28, s * 0.84),
        QPointF(s * 0.10, s * 0.54),
    ]))
    # Cạnh vàng (phải)
    p.setBrush(QColor("#F4B400"))
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.34, s * 0.12),
        QPointF(s * 0.66, s * 0.12),
        QPointF(s * 0.90, s * 0.54),
        QPointF(s * 0.58, s * 0.54),
    ]))
    # Cạnh xanh dương (đáy)
    p.setBrush(QColor("#4285F4"))
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.36, s * 0.56),
        QPointF(s * 0.90, s * 0.56),
        QPointF(s * 0.74, s * 0.84),
        QPointF(s * 0.22, s * 0.84),
    ]))
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=32)
def pc_icon(size: int = 18) -> QIcon:
    pm, p = _make_canvas(size)
    s = float(size)
    p.setPen(QPen(QColor("#5ea1ff"), 1.5))
    p.setBrush(QColor("#1e2d45"))
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.15, s * 0.76, s * 0.52), 2.5, 2.5)
    p.setPen(QPen(QColor("#9aa5b8"), 1.6))
    p.drawLine(QPointF(s * 0.50, s * 0.68), QPointF(s * 0.50, s * 0.84))
    p.drawLine(QPointF(s * 0.30, s * 0.84), QPointF(s * 0.70, s * 0.84))
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=32)
def folder_icon(color: str = "#f0be4b", size: int = 18) -> QIcon:
    pm, p = _make_canvas(size)
    s = float(size)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color).darker(118))
    path = QPainterPath()
    path.moveTo(s * 0.08, s * 0.28)
    path.lineTo(s * 0.08, s * 0.20)
    path.lineTo(s * 0.42, s * 0.20)
    path.lineTo(s * 0.50, s * 0.30)
    path.lineTo(s * 0.92, s * 0.30)
    path.lineTo(s * 0.92, s * 0.82)
    path.lineTo(s * 0.08, s * 0.82)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(s * 0.08, s * 0.34, s * 0.84, s * 0.48), 2.0, 2.0)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=32)
def disk_icon(size: int = 18) -> QIcon:
    pm, p = _make_canvas(size)
    s = float(size)
    p.setPen(QPen(QColor("#8c96a8"), 1.4))
    p.setBrush(QColor("#2c3240"))
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.30, s * 0.76, s * 0.44), 3, 3)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3ecf8e"))
    p.drawEllipse(QPointF(s * 0.74, s * 0.52), s * 0.07, s * 0.07)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=32)
def nas_icon(size: int = 18) -> QIcon:
    """Biểu tượng NAS Xpenology / Synology (hộp máy chủ 2 khay ổ cứng + đèn LED xanh)."""
    pm, p = _make_canvas(size)
    s = float(size)
    # Thân máy NAS
    p.setPen(QPen(QColor("#5ea1ff"), 1.3))
    p.setBrush(QColor("#1c2433"))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.12, s * 0.72, s * 0.76), 3.0, 3.0)
    # 2 khay ổ cứng dọc
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2c3850"))
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.22, s * 0.24, s * 0.56), 1.5, 1.5)
    p.drawRoundedRect(QRectF(s * 0.52, s * 0.22, s * 0.24, s * 0.56), 1.5, 1.5)
    # Đèn LED trạng thái xanh lá + xanh dương
    p.setBrush(QColor("#3ecf8e"))
    p.drawEllipse(QPointF(s * 0.34, s * 0.32), s * 0.055, s * 0.055)
    p.drawEllipse(QPointF(s * 0.64, s * 0.32), s * 0.055, s * 0.055)
    p.setBrush(QColor("#5ea1ff"))
    p.drawRoundedRect(QRectF(s * 0.27, s * 0.64, s * 0.14, s * 0.06), 1.0, 1.0)
    p.drawRoundedRect(QRectF(s * 0.57, s * 0.64, s * 0.14, s * 0.06), 1.0, 1.0)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=32)
def badge_file_icon(tag: str, color: str, size: int = 18) -> QIcon:
    pm, p = _make_canvas(size)
    s = float(size)
    p.setPen(QPen(QColor(color), 1.3))
    p.setBrush(QColor("#1c2028"))
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.10, s * 0.68, s * 0.80), 2.5, 2.5)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(s * 0.10, s * 0.48, s * 0.80, s * 0.34), 2, 2)
    f = QFont("Segoe UI", max(5, int(size * 0.25)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#111318"))
    p.drawText(QRectF(s * 0.10, s * 0.48, s * 0.80, s * 0.34), Qt.AlignCenter, tag)
    p.end()
    return QIcon(pm)


_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".mxf", ".mts", ".m4v", ".ts"}
_AUDIO_EXT = {".wav", ".mp3", ".flac", ".aac", ".m4a", ".ogg", ".wma"}
_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".psd", ".tif", ".tiff"}
_ARC_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}
_PR_EXT = {".prproj", ".xml", ".aep", ".drp"}


def file_icon(filename: str, is_dir: bool = False) -> QIcon:
    if is_dir:
        return folder_icon()
    ext = Path(filename).suffix.lower()
    if ext in _VIDEO_EXT:
        return badge_file_icon("VID", "#ff8a3d")
    if ext in _AUDIO_EXT:
        return badge_file_icon("AUD", "#3ecf8e")
    if ext in _IMG_EXT:
        return badge_file_icon("IMG", "#c084fc")
    if ext in _ARC_EXT:
        return badge_file_icon("ZIP", "#f5b841")
    if ext in _PR_EXT:
        return badge_file_icon("PR", "#9d7bff")
    if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".csv"}:
        return badge_file_icon("DOC", "#4f8cff")
    return badge_file_icon("FILE", "#8b93a1")


@lru_cache(maxsize=32)
def ribbon_icon(kind: str, size: int = 20) -> QIcon:
    if kind == "explorer":
        return folder_icon("#4f8cff", size)
    pm, p = _make_canvas(size)
    s = float(size)
    if kind == "accounts":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#4f8cff"))
        p.drawEllipse(QPointF(s * 0.50, s * 0.34), s * 0.18, s * 0.18)
        p.drawRoundedRect(QRectF(s * 0.22, s * 0.56, s * 0.56, s * 0.28), 5, 5)
    elif kind == "sync":
        p.setPen(QPen(QColor("#3ecf8e"), 2.0))
        p.drawLine(QPointF(s * 0.18, s * 0.36), QPointF(s * 0.80, s * 0.36))
        p.drawLine(QPointF(s * 0.64, s * 0.22), QPointF(s * 0.80, s * 0.36))
        p.drawLine(QPointF(s * 0.64, s * 0.50), QPointF(s * 0.80, s * 0.36))
        p.setPen(QPen(QColor("#4f8cff"), 2.0))
        p.drawLine(QPointF(s * 0.82, s * 0.68), QPointF(s * 0.20, s * 0.68))
        p.drawLine(QPointF(s * 0.36, s * 0.54), QPointF(s * 0.20, s * 0.68))
        p.drawLine(QPointF(s * 0.36, s * 0.82), QPointF(s * 0.20, s * 0.68))
    elif kind == "auto":
        p.setPen(QPen(QColor("#f5b841"), 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.36, s * 0.36)
        p.drawLine(QPointF(s * 0.50, s * 0.50), QPointF(s * 0.50, s * 0.28))
        p.drawLine(QPointF(s * 0.50, s * 0.50), QPointF(s * 0.68, s * 0.58))
    elif kind == "ai":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#c084fc"))
        # Ngôi sao 4 cánh AI
        p.drawPolygon(QPolygonF([
            QPointF(s * 0.50, s * 0.10),
            QPointF(s * 0.60, s * 0.40),
            QPointF(s * 0.90, s * 0.50),
            QPointF(s * 0.60, s * 0.60),
            QPointF(s * 0.50, s * 0.90),
            QPointF(s * 0.40, s * 0.60),
            QPointF(s * 0.10, s * 0.50),
            QPointF(s * 0.40, s * 0.40),
        ]))
    elif kind == "settings":
        p.setPen(QPen(QColor("#9aa5b8"), 2.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.26, s * 0.26)
        p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.10, s * 0.10)
    elif kind == "syau":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ff8a3d"))
        p.drawRoundedRect(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76), 4, 4)
        f = QFont("Segoe UI", int(size * 0.52))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#1b1206"))
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "S")
    p.end()
    return QIcon(pm)
