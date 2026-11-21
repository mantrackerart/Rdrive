"""Hộp thoại 'Tính Chất' (Properties — xem dung lượng thật của folder/file) & 'Chia sẻ liên kết'."""
from __future__ import annotations

import os

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.media_info import probe_local_media_detail
from ..core.rclone_client import RcloneClient, format_size
from . import icons


class _SizeProbeWorker(QThread):
    done = Signal(int, int, str)   # file_count, total_bytes, share_link
    err = Signal(str)

    def __init__(
        self,
        rclone: RcloneClient,
        is_local: bool,
        spec: str,
        is_dir: bool,
        init_size: int,
        item_id: str,
        fetch_link: bool,
    ) -> None:
        super().__init__()
        self.rclone = rclone
        self.is_local = is_local
        self.spec = spec
        self.is_dir = is_dir
        self.init_size = init_size
        self.item_id = item_id
        self.fetch_link = fetch_link

    def run(self) -> None:
        try:
            count = 1
            total = self.init_size
            if self.is_local:
                if self.is_dir and os.path.isdir(self.spec):
                    c, b = 0, 0
                    for dp, _dn, fns in os.walk(self.spec):
                        for fn in fns:
                            c += 1
                            try:
                                b += os.path.getsize(os.path.join(dp, fn))
                            except OSError:
                                pass
                    count, total = c, b
                elif os.path.isfile(self.spec):
                    total = os.path.getsize(self.spec)
                self.done.emit(count, total, "")
                return

            if self.is_dir:
                count, total = self.rclone.folder_size_info(self.spec)
            link = ""
            if self.fetch_link:
                try:
                    link = self.rclone.create_share_link(self.spec, self.item_id, self.is_dir)
                except Exception:
                    link = ""
            self.done.emit(count, total, link)
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc))


class PropertiesDialog(QDialog):
    """Hiển thị đầy đủ Tính chất (Properties): dung lượng thật của thư mục/file, số lượng file, thời lượng video, link chia sẻ."""

    def __init__(
        self,
        parent,
        rclone: RcloneClient,
        *,
        name: str,
        spec: str,
        is_local: bool,
        is_dir: bool,
        size: int = 0,
        mod_display: str = "",
        kind_label: str = "",
        item_id: str = "",
        detail: str = "",
        owner: str = "",
    ) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self._live_workers: list[QThread] = []
        self.setWindowTitle(f"Tính Chất — {name}")
        self.setWindowIcon(icons.file_icon(name, is_dir))
        self.setMinimumWidth(520)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel(name)
        title.setStyleSheet("font-size: 11.5pt; font-weight: 700;")
        title.setWordWrap(True)
        lay.addWidget(title)

        form = QFormLayout()
        form.setSpacing(7)
        form.addRow("Loại:", QLabel("Thư mục" if is_dir else (kind_label or "Tệp tin")))
        ed_loc = QLineEdit(spec)
        ed_loc.setReadOnly(True)
        form.addRow("Đường dẫn:", ed_loc)

        self.lbl_size = QLabel("Đang tính tổng dung lượng…" if is_dir else f"{format_size(size)} ({size:,} bytes)")
        self.lbl_size.setStyleSheet("font-weight: 600; color: #3ecf8e;")
        form.addRow("Dung lượng:", self.lbl_size)

        self.lbl_count = QLabel("Đang đếm…" if is_dir else "1 tệp")
        form.addRow("Số lượng tệp:", self.lbl_count)

        if mod_display:
            form.addRow("Ngày sửa đổi:", QLabel(mod_display))
        if owner:
            form.addRow("Chủ sở hữu:", QLabel(owner))
        if item_id:
            ed_id = QLineEdit(item_id)
            ed_id.setReadOnly(True)
            form.addRow("Google Drive ID:", ed_id)

        media_info = detail or (probe_local_media_detail(spec) if is_local and not is_dir else "")
        if media_info:
            form.addRow("Chi tiết Media:", QLabel(media_info))

        if not is_local:
            lrow = QHBoxLayout()
            self.ed_link = QLineEdit()
            self.ed_link.setReadOnly(True)
            self.ed_link.setPlaceholderText("Đang lấy liên kết chia sẻ…")
            btn_cp = QPushButton("Copy Link")
            btn_cp.clicked.connect(lambda: self.ed_link.text() and QApplication.clipboard().setText(self.ed_link.text()))
            lrow.addWidget(self.ed_link, 1)
            lrow.addWidget(btn_cp)
            form.addRow("Liên kết chia sẻ:", lrow)
        else:
            self.ed_link = None

        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Đóng")
        bb.rejected.connect(self.accept)
        lay.addWidget(bb)

        w = _SizeProbeWorker(rclone, is_local, spec, is_dir, size, item_id, not is_local)
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.done.connect(self._on_done)
        w.err.connect(self._on_err)
        w.start()

    def _on_done(self, count: int, total: int, link: str) -> None:
        self.lbl_size.setText(f"{format_size(total)} ({total:,} bytes)")
        self.lbl_count.setText(f"{count:,} tệp")
        if self.ed_link is not None:
            self.ed_link.setText(link or "(Chưa tạo được link công khai)")

    def _on_err(self, msg: str) -> None:
        self.lbl_size.setText(f"Lỗi tính dung lượng: {msg[:100]}")

    def closeEvent(self, event) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.wait(1000)
                if w.isRunning():
                    w.terminate()
                    w.wait(300)
        super().closeEvent(event)
