"""Hộp thoại Auto / Scheduler (nền tảng quy tắc tự động backup & sắp xếp thư mục — sẵn sàng mở rộng)."""
from __future__ import annotations

import uuid

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.config import AppConfig, AutoRule
from . import icons


class AutoDialog(QDialog):
    """Quản lý danh sách quy tắc Auto Backup / Tự động sắp xếp thư mục (sẽ mở rộng chi tiết theo workflow của bạn)."""

    def __init__(self, parent, config: AppConfig, left_hint: str = "", right_hint: str = "") -> None:
        super().__init__(parent)
        self.config = config
        self.left_hint = left_hint
        self.right_hint = right_hint
        self.rules = config.get_rules()

        self.setWindowTitle("Auto & Lịch trình (Scheduler) — Rdrive")
        self.setWindowIcon(icons.ribbon_icon("auto", 24))
        self.resize(780, 460)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        banner = QLabel(
            "Khung Tự Động (Auto Backup / Tự động sắp xếp thư mục / Kết hợp AI).\n"
            "Bạn có thể tạo sẵn các quy tắc cơ bản tại đây; phần logic tự động chi tiết sẽ được lắp thêm vào đúng module này."
        )
        banner.setWordWrap(True)
        banner.setObjectName("Muted")
        lay.addWidget(banner)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Bật", "Tên quy tắc", "Loại", "Nguồn", "Đích", "Lịch / AI"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("VD: Tự backup thư mục quay tháng 11 lên Drive B")
        self.cb_kind = QComboBox()
        self.cb_kind.addItem("Backup (Copy an toàn)", "backup")
        self.cb_kind.addItem("Tự động sắp xếp thư mục", "sort")
        self.cb_kind.addItem("Đồng bộ (Sync)", "sync")
        self.ed_src = QLineEdit(left_hint)
        self.ed_dst = QLineEdit(right_hint)
        self.cb_sched = QComboBox()
        self.cb_sched.addItem("Chạy thủ công", "manual")
        self.cb_sched.addItem("Theo dõi thư mục (Watch)", "watch")
        self.cb_sched.addItem("Mỗi giờ", "hourly")
        self.cb_sched.addItem("Hàng ngày", "daily")
        self.chk_ai = QCheckBox("Cho phép AI hỗ trợ phân loại thư mục")

        form.addRow("Tên quy tắc:", self.ed_name)
        form.addRow("Loại:", self.cb_kind)
        form.addRow("Nguồn:", self.ed_src)
        form.addRow("Đích:", self.ed_dst)
        form.addRow("Lịch chạy:", self.cb_sched)
        form.addRow("", self.chk_ai)
        lay.addLayout(form)

        row_btn = QHBoxLayout()
        btn_add = QPushButton("+ Thêm quy tắc")
        btn_add.setObjectName("PrimaryBtn")
        btn_add.clicked.connect(self._add_rule)
        btn_del = QPushButton("Xóa quy tắc chọn")
        btn_del.setObjectName("DangerBtn")
        btn_del.clicked.connect(self._del_rule)
        row_btn.addWidget(btn_add)
        row_btn.addWidget(btn_del)
        row_btn.addStretch(1)
        lay.addLayout(row_btn)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Đóng")
        bb.rejected.connect(self.accept)
        lay.addWidget(bb)

        self._render()

    def _render(self) -> None:
        self.table.setRowCount(0)
        for r in self.rules:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem("✓" if r.enabled else "—"))
            self.table.setItem(row, 1, QTableWidgetItem(r.name))
            self.table.setItem(row, 2, QTableWidgetItem(r.kind))
            self.table.setItem(row, 3, QTableWidgetItem(r.source))
            self.table.setItem(row, 4, QTableWidgetItem(r.destination))
            self.table.setItem(row, 5, QTableWidgetItem(f"{r.schedule}{' + AI' if r.use_ai else ''}"))

    def _add_rule(self) -> None:
        name = self.ed_name.text().strip() or "Quy tắc tự động mới"
        rule = AutoRule(
            id=uuid.uuid4().hex[:8],
            name=name,
            enabled=True,
            kind=str(self.cb_kind.currentData() or "backup"),
            source=self.ed_src.text().strip(),
            destination=self.ed_dst.text().strip(),
            schedule=str(self.cb_sched.currentData() or "manual"),
            use_ai=self.chk_ai.isChecked(),
        )
        self.rules.append(rule)
        self.config.set_rules(self.rules)
        self.config.save()
        self.ed_name.clear()
        self._render()

    def _del_rule(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rules):
            self.rules.pop(row)
            self.config.set_rules(self.rules)
            self.config.save()
            self._render()
