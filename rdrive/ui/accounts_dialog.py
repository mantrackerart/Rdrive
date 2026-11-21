"""Hộp thoại Quản lý & Đăng nhập nhiều tài khoản Google Drive qua rclone OAuth."""
from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.config import AccountMeta, AppConfig
from ..core.rclone_client import RcloneClient, RcloneError, format_size, sanitize_remote_name
from . import icons


class _OAuthWorker(QThread):
    auth_url = Signal(str)
    log_line = Signal(str)
    finished_ok = Signal(str)      # remote_name
    finished_err = Signal(str)

    def __init__(
        self,
        rclone: RcloneClient,
        remote: str,
        *,
        scope: str = "drive",
        client_id: str = "",
        client_secret: str = "",
        update_existing: bool = False,
    ) -> None:
        super().__init__()
        self.rclone = rclone
        self.remote = remote
        self.scope = scope
        self.client_id = client_id
        self.client_secret = client_secret
        self.update_existing = update_existing
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            token_json = self.rclone.authorize_google_drive(
                client_id=self.client_id,
                client_secret=self.client_secret,
                scope=self.scope,
                on_auth_url=self.auth_url.emit,
                on_log=self.log_line.emit,
                cancel_event=self.cancel_event,
            )
            self.log_line.emit("Đã nhận token Google. Đang lưu cấu hình rclone...")
            self.rclone.create_or_update_drive_remote(
                self.remote,
                token_json,
                scope=self.scope,
                client_id=self.client_id,
                client_secret=self.client_secret,
                update_existing=self.update_existing,
            )
            # Tự đọc tên hiển thị & email Google (không bắt buộc nếu lỗi mạng)
            try:
                self.rclone.about(self.remote)
            except Exception:
                pass
            self.finished_ok.emit(self.remote)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(str(exc))


class _QuotaProbeWorker(QThread):
    result_ready = Signal(str, bool, str)   # remote, ok, text

    def __init__(self, rclone: RcloneClient, remotes: list[str]) -> None:
        super().__init__()
        self.rclone = rclone
        self.remotes = remotes

    def run(self) -> None:
        for name in self.remotes:
            try:
                ab = self.rclone.about(name)
                if ab.total > 0:
                    txt = f"Trống {format_size(ab.free)} / {format_size(ab.total)}"
                else:
                    txt = f"Đã dùng {format_size(ab.used)} (Không giới hạn)"
                self.result_ready.emit(name, True, txt)
            except RcloneError as exc:
                self.result_ready.emit(name, False, str(exc))


class GoogleLoginDialog(QDialog):
    """Hộp thoại đăng nhập tài khoản Google Drive mới hoặc đăng nhập lại (OAuth trình duyệt)."""

    def __init__(
        self,
        parent,
        rclone: RcloneClient,
        config: AppConfig,
        existing_remotes: set[str],
        reconnect_remote: str = "",
    ) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self.existing_remotes = existing_remotes
        self.reconnect_remote = reconnect_remote
        self.created_remote = ""
        self._worker: _OAuthWorker | None = None
        self._last_auth_url = ""

        is_re = bool(reconnect_remote)
        self.setWindowTitle("Đăng nhập lại Google Drive" if is_re else "Thêm tài khoản Google Drive")
        self.setWindowIcon(icons.gdrive_icon(24))
        self.setMinimumWidth(520)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel(
            f"Đăng nhập lại tài khoản: {reconnect_remote}" if is_re else "Đăng nhập tài khoản Google Drive mới"
        )
        title.setStyleSheet("font-size: 12pt; font-weight: 700;")
        lay.addWidget(title)

        desc = QLabel(
            "Rdrive sẽ mở trình duyệt web để bạn chọn tài khoản Google và bấm Cho phép (Allow). "
            "Bạn có thể đăng nhập nhiều tài khoản Google cùng lúc."
        )
        desc.setWordWrap(True)
        desc.setObjectName("Muted")
        lay.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(8)

        meta = config.get_account_meta(reconnect_remote) if is_re else None
        self.ed_display = QLineEdit(meta.display_name if meta else "")
        self.ed_display.setPlaceholderText("Để trống sẽ tự lấy tên tài khoản Google sau khi đăng nhập")
        form.addRow("Tên hiển thị:", self.ed_display)

        self.ed_remote = QLineEdit(reconnect_remote)
        self.ed_remote.setPlaceholderText("Tự động tạo (không cần nhập)")
        self.ed_remote.setReadOnly(is_re)
        if not is_re:
            self.ed_remote.setToolTip("Tên kỹ thuật lưu trong rclone.conf. Có thể để trống để Rdrive tự đặt.")
        form.addRow("Mã remote rclone:", self.ed_remote)

        self.ed_email = QLineEdit(meta.email if meta else "")
        self.ed_email.setPlaceholderText("Tự động lấy từ Google sau khi đăng nhập (tùy chọn)")
        form.addRow("Email / Ghi chú:", self.ed_email)

        self.cb_scope = QComboBox()
        self.cb_scope.addItem("Toàn quyền Google Drive (drive) — Khuyên dùng", "drive")
        self.cb_scope.addItem("Chỉ đọc (drive.readonly)", "drive.readonly")
        form.addRow("Quyền truy cập:", self.cb_scope)

        lay.addLayout(form)

        # Phần nâng cao: Client ID / Client Secret riêng
        self.chk_adv = QCheckBox("Dùng Google OAuth Client ID && Secret riêng (tùy chọn — khuyên dùng để tốc độ cao)")
        lay.addWidget(self.chk_adv)

        self.adv_box = QFrame()
        adv_form = QFormLayout(self.adv_box)
        adv_form.setContentsMargins(12, 6, 6, 6)
        self.ed_cid = QLineEdit()
        self.ed_cid.setPlaceholderText("Google Cloud OAuth Client ID")
        self.ed_csec = QLineEdit()
        self.ed_csec.setPlaceholderText("Google Cloud OAuth Client Secret")
        self.ed_csec.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        adv_form.addRow("Client ID:", self.ed_cid)
        adv_form.addRow("Client Secret:", self.ed_csec)
        self.adv_box.setVisible(False)
        lay.addWidget(self.adv_box)
        self.chk_adv.toggled.connect(self.adv_box.setVisible)

        # Trạng thái OAuth
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setObjectName("Muted")
        lay.addWidget(self.lbl_status)

        self.btn_open_url = QPushButton("Mở lại trang đăng nhập trên trình duyệt")
        self.btn_open_url.setVisible(False)
        self.btn_open_url.clicked.connect(lambda: self._last_auth_url and webbrowser.open(self._last_auth_url))
        lay.addWidget(self.btn_open_url)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Đóng")
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_login = QPushButton("Đăng nhập Google (Mở trình duyệt)")
        self.btn_login.setObjectName("PrimaryBtn")
        self.btn_login.setIcon(icons.gdrive_icon(16))
        self.btn_login.clicked.connect(self._start_login)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_login)
        lay.addLayout(btns)

        if not is_re:
            self.ed_display.textChanged.connect(self._sync_remote_name)

    def _sync_remote_name(self, text: str) -> None:
        if not self.reconnect_remote:
            self.ed_remote.setText(sanitize_remote_name(text))

    def _start_login(self) -> None:
        remote = self.reconnect_remote or sanitize_remote_name(self.ed_remote.text() or self.ed_display.text())
        if not remote:
            # Tự sinh mã remote duy nhất nếu người dùng bấm đăng nhập luôn (kiểu 2 khung)
            idx = 1
            while f"gdrive_{idx}" in self.existing_remotes:
                idx += 1
            remote = f"gdrive_{idx}"
            self.ed_remote.setText(remote)
        if not self.reconnect_remote and remote in self.existing_remotes:
            QMessageBox.warning(
                self,
                "Trùng mã remote",
                f"Remote '{remote}' đã tồn tại trong rclone.\nHãy chọn tên khác hoặc dùng nút 'Đăng nhập lại'.",
            )
            return

        self.btn_login.setEnabled(False)
        self.btn_cancel.setText("Hủy đăng nhập")
        self.progress.setVisible(True)
        self.lbl_status.setText("Đang khởi tạo đăng nhập Google OAuth... Trình duyệt sẽ tự mở trong giây lát.")

        cid = self.ed_cid.text().strip() if self.chk_adv.isChecked() else ""
        csec = self.ed_csec.text().strip() if self.chk_adv.isChecked() else ""
        scope = str(self.cb_scope.currentData() or "drive")

        self._worker = _OAuthWorker(
            self.rclone,
            remote,
            scope=scope,
            client_id=cid,
            client_secret=csec,
            update_existing=bool(self.reconnect_remote),
        )
        self._worker.auth_url.connect(self._on_auth_url)
        self._worker.log_line.connect(self._on_log)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.finished_err.connect(self._on_err)
        self._worker.start()

    def _on_auth_url(self, url: str) -> None:
        self._last_auth_url = url
        self.btn_open_url.setVisible(True)
        self.lbl_status.setText("Đã mở trình duyệt. Hãy đăng nhập Google và bấm Cho phép (Allow) để hoàn tất.")

    def _on_log(self, line: str) -> None:
        if "waiting for code" in line.lower() or "got code" in line.lower() or "token" in line.lower():
            self.lbl_status.setText(line[:180])

    def _on_ok(self, remote: str) -> None:
        self.progress.setVisible(False)
        self.created_remote = remote
        meta = self.config.get_account_meta(remote)
        g_name, g_email = self.rclone.fetch_google_profile(remote)
        meta.display_name = self.ed_display.text().strip() or g_name or meta.display_name or remote
        meta.email = self.ed_email.text().strip() or g_email or meta.email
        self.config.set_account_meta(meta)
        self.config.save()
        self.accept()

    def _on_err(self, msg: str) -> None:
        self.progress.setVisible(False)
        self.btn_login.setEnabled(True)
        self.btn_cancel.setText("Đóng")
        self.lbl_status.setText(f"Lỗi: {msg}")

    def _on_cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel_event.set()
            self._worker.wait(2500)
        self.reject()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel_event.set()
            self._worker.wait(2500)
        super().closeEvent(event)


class AccountsDialog(QDialog):
    """Cửa sổ Quản lý nhiều tài khoản Google Drive (tương tự nút 'Tài Khoản' của app 2 khung)."""

    accounts_changed = Signal()
    open_remote_requested = Signal(str, str)   # remote_name, "left" | "right"

    def __init__(self, parent, rclone: RcloneClient, config: AppConfig) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self._probe: _QuotaProbeWorker | None = None
        self._live_probes: list[QThread] = []
        self.setWindowTitle("Quản lý Tài khoản Google Drive — Rdrive")
        self.setWindowIcon(icons.ribbon_icon("accounts", 24))
        self.resize(820, 480)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        top = QHBoxLayout()
        lbl = QLabel("Danh sách tài khoản Google Drive (đọc trực tiếp từ rclone)")
        lbl.setStyleSheet("font-size: 11pt; font-weight: 700;")
        top.addWidget(lbl)
        top.addStretch(1)

        btn_add = QPushButton("+ Đăng nhập Google mới")
        btn_add.setObjectName("PrimaryBtn")
        btn_add.setIcon(icons.gdrive_icon(16))
        btn_add.clicked.connect(self._add_account)
        top.addWidget(btn_add)
        lay.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Tên hiển thị", "Remote rclone", "Email / Ghi chú", "Trạng thái", "Dung lượng"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.btn_open_left = QPushButton("Mở khung Trái")
        self.btn_open_right = QPushButton("Mở khung Phải")
        self.btn_reconnect = QPushButton("Đăng nhập lại")
        self.btn_rename = QPushButton("Sửa tên / Ghi chú")
        self.btn_probe = QPushButton("Kiểm tra kết nối")
        self.btn_delete = QPushButton("Xóa tài khoản")
        self.btn_delete.setObjectName("DangerBtn")

        self.btn_open_left.clicked.connect(lambda: self._open_selected("left"))
        self.btn_open_right.clicked.connect(lambda: self._open_selected("right"))
        self.btn_reconnect.clicked.connect(self._reconnect)
        self.btn_rename.clicked.connect(self._edit_meta)
        self.btn_probe.clicked.connect(self._probe_all)
        self.btn_delete.clicked.connect(self._delete_selected)

        for b in (self.btn_open_left, self.btn_open_right, self.btn_reconnect, self.btn_rename, self.btn_probe, self.btn_delete):
            actions.addWidget(b)
        actions.addStretch(1)
        lay.addLayout(actions)

        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("Muted")
        lay.addWidget(self.lbl_info)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Đóng")
        bb.rejected.connect(self.accept)
        lay.addWidget(bb)

        self._reload()

    def _selected_remote(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 1)
        return item.text().strip() if item else ""

    def _reload(self) -> None:
        self.table.setRowCount(0)
        try:
            remotes = self.rclone.list_remotes(only_drive=True)
            cfg_file = self.rclone.config_file()
            self.lbl_info.setText(f"File cấu hình rclone: {cfg_file}  ·  Tổng {len(remotes)} tài khoản Google Drive")
        except RcloneError as exc:
            self.lbl_info.setText(f"Lỗi rclone: {exc}")
            return

        for rem in remotes:
            meta = self.config.get_account_meta(rem.name)
            row = self.table.rowCount()
            self.table.insertRow(row)
            it0 = QTableWidgetItem(meta.label())
            it0.setIcon(icons.gdrive_icon(16))
            self.table.setItem(row, 0, it0)
            self.table.setItem(row, 1, QTableWidgetItem(rem.name))
            self.table.setItem(row, 2, QTableWidgetItem(meta.email))
            self.table.setItem(row, 3, QTableWidgetItem("Đang kiểm tra…"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))

        if self.table.rowCount() > 0:
            self.table.selectRow(0)
            self._probe_all()

    def _probe_all(self) -> None:
        if self._probe and self._probe.isRunning():
            return
        names = [self.table.item(r, 1).text() for r in range(self.table.rowCount()) if self.table.item(r, 1)]
        if not names:
            return
        for r in range(self.table.rowCount()):
            self.table.item(r, 3).setText("Đang kiểm tra…")
        p = _QuotaProbeWorker(self.rclone, names)
        self._probe = p
        self._live_probes.append(p)
        p.finished.connect(lambda w=p: (self._live_probes.remove(w) if w in self._live_probes else None, w.deleteLater()))
        p.result_ready.connect(self._on_probe_result)
        p.start()

    def _on_probe_result(self, remote: str, ok: bool, text: str) -> None:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 1)
            if it and it.text() == remote:
                st = self.table.item(r, 3)
                q = self.table.item(r, 4)
                if ok:
                    st.setText("Đã kết nối")
                    st.setForeground(Qt.green)
                    q.setText(text)
                else:
                    st.setText("Cần đăng nhập lại")
                    st.setForeground(Qt.red)
                    q.setText(text[:90])
                break

    def _add_account(self) -> None:
        existing = {self.table.item(r, 1).text() for r in range(self.table.rowCount()) if self.table.item(r, 1)}
        dlg = GoogleLoginDialog(self, self.rclone, self.config, existing)
        if dlg.exec() == QDialog.Accepted and dlg.created_remote:
            self._reload()
            self.accounts_changed.emit()

    def _reconnect(self) -> None:
        remote = self._selected_remote()
        if not remote:
            return
        existing = {self.table.item(r, 1).text() for r in range(self.table.rowCount()) if self.table.item(r, 1)}
        dlg = GoogleLoginDialog(self, self.rclone, self.config, existing, reconnect_remote=remote)
        if dlg.exec() == QDialog.Accepted:
            self._reload()
            self.accounts_changed.emit()

    def _edit_meta(self) -> None:
        remote = self._selected_remote()
        if not remote:
            return
        meta = self.config.get_account_meta(remote)
        name, ok = QInputDialog.getText(self, "Đổi tên hiển thị", f"Tên hiển thị cho '{remote}':", text=meta.display_name or remote)
        if not ok:
            return
        email, ok2 = QInputDialog.getText(self, "Email / Ghi chú", f"Email hoặc ghi chú cho '{remote}':", text=meta.email)
        if not ok2:
            return
        meta.display_name = name.strip()
        meta.email = email.strip()
        self.config.set_account_meta(meta)
        self.config.save()
        self._reload()
        self.accounts_changed.emit()

    def _delete_selected(self) -> None:
        remote = self._selected_remote()
        if not remote:
            return
        ans = QMessageBox.question(
            self,
            "Xóa tài khoản",
            f"Xóa tài khoản '{remote}' khỏi rclone?\n(Chỉ xóa kết nối trên máy, KHÔNG xóa dữ liệu trên Google Drive.)",
        )
        if ans != QMessageBox.Yes:
            return
        try:
            self.rclone.delete_remote(remote)
            self.config.remove_account_meta(remote)
            self.config.save()
            self._reload()
            self.accounts_changed.emit()
        except RcloneError as exc:
            QMessageBox.critical(self, "Lỗi xóa tài khoản", str(exc))

    def _open_selected(self, side: str) -> None:
        remote = self._selected_remote()
        if remote:
            self.open_remote_requested.emit(remote, side)
            self.accept()

    def closeEvent(self, event) -> None:
        for w in list(self._live_probes):
            if w.isRunning():
                w.wait(1500)
                if w.isRunning():
                    w.terminate()
                    w.wait(500)
        super().closeEvent(event)
