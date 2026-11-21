"""Khung dưới cùng hiển thị Hàng đợi truyền tải, Lịch sử lưu bền vững (data/history.json) và Nhật ký (logs/rdrive.log)."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.history import HistoryEntry, HistoryStore
from ..core.logger import LOG_FILE, log_dir_path
from ..core.rclone_client import format_size
from ..core.transfer_manager import TransferJob, TransferManager


def _fmt_eta(eta: float | None) -> str:
    if eta is None or eta <= 0:
        return "—"
    sec = int(eta)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _open_path(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    if sys.platform == "win32":
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path)


STATE_VI = {
    "queued": "Chờ chạy",
    "running": "Đang Chạy",
    "paused": "Tạm dừng",
    "done": "Hoàn tất",
    "error": "Lỗi",
    "cancelled": "Đã hủy",
}

DIR_VI = {
    "upload": "⬆ Upload",
    "download": "⬇ Download",
    "cloud": "☁ Drive→Drive",
    "local": "⇄ Local",
    "sync": "⇄ Đồng bộ",
    "login": "🔑 Đăng nhập",
    "syau": "S SyAu",
    "ai": "✦ AI",
    "app": "• Hệ thống",
}

HIST_STATUS_VI = {
    "ok": "Thành công",
    "error": "Lỗi",
    "cancelled": "Đã hủy",
    "info": "Thông tin",
}


class TransferPanel(QWidget):
    """Bảng theo dõi tiến độ truyền tải, Lịch sử bền vững và Nhật ký hệ thống/rclone."""

    schedule_job_requested = Signal(object)   # TransferJob -> đưa vào Auto / Lịch
    fix_account_requested = Signal(str)       # remote_name -> mở Đăng nhập lại khi lỗi token

    def __init__(
        self,
        manager: TransferManager,
        history: HistoryStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.history = history
        self._row_of: dict[str, int] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # ---- Tab 1: Hàng đợi truyền tải
        qtab = QWidget()
        qlay = QVBoxLayout(qtab)
        qlay.setContentsMargins(6, 4, 6, 4)
        qlay.setSpacing(4)

        bar = QHBoxLayout()
        self.lbl_summary = QLabel("Hàng đợi truyền tải trống")
        self.lbl_summary.setObjectName("Muted")
        bar.addWidget(self.lbl_summary)
        bar.addStretch(1)

        btn_pause = QPushButton("⏸ Dừng")
        btn_pause.clicked.connect(self._pause_selected)
        btn_resume = QPushButton("▶ Tiếp Tục")
        btn_resume.clicked.connect(self._resume_selected)
        btn_cancel = QPushButton("✖ Bãi Bỏ")
        btn_cancel.clicked.connect(self._cancel_selected)
        btn_retry = QPushButton("↻ Chạy Lại")
        btn_retry.clicked.connect(self._retry_selected)
        btn_clear = QPushButton("Xóa Hoàn Thành")
        btn_clear.clicked.connect(self._clear_done)
        btn_clear_err = QPushButton("Xóa Thất Bại")
        btn_clear_err.clicked.connect(self._clear_failed)
        for b in (btn_pause, btn_resume, btn_cancel, btn_retry, btn_clear, btn_clear_err):
            b.setStyleSheet("padding: 3px 8px; font-size: 8.5pt;")
            bar.addWidget(b)
        qlay.addLayout(bar)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Loại", "Tác vụ", "Quá Trình", "Kích Thước", "Tốc Độ", "Ước Tính", "Trạng Thái"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_queue_context_menu)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 150)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.Stretch)
        qlay.addWidget(self.table, 1)

        # Thanh tổng kết dưới cùng kiểu 2 khung: Total: X / Y GB [progress] ↓ DL ↑ UL
        tot_bar = QFrame()
        tot_bar.setObjectName("PaneStatus")
        tlay = QHBoxLayout(tot_bar)
        tlay.setContentsMargins(8, 2, 8, 2)
        tlay.setSpacing(10)
        self.lbl_tot_bytes = QLabel("Total: 0 B / 0 B")
        self.lbl_tot_bytes.setObjectName("Muted")
        self.tot_prog = QProgressBar()
        self.tot_prog.setRange(0, 100)
        self.tot_prog.setValue(0)
        self.tot_prog.setFixedWidth(130)
        self.tot_prog.setFixedHeight(10)
        self.lbl_dl_spd = QLabel("⬇ 0 KB/s")
        self.lbl_dl_spd.setStyleSheet("color: #3ecf8e; font-weight: 600; font-size: 8.5pt;")
        self.lbl_ul_spd = QLabel("⬆ 0 KB/s")
        self.lbl_ul_spd.setStyleSheet("color: #4f8cff; font-weight: 600; font-size: 8.5pt;")
        tlay.addStretch(1)
        tlay.addWidget(self.lbl_tot_bytes)
        tlay.addWidget(self.tot_prog)
        tlay.addWidget(self.lbl_dl_spd)
        tlay.addWidget(self.lbl_ul_spd)
        qlay.addWidget(tot_bar)

        # ---- Tab 2: Lịch sử bền vững (data/history.json)
        htab = QWidget()
        hlay = QVBoxLayout(htab)
        hlay.setContentsMargins(6, 4, 6, 4)
        hlay.setSpacing(4)

        hbar = QHBoxLayout()
        self.ed_hist_filter = QLineEdit()
        self.ed_hist_filter.setPlaceholderText("Tìm trong lịch sử...")
        self.ed_hist_filter.setMaximumWidth(260)
        self.ed_hist_filter.setClearButtonEnabled(True)
        self.ed_hist_filter.textChanged.connect(self._render_history)
        hbar.addWidget(self.ed_hist_filter)
        self.lbl_hist_count = QLabel("")
        self.lbl_hist_count.setObjectName("Muted")
        hbar.addWidget(self.lbl_hist_count)
        hbar.addStretch(1)

        btn_open_logs = QPushButton("Mở thư mục logs")
        btn_open_logs.clicked.connect(lambda: _open_path(str(log_dir_path())))
        btn_clear_hist = QPushButton("Xóa lịch sử")
        btn_clear_hist.clicked.connect(self._clear_history)
        for b in (btn_open_logs, btn_clear_hist):
            b.setStyleSheet("padding: 3px 9px; font-size: 8.5pt;")
            hbar.addWidget(b)
        hlay.addLayout(hbar)

        self.hist_table = QTableWidget(0, 6)
        self.hist_table.setHorizontalHeaderLabels(["Thời gian", "Hành động", "Tác vụ", "Trạng thái", "Dung lượng", "Chi tiết / Đường dẫn"])
        self.hist_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.hist_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hist_table.verticalHeader().setVisible(False)
        self.hist_table.verticalHeader().setDefaultSectionSize(24)
        hh = self.hist_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        hlay.addWidget(self.hist_table, 1)

        # ---- Tab 3: Nhật ký (rclone + rdrive.log)
        ltab = QWidget()
        llay = QVBoxLayout(ltab)
        llay.setContentsMargins(6, 4, 6, 4)
        llay.setSpacing(4)

        lbar = QHBoxLayout()
        lbl_log_path = QLabel(f"File log: {LOG_FILE}")
        lbl_log_path.setObjectName("Muted")
        lbar.addWidget(lbl_log_path)
        lbar.addStretch(1)
        btn_show_log = QPushButton("Mở file rdrive.log")
        btn_show_log.setStyleSheet("padding: 3px 9px; font-size: 8.5pt;")
        btn_show_log.clicked.connect(lambda: _open_path(str(LOG_FILE)))
        lbar.addWidget(btn_show_log)
        llay.addLayout(lbar)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(1500)
        self.log_box.setStyleSheet("font-family: Consolas, monospace; font-size: 8.5pt;")
        llay.addWidget(self.log_box, 1)

        self.tabs.addTab(qtab, "Hàng đợi truyền tải (0)")
        self.tabs.addTab(htab, "Lịch sử (0)")
        self.tabs.addTab(ltab, "Nhật ký (Log)")
        lay.addWidget(self.tabs)

        self.manager.job_added.connect(self._on_job_added)
        self.manager.job_updated.connect(self._on_job_updated)
        self.manager.log_message.connect(self._append_log)
        if self.history is not None:
            self.history.entry_added.connect(lambda _e: self._render_history())
            self.history.cleared.connect(self._render_history)
            self._render_history()

    def _render_history(self) -> None:
        if self.history is None:
            return
        q = self.ed_hist_filter.text().strip().lower()
        items = self.history.entries()
        self.tabs.setTabText(1, f"Lịch sử ({len(items)})")
        self.hist_table.setRowCount(0)
        shown = 0
        for e in items:
            detail = e.detail or (f"{e.src} → {e.dst}" if (e.src or e.dst) else "")
            if q and q not in f"{e.title} {e.action} {detail} {e.timestamp}".lower():
                continue
            row = self.hist_table.rowCount()
            self.hist_table.insertRow(row)
            self.hist_table.setItem(row, 0, QTableWidgetItem(e.timestamp))
            self.hist_table.setItem(row, 1, QTableWidgetItem(DIR_VI.get(e.action, e.action)))
            self.hist_table.setItem(row, 2, QTableWidgetItem(e.title))
            st_it = QTableWidgetItem(HIST_STATUS_VI.get(e.status, e.status))
            if e.status == "ok":
                st_it.setForeground(Qt.green)
            elif e.status == "error":
                st_it.setForeground(Qt.red)
            self.hist_table.setItem(row, 3, st_it)
            self.hist_table.setItem(row, 4, QTableWidgetItem(format_size(e.size_bytes) if e.size_bytes > 0 else "—"))
            self.hist_table.setItem(row, 5, QTableWidgetItem(detail))
            shown += 1
        self.lbl_hist_count.setText(f"{shown}/{len(items)} mục")

    def _clear_history(self) -> None:
        if self.history is None:
            return
        if QMessageBox.question(self, "Xóa lịch sử", "Xóa toàn bộ lịch sử hoạt động trong data/history.json?") == QMessageBox.Yes:
            self.history.clear()

    def _on_job_added(self, job: TransferJob) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_of[job.id] = row

        it_dir = QTableWidgetItem(DIR_VI.get(job.direction, job.direction))
        it_dir.setData(Qt.UserRole, job.id)
        self.table.setItem(row, 0, it_dir)
        self.table.setItem(row, 1, QTableWidgetItem(job.title))

        pb = QProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self.table.setCellWidget(row, 2, pb)

        self.table.setItem(row, 3, QTableWidgetItem("—"))
        self.table.setItem(row, 4, QTableWidgetItem("—"))
        self.table.setItem(row, 5, QTableWidgetItem("—"))
        self.table.setItem(row, 6, QTableWidgetItem(STATE_VI.get(job.state, job.state)))
        self._update_summary()

    def _on_job_updated(self, job: TransferJob) -> None:
        row = self._row_of.get(job.id)
        if row is None or row >= self.table.rowCount():
            return
        title = f"{job.title} — {job.current_file}" if job.current_file and job.state == "running" else job.title
        self.table.item(row, 1).setText(title)

        pb = self.table.cellWidget(row, 2)
        if isinstance(pb, QProgressBar):
            pb.setValue(int(job.percent))
            pb.setProperty("state", job.state if job.state in ("done", "error") else "")
            pb.style().unpolish(pb)
            pb.style().polish(pb)

        if job.bytes_total > 0:
            self.table.item(row, 3).setText(f"{format_size(job.bytes_done)} / {format_size(job.bytes_total)}")
        elif job.bytes_done > 0:
            self.table.item(row, 3).setText(format_size(job.bytes_done))

        spd = f"{format_size(job.speed)}/s" if job.speed > 0 and job.state == "running" else "—"
        self.table.item(row, 4).setText(spd)
        self.table.item(row, 5).setText(_fmt_eta(job.eta) if job.state == "running" else "—")

        st_txt = STATE_VI.get(job.state, job.state)
        if job.error:
            st_txt = f"{st_txt}: {job.error[:90]}"
        st_item = self.table.item(row, 6)
        st_item.setText(st_txt)
        if job.state == "done":
            st_item.setForeground(Qt.green)
        elif job.state == "error":
            st_item.setForeground(Qt.red)
        else:
            st_item.setForeground(Qt.white)

        self._update_summary()

    def _update_summary(self) -> None:
        jobs = self.manager.jobs()
        active = sum(1 for j in jobs if j.state in ("queued", "running"))
        done = sum(1 for j in jobs if j.state == "done")
        err = sum(1 for j in jobs if j.state == "error")
        self.tabs.setTabText(0, f"Hàng đợi truyền tải ({active})")
        if not jobs:
            self.lbl_summary.setText("Hàng đợi truyền tải trống")
        else:
            self.lbl_summary.setText(f"Đang chạy/chờ: {active}  ·  Hoàn tất: {done}  ·  Lỗi: {err}  (Chuột phải vào dòng để Dừng/Tiếp tục/Chạy lại/Sửa lỗi)")

        tot_done = sum(int(j.bytes_done or 0) for j in jobs)
        tot_all = sum(int(j.bytes_total or j.bytes_done or 0) for j in jobs)
        dl_spd = sum(float(j.speed or 0.0) for j in jobs if j.state == "running" and j.direction == "download")
        ul_spd = sum(float(j.speed or 0.0) for j in jobs if j.state == "running" and j.direction != "download")
        self.lbl_tot_bytes.setText(f"Total: {format_size(tot_done)} / {format_size(tot_all)}")
        self.tot_prog.setValue(int(tot_done / tot_all * 100) if tot_all > 0 else 0)
        self.lbl_dl_spd.setText(f"⬇ {format_size(dl_spd)}/s" if dl_spd > 0 else "⬇ 0 KB/s")
        self.lbl_ul_spd.setText(f"⬆ {format_size(ul_spd)}/s" if ul_spd > 0 else "⬆ 0 KB/s")

    def _selected_job_id(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        it = self.table.item(row, 0)
        return str(it.data(Qt.UserRole) or "") if it else ""

    def _selected_job(self) -> TransferJob | None:
        jid = self._selected_job_id()
        for j in self.manager.jobs():
            if j.id == jid:
                return j
        return None

    def _pause_selected(self) -> None:
        jid = self._selected_job_id()
        if jid:
            self.manager.pause_job(jid)

    def _resume_selected(self) -> None:
        jid = self._selected_job_id()
        if jid:
            self.manager.resume_job(jid)

    def _cancel_selected(self) -> None:
        jid = self._selected_job_id()
        if jid:
            self.manager.cancel_job(jid)

    def _retry_selected(self) -> None:
        jid = self._selected_job_id()
        if jid:
            self.manager.retry_job(jid)

    def _rebuild_queue_rows(self) -> None:
        self.table.setRowCount(0)
        self._row_of.clear()
        for job in self.manager.jobs():
            self._on_job_added(job)
            self._on_job_updated(job)
        self._update_summary()

    def _clear_done(self) -> None:
        self.manager.clear_finished()
        self._rebuild_queue_rows()

    def _clear_failed(self) -> None:
        self.manager.clear_failed()
        self._rebuild_queue_rows()

    def _move_selected(self, delta: int) -> None:
        jid = self._selected_job_id()
        if jid and self.manager.move_job(jid, delta):
            self._rebuild_queue_rows()

    def _diagnose_and_fix(self, job: TransferJob) -> None:
        err = (job.error or "").lower()
        if "đăng nhập lại" in err or "token" in err or "oauth" in err or "401" in err:
            rem = job.src.split(":", 1)[0].split(",", 1)[0] if ":" in job.src and not os.path.isabs(job.src) else job.dst.split(":", 1)[0].split(",", 1)[0]
            ans = QMessageBox.question(
                self,
                "Sửa lỗi xác thực tài khoản",
                f"Tác vụ bị lỗi phiên đăng nhập OAuth ({rem}).\nBạn có muốn mở cửa sổ Đăng nhập lại rồi tự động Chạy lại tác vụ này không?",
            )
            if ans == QMessageBox.Yes:
                self.fix_account_requested.emit(rem)
                self.manager.retry_job(job.id)
            return
        ans = QMessageBox.question(
            self,
            "Chẩn đoán & Sửa lỗi truyền tải",
            f"Tác vụ: {job.title}\nLỗi: {job.error or 'Không rõ'}\n\n"
            "Bấm Yes để thử chạy lại tác vụ này với chế độ tự phục hồi (Retry).",
        )
        if ans == QMessageBox.Yes:
            self.manager.retry_job(job.id)

    def _show_queue_context_menu(self, pos) -> None:
        job = self._selected_job()
        menu = QMenu(self)
        act_pause = menu.addAction("⏸  Dừng (Tạm dừng)")
        act_resume = menu.addAction("▶  Tiếp Tục")
        act_cancel = menu.addAction("✖  Bãi Bỏ")
        act_retry = menu.addAction("↻  Chạy Lại")
        menu.addSeparator()
        act_clr_done = menu.addAction("🧹  Xóa Hoàn Thành")
        act_clr_fail = menu.addAction("🧹  Xóa Thất Bại")
        menu.addSeparator()
        act_up = menu.addAction("↑  Move Up (Đưa lên trên)")
        act_down = menu.addAction("↓  Move Down (Đưa xuống dưới)")
        menu.addSeparator()
        act_sched = menu.addAction("📅  Schedule This Task (Đưa vào Auto / Lịch)")

        act_pause.setEnabled(bool(job and job.state in ("running", "queued")))
        act_resume.setEnabled(bool(job and job.state == "paused"))
        act_cancel.setEnabled(bool(job and job.state in ("running", "queued", "paused")))
        act_retry.setEnabled(bool(job and job.state in ("error", "cancelled", "paused")))
        act_up.setEnabled(bool(job))
        act_down.setEnabled(bool(job))
        act_sched.setEnabled(bool(job))

        act_pause.triggered.connect(self._pause_selected)
        act_resume.triggered.connect(self._resume_selected)
        act_cancel.triggered.connect(self._cancel_selected)
        act_retry.triggered.connect(self._retry_selected)
        act_clr_done.triggered.connect(self._clear_done)
        act_clr_fail.triggered.connect(self._clear_failed)
        act_up.triggered.connect(lambda: self._move_selected(-1))
        act_down.triggered.connect(lambda: self._move_selected(1))
        if job:
            act_sched.triggered.connect(lambda: self.schedule_job_requested.emit(job))
            if job.state == "error":
                menu.addSeparator()
                act_fix = menu.addAction("🔧  Chẩn đoán & Sửa lỗi…")
                act_fix.triggered.connect(lambda: self._diagnose_and_fix(job))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _append_log(self, msg: str) -> None:
        self.log_box.appendPlainText(msg)
