"""Màn hình 'Tìm Kiếm' file trong Google Drive chỉ định (chuẩn — tìm siêu nhanh qua Google Drive API v3)."""
from __future__ import annotations

import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppConfig
from ..core.rclone_client import RcloneClient, RcloneError, format_size
from . import icons

_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".mxf", ".m4v", ".ts"}
_AUDIO_EXT = {".wav", ".mp3", ".flac", ".aac", ".m4a", ".ogg"}
_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".psd", ".tif"}
_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".xml", ".prproj"}
_ARC_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}


def _kind_category(name: str, is_dir: bool) -> str:
    if is_dir:
        return "dir"
    ext = Path(name).suffix.lower()
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    if ext in _IMG_EXT:
        return "image"
    if ext in _DOC_EXT:
        return "doc"
    if ext in _ARC_EXT:
        return "archive"
    return "other"


class _SearchWorker(QThread):
    results_ready = Signal(list, list)   # list[dict], list[str] errors

    def __init__(
        self,
        rclone: RcloneClient,
        keyword: str,
        targets: list[tuple[str, str]],   # [(remote_name, display_label)]
        folder_scope_spec: str = "",
        folder_scope_remote: str = "",
        folder_scope_label: str = "",
    ) -> None:
        super().__init__()
        self.rclone = rclone
        self.keyword = keyword
        self.targets = targets
        self.folder_scope_spec = folder_scope_spec
        self.folder_scope_remote = folder_scope_remote
        self.folder_scope_label = folder_scope_label

    def run(self) -> None:
        all_rows: list[dict] = []
        errors: list[str] = []

        if self.folder_scope_spec:
            try:
                inc = f"*{self.keyword}*" if self.keyword else ""
                items = self.rclone.lsjson_recursive(self.folder_scope_spec, include_pattern=inc)
                for it in items:
                    name = str(it.get("Name") or "")
                    path = str(it.get("Path") or name)
                    parent = "/".join(path.replace("\\", "/").split("/")[:-1]) or "/"
                    all_rows.append(
                        {
                            "id": str(it.get("ID") or ""),
                            "name": name,
                            "is_dir": bool(it.get("IsDir", False)),
                            "size": int(it.get("Size") or 0),
                            "mod_time": str(it.get("ModTime") or ""),
                            "remote": self.folder_scope_remote,
                            "cloud_label": self.folder_scope_label,
                            "parent_name": parent,
                            "parent_id": "",
                            "rel_path": path,
                            "scope_spec": self.folder_scope_spec,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            self.results_ready.emit(all_rows, errors)
            return

        for remote, label in self.targets:
            if self.isInterruptionRequested():
                break
            try:
                items = self.rclone.search_google_drive(remote, self.keyword, max_results=200)
                for it in items:
                    it["cloud_label"] = label
                    all_rows.append(it)
            except RcloneError as exc:
                errors.append(str(exc))
        self.results_ready.emit(all_rows, errors)


class SearchView(QWidget):
    """Màn hình 'Tìm Kiếm' kiểu 2 khung: tìm theo Google Drive chỉ định + lọc Ngày / Loại / Kích Thước."""

    download_requested = Signal(str, str, str, str, bool)   # remote, item_id, parent_id, name, is_dir
    open_folder_requested = Signal(str, str, str)      # remote, folder_id, folder_label
    status_message = Signal(str)

    def __init__(self, rclone: RcloneClient, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self._all_results: list[dict] = []
        self._live_workers: list[QThread] = []
        self._right_pane_getter = None
        self._build_ui()

    def set_right_pane_getter(self, fn) -> None:
        self._right_pane_getter = fn

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Thanh tìm kiếm chính (kiểu 2 khung: [Ô nhập từ khóa] [Chọn Google Drive chỉ định] [Tìm Kiếm])
        bar1 = QFrame()
        bar1.setObjectName("NavBar")
        b1 = QHBoxLayout(bar1)
        b1.setContentsMargins(10, 8, 10, 6)
        b1.setSpacing(8)

        self.ed_query = QLineEdit()
        self.ed_query.setPlaceholderText("Nhập tên file hoặc thư mục cần tìm trong Google Drive...")
        self.ed_query.setClearButtonEnabled(True)
        self.ed_query.returnPressed.connect(self.run_search)

        self.cb_scope = QComboBox()
        self.cb_scope.setMinimumWidth(240)

        self.btn_search = QPushButton("🔍 Tìm Kiếm")
        self.btn_search.setObjectName("PrimaryBtn")
        self.btn_search.clicked.connect(self.run_search)

        self.btn_stop = QPushButton("Dừng")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_search)

        b1.addWidget(self.ed_query, 1)
        b1.addWidget(QLabel("Tìm trong:"))
        b1.addWidget(self.cb_scope)
        b1.addWidget(self.btn_search)
        b1.addWidget(self.btn_stop)
        root.addWidget(bar1)

        # Thanh lọc kết quả kiểu 2 khung: [Ngày] [Loại] [Kích Thước]
        bar2 = QFrame()
        bar2.setObjectName("MiniToolbar")
        b2 = QHBoxLayout(bar2)
        b2.setContentsMargins(10, 4, 10, 4)
        b2.setSpacing(10)

        self.cb_date = QComboBox()
        self.cb_date.addItem("Ngày: Tất cả", "all")
        self.cb_date.addItem("Hôm nay", "today")
        self.cb_date.addItem("7 ngày qua", "7d")
        self.cb_date.addItem("30 ngày qua", "30d")
        self.cb_date.addItem("Năm nay", "year")

        self.cb_type = QComboBox()
        self.cb_type.addItem("Loại: Tất cả", "all")
        self.cb_type.addItem("Thư mục", "dir")
        self.cb_type.addItem("Video (.mp4, .mov…)", "video")
        self.cb_type.addItem("Âm thanh (.wav, .mp3…)", "audio")
        self.cb_type.addItem("Hình ảnh", "image")
        self.cb_type.addItem("Tài liệu / Premiere", "doc")
        self.cb_type.addItem("File nén (.zip, .rar)", "archive")

        self.cb_size = QComboBox()
        self.cb_size.addItem("Kích Thước: Tất cả", "all")
        self.cb_size.addItem("< 10 MB", "lt10m")
        self.cb_size.addItem("10 MB – 100 MB", "10_100m")
        self.cb_size.addItem("100 MB – 1 GB", "100m_1g")
        self.cb_size.addItem("> 1 GB", "gt1g")

        for cb in (self.cb_date, self.cb_type, self.cb_size):
            cb.currentIndexChanged.connect(self._render_results)
            b2.addWidget(cb)

        b2.addStretch(1)
        self.lbl_info = QLabel("Chọn Google Drive chỉ định rồi nhập từ khóa để tìm.")
        self.lbl_info.setObjectName("Muted")
        b2.addWidget(self.lbl_info)
        root.addWidget(bar2)

        # Bảng kết quả 6 cột kiểu 2 khung: Tên | Kích Thước | Ngày | Loại | Cloud | Thư Mục
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Tên", "Kích Thước", "Ngày", "Loại", "Cloud", "Thư Mục"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(25)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemDoubleClicked.connect(self._on_double_click)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

    def refresh_scopes(self) -> None:
        cur = self.cb_scope.currentData()
        self.cb_scope.clear()
        self.cb_scope.addItem("🌐 Tất cả tài khoản Google Drive", ("all", ""))
        if self._right_pane_getter is not None:
            rt = self._right_pane_getter()
            if rt and rt.kind == "drive":
                self.cb_scope.addItem(f"📂 Thư mục đang mở ở Khung Phải ({rt.display_path()[:40]})", ("right_folder", rt.remote))
        try:
            remotes = self.rclone.list_remotes(only_drive=True)
        except RcloneError:
            remotes = []
        for rem in remotes:
            meta = self.config.get_account_meta(rem.name)
            label = f"{meta.label()} ({rem.name}:)"
            self.cb_scope.addItem(icons.gdrive_icon(15), label, ("remote", rem.name))
        if cur:
            idx = self.cb_scope.findData(cur)
            if idx >= 0:
                self.cb_scope.setCurrentIndex(idx)

    def run_search(self) -> None:
        kw = self.ed_query.text().strip()
        if not kw:
            self.lbl_info.setText("Hãy nhập từ khóa cần tìm.")
            return
        scope_kind, scope_val = self.cb_scope.currentData() or ("all", "")

        folder_spec = ""
        folder_remote = ""
        folder_label = ""
        targets: list[tuple[str, str]] = []

        if scope_kind == "right_folder" and self._right_pane_getter is not None:
            rt = self._right_pane_getter()
            if rt and rt.kind == "drive":
                folder_spec = rt.rclone_spec()[0]
                folder_remote = rt.remote
                folder_label = self.config.get_account_meta(rt.remote).label()
        elif scope_kind == "remote" and scope_val:
            targets = [(scope_val, self.config.get_account_meta(scope_val).label())]
        else:
            try:
                for rem in self.rclone.list_remotes(only_drive=True):
                    targets.append((rem.name, self.config.get_account_meta(rem.name).label()))
            except RcloneError as exc:
                self.lbl_info.setText(f"Lỗi rclone: {exc}")
                return

        self.btn_search.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl_info.setText(f"Đang tìm '{kw}' trên Google Drive…")
        self.table.setRowCount(0)

        w = _SearchWorker(
            self.rclone,
            kw,
            targets,
            folder_scope_spec=folder_spec,
            folder_scope_remote=folder_remote,
            folder_scope_label=folder_label,
        )
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.results_ready.connect(self._on_results)
        w.start()

    def _stop_search(self) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.requestInterruption()
        self.btn_stop.setEnabled(False)
        self.btn_search.setEnabled(True)
        self.lbl_info.setText("Đã dừng tìm kiếm.")

    def _on_results(self, rows: list[dict], errors: list[str]) -> None:
        self.btn_search.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._all_results = rows
        self._render_results()
        if errors:
            self.status_message.emit(" | ".join(errors[:2]))

    def _render_results(self) -> None:
        d_flt = str(self.cb_date.currentData() or "all")
        t_flt = str(self.cb_type.currentData() or "all")
        s_flt = str(self.cb_size.currentData() or "all")
        now = datetime.now(timezone.utc)

        self.table.setRowCount(0)
        shown = 0
        for item in self._all_results:
            is_dir = bool(item.get("is_dir"))
            name = item.get("name", "")
            sz = int(item.get("size") or 0)
            cat = _kind_category(name, is_dir)

            if t_flt != "all" and cat != t_flt:
                continue
            if s_flt == "lt10m" and (is_dir or sz >= 10 * 1024 * 1024):
                continue
            if s_flt == "10_100m" and (is_dir or not (10 * 1024 * 1024 <= sz < 100 * 1024 * 1024)):
                continue
            if s_flt == "100m_1g" and (is_dir or not (100 * 1024 * 1024 <= sz < 1024 * 1024 * 1024)):
                continue
            if s_flt == "gt1g" and (is_dir or sz < 1024 * 1024 * 1024):
                continue

            mod_disp = ""
            mt_raw = str(item.get("mod_time") or "")
            if mt_raw:
                try:
                    dt = datetime.fromisoformat(mt_raw.replace("Z", "+00:00"))
                    mod_disp = dt.astimezone().strftime("%d/%m/%Y %H:%M")
                    if d_flt == "today" and dt.astimezone().date() != datetime.now().date():
                        continue
                    if d_flt == "7d" and (now - dt) > timedelta(days=7):
                        continue
                    if d_flt == "30d" and (now - dt) > timedelta(days=30):
                        continue
                    if d_flt == "year" and dt.astimezone().year != datetime.now().year:
                        continue
                except Exception:
                    mod_disp = mt_raw[:16]
            elif d_flt != "all":
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)
            it0 = QTableWidgetItem(name)
            it0.setIcon(icons.file_icon(name, is_dir))
            it0.setData(Qt.UserRole, item)
            self.table.setItem(row, 0, it0)
            self.table.setItem(row, 1, QTableWidgetItem("" if is_dir else format_size(sz)))
            self.table.setItem(row, 2, QTableWidgetItem(mod_disp))
            kind_txt = "Thư mục" if is_dir else (f"{Path(name).suffix[1:].upper()} File" if Path(name).suffix else "Tệp")
            self.table.setItem(row, 3, QTableWidgetItem(kind_txt))
            self.table.setItem(row, 4, QTableWidgetItem(item.get("cloud_label") or item.get("remote", "")))
            self.table.setItem(row, 5, QTableWidgetItem(item.get("parent_name", "")))
            shown += 1

        self.lbl_info.setText(f"Tìm thấy {shown} / {len(self._all_results)} kết quả")

    def _selected_item(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        it = self.table.item(row, 0)
        return it.data(Qt.UserRole) if it else None

    def _on_double_click(self) -> None:
        item = self._selected_item()
        if not item:
            return
        fid = item.get("id") if item.get("is_dir") else item.get("parent_id")
        if fid:
            self.open_folder_requested.emit(item["remote"], fid, item["name"] if item["is_dir"] else item.get("parent_name", "Folder"))

    def _context_menu(self, pos) -> None:
        item = self._selected_item()
        if not item:
            return
        menu = QMenu(self)
        fid = item.get("id", "")
        pid = item.get("parent_id", "")
        is_dir = bool(item.get("is_dir"))
        url = f"https://drive.google.com/drive/folders/{fid}" if is_dir else f"https://drive.google.com/file/d/{fid}/view"

        if is_dir and fid:
            menu.addAction("Mở thư mục này trên Khung Phải").triggered.connect(
                lambda: self.open_folder_requested.emit(item["remote"], fid, item["name"])
            )
        elif pid:
            menu.addAction("Mở thư mục chứa trên Khung Phải").triggered.connect(
                lambda: self.open_folder_requested.emit(item["remote"], pid, item.get("parent_name", "Thư mục"))
            )
        if fid:
            menu.addAction("Tải về thư mục đang mở ở Khung Trái (Download)").triggered.connect(
                lambda: self.download_requested.emit(item["remote"], fid, pid, item["name"], is_dir)
            )
            menu.addSeparator()
            menu.addAction("Mở trên Google Drive web").triggered.connect(lambda: webbrowser.open(url))
            menu.addAction("Sao chép link Google Drive").triggered.connect(
                lambda: (QApplication.clipboard().setText(url), self.status_message.emit("Đã sao chép link Google Drive."))
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def shutdown(self) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.requestInterruption()
                w.wait(1200)
                if w.isRunning():
                    w.terminate()
                    w.wait(400)
