"""Quản lý Mount Cloud to Drive (gắn Google Drive / NAS thành ổ đĩa Windows) + Chạy ngầm + Khởi động cùng Windows + Quyền Admin."""
from __future__ import annotations

import ctypes
import os
import string
import subprocess
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.config import LOGS_DIR, AppConfig, ROOT_DIR
from ..core.logger import get_logger
from ..core.rclone_client import NO_WINDOW, RcloneClient, RcloneError
from . import icons

log = get_logger()
RUN_KEY = r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
_ACTIVE_MOUNTS: dict[str, subprocess.Popen] = {}  # letter -> Popen


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def has_winfsp() -> bool:
    return (
        os.path.isdir(r"C:\Program Files (x86)\WinFsp")
        or os.path.isdir(r"C:\Program Files\WinFsp")
    )


def set_windows_autostart(enable: bool) -> None:
    if sys.platform != "win32":
        return
    reg = QSettings(RUN_KEY, QSettings.NativeFormat)
    if enable:
        py = sys.executable
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(pyw):
            py = pyw
        cmd = f'"{py}" "{ROOT_DIR / "Rdrive.pyw"}" --tray'
        reg.setValue("Rdrive", cmd)
    else:
        reg.remove("Rdrive")


def stop_all_mounts() -> None:
    for letter, proc in list(_ACTIVE_MOUNTS.items()):
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        _ACTIVE_MOUNTS.pop(letter, None)


class MountDialog(QDialog):
    """Gắn bất kỳ Cloud / Thư mục nào thành ổ đĩa cục bộ (X:, Y:, Z:...) trên Windows và quản lý chạy ngầm."""

    def __init__(self, parent, rclone: RcloneClient, config: AppConfig, current_right_spec: str = "") -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self.setWindowTitle("Mount Cloud thành Ổ Đĩa Windows & Chạy Ngầm — Rdrive")
        self.setWindowIcon(icons.disk_icon(24))
        self.resize(740, 520)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # Kiểm tra WinFsp
        wf_ok = has_winfsp()
        wf_row = QHBoxLayout()
        self.lbl_wf = QLabel(
            "✓ Đã có WinFsp (sẵn sàng Mount ổ đĩa ảo)"
            if wf_ok
            else "⚠ Máy chưa cài WinFsp (cần WinFsp để Windows tạo ổ đĩa ảo cho rclone mount)"
        )
        self.lbl_wf.setObjectName("BadgeGood" if wf_ok else "BadgeWarn")
        wf_row.addWidget(self.lbl_wf)
        if not wf_ok:
            btn_wf = QPushButton("Tự động cài WinFsp ngay (winget)")
            btn_wf.clicked.connect(self._install_winfsp)
            wf_row.addWidget(btn_wf)
        wf_row.addStretch(1)

        btn_admin = QPushButton("🛡 Đang chạy quyền Admin" if is_admin() else "🛡 Khởi động lại Rdrive với quyền Admin")
        btn_admin.setEnabled(not is_admin())
        btn_admin.clicked.connect(self._relaunch_as_admin)
        wf_row.addWidget(btn_admin)
        lay.addLayout(wf_row)

        # Form tạo Mount mới
        grp = QGroupBox("Gắn (Mount) Cloud hoặc Thư mục bất kỳ thành ổ đĩa")
        fl = QFormLayout(grp)

        self.cb_remote = QComboBox()
        try:
            for r in self.rclone.list_remotes(only_drive=False):
                meta = self.config.get_account_meta(r.name)
                self.cb_remote.addItem(f"{meta.label()} ({r.name}: — {r.kind})", r.name)
        except RcloneError:
            pass
        fl.addRow("Chọn Cloud / NAS:", self.cb_remote)

        self.ed_subpath = QLineEdit()
        if ":" in current_right_spec:
            rem_part, sub_part = current_right_spec.split(":", 1)
            self.ed_subpath.setText(sub_part)
            idx = self.cb_remote.findData(rem_part.split(",", 1)[0])
            if idx >= 0:
                self.cb_remote.setCurrentIndex(idx)
        self.ed_subpath.setPlaceholderText("Để trống = Mount cả ổ gốc, hoặc nhập thư mục con (VD: 2026/quay thang 11)")
        fl.addRow("Thư mục chỉ định:", self.ed_subpath)

        lrow = QHBoxLayout()
        self.cb_letter = QComboBox()
        used = {f"{c}:" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")}
        for c in reversed(string.ascii_uppercase):
            if f"{c}:" not in used or f"{c}:" in _ACTIVE_MOUNTS:
                self.cb_letter.addItem(f"{c}:", f"{c}:")
        self.ed_vol = QLineEdit("RdriveCloud")
        lrow.addWidget(self.cb_letter)
        lrow.addSpacing(12)
        lrow.addWidget(QLabel("Tên nhãn ổ đĩa:"))
        lrow.addWidget(self.ed_vol, 1)
        fl.addRow("Ký tự ổ đĩa:", lrow)

        btn_mount = QPushButton("🔌 Mount thành ổ đĩa ngay")
        btn_mount.setObjectName("PrimaryBtn")
        btn_mount.clicked.connect(self._mount_now)
        fl.addRow("", btn_mount)
        lay.addWidget(grp)

        # Danh sách ổ đĩa đang Mount
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ổ đĩa", "Nguồn Cloud / Thư mục", "Trạng thái", "Tên nhãn"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

        # Hàng điều khiển chạy ngầm & khởi động cùng Windows
        sys_box = QGroupBox("Chế độ chạy ngầm & Khởi động cùng Windows")
        sl = QVBoxLayout(sys_box)
        self.chk_auto = QCheckBox("Bật máy tự bật Rdrive chạy ngầm ở khay hệ thống (Start with Windows)")
        self.chk_auto.setChecked(self.config.autostart_windows)
        self.chk_auto.toggled.connect(self._toggle_autostart)
        self.chk_tray = QCheckBox("Đóng cửa sổ thì thu nhỏ Rdrive xuống khay hệ thống để giữ các ổ đĩa Mount hoạt động")
        self.chk_tray.setChecked(self.config.minimize_to_tray)
        self.chk_tray.toggled.connect(self._toggle_tray)
        sl.addWidget(self.chk_auto)
        sl.addWidget(self.chk_tray)
        lay.addWidget(sys_box)

        btns = QHBoxLayout()
        btn_unmount = QPushButton("Ngắt kết nối (Unmount) ổ đang chọn")
        btn_unmount.setObjectName("DangerBtn")
        btn_unmount.clicked.connect(self._unmount_selected)
        btns.addWidget(btn_unmount)
        btns.addStretch(1)
        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        self._render_mounts()

    def _install_winfsp(self) -> None:
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", "winget install -e --id WinFsp.WinFsp"])
        QMessageBox.information(
            self,
            "Đang cài WinFsp",
            "Cửa sổ cài đặt WinFsp đã mở. Sau khi cài xong WinFsp, hãy bấm 'Mount thành ổ đĩa ngay'.",
        )

    def _relaunch_as_admin(self) -> None:
        if sys.platform != "win32":
            return
        py = sys.executable
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(pyw):
            py = pyw
        script = str(ROOT_DIR / "Rdrive.pyw")
        ctypes.windll.shell32.ShellExecuteW(None, "runas", py, f'"{script}"', str(ROOT_DIR), 1)
        QMessageBox.information(self, "Quyền Admin", "Đã yêu cầu mở Rdrive với quyền Administrator. Bạn có thể đóng cửa sổ cũ.")

    def _toggle_autostart(self, on: bool) -> None:
        self.config.autostart_windows = on
        self.config.save()
        set_windows_autostart(on)

    def _toggle_tray(self, on: bool) -> None:
        self.config.minimize_to_tray = on
        self.config.save()

    def _mount_now(self) -> None:
        if not has_winfsp():
            QMessageBox.warning(
                self,
                "Thiếu WinFsp",
                "Máy chưa cài WinFsp nên rclone chưa thể tạo ổ đĩa ảo trên Windows.\nHãy bấm nút 'Tự động cài WinFsp ngay (winget)' ở góc trên trước!",
            )
            return
        rem = str(self.cb_remote.currentData() or "")
        if not rem:
            return
        sub = self.ed_subpath.text().strip().strip("/")
        letter = str(self.cb_letter.currentData() or "Z:")
        vol = self.ed_vol.text().strip() or f"Rdrive_{rem}"
        spec = f"{rem}:{sub}" if sub else f"{rem}:"

        if letter in _ACTIVE_MOUNTS and _ACTIVE_MOUNTS[letter].poll() is None:
            QMessageBox.warning(self, "Đang sử dụng", f"Ổ đĩa {letter} đang được Mount rồi.")
            return

        exe = self.rclone.ensure_exe()
        cmd = [
            exe,
            "mount",
            spec,
            letter,
            "--volname",
            vol,
            "--vfs-cache-mode",
            "writes",
            "--dir-cache-time",
            "30m",
        ]
        # Nhat ky cua rclone mount ghi ra file rieng. Truoc day de stderr=PIPE ma khong ai doc: sau vai gio
        # ong day, rclone dung lai cho -> o dia ao bi treo (Explorer / Premiere dung theo).
        mount_log = LOGS_DIR / f"mount_{letter.rstrip(':')}.log"
        cmd += ["--log-file", str(mount_log), "--log-level", "NOTICE"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            _ACTIVE_MOUNTS[letter] = proc
            self.config.minimize_to_tray = True
            self.chk_tray.setChecked(True)
            self.config.save()
            log.info("MOUNT STARTED: %s -> %s (%s) | log: %s", spec, letter, vol, mount_log)
            self._render_mounts()
            QTimer.singleShot(4000, lambda p=proc, lt=letter, lf=mount_log: self._check_mount(p, lt, lf))
        except OSError as exc:
            QMessageBox.critical(self, "Lỗi Mount", str(exc))

    def _check_mount(self, proc, letter: str, log_file) -> None:
        """Vai giay sau khi mount: neu rclone da thoat (sai o dia, thieu quyen...) thi bao loi kem nhat ky."""
        if proc.poll() is None:
            return
        tail = ""
        try:
            with open(log_file, encoding="utf-8", errors="replace") as fh:
                tail = "".join(fh.readlines()[-8:])
        except OSError:
            pass
        log.error("MOUNT FAILED: %s exit=%s\n%s", letter, proc.returncode, tail)
        self._render_mounts()
        QMessageBox.warning(self, "Mount không thành công",
                            f"rclone dừng ngay sau khi mount {letter} (mã {proc.returncode}).\n\n{tail[-1200:] or '(không có nhật ký)'}")

    def _unmount_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        letter = self.table.item(row, 0).text()
        proc = _ACTIVE_MOUNTS.pop(letter, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        log.info("UNMOUNT: %s", letter)
        self._render_mounts()

    def _render_mounts(self) -> None:
        self.table.setRowCount(0)
        for letter, proc in list(_ACTIVE_MOUNTS.items()):
            alive = proc.poll() is None
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(letter))
            self.table.setItem(row, 1, QTableWidgetItem(" ".join(proc.args[2:4]) if isinstance(proc.args, list) else letter))
            self.table.setItem(row, 2, QTableWidgetItem("Đang chạy (Mounted)" if alive else "Đã dừng"))
            self.table.setItem(row, 3, QTableWidgetItem(self.ed_vol.text()))
