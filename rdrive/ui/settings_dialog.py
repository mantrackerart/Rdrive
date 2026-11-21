"""Hộp thoại 'Tùy Chọn' chuẩn (Sidebar trái: General | Logs | Proxy | rclone & SyAu)."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppConfig, LOGS_DIR, ROOT_DIR
from ..core.rclone_client import find_rclone
from ..core.telegram_notifier import (
    fetch_latest_chat_id,
    load_bot_token,
    save_bot_token,
    send_telegram_sync,
)
from . import icons
from .keymap_dialog import KeymapDialog


class _TelegramWorker(QThread):
    """Gọi Telegram API ở luồng nền (lấy Chat ID hoặc gửi tin nhắn thử) để không bao giờ đứng giao diện."""

    done = Signal(bool, str, str)  # ok, chat_id_if_any, message

    def __init__(self, mode: str, token: str, chat_id: str = "") -> None:
        super().__init__()
        self.mode = mode
        self.token = token
        self.chat_id = chat_id

    def run(self) -> None:
        if self.mode == "chat_id":
            ok, cid, info = fetch_latest_chat_id(self.token)
            self.done.emit(ok, cid, info)
        else:
            ok, msg = send_telegram_sync(
                self.token,
                self.chat_id,
                "🔔 <b>Rdrive — Kết nối Telegram thành công!</b>\n"
                "Từ giờ Rdrive sẽ báo ngay cho bạn khi Upload/Download xong, có lỗi hoặc phát hiện nghẽn mạng.",
            )
            self.done.emit(ok, "", msg)


class SettingsDialog(QDialog):
    def __init__(self, parent, config: AppConfig) -> None:
        super().__init__(parent)
        self.config = config
        self._live_workers: list[QThread] = []
        self.setWindowTitle("Tùy Chọn — Rdrive")
        self.setWindowIcon(icons.ribbon_icon("settings", 24))
        self.resize(740, 520)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(12)

        # Sidebar trái kiểu 2 khung: General / Telegram / Logs / Proxy / rclone & SyAu
        self.nav = QListWidget()
        self.nav.setFixedWidth(155)
        self.nav.setStyleSheet(
            "QListWidget { background: #161920; border: 1px solid #2d323e; border-radius: 6px; padding: 4px; }"
            "QListWidget::item { padding: 8px 10px; border-radius: 5px; }"
            "QListWidget::item:selected { background: #26344d; color: #ffffff; }"
        )
        for label, icon_k in [
            ("General", "settings"),
            ("Telegram", "auto"),
            ("Logs", "explorer"),
            ("Proxy", "sync"),
            ("rclone & SyAu", "syau"),
        ]:
            it = QListWidgetItem(icons.ribbon_icon(icon_k, 16), label)
            self.nav.addItem(it)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_general())
        self.stack.addWidget(self._build_telegram())
        self.stack.addWidget(self._build_logs())
        self.stack.addWidget(self._build_proxy())
        self.stack.addWidget(self._build_rclone_syau())

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        # Nút OK / Bãi Bỏ
        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_ok = QPushButton("OK")
        btn_ok.setObjectName("PrimaryBtn")
        btn_ok.setMinimumWidth(90)
        btn_ok.clicked.connect(self._save)
        btn_cancel = QPushButton("Bãi Bỏ")
        btn_cancel.setMinimumWidth(90)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        root.addLayout(btns)

    def _build_general(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        top = QFormLayout()
        top.setSpacing(8)

        self.cb_lang = QComboBox()
        self.cb_lang.addItem("Tiếng Việt (Việt Nam)", "vi")
        top.addRow("Ngôn Ngữ:", self.cb_lang)

        par_row = QHBoxLayout()
        self.sp_ul_threads = QSpinBox()
        self.sp_ul_threads.setRange(1, 20)
        self.sp_ul_threads.setValue(getattr(self.config, "upload_threads", 3) or 3)
        self.sp_dl_threads = QSpinBox()
        self.sp_dl_threads.setRange(1, 20)
        self.sp_dl_threads.setValue(getattr(self.config, "download_threads", 3) or 3)
        par_row.addWidget(QLabel("Số luồng Upload cùng lúc:"))
        par_row.addWidget(self.sp_ul_threads)
        par_row.addSpacing(16)
        par_row.addWidget(QLabel("Số luồng Download cùng lúc:"))
        par_row.addWidget(self.sp_dl_threads)
        par_row.addStretch(1)
        top.addRow("Chạy đa luồng:", par_row)

        ret_row = QHBoxLayout()
        self.sp_retries = QSpinBox()
        self.sp_retries.setRange(1, 20)
        self.sp_retries.setValue(self.config.retries)
        self.cb_chunk = QComboBox()
        for mb, lbl in [
            (32, "32 MB (Tiết kiệm RAM)"),
            (64, "64 MB (Tiêu chuẩn)"),
            (128, "128 MB (Nhanh)"),
            (256, "256 MB (Tối ưu tốc độ cao)"),
            (512, "512 MB (Tối đa — cần RAM ≥ 16GB)"),
        ]:
            self.cb_chunk.addItem(lbl, mb)
        cidx = self.cb_chunk.findData(self.config.drive_chunk_mb)
        self.cb_chunk.setCurrentIndex(cidx if cidx >= 0 else 3)
        self.cb_chunk.setToolTip(
            "Kích thước mỗi gói dữ liệu gửi lên Google Drive (càng lớn càng nhanh nhưng tốn thêm RAM tương ứng cho mỗi luồng upload)."
        )
        self.sp_idm = QSpinBox()
        self.sp_idm.setRange(1, 64)
        self.sp_idm.setValue(getattr(self.config, "idm_streams", 16) or 16)
        ret_row.addWidget(self.sp_retries)
        ret_row.addSpacing(12)
        ret_row.addWidget(QLabel("Gói Upload Drive:"))
        ret_row.addWidget(self.cb_chunk)
        ret_row.addSpacing(12)
        ret_row.addWidget(QLabel("Luồng IDM/1 file:"))
        ret_row.addWidget(self.sp_idm)
        ret_row.addStretch(1)
        top.addRow("Thử lại khi thất bại:", ret_row)
        lay.addLayout(top)

        # Speed Limits kiểu 2 khung
        grp = QGroupBox("Speed Limits (Giới hạn tốc độ)")
        gl = QFormLayout(grp)
        dl_row = QHBoxLayout()
        self.chk_dl = QCheckBox("Giới hạn tải xuống:")
        self.chk_dl.setChecked(self.config.dl_limit_enabled)
        self.sp_dl = QSpinBox()
        self.sp_dl.setRange(50, 500000)
        self.sp_dl.setSingleStep(500)
        self.sp_dl.setSuffix(" KB/s")
        self.sp_dl.setValue(self.config.dl_limit_kbps or 5000)
        dl_row.addWidget(self.chk_dl)
        dl_row.addWidget(self.sp_dl)
        dl_row.addStretch(1)

        ul_row = QHBoxLayout()
        self.chk_ul = QCheckBox("Giới hạn tải lên:")
        self.chk_ul.setChecked(self.config.ul_limit_enabled)
        self.sp_ul = QSpinBox()
        self.sp_ul.setRange(50, 500000)
        self.sp_ul.setSingleStep(500)
        self.sp_ul.setSuffix(" KB/s")
        self.sp_ul.setValue(self.config.ul_limit_kbps or 5000)
        ul_row.addWidget(self.chk_ul)
        ul_row.addWidget(self.sp_ul)
        ul_row.addStretch(1)

        gl.addRow(dl_row)
        gl.addRow(ul_row)
        lay.addWidget(grp)

        self.chk_multi = QCheckBox("Tải file lớn đa luồng để tăng tốc (--multi-thread-streams, chỉ khi tải về)")
        self.chk_multi.setChecked(self.config.multi_thread_download)
        self.chk_rcd = QCheckBox("Duyệt Google Drive nhanh (giữ 1 rclone chạy nền — mở folder nhanh hơn)")
        self.chk_rcd.setToolTip("Tắt nếu duyệt folder bị lỗi: Rdrive quay về cách cũ (mỗi lần mở folder bật 1 rclone).\n"
                                "Nhật ký: logs\\rcd.log")
        self.chk_rcd.setChecked(bool(getattr(self.config, "use_rcd", True)))
        self.chk_ignore = QCheckBox("Mặc định bỏ qua file đã tồn tại ở đích (--ignore-existing)")
        self.chk_ignore.setChecked(self.config.ignore_existing)
        self.chk_hidden = QCheckBox("Hiển thị tệp tin && thư mục ẩn")
        self.chk_hidden.setChecked(self.config.show_hidden)
        self.chk_clear_done = QCheckBox("Tự dọn tác vụ đã hoàn thành khỏi hàng đợi")
        self.chk_clear_done.setChecked(self.config.auto_clear_done)
        self.chk_tray = QCheckBox("Thu xuống khay hệ thống khi thu nhỏ (Minimize to Tray)")
        self.chk_tray.setChecked(self.config.minimize_to_tray)

        for c in (self.chk_multi, self.chk_rcd, self.chk_ignore, self.chk_hidden, self.chk_clear_done, self.chk_tray):
            lay.addWidget(c)

        hk_row = QHBoxLayout()
        btn_hk = QPushButton("⌨  Tùy chỉnh Phím tắt (Custom Hotkeys)…")
        btn_hk.clicked.connect(lambda: KeymapDialog(self, self.config).exec())
        btn_clean = QPushButton("📦  Đóng gói bộ cài sạch cho máy mới (0 tài khoản / 0 log)")
        btn_clean.clicked.connect(self._build_clean_pkg)
        hk_row.addWidget(btn_hk)
        hk_row.addWidget(btn_clean)
        hk_row.addStretch(1)
        lay.addLayout(hk_row)
        lay.addStretch(1)
        return w

    def _build_clean_pkg(self) -> None:
        import subprocess
        from PySide6.QtWidgets import QMessageBox

        ps1 = ROOT_DIR / "tools" / "build_clean_installer.ps1"
        if ps1.is_file():
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)])
            QMessageBox.information(
                self,
                "Đóng gói bộ cài sạch",
                "Đang đóng gói Rdrive-Setup-Clean.zip (bên ngoài thư mục Rdrive).\n"
                "Bản này hoàn toàn KHÔNG chứa tài khoản, token, lịch sử hay log cá nhân!",
            )

    def _build_telegram(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        self.chk_tg_enable = QCheckBox("Bật thông báo Telegram (báo ngay khi upload/download xong, lỗi hoặc nghẽn)")
        self.chk_tg_enable.setStyleSheet("font-weight: 600;")
        self.chk_tg_enable.setChecked(bool(getattr(self.config, "telegram_enabled", False)))
        lay.addWidget(self.chk_tg_enable)

        guide = QLabel(
            "Thiết lập siêu nhanh (30 giây):\n"
            "1. Mở Telegram tìm @BotFather → nhắn /newbot để lấy Bot Token → dán vào ô dưới.\n"
            "2. Nhấn vào link bot bạn vừa tạo rồi bấm Start (hoặc nhắn 'hi').\n"
            "3. Quay lại đây bấm '🔍 Tự lấy Chat ID' rồi bấm '📨 Gửi thử' là xong!"
        )
        guide.setWordWrap(True)
        guide.setStyleSheet(
            "background: #1c2433; border: 1px solid #30415d; border-radius: 6px; "
            "padding: 8px 10px; color: #b9c8e3; font-size: 8.6pt;"
        )
        lay.addWidget(guide)

        form = QFormLayout()
        form.setSpacing(8)

        self.ed_tg_token = QLineEdit(load_bot_token())
        self.ed_tg_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.ed_tg_token.setPlaceholderText("VD: 7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxx")
        self.ed_tg_token.setToolTip("Bot Token được lưu bảo mật trong Windows Credential Manager (không ghi ra file config.json).")
        form.addRow("Bot Token:", self.ed_tg_token)

        cid_row = QHBoxLayout()
        self.ed_tg_chat_id = QLineEdit(str(getattr(self.config, "telegram_chat_id", "") or ""))
        self.ed_tg_chat_id.setPlaceholderText("VD: 123456789 (hoặc bấm nút Tự lấy bên cạnh)")
        self.btn_tg_fetch = QPushButton("🔍 Tự lấy Chat ID")
        self.btn_tg_fetch.clicked.connect(self._fetch_tg_chat_id)
        cid_row.addWidget(self.ed_tg_chat_id, 1)
        cid_row.addWidget(self.btn_tg_fetch)
        form.addRow("Chat ID:", cid_row)
        lay.addLayout(form)

        grp = QGroupBox("Chọn các sự kiện muốn nhận tin nhắn")
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)
        self.chk_tg_done = QCheckBox("Báo tổng kết khi hoàn tất toàn bộ hàng đợi (số file, tổng GB, tốc độ TB & Đỉnh)")
        self.chk_tg_done.setChecked(bool(getattr(self.config, "telegram_notify_done", True)))
        self.chk_tg_err = QCheckBox("Báo ngay lập tức khi có tác vụ bị lỗi")
        self.chk_tg_err.setChecked(bool(getattr(self.config, "telegram_notify_error", True)))
        self.chk_tg_stall = QCheckBox("Báo cảnh báo khi phát hiện nghẽn mạng / tụt tốc đột ngột")
        self.chk_tg_stall.setChecked(bool(getattr(self.config, "telegram_notify_stall", True)))
        self.chk_tg_each = QCheckBox("Báo chi tiết từng file hoàn tất (có thể nhiều tin nếu up hàng loạt)")
        self.chk_tg_each.setChecked(bool(getattr(self.config, "telegram_notify_each", False)))
        for cb in (self.chk_tg_done, self.chk_tg_err, self.chk_tg_stall, self.chk_tg_each):
            gl.addWidget(cb)
        lay.addWidget(grp)

        trow = QHBoxLayout()
        self.btn_tg_test = QPushButton("📨 Gửi tin nhắn thử (Test)")
        self.btn_tg_test.clicked.connect(self._test_telegram)
        self.lbl_tg_status = QLabel("Bot Token được lưu an toàn trong Windows Credential Manager.")
        self.lbl_tg_status.setObjectName("Muted")
        self.lbl_tg_status.setWordWrap(True)
        trow.addWidget(self.btn_tg_test)
        trow.addWidget(self.lbl_tg_status, 1)
        lay.addLayout(trow)
        lay.addStretch(1)
        return w

    def _run_tg_worker(self, mode: str) -> None:
        self.btn_tg_fetch.setEnabled(False)
        self.btn_tg_test.setEnabled(False)
        self.lbl_tg_status.setStyleSheet("color: #f5b841;")
        self.lbl_tg_status.setText("Đang kết nối Telegram…")
        w = _TelegramWorker(mode, self.ed_tg_token.text().strip(), self.ed_tg_chat_id.text().strip())
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.done.connect(lambda ok, cid, msg, m=mode: self._on_tg_worker_done(m, ok, cid, msg))
        w.start()

    def _fetch_tg_chat_id(self) -> None:
        self._run_tg_worker("chat_id")

    def _test_telegram(self) -> None:
        self._run_tg_worker("test")

    def _on_tg_worker_done(self, mode: str, ok: bool, cid: str, msg: str) -> None:
        self.btn_tg_fetch.setEnabled(True)
        self.btn_tg_test.setEnabled(True)
        if ok:
            self.lbl_tg_status.setStyleSheet("color: #3ecf8e;")
            if mode == "chat_id" and cid:
                self.ed_tg_chat_id.setText(cid)
                self.lbl_tg_status.setText(f"Đã tìm thấy Chat ID: {cid} ({msg})")
            else:
                self.lbl_tg_status.setText(msg)
        else:
            self.lbl_tg_status.setStyleSheet("color: #ff7a85;")
            self.lbl_tg_status.setText(msg)

    def _build_logs(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        self.chk_detail_log = QCheckBox("Ghi nhật ký chi tiết (Detailed log)")
        self.chk_detail_log.setChecked(self.config.detailed_log)
        lay.addWidget(self.chk_detail_log)

        row = QHBoxLayout()
        self.ed_log_dir = QLineEdit(self.config.log_dir or str(LOGS_DIR))
        btn_br = QPushButton("Browse…")
        btn_br.clicked.connect(self._pick_log_dir)
        btn_open = QPushButton("Mở thư mục")
        btn_open.clicked.connect(lambda: os.path.isdir(self.ed_log_dir.text()) and sys.platform == "win32" and os.startfile(self.ed_log_dir.text()))
        row.addWidget(QLabel("Lưu log tại:"))
        row.addWidget(self.ed_log_dir, 1)
        row.addWidget(btn_br)
        row.addWidget(btn_open)
        lay.addLayout(row)

        rot = QHBoxLayout()
        rot.addWidget(QLabel("Tự tạo file log mới sau:"))
        self.sp_rot = QSpinBox()
        self.sp_rot.setRange(1, 500)
        self.sp_rot.setSuffix(" MB")
        self.sp_rot.setValue(self.config.log_rotate_mb or 10)
        rot.addWidget(self.sp_rot)
        rot.addStretch(1)
        lay.addLayout(rot)
        lay.addStretch(1)
        return w

    def _build_proxy(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Connection / Proxy")
        gl = QVBoxLayout(grp)

        self.rb_proxy_sys = QRadioButton("Dùng cấu hình mạng mặc định của Windows (hoặc không dùng Proxy)")
        self.rb_proxy_custom = QRadioButton("Dùng Proxy tùy chỉnh (HTTP / HTTPS)")
        self.rb_proxy_sys.setChecked(not self.config.proxy_enabled)
        self.rb_proxy_custom.setChecked(self.config.proxy_enabled)
        gl.addWidget(self.rb_proxy_sys)
        gl.addWidget(self.rb_proxy_custom)

        fl = QFormLayout()
        self.ed_proxy_url = QLineEdit(self.config.proxy_url)
        self.ed_proxy_url.setPlaceholderText("VD: http://127.0.0.1:7890")
        self.ed_proxy_user = QLineEdit(self.config.proxy_user)
        self.ed_proxy_pass = QLineEdit(self.config.proxy_pass)
        self.ed_proxy_pass.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        fl.addRow("Địa chỉ Proxy:", self.ed_proxy_url)
        fl.addRow("Người Dùng:", self.ed_proxy_user)
        fl.addRow("Mật Khẩu:", self.ed_proxy_pass)
        gl.addLayout(fl)

        lay.addWidget(grp)
        lay.addStretch(1)
        return w

    def _build_rclone_syau(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        form.setSpacing(8)

        rc_row = QHBoxLayout()
        self.ed_rclone = QLineEdit(self.config.rclone_path or find_rclone())
        btn_rc = QPushButton("Chọn…")
        btn_rc.clicked.connect(self._pick_rclone)
        rc_row.addWidget(self.ed_rclone, 1)
        rc_row.addWidget(btn_rc)
        form.addRow("Đường dẫn rclone.exe:", rc_row)

        self.ed_update_repo = QLineEdit(self.config.update_repo)
        self.ed_update_repo.setPlaceholderText("chủ/kho trên GitHub, VD: ten-ban/rdrive-releases (trống = tắt)")
        form.addRow("Kho cập nhật GitHub:", self.ed_update_repo)
        self.chk_update_auto = QCheckBox("Tự kiểm tra bản mới khi mở Rdrive")
        self.chk_update_auto.setChecked(self.config.update_auto_check)
        form.addRow("", self.chk_update_auto)

        self.ed_syau_dir = QLineEdit(self.config.syau_dir)
        form.addRow("Thư mục app SyAu:", self.ed_syau_dir)

        self.ed_syau_url = QLineEdit(self.config.syau_server_url)
        form.addRow("Cổng nội bộ SyAu:", self.ed_syau_url)

        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _pick_rclone(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn rclone.exe", self.ed_rclone.text(), "rclone.exe (rclone.exe);;All (*.*)")
        if path:
            self.ed_rclone.setText(path)

    def _pick_log_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu log", self.ed_log_dir.text())
        if d:
            self.ed_log_dir.setText(d)

    def _save(self) -> None:
        self.config.upload_threads = self.sp_ul_threads.value()
        self.config.download_threads = self.sp_dl_threads.value()
        self.config.transfers = self.sp_ul_threads.value()
        self.config.retries = self.sp_retries.value()
        self.config.drive_chunk_mb = int(self.cb_chunk.currentData() or 256)
        self.config.idm_streams = self.sp_idm.value()
        self.config.dl_limit_enabled = self.chk_dl.isChecked()
        self.config.dl_limit_kbps = self.sp_dl.value()
        self.config.ul_limit_enabled = self.chk_ul.isChecked()
        self.config.ul_limit_kbps = self.sp_ul.value()
        self.config.multi_thread_download = self.chk_multi.isChecked()
        self.config.use_rcd = self.chk_rcd.isChecked()
        self.config.ignore_existing = self.chk_ignore.isChecked()
        self.config.show_hidden = self.chk_hidden.isChecked()
        self.config.auto_clear_done = self.chk_clear_done.isChecked()
        self.config.minimize_to_tray = self.chk_tray.isChecked()
        self.config.detailed_log = self.chk_detail_log.isChecked()
        self.config.log_dir = self.ed_log_dir.text().strip()
        self.config.log_rotate_mb = self.sp_rot.value()
        self.config.proxy_enabled = self.rb_proxy_custom.isChecked()
        self.config.proxy_url = self.ed_proxy_url.text().strip()
        self.config.proxy_user = self.ed_proxy_user.text().strip()
        self.config.proxy_pass = self.ed_proxy_pass.text().strip()
        self.config.rclone_path = self.ed_rclone.text().strip()
        self.config.syau_dir = self.ed_syau_dir.text().strip()
        self.config.update_repo = self.ed_update_repo.text().strip()
        self.config.update_auto_check = self.chk_update_auto.isChecked()
        self.config.syau_server_url = self.ed_syau_url.text().strip() or "http://127.0.0.1:47811"
        self.config.telegram_enabled = self.chk_tg_enable.isChecked()
        self.config.telegram_chat_id = self.ed_tg_chat_id.text().strip()
        self.config.telegram_notify_done = self.chk_tg_done.isChecked()
        self.config.telegram_notify_error = self.chk_tg_err.isChecked()
        self.config.telegram_notify_stall = self.chk_tg_stall.isChecked()
        self.config.telegram_notify_each = self.chk_tg_each.isChecked()
        save_bot_token(self.ed_tg_token.text().strip())
        self.config.save()
        self.accept()

    def closeEvent(self, event) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.wait(1500)
        super().closeEvent(event)
