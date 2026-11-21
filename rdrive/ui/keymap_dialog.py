"""Hộp thoại Tùy chỉnh Phím tắt (Custom Hotkeys) cho Rdrive."""
from __future__ import annotations

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.config import AppConfig
from . import icons

DEFAULT_HOTKEYS: list[tuple[str, str, str]] = [
    ("copy", "Sao chép mục đang chọn (Copy)", "Ctrl+C"),
    ("cut", "Cắt mục đang chọn (Cut)", "Ctrl+X"),
    ("paste", "Dán vào khung hiện tại (Paste)", "Ctrl+V"),
    ("refresh", "Làm mới thư mục hiện tại", "F5"),
    ("rename", "Đổi tên mục đang chọn", "F2"),
    ("mkdir", "Tạo thư mục mới", "Ctrl+Shift+N"),
    ("delete", "Xóa mục đang chọn", "Delete"),
    ("properties", "Xem Tính Chất (Properties / Dung lượng)", "Alt+Return"),
    ("new_tab", "Mở thêm Tab mới", "Ctrl+T"),
    ("search", "Chuyển sang màn hình Tìm Kiếm", "Ctrl+F"),
    ("sync", "Mở cửa sổ Đồng bộ", "Ctrl+Y"),
]


def get_hotkey(config: AppConfig, action_id: str) -> str:
    custom = (config.hotkeys or {}).get(action_id)
    if custom is not None:
        return custom
    for aid, _lbl, default in DEFAULT_HOTKEYS:
        if aid == action_id:
            return default
    return ""


class KeymapDialog(QDialog):
    """Bảng tùy chỉnh phím tắt (Custom Hotkeys) lưu vào data/config.json."""

    def __init__(self, parent, config: AppConfig) -> None:
        super().__init__(parent)
        self.config = config
        self._edits: dict[str, QKeySequenceEdit] = {}
        self.setWindowTitle("Tùy chỉnh Phím tắt (Custom Hotkeys) — Rdrive")
        self.setWindowIcon(icons.ribbon_icon("settings", 24))
        self.resize(620, 480)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        lbl = QLabel("Bấm vào ô Phím tắt rồi nhấn tổ hợp phím mới theo ý bạn:")
        lbl.setObjectName("Muted")
        lay.addWidget(lbl)

        self.table = QTableWidget(len(DEFAULT_HOTKEYS), 2)
        self.table.setHorizontalHeaderLabels(["Chức năng", "Phím tắt"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 200)

        for row, (aid, label, _def_key) in enumerate(DEFAULT_HOTKEYS):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            ed = QKeySequenceEdit(QKeySequence(get_hotkey(config, aid)))
            self._edits[aid] = ed
            self.table.setCellWidget(row, 1, ed)

        lay.addWidget(self.table, 1)

        row_btn = QHBoxLayout()
        btn_reset = QPushButton("Khôi phục mặc định")
        btn_reset.clicked.connect(self._reset_defaults)
        row_btn.addWidget(btn_reset)
        row_btn.addStretch(1)
        lay.addLayout(row_btn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu phím tắt")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _reset_defaults(self) -> None:
        for aid, _lbl, def_key in DEFAULT_HOTKEYS:
            if aid in self._edits:
                self._edits[aid].setKeySequence(QKeySequence(def_key))

    def _save(self) -> None:
        hk: dict[str, str] = {}
        for aid, ed in self._edits.items():
            hk[aid] = ed.keySequence().toString(QKeySequence.PortableText)
        self.config.hotkeys = hk
        self.config.save()
        self.accept()
