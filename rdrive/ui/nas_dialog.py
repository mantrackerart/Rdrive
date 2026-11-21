"""Hộp thoại kết nối & chỉnh sửa NAS Xpenology / Synology (SMB / SFTP / WebDAV / FTP) vào Rdrive."""
from __future__ import annotations

import re

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..core.config import AccountMeta, AppConfig
from ..core.rclone_client import RcloneClient, RcloneError, sanitize_remote_name
from . import icons


class _NasSaveWorker(QThread):
    """Lưu cấu hình NAS & kiểm tra kết nối ở luồng nền (không bao giờ treo giao diện)."""

    ok = Signal(str, int)        # remote, folder_count
    warn = Signal(str, str)      # remote, test_err
    fail = Signal(str)           # create_err

    def __init__(
        self,
        rclone: RcloneClient,
        remote: str,
        proto: str,
        host: str,
        port: int,
        user: str,
        password: str,
        share: str,
    ) -> None:
        super().__init__()
        self.rclone = rclone
        self.remote = remote
        self.proto = proto
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.share = share

    def run(self) -> None:
        try:
            self.rclone.create_nas_remote(
                self.remote,
                self.proto,
                self.host,
                self.port,
                self.user,
                self.password,
                self.share,
            )
        except RcloneError as exc:
            self.fail.emit(str(exc))
            return
        try:
            dirs = self.rclone.lsjson(self.remote, "", dirs_only=True, timeout=12.0)
            self.ok.emit(self.remote, len(dirs))
        except Exception as exc:  # noqa: BLE001
            self.warn.emit(self.remote, str(exc))


class NasDialog(QDialog):
    """Thêm hoặc chỉnh sửa kết nối NAS Xpenology / Synology vào Rdrive để duyệt & backup trực tiếp với Google Drive."""

    def __init__(self, parent, rclone: RcloneClient, config: AppConfig, edit_remote: str = "") -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self.edit_remote = edit_remote
        self.created_remote = ""
        self.unc_path = ""

        self.setWindowTitle(
            f"Cấu hình NAS Xpenology — {edit_remote}" if edit_remote else "Kết nối NAS Xpenology / Synology — Rdrive"
        )
        self.setWindowIcon(icons.nas_icon(24))
        self.setMinimumWidth(560)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel(
            f"Chỉnh sửa kết nối NAS: {edit_remote}" if edit_remote else "Thêm NAS Xpenology / Synology vào Rdrive"
        )
        title.setStyleSheet("font-size: 12pt; font-weight: 700;")
        lay.addWidget(title)

        desc = QLabel(
            "Lưu ý cổng (Port) chuẩn của Xpenology / Synology DSM:\n"
            "• SMB / CIFS (khuyên dùng trong mạng LAN): cổng 445  (KHÔNG dùng cổng 5000 của trang web DSM!)\n"
            "• SFTP / SSH: cổng 22   ·   WebDAV: cổng 5005 (HTTP) / 5006 (HTTPS)   ·   FTP: cổng 21"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            "background: #1c2433; border: 1px solid #30415d; border-radius: 6px; "
            "padding: 8px 10px; color: #b9c8e3; font-size: 8.8pt;"
        )
        lay.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(8)

        self.ed_name = QLineEdit(edit_remote or "Xpenology_NAS")
        if edit_remote:
            meta = config.get_account_meta(edit_remote)
            self.ed_name.setText(meta.display_name or edit_remote)
        form.addRow("Tên hiển thị NAS:", self.ed_name)

        self.cb_proto = QComboBox()
        self.cb_proto.addItem("SMB / CIFS (Cổng 445 — Mạng LAN Windows, khuyên dùng)", "smb")
        self.cb_proto.addItem("SFTP / SSH (Cổng 22 — ổn định, bảo mật)", "sftp")
        self.cb_proto.addItem("WebDAV (Synology WebDAV Server — Cổng 5005 / 5006)", "webdav")
        self.cb_proto.addItem("FTP (Cổng 21)", "ftp")
        self.cb_proto.currentIndexChanged.connect(self._on_proto_changed)
        form.addRow("Giao thức kết nối:", self.cb_proto)

        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("VD: 192.168.1.10 (chỉ nhập IP/Host, không cần http://)")
        form.addRow("Địa chỉ IP / Host NAS:", self.ed_host)

        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(445)
        form.addRow("Cổng (Port):", self.sp_port)

        self.ed_user = QLineEdit()
        self.ed_user.setPlaceholderText("Tài khoản DSM / Xpenology (VD: admin)")
        form.addRow("Tài khoản (Username):", self.ed_user)

        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.ed_pass.setPlaceholderText("Mật khẩu đăng nhập NAS")
        form.addRow("Mật khẩu (Password):", self.ed_pass)

        self.ed_share = QLineEdit()
        self.ed_share.setPlaceholderText("Tùy chọn: tên Shared Folder (VD: video, backup — để trống sẽ hiện tất cả)")
        form.addRow("Thư mục chia sẻ:", self.ed_share)

        self.sp_capacity = QSpinBox()
        self.sp_capacity.setRange(0, 10_000_000)
        self.sp_capacity.setSuffix(" GB")
        self.sp_capacity.setSpecialValueText("Tự đọc (Windows)")
        self.sp_capacity.setToolTip("Chỉ cần nhập khi Rdrive không tự đọc được dung lượng NAS. VD: 34,8 TB ≈ 35635 GB")
        form.addRow("Dung lượng tối đa NAS:", self.sp_capacity)

        lay.addLayout(form)

        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        lay.addWidget(self.lbl_test)

        btns = QHBoxLayout()
        btn_unc = QPushButton("Mở nhanh \\\\IP\\Share ở Khung Trái")
        btn_unc.setToolTip("Mở thẳng thư mục mạng LAN Windows (SMB) mà không cần cấu hình rclone")
        btn_unc.clicked.connect(self._open_unc)
        btns.addWidget(btn_unc)
        btns.addStretch(1)
        btn_cancel = QPushButton("Đóng")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Lưu & Kiểm tra kết nối NAS")
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.clicked.connect(self._save_nas)
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_save)
        lay.addLayout(btns)
        self._live_workers: list[QThread] = []

        if edit_remote:
            self._load_existing(edit_remote)

    def _load_existing(self, remote: str) -> None:
        try:
            sec = self.rclone.config_dump().get(remote) or {}
        except Exception:
            sec = {}
        rtype = str(sec.get("type") or "smb").lower()
        try:
            self.sp_capacity.setValue(int(self.config.get_account_meta(remote).capacity_gb or 0))
        except Exception:
            pass
        idx = self.cb_proto.findData(rtype)
        if idx >= 0:
            self.cb_proto.blockSignals(True)
            self.cb_proto.setCurrentIndex(idx)
            self.cb_proto.blockSignals(False)
        if rtype == "webdav":
            url = str(sec.get("url") or "")
            m = re.match(r"^https?://([^:/]+)(?::(\d+))?(?:/(.*))?$", url)
            if m:
                self.ed_host.setText(m.group(1) or "")
                self.sp_port.setValue(int(m.group(2) or 5005))
                self.ed_share.setText(m.group(3) or "")
            else:
                self.ed_host.setText(url)
        else:
            self.ed_host.setText(str(sec.get("host") or ""))
            default_p = {"smb": 445, "sftp": 22, "ftp": 21}.get(rtype, 445)
            try:
                self.sp_port.setValue(int(sec.get("port") or default_p))
            except ValueError:
                self.sp_port.setValue(default_p)
        self.ed_user.setText(str(sec.get("user") or ""))
        self.ed_pass.setPlaceholderText("Nhập lại mật khẩu NAS (bắt buộc khi cập nhật)")

    def _on_proto_changed(self) -> None:
        proto = str(self.cb_proto.currentData() or "smb")
        default_ports = {"smb": 445, "sftp": 22, "webdav": 5005, "ftp": 21}
        self.sp_port.setValue(default_ports.get(proto, 445))

    def _open_unc(self) -> None:
        host = self._clean_host(self.ed_host.text())
        share = self.ed_share.text().strip().strip("\\/")
        if not host:
            QMessageBox.warning(self, "Thiếu IP NAS", "Hãy nhập địa chỉ IP của NAS Xpenology.")
            return
        self.unc_path = f"\\\\{host}\\{share}" if share else f"\\\\{host}"
        self.accept()

    @staticmethod
    def _clean_host(raw: str) -> str:
        s = raw.strip().strip("\\/")
        s = re.sub(r"^(smb|sftp|ftp|https?)://", "", s, flags=re.IGNORECASE)
        if ":" in s and not s.startswith("["):
            s = s.split(":", 1)[0]
        return s.strip()

    def _save_nas(self) -> None:
        host = self._clean_host(self.ed_host.text())
        if not host:
            QMessageBox.warning(self, "Thiếu địa chỉ NAS", "Hãy nhập địa chỉ IP hoặc tên miền của NAS Xpenology.")
            return
        proto = str(self.cb_proto.currentData() or "smb")
        port = self.sp_port.value()

        # Cảnh báo lỗi phổ biến nhất trên Xpenology/Synology: nhập cổng web DSM 5000/5001 cho giao thức SMB
        if proto == "smb" and port in (5000, 5001):
            ans = QMessageBox.question(
                self,
                "Sai cổng SMB trên Xpenology / Synology",
                f"Bạn đang chọn giao thức SMB với cổng {port}.\n"
                "Trên Synology / Xpenology, cổng 5000/5001 là giao diện Web DSM — KHÔNG phải cổng SMB!\n\n"
                "Cổng chuẩn của SMB là 445.\n"
                "Bạn có muốn tự động đổi sang cổng 445 để kết nối ngay không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans == QMessageBox.Yes:
                port = 445
                self.sp_port.setValue(445)

        disp = self.ed_name.text().strip() or f"NAS_{host}"
        remote = self.edit_remote or sanitize_remote_name(disp) or "xpenology_nas"
        self._pending_meta = AccountMeta(
            remote=remote,
            display_name=disp,
            email=f"{proto.upper()}://{host}:{port}",
            category="NAS Xpenology",
            capacity_gb=int(self.sp_capacity.value()),
        )
        self._pending_summary = f"{disp} ({proto.upper()}://{host}:{port})"

        self.btn_save.setEnabled(False)
        self.btn_save.setText("Đang kiểm tra kết nối…")
        self.lbl_test.setStyleSheet("color: #f5b841;")
        self.lbl_test.setText(f"Đang kết nối tới {proto.upper()}://{host}:{port}…")

        w = _NasSaveWorker(
            self.rclone,
            remote,
            proto,
            host,
            port,
            self.ed_user.text().strip(),
            self.ed_pass.text(),
            self.ed_share.text().strip(),
        )
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.ok.connect(self._on_save_ok)
        w.warn.connect(self._on_save_warn)
        w.fail.connect(self._on_save_fail)
        w.start()

    def _save_meta(self) -> None:
        if "NAS Xpenology" not in self.config.categories:
            self.config.categories.append("NAS Xpenology")
        self.config.set_account_meta(self._pending_meta)
        self.config.save()

    def _on_save_ok(self, remote: str, folder_count: int) -> None:
        self._save_meta()
        self.btn_save.setEnabled(True)
        self.btn_save.setText("Lưu & Kiểm tra kết nối NAS")
        self.created_remote = remote
        QMessageBox.information(
            self,
            "Kết nối NAS thành công",
            f"Đã kết nối thành công tới {self._pending_summary}!\n"
            f"Tìm thấy {folder_count} thư mục chia sẻ trên NAS.",
        )
        self.accept()

    def _on_save_warn(self, remote: str, test_err: str) -> None:
        self._save_meta()
        self.btn_save.setEnabled(True)
        self.btn_save.setText("Lưu & Kiểm tra kết nối NAS")
        self.created_remote = remote
        self.lbl_test.setStyleSheet("color: #ff7a85;")
        self.lbl_test.setText(f"Đã lưu cấu hình nhưng kiểm tra kết nối thất bại:\n{test_err}")
        ans = QMessageBox.warning(
            self,
            "Kiểm tra kết nối NAS thất bại",
            f"Đã lưu cấu hình '{self._pending_summary}' nhưng chưa đăng nhập được vào NAS:\n\n{test_err}\n\n"
            "Gợi ý kiểm tra trên Xpenology DSM:\n"
            "• Với SMB: bật Control Panel → File Services → SMB (cổng 445).\n"
            "• Kiểm tra lại đúng Username / Password và quyền đọc/ghi Shared Folder.\n\n"
            "Bạn có muốn ở lại hộp thoại này để sửa lại thông tin không?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ans == QMessageBox.No:
            self.accept()

    def _on_save_fail(self, err: str) -> None:
        self.btn_save.setEnabled(True)
        self.btn_save.setText("Lưu & Kiểm tra kết nối NAS")
        self.lbl_test.setStyleSheet("color: #ff7a85;")
        self.lbl_test.setText(f"Lỗi lưu cấu hình: {err}")
        QMessageBox.critical(self, "Lỗi tạo kết nối NAS", err)

    def closeEvent(self, event) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.wait(500)
        super().closeEvent(event)
