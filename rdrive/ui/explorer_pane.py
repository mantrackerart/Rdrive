"""Một khung duyệt file hoàn chỉnh theo phong cách 2 khung (Tab bar + Nav bar + Cây thư mục + Danh sách file)."""
from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppConfig
from ..core.drive_cache import DriveCache
from ..core.media_info import probe_local_media_detail
from ..core.rclone_client import (
    DriveAbout,
    DriveEntry,
    RcloneClient,
    RcloneError,
    SharedDrive,
    folder_id_from_link,
    format_size,
    natural_sort_key,
    win_logical_less,
)
from . import icons
from .properties_dialog import PropertiesDialog

_INTERNAL_CLIPBOARD: dict = {"side": "", "mode": "copy", "entries": [], "src_tab": None}
_TAB_MIME = "application/x-rdrive-tab"


class _PaneTabItem(QFrame):
    """Một tab trên thanh ngang: Icon + Tên + nút '×' đóng tab + kéo thả đổi vị trí / kéo sang khung đối diện."""

    clicked = Signal(int)
    close_clicked = Signal(int)
    context_requested = Signal(int, object)

    def __init__(
        self,
        side: str,
        index: int,
        title: str,
        icon,
        active: bool,
        closable: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.side = side
        self.tab_index = index
        self._drag_start: QPoint | None = None
        self._did_drag = False

        self.setObjectName("PaneTabItem")
        self.setProperty("active", active)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{title}\n• Kéo để đổi vị trí hoặc kéo sang khung đối diện\n• Bấm × hoặc chuột giữa để đóng tab")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 4, 6, 4)
        lay.setSpacing(6)

        ico_lbl = QLabel()
        ico_lbl.setPixmap(icon.pixmap(15, 15))
        ico_lbl.setFixedSize(16, 16)
        ico_lbl.setStyleSheet("background: transparent; border: none;")
        ico_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(ico_lbl)

        txt_lbl = QLabel(title)
        txt_lbl.setObjectName("PaneTabLabel")
        txt_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(txt_lbl)

        if closable:
            btn_x = QToolButton()
            btn_x.setObjectName("PaneTabClose")
            btn_x.setText("×")
            btn_x.setFixedSize(16, 16)
            btn_x.setToolTip("Đóng tab này")
            btn_x.clicked.connect(lambda _=False: self.close_clicked.emit(self.tab_index))
            lay.addWidget(btn_x)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # QUAN TRỌNG: Không emit clicked ở mousePressEvent vì switch_tab() sẽ rebuild & xóa widget này ngay,
            # khiến mouseMoveEvent (kéo thả tab) không bao giờ chạy được!
            self._drag_start = event.position().toPoint()
            self._did_drag = False
            event.accept()
            return
        if event.button() == Qt.MiddleButton:
            self.close_clicked.emit(self.tab_index)
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.context_requested.emit(self.tab_index, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_start is not None
            and (event.buttons() & Qt.LeftButton)
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._drag_start = None
            self._did_drag = True
            drag = QDrag(self)
            md = QMimeData()
            payload = json.dumps({"side": self.side, "index": self.tab_index}).encode("utf-8")
            md.setData(_TAB_MIME, payload)
            drag.setMimeData(md)
            pm = self.grab()
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
            drag.exec(Qt.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_press = self._drag_start is not None
        did_drag = self._did_drag
        self._drag_start = None
        self._did_drag = False
        if event.button() == Qt.LeftButton and was_press and not did_drag:
            self.clicked.emit(self.tab_index)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _PaneTabBar(QFrame):
    """Thanh tab ngang nhận thả tab để sắp xếp lại vị trí hoặc di chuyển giữa Khung Trái ↔ Khung Phải."""

    tab_dropped = Signal(str, int, int)  # src_side, src_idx, dst_idx

    def __init__(self, side: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.side = side
        self.setObjectName("PaneTabBar")
        self.setFixedHeight(34)
        self.setAcceptDrops(True)
        self._drop_indicator = QFrame(self)
        self._drop_indicator.setFixedSize(3, 26)
        self._drop_indicator.setStyleSheet("background: #ff8a3d; border-radius: 1px;")
        self._drop_indicator.hide()

    def _calc_drop_pos(self, drop_x: int) -> tuple[int, int]:
        tab_widgets = [w for w in self.findChildren(_PaneTabItem) if w.parentWidget() is self]
        tab_widgets.sort(key=lambda w: w.x())
        dst_idx = len(tab_widgets)
        ind_x = 4
        for i, tw in enumerate(tab_widgets):
            if drop_x < tw.x() + tw.width() // 2:
                dst_idx = i
                ind_x = max(2, tw.x() - 1)
                break
            ind_x = tw.x() + tw.width() + 1
        return dst_idx, ind_x

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_TAB_MIME):
            event.acceptProposedAction()
            _idx, ind_x = self._calc_drop_pos(event.position().toPoint().x())
            self._drop_indicator.move(ind_x, 4)
            self._drop_indicator.show()
            self._drop_indicator.raise_()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(_TAB_MIME):
            event.acceptProposedAction()
            _idx, ind_x = self._calc_drop_pos(event.position().toPoint().x())
            self._drop_indicator.move(ind_x, 4)
            self._drop_indicator.show()
            self._drop_indicator.raise_()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._drop_indicator.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._drop_indicator.hide()
        md = event.mimeData()
        if not md.hasFormat(_TAB_MIME):
            super().dropEvent(event)
            return
        try:
            raw = bytes(md.data(_TAB_MIME)).decode("utf-8", "replace")
            data = json.loads(raw)
            src_side = str(data.get("side") or "")
            src_idx = int(data.get("index", -1))
        except Exception:
            return
        if src_idx < 0 or not src_side:
            return

        dst_idx, _ = self._calc_drop_pos(event.position().toPoint().x())
        self.tab_dropped.emit(src_side, src_idx, dst_idx)
        event.acceptProposedAction()


class _FileTreeWidget(QTreeWidget):
    """QTreeWidget hỗ trợ Kéo Thả (Drag & Drop) từ Windows Explorer vào Rdrive và giữa 2 cột Trái ↔ Phải."""

    external_files_dropped = Signal(list)         # list[str] đường dẫn Windows
    internal_pane_dropped = Signal(str, list)     # from_side, list[dict] entries
    tab_dropped_here = Signal(str, int)           # src_side, src_idx

    def __init__(self, side: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.side = side
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeData(self, items: list[QTreeWidgetItem]) -> QMimeData:
        md = QMimeData()
        entries: list[dict] = []
        urls: list[QUrl] = []
        for it in items:
            d = it.data(0, Qt.UserRole)
            if isinstance(d, dict) and d.get("name"):
                entries.append(d)
                fp = d.get("full_path", "")
                if fp and os.path.exists(fp):
                    urls.append(QUrl.fromLocalFile(fp))
        payload = json.dumps({"side": self.side, "entries": entries}, ensure_ascii=False).encode("utf-8")
        md.setData("application/x-rdrive-entries", payload)
        if urls:
            md.setUrls(urls)
        return md

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(_TAB_MIME) or md.hasFormat("application/x-rdrive-entries") or md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(_TAB_MIME) or md.hasFormat("application/x-rdrive-entries") or md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(_TAB_MIME):
            try:
                raw = bytes(md.data(_TAB_MIME)).decode("utf-8", "replace")
                data = json.loads(raw)
                src_side = str(data.get("side") or "")
                src_idx = int(data.get("index", -1))
                if src_side and src_idx >= 0:
                    self.tab_dropped_here.emit(src_side, src_idx)
                    event.acceptProposedAction()
                    return
            except Exception:
                pass
        if md.hasFormat("application/x-rdrive-entries"):
            try:
                raw = bytes(md.data("application/x-rdrive-entries")).decode("utf-8", "replace")
                data = json.loads(raw)
                from_side = str(data.get("side") or "")
                entries = data.get("entries") or []
                if from_side and from_side != self.side and entries:
                    self.internal_pane_dropped.emit(from_side, entries)
                    event.acceptProposedAction()
                    return
            except Exception:
                pass
        if md.hasUrls():
            paths = []
            for u in md.urls():
                if u.isLocalFile():
                    p = u.toLocalFile()
                    if p and os.path.exists(p):
                        paths.append(os.path.normpath(p))
            if paths:
                self.external_files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


@dataclass
class PaneTabState:
    """Trạng thái của một tab bên trong ExplorerPane."""
    kind: str = "local"              # "local" | "drive"
    remote: str = ""                 # tên remote rclone nếu kind == "drive"
    title: str = "This PC"
    # Local state
    local_path: str = ""
    # Drive state
    drive_mode: str = "my_drive"     # "my_drive" | "shared_with_me" | "team_drive" | "folder_id"
    rel_path: str = ""               # đường dẫn tương đối (vd "2026/quay thang 11")
    team_drive_id: str = ""
    team_drive_name: str = ""
    root_folder_id: str = ""
    root_folder_label: str = ""
    history: list[dict] = field(default_factory=list)
    expanded_keys: set[tuple] = field(default_factory=set)

    def cache_key(self) -> tuple:
        return DriveCache.make_key(
            self.remote,
            self.drive_mode,
            self.team_drive_id if self.drive_mode == "team_drive" else "",
            self.root_folder_id if self.drive_mode in ("folder_id", "shared_owner") else "",
            self.rel_path,
        )

    def rclone_spec(self, child_name: str = "") -> tuple[str, list[str]]:
        """Trả về (spec, extra_flags) dùng chuỗi kết nối riêng cho từng remote (hỗ trợ Drive A → Drive B an toàn)."""
        if self.kind == "local":
            p = os.path.join(self.local_path, child_name) if child_name else self.local_path
            return p, []
        rel = self.rel_path.strip("/")
        if child_name:
            rel = f"{rel}/{child_name}" if rel else child_name
        opts: list[str] = []
        if self.drive_mode == "team_drive" and self.team_drive_id:
            opts.append(f"team_drive={self.team_drive_id}")
        elif self.drive_mode == "folder_id" and self.root_folder_id:
            opts.append(f"root_folder_id={self.root_folder_id}")
        elif self.drive_mode in ("shared_with_me", "shared_owner"):
            opts.append("shared_with_me=true")
        rem = f"{self.remote},{','.join(opts)}" if opts else self.remote
        spec = f"{rem}:{rel}" if rel else f"{rem}:"
        return spec, []

    def display_path(self) -> str:
        if self.kind == "local":
            return self.local_path or "This PC"
        prefix = self.title
        if self.drive_mode == "shared_with_me":
            sec = "Được chia sẻ với tôi"
        elif self.drive_mode == "shared_owner":
            sec = self.root_folder_label or self.root_folder_id or "Được chia sẻ"
        elif self.drive_mode == "team_drive":
            sec = self.team_drive_name or "Shared Drive"
        elif self.drive_mode == "folder_id":
            sec = self.root_folder_label or f"ID:{self.root_folder_id[:10]}"
        else:
            sec = "Drive của tôi"
        rel = self.rel_path.strip("/")
        return f"{prefix} > {sec}" + (f" > {rel.replace('/', ' > ')}" if rel else "")


class _SortableItem(QTreeWidgetItem):
    """Hàng trong danh sách file sắp xếp chuẩn (Thư mục luôn trên cùng, kích thước theo số)."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        d1 = self.data(0, Qt.UserRole) or {}
        d2 = other.data(0, Qt.UserRole) or {}
        is_dir1 = bool(d1.get("is_dir"))
        is_dir2 = bool(d2.get("is_dir"))
        if is_dir1 != is_dir2:
            # Giữ thư mục luôn ở trên bất kể chiều sort
            order = tree.header().sortIndicatorOrder() if tree else Qt.AscendingOrder
            return is_dir1 if order == Qt.AscendingOrder else not is_dir1
        if col == 1:
            return int(d1.get("size", -1)) < int(d2.get("size", -1))
        if col == 2:
            return str(d1.get("mod_sort", "")) < str(d2.get("mod_sort", ""))
        if col == 0:
            return win_logical_less(self.text(0), other.text(0))
        return win_logical_less(self.text(col), other.text(col))


class _DriveListWorker(QThread):
    done = Signal(int, list)       # req_id, list[DriveEntry]
    err = Signal(int, str)

    def __init__(
        self,
        req_id: int,
        rclone: RcloneClient,
        remote: str,
        rel_path: str,
        *,
        folder_id: str = "",
        shared_with_me: bool = False,
        team_drive: str = "",
        dirs_only: bool = False,
    ) -> None:
        super().__init__()
        self.req_id = req_id
        self.rclone = rclone
        self.remote = remote
        self.rel_path = rel_path
        self.folder_id = folder_id
        self.shared_with_me = shared_with_me
        self.team_drive = team_drive
        self.dirs_only = dirs_only

    def run(self) -> None:
        try:
            items = self.rclone.lsjson(
                self.remote,
                self.rel_path,
                folder_id=self.folder_id,
                shared_with_me=self.shared_with_me,
                team_drive=self.team_drive,
                dirs_only=self.dirs_only,
            )
            self.done.emit(self.req_id, items)
        except Exception as exc:  # noqa: BLE001
            self.err.emit(self.req_id, str(exc))


class _DriveRootsWorker(QThread):
    done = Signal(str, list, object, dict)   # remote, list[SharedDrive], DriveAbout|None, shared_owners
    err = Signal(str, str)

    def __init__(self, rclone: RcloneClient, remote: str) -> None:
        super().__init__()
        self.rclone = rclone
        self.remote = remote

    def run(self) -> None:
        about_info: DriveAbout | None = None
        drives: list[SharedDrive] = []
        owners: dict[str, list[DriveEntry]] = {}
        rtype = "drive"
        try:
            cfg = self.rclone.config_dump().get(self.remote) or {}
            rtype = str(cfg.get("type") or "drive").lower()
        except Exception:
            rtype = "drive"

        if rtype != "drive":
            # NAS (SMB / SFTP / WebDAV / FTP): about() là tùy chọn, không bắt buộc
            try:
                about_info = self.rclone.about(self.remote)
            except Exception:
                about_info = None
            self.done.emit(self.remote, [], about_info, {})
            return

        try:
            about_info = self.rclone.about(self.remote)
        except RcloneError as exc:
            self.err.emit(self.remote, str(exc))
            return
        try:
            drives = self.rclone.list_shared_drives(self.remote)
        except RcloneError:
            drives = []
        try:
            owners = self.rclone.list_shared_by_owners(self.remote)
        except Exception:
            owners = {}
        self.done.emit(self.remote, drives, about_info, owners)


class _SharedOwnerRefreshWorker(QThread):
    """Tải lại danh sách mục được chia sẻ theo từng chủ sở hữu '<owner> Drive'."""

    done = Signal(int, str, str, dict)  # req_id, remote, owner_label, owners_map
    err = Signal(int, str)

    def __init__(self, req_id: int, rclone: RcloneClient, remote: str, owner_label: str) -> None:
        super().__init__()
        self.req_id = req_id
        self.rclone = rclone
        self.remote = remote
        self.owner_label = owner_label

    def run(self) -> None:
        try:
            owners = self.rclone.list_shared_by_owners(self.remote)
            self.done.emit(self.req_id, self.remote, self.owner_label, owners)
        except Exception as exc:  # noqa: BLE001
            self.err.emit(self.req_id, str(exc))


class _DriveOpWorker(QThread):
    done = Signal(str)
    err = Signal(str)

    def __init__(self, fn, ok_msg: str) -> None:
        super().__init__()
        self.fn = fn
        self.ok_msg = ok_msg

    def run(self) -> None:
        try:
            self.fn()
            self.done.emit(self.ok_msg)
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc))


def _windows_drives() -> list[str]:
    if sys.platform != "win32":
        return ["/"]
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{ch}:\\" for i, ch in enumerate(string.ascii_uppercase) if mask & (1 << i)]
    except Exception:
        return [f"{ch}:\\" for ch in string.ascii_uppercase if os.path.exists(f"{ch}:\\")]


def _fmt_iso_date(iso: str) -> tuple[str, str]:
    if not iso:
        return "", ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S"), dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso[:19].replace("T", " "), iso[:19]


class ExplorerPane(QFrame):
    """Khung duyệt 1 bên (Trái hoặc Phải) kiểu 2 khung."""

    transfer_requested = Signal(str, list, str)    # side, list[dict(name,is_dir,size,id)], mode("copy"|"move")
    external_drop_requested = Signal(str, list)    # target_side, list[str] local_paths
    status_message = Signal(str)
    add_account_requested = Signal()
    send_to_syau_sync = Signal(list)
    send_to_syau_convert = Signal(list)
    folder_changed = Signal(str, str)              # side, leaf_folder_name (cho chế độ khóa điều hướng)
    tab_move_requested = Signal(str, int, str, int)  # src_side, src_idx, dst_side, dst_idx

    def __init__(
        self,
        side: str,
        rclone: RcloneClient,
        config: AppConfig,
        cache: DriveCache | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.side = side
        self.rclone = rclone
        self.config = config
        self.cache = cache if cache is not None else DriveCache()
        self.setObjectName("PaneFrame")
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(260)

        self.tabs: list[PaneTabState] = []
        self.active_tab_idx: int = -1
        self._req_seq = 0
        self._req_keys: dict[int, tuple] = {}
        self._live_workers: list[QThread] = []
        self._list_worker: _DriveListWorker | None = None
        self._roots_worker: _DriveRootsWorker | None = None
        self._op_worker: _DriveOpWorker | None = None
        self._all_entries: list[dict] = []

        self._build_ui()

    def _track_worker(self, worker: QThread) -> None:
        """Giữ tham chiếu tới mọi QThread đang chạy cho tới khi finished để tránh crash C++."""
        self._live_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._retire_worker(w))

    def _retire_worker(self, worker: QThread) -> None:
        if worker in self._live_workers:
            self._live_workers.remove(worker)
        if self._list_worker is worker:
            self._list_worker = None
        if self._roots_worker is worker:
            self._roots_worker = None
        if self._op_worker is worker:
            self._op_worker = None
        worker.deleteLater()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 1) Tab bar của khung (hỗ trợ kéo thả sắp xếp tab & kéo sang khung đối diện)
        self.tab_bar = _PaneTabBar(self.side)
        from PySide6.QtWidgets import QSizePolicy
        self.tab_bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.tab_bar.setMinimumWidth(0)
        self.tab_bar.tab_dropped.connect(self._on_tab_dropped)
        self.tab_layout = QHBoxLayout(self.tab_bar)
        self.tab_layout.setContentsMargins(4, 0, 4, 0)
        self.tab_layout.setSpacing(2)
        self.tab_layout.addStretch(1)
        lay.addWidget(self.tab_bar)

        # Khung nội dung: Trang 0 = Trình duyệt file | Trang 1 = Màn hình Tab mới / Trống kiểu 2 khung
        self.body_stack = QStackedWidget()
        explorer_page = QWidget()
        elay = QVBoxLayout(explorer_page)
        elay.setContentsMargins(0, 0, 0, 0)
        elay.setSpacing(0)

        # 2) Navigation & Path bar
        nav = QFrame()
        nav.setObjectName("NavBar")
        nlay = QHBoxLayout(nav)
        nlay.setContentsMargins(6, 4, 6, 4)
        nlay.setSpacing(4)

        self.btn_back = self._icon_btn("←", "Quay lại")
        self.btn_up = self._icon_btn("↑", "Lên thư mục cha")
        self.btn_refresh = self._icon_btn("↻", "Làm mới thư mục hiện tại (F5) · Shift+Click để làm mới cả tài khoản")
        self.btn_back.clicked.connect(self._go_back)
        self.btn_up.clicked.connect(self._go_up)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)

        self.cb_drive_view = QComboBox()
        self.cb_drive_view.setMinimumWidth(145)
        self.cb_drive_view.addItem("Drive của tôi", "my_drive")
        self.cb_drive_view.addItem("Được chia sẻ với tôi", "shared_with_me")
        self.cb_drive_view.addItem("Dán link folder…", "paste_link")
        self.cb_drive_view.activated.connect(self._on_drive_view_picked)
        self.cb_drive_view.setVisible(False)

        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText("Nhập đường dẫn thư mục hoặc dán link Google Drive rồi bấm Enter...")
        self.ed_path.returnPressed.connect(self._on_path_entered)

        nlay.addWidget(self.btn_back)
        nlay.addWidget(self.btn_up)
        nlay.addWidget(self.btn_refresh)
        nlay.addWidget(self.cb_drive_view)
        nlay.addWidget(self.ed_path, 1)
        elay.addWidget(nav)

        # 3) Mini action toolbar (kiểu 2 khung)
        mini = QFrame()
        mini.setObjectName("MiniToolbar")
        mlay = QHBoxLayout(mini)
        mlay.setContentsMargins(6, 3, 6, 3)
        mlay.setSpacing(4)

        self.btn_mkdir = self._icon_btn("+ Thư mục", "Tạo thư mục mới (Ctrl+Shift+N)")
        self.btn_rename = self._icon_btn("Đổi tên", "Đổi tên mục đang chọn (F2)")
        self.btn_delete = self._icon_btn("Xóa", "Xóa mục đang chọn (Delete)")
        self.btn_open = self._icon_btn("Mở ngoài", "Mở trong Windows Explorer hoặc trình duyệt Google Drive")
        self.btn_mkdir.clicked.connect(self._mkdir)
        self.btn_rename.clicked.connect(self._rename_selected)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_open.clicked.connect(self._open_external)

        # Nút ★ Favourite (Lối tắt địa chỉ ưa thích — Mục #8)
        self.btn_fav = self._icon_btn("★ ▾", "Lưu & mở nhanh địa chỉ ưa thích (Favourite)")
        self.btn_fav.setPopupMode(QToolButton.InstantPopup)
        self.fav_menu = QMenu(self)
        self.fav_menu.aboutToShow.connect(self._populate_fav_menu)
        self.btn_fav.setMenu(self.fav_menu)

        # Nút Chế độ Hiển thị (Biểu tượng Rất Lớn / Lớn / Trung Bình / Danh Sách / Chi Tiết — Mục #15)
        self.btn_view_mode = self._icon_btn("☰ ▾", "Chế độ hiển thị thư mục & tệp")
        self.btn_view_mode.setPopupMode(QToolButton.InstantPopup)
        vm_menu = QMenu(self)
        for vm_label, icon_sz, show_cols in [
            ("Biểu Tượng Rất Lớn", 42, False),
            ("Biểu Tượng Lớn", 30, False),
            ("Biểu Tượng Trung Bình", 22, False),
            ("Danh Sách", 16, False),
            ("Chi Tiết (Đầy đủ cột + Thời lượng Video)", 18, True),
        ]:
            vm_menu.addAction(vm_label).triggered.connect(
                lambda _=False, sz=icon_sz, sc=show_cols: self._apply_view_mode(sz, sc)
            )
        self.btn_view_mode.setMenu(vm_menu)

        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("Lọc nhanh tên file...")
        self.ed_filter.setMaximumWidth(190)
        self.ed_filter.setClearButtonEnabled(True)
        self.ed_filter.textChanged.connect(self._apply_filter)

        mlay.addWidget(self.btn_mkdir)
        mlay.addWidget(self.btn_rename)
        mlay.addWidget(self.btn_delete)
        mlay.addWidget(self.btn_open)
        mlay.addWidget(self.btn_fav)
        mlay.addWidget(self.btn_view_mode)
        mlay.addStretch(1)
        mlay.addWidget(self.ed_filter)
        elay.addWidget(mini)

        # 4) Splitter: Cây thư mục (trái) + Danh sách file (phải)
        self.splitter = QSplitter(Qt.Horizontal)

        self.dir_tree = QTreeWidget()
        self.dir_tree.setObjectName("DirTree")
        self.dir_tree.setHeaderHidden(True)
        self.dir_tree.setIndentation(16)
        self.dir_tree.setUniformRowHeights(True)
        self.dir_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.dir_tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.dir_tree.itemExpanded.connect(self._on_tree_expanded)
        self.dir_tree.itemCollapsed.connect(self._on_tree_collapsed)
        self.dir_tree.itemClicked.connect(self._on_tree_clicked)

        self.file_list = _FileTreeWidget(self.side, self)
        self.file_list.setObjectName("FileList")
        self.file_list.setColumnCount(5)
        self.file_list.setHeaderLabels(["Tên", "Kích thước", "Ngày", "Loại", "Chi Tiết"])
        self.file_list.setRootIsDecorated(False)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setSortingEnabled(True)
        self.file_list.sortByColumn(0, Qt.AscendingOrder)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_context_menu)
        self.file_list.itemDoubleClicked.connect(self._on_file_double_clicked)
        self.file_list.itemSelectionChanged.connect(self._update_status_counts)
        self.file_list.external_files_dropped.connect(lambda paths: self.external_drop_requested.emit(self.side, paths))
        self.file_list.internal_pane_dropped.connect(lambda from_side, entries: self.transfer_requested.emit(from_side, entries, "copy"))
        self.file_list.tab_dropped_here.connect(lambda s_side, s_idx: self._on_tab_dropped(s_side, s_idx, len(self.tabs)))

        fhdr = self.file_list.header()
        fhdr.setStretchLastSection(True)
        fhdr.setSectionResizeMode(0, QHeaderView.Interactive)
        fhdr.setSectionResizeMode(1, QHeaderView.Interactive)
        fhdr.setSectionResizeMode(2, QHeaderView.Interactive)
        fhdr.setSectionResizeMode(3, QHeaderView.Interactive)
        self.file_list.setColumnWidth(0, 245)
        self.file_list.setColumnWidth(1, 86)
        self.file_list.setColumnWidth(2, 128)
        self.file_list.setColumnWidth(3, 88)

        self.splitter.addWidget(self.dir_tree)
        self.splitter.addWidget(self.file_list)
        self.splitter.setSizes([210, 460])
        elay.addWidget(self.splitter, 1)

        # 5) Pane status bar
        st = QFrame()
        st.setObjectName("PaneStatus")
        st.setFixedHeight(26)
        slay = QHBoxLayout(st)
        slay.setContentsMargins(8, 2, 8, 2)
        slay.setSpacing(8)
        self.lbl_count = QLabel("0 mục")
        self.lbl_count.setObjectName("Muted")
        self.lbl_quota = QLabel("")
        self.lbl_quota.setObjectName("Muted")
        self.lbl_quota.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slay.addWidget(self.lbl_count, 1)
        slay.addWidget(self.lbl_quota, 1)
        elay.addWidget(st)

        # Trang 1: Màn hình Tab mới / Khung trống kiểu 2 khung (Tất Cả + Tìm Kiếm + This PC + ★ Favourite + Tài Khoản)
        self.launcher_page = QWidget()
        self.launcher_page.setStyleSheet("background: #15181e;")
        llay = QVBoxLayout(self.launcher_page)
        llay.setContentsMargins(12, 10, 12, 10)
        llay.setSpacing(8)

        lbar = QHBoxLayout()
        lbar.setSpacing(8)
        self.cb_launch_cat = QComboBox()
        self.cb_launch_cat.setMinimumWidth(140)
        self.cb_launch_cat.currentIndexChanged.connect(self._refresh_launcher)
        self.ed_launch_search = QLineEdit()
        self.ed_launch_search.setPlaceholderText("Tìm Kiếm tài khoản, NAS hoặc Favourite…")
        self.ed_launch_search.setClearButtonEnabled(True)
        self.ed_launch_search.textChanged.connect(self._refresh_launcher)
        lbar.addWidget(self.cb_launch_cat)
        lbar.addWidget(self.ed_launch_search, 1)
        llay.addLayout(lbar)

        self.launch_tree = QTreeWidget()
        self.launch_tree.setObjectName("DirTree")
        self.launch_tree.setHeaderHidden(True)
        self.launch_tree.setIndentation(18)
        self.launch_tree.setIconSize(QSize(18, 18))
        self.launch_tree.setStyleSheet(
            "QTreeWidget { background: #15181e; border: none; font-size: 9.5pt; }"
            "QTreeWidget::item { padding: 5px 6px; border-radius: 4px; }"
            "QTreeWidget::item:hover { background: #222733; }"
        )
        self.launch_tree.itemClicked.connect(self._on_launcher_item_clicked)
        self.launch_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.launch_tree.customContextMenuRequested.connect(self._show_launcher_menu)
        llay.addWidget(self.launch_tree, 1)

        self.body_stack.addWidget(explorer_page)        # 0: Explorer
        self.body_stack.addWidget(self.launcher_page)   # 1: New Tab / Launcher
        lay.addWidget(self.body_stack, 1)

    @staticmethod
    def _icon_btn(text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("IconBtn")
        b.setText(text)
        b.setToolTip(tip)
        return b

    # ------------------------------------------------------------ Tabs & Launcher
    def current_tab(self) -> PaneTabState | None:
        if 0 <= self.active_tab_idx < len(self.tabs):
            t = self.tabs[self.active_tab_idx]
            return None if t.kind == "new_tab" else t
        return None

    def _replace_new_tab_or_append(self, state: PaneTabState) -> int:
        if 0 <= self.active_tab_idx < len(self.tabs) and self.tabs[self.active_tab_idx].kind == "new_tab":
            self.tabs[self.active_tab_idx] = state
            return self.active_tab_idx
        self.tabs.append(state)
        return len(self.tabs) - 1

    def open_new_tab(self) -> None:
        """Mở 1 Tab mới với màn hình chọn nhanh This PC / ★ Favourite / Tài Khoản (kiểu 2 khung)."""
        for i, t in enumerate(self.tabs):
            if t.kind == "new_tab":
                self.switch_tab(i)
                return
        self.tabs.append(PaneTabState(kind="new_tab", title="Tab mới"))
        self.switch_tab(len(self.tabs) - 1)

    def show_launcher(self) -> None:
        """Hiển thị màn hình chọn nhanh kiểu 2 khung khi khung không còn tab nào (tránh khung bị trắng)."""
        self.active_tab_idx = -1
        self._rebuild_tab_bar()
        self._refresh_launcher_categories()
        self._refresh_launcher()
        self.body_stack.setCurrentIndex(1)

    def open_local_tab(self, path: str = "") -> None:
        start_path = path if path and os.path.isdir(path) else str(Path.home() / "Desktop")
        if not os.path.isdir(start_path):
            start_path = str(Path.home())
        state = PaneTabState(kind="local", title="This PC", local_path=start_path)
        idx = self._replace_new_tab_or_append(state)
        self.switch_tab(idx)

    def open_drive_tab(self, remote: str, display_name: str = "", *, force_new: bool = False) -> None:
        if not force_new:
            for i, t in enumerate(self.tabs):
                if t.kind == "drive" and t.remote == remote:
                    t.title = display_name or t.title
                    self.switch_tab(i)
                    return
        meta = self.config.get_account_meta(remote)
        state = PaneTabState(kind="drive", remote=remote, title=display_name or meta.label())
        idx = self._replace_new_tab_or_append(state)
        self.switch_tab(idx)

    def open_drive_folder_id(self, remote: str, folder_id: str, label: str = "") -> None:
        """Nhảy trực tiếp tới 1 thư mục cụ thể theo Folder ID trên remote Google Drive."""
        self.open_drive_tab(remote)
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        self._push_drive_history(tab)
        tab.drive_mode = "folder_id"
        tab.root_folder_id = folder_id
        tab.root_folder_label = label or f"ID:{folder_id[:8]}"
        tab.rel_path = ""
        tab.team_drive_id = ""
        self._load_drive_current()

    def refresh_tab_labels(self) -> None:
        for t in self.tabs:
            if t.kind == "drive":
                t.title = self.config.get_account_meta(t.remote).label()
        self._rebuild_tab_bar()

    def close_remote_tabs(self, remote: str) -> None:
        """Đóng mọi tab đang mở của tài khoản vừa đăng xuất / xóa."""
        removed = False
        for i in range(len(self.tabs) - 1, -1, -1):
            if self.tabs[i].kind == "drive" and self.tabs[i].remote == remote:
                self.tabs.pop(i)
                removed = True
        if not removed:
            return
        if not self.tabs:
            self.show_launcher()
            return
        new_idx = min(max(0, self.active_tab_idx), len(self.tabs) - 1)
        self.active_tab_idx = -1
        self.switch_tab(new_idx)

    def _is_nas_remote(self, remote: str) -> bool:
        if not remote:
            return False
        meta = self.config.get_account_meta(remote)
        if meta.category == "NAS Xpenology" or any(
            (meta.email or "").upper().startswith(p) for p in ("SMB://", "SFTP://", "WEBDAV://", "FTP://")
        ):
            return True
        return False

    def _rebuild_tab_bar(self) -> None:
        from PySide6.QtWidgets import QSizePolicy
        while self.tab_layout.count():
            item = self.tab_layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        for idx, tab in enumerate(self.tabs):
            if tab.kind == "local":
                tab_ico = icons.pc_icon(15)
            elif tab.kind == "new_tab":
                tab_ico = icons.ribbon_icon("explorer", 15)
            elif self._is_nas_remote(tab.remote):
                tab_ico = icons.nas_icon(15)
            else:
                tab_ico = icons.gdrive_icon(15)
            item_w = _PaneTabItem(
                side=self.side,
                index=idx,
                title=tab.title,
                icon=tab_ico,
                active=(idx == self.active_tab_idx),
                closable=True,
                parent=self.tab_bar,
            )
            item_w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            item_w.clicked.connect(self.switch_tab)
            item_w.close_clicked.connect(self._close_tab)
            item_w.context_requested.connect(self._show_tab_menu)
            self.tab_layout.addWidget(item_w)
            item_w.show()

        btn_add = QPushButton("+", self.tab_bar)
        btn_add.setObjectName("PaneAddTab")
        btn_add.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn_add.setToolTip("Mở Tab mới (chọn This PC, ★ Favourite, Google Drive hoặc NAS)")
        btn_add.clicked.connect(self.open_new_tab)
        self.tab_layout.addWidget(btn_add)
        btn_add.show()
        self.tab_layout.addStretch(1)
        self.tab_bar.updateGeometry()

    def _on_tab_dropped(self, src_side: str, src_idx: int, dst_idx: int) -> None:
        """Xử lý kéo thả đổi vị trí tab trong cùng khung hoặc kéo tab sang khung đối diện."""
        if src_side == self.side:
            if not (0 <= src_idx < len(self.tabs)):
                return
            dst_idx = max(0, min(dst_idx, len(self.tabs)))
            if src_idx == dst_idx or src_idx + 1 == dst_idx:
                return
            active_tab = self.tabs[self.active_tab_idx] if 0 <= self.active_tab_idx < len(self.tabs) else None
            moved = self.tabs.pop(src_idx)
            if dst_idx > src_idx:
                dst_idx -= 1
            self.tabs.insert(dst_idx, moved)
            if active_tab in self.tabs:
                self.active_tab_idx = self.tabs.index(active_tab)
            else:
                self.active_tab_idx = dst_idx
            self._rebuild_tab_bar()
        else:
            self.tab_move_requested.emit(src_side, src_idx, self.side, dst_idx)

    def _show_tab_menu(self, idx: int, global_pos) -> None:
        if not (0 <= idx < len(self.tabs)):
            return
        tab = self.tabs[idx]
        menu = QMenu(self)
        if tab.kind == "local":
            menu.addAction("Nhân đôi tab (This PC)").triggered.connect(lambda: self.open_local_tab(tab.local_path))
        else:
            menu.addAction(f"Mở thêm tab cùng tài khoản ({tab.title})").triggered.connect(
                lambda: self.open_drive_tab(tab.remote, tab.title, force_new=True)
            )
            menu.addAction("Làm mới toàn bộ cache tài khoản này").triggered.connect(
                lambda: (self.cache.invalidate_remote(tab.remote), self.switch_tab(idx))
            )
        menu.addSeparator()
        menu.addAction("Đóng tab").triggered.connect(lambda: self._close_tab(idx))
        menu.exec(global_pos)

    def _close_tab(self, idx: int) -> None:
        if not (0 <= idx < len(self.tabs)):
            return
        self.tabs.pop(idx)
        if not self.tabs:
            self.show_launcher()
            return
        new_idx = min(self.active_tab_idx, len(self.tabs) - 1)
        self.active_tab_idx = -1
        self.switch_tab(new_idx)

    def _refresh_launcher_categories(self) -> None:
        cur = self.cb_launch_cat.currentData() or "__ALL__"
        self.cb_launch_cat.blockSignals(True)
        self.cb_launch_cat.clear()
        self.cb_launch_cat.addItem("Tất Cả", "__ALL__")
        self.cb_launch_cat.addItem("GoogleDrive", "__GDRIVE__")
        for cat in self.config.categories:
            self.cb_launch_cat.addItem(cat, cat)
        idx = self.cb_launch_cat.findData(cur)
        self.cb_launch_cat.setCurrentIndex(max(0, idx))
        self.cb_launch_cat.blockSignals(False)

    def _refresh_launcher(self) -> None:
        """Hiển thị màn hình chọn nhanh kiểu 2 khung: Đăng ký tài khoản + This PC + ★ Favourite + Tài Khoản / NAS."""
        self.launch_tree.clear()
        q = self.ed_launch_search.text().strip().lower()
        cat = str(self.cb_launch_cat.currentData() or "__ALL__")

        # 1) Đăng ký tài khoản Google mới
        if not q or "đăng" in q or "google" in q or "tài khoản" in q:
            it_reg = QTreeWidgetItem(["+ Đăng Ký Tài Khoản Google"])
            it_reg.setIcon(0, icons.gdrive_icon(17))
            it_reg.setData(0, Qt.UserRole, {"action": "add_account"})
            self.launch_tree.addTopLevelItem(it_reg)

        # 2) This PC
        if not q or "this pc" in q or "máy tính" in q or "local" in q or "pc" in q:
            it_pc = QTreeWidgetItem(["This PC"])
            it_pc.setIcon(0, icons.pc_icon(17))
            it_pc.setData(0, Qt.UserRole, {"action": "local"})
            self.launch_tree.addTopLevelItem(it_pc)

        # 3) ★ Favourite (Lối tắt nhanh)
        favs = self.config.favourites or []
        fav_group = QTreeWidgetItem([f"★  Favourite ({len(favs)})"])
        fav_group.setData(0, Qt.UserRole, {"action": "group"})
        fav_added = 0
        for idx, fav in enumerate(favs):
            title = str(fav.get("title") or fav.get("local_path") or fav.get("remote") or "Lối tắt")
            sub = str(fav.get("local_path") or fav.get("rel_path") or fav.get("root_folder_label") or "")
            if q and q not in f"{title} {sub}".lower():
                continue
            child = QTreeWidgetItem([title])
            if fav.get("kind") == "local":
                child.setIcon(0, icons.folder_icon("#f0be4b", 16))
            elif self._is_nas_remote(fav.get("remote", "")):
                child.setIcon(0, icons.nas_icon(16))
            else:
                child.setIcon(0, icons.gdrive_icon(16))
            child.setToolTip(0, f"★ Favourite — {title}\n(Click để mở ngay · Chuột phải để xóa)")
            child.setData(0, Qt.UserRole, {"action": "favourite", "fav": fav, "fav_idx": idx})
            fav_group.addChild(child)
            fav_added += 1
        if fav_added == 0 and not q:
            hint = QTreeWidgetItem(["(Chưa có Favourite — bấm nút ★ trên thanh công cụ để lưu lối tắt)"])
            hint.setDisabled(True)
            fav_group.addChild(hint)
        if fav_added > 0 or not q:
            self.launch_tree.addTopLevelItem(fav_group)
            fav_group.setExpanded(True)

        # 4) Tài Khoản Google Drive & NAS Xpenology
        acc_group = QTreeWidgetItem(["Tài Khoản & NAS"])
        acc_group.setData(0, Qt.UserRole, {"action": "group"})
        try:
            remotes = self.rclone.list_remotes(only_drive=True)
        except RcloneError:
            remotes = []
        acc_added = 0
        for rem in remotes:
            meta = self.config.get_account_meta(rem.name)
            is_nas = rem.kind != "drive"
            cat_ok = (
                cat == "__ALL__"
                or (cat == "__GDRIVE__" and not is_nas)
                or meta.category == cat
            )
            if not cat_ok:
                continue
            prefix = f"NAS ({rem.kind.upper()})" if is_nas else "GoogleDrive"
            label = f"{prefix}   {meta.label()}"
            if q and q not in f"{label} {rem.name} {meta.email} {meta.category}".lower():
                continue
            child = QTreeWidgetItem([label])
            child.setIcon(0, icons.nas_icon(16) if is_nas else icons.gdrive_icon(16))
            child.setToolTip(0, f"{meta.label()} ({rem.name}:)\n{meta.email or ''}")
            child.setData(0, Qt.UserRole, {"action": "remote", "remote": rem.name, "label": meta.label()})
            acc_group.addChild(child)
            acc_added += 1
        if acc_added == 0 and not q:
            empty = QTreeWidgetItem(["(Chưa có tài khoản nào — bấm '+ Đăng Ký Tài Khoản Google' ở trên)"])
            empty.setDisabled(True)
            acc_group.addChild(empty)
        self.launch_tree.addTopLevelItem(acc_group)
        acc_group.setExpanded(True)

    def _on_launcher_item_clicked(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        act = data.get("action")
        if act == "group":
            item.setExpanded(not item.isExpanded())
        elif act == "add_account":
            self.add_account_requested.emit()
        elif act == "local":
            self.open_local_tab()
        elif act == "remote":
            self.open_drive_tab(data["remote"], data.get("label", ""), force_new=True)
        elif act == "favourite":
            self._jump_to_favourite(data["fav"])

    def _show_launcher_menu(self, pos) -> None:
        item = self.launch_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole) or {}
        if data.get("action") == "favourite":
            menu = QMenu(self)
            menu.addAction("Mở lối tắt này").triggered.connect(lambda: self._jump_to_favourite(data["fav"]))
            menu.addAction("Xóa khỏi Favourite").triggered.connect(
                lambda: (self._remove_favourite(data["fav_idx"]), self._refresh_launcher())
            )
            menu.exec(self.launch_tree.viewport().mapToGlobal(pos))

    def _show_add_tab_menu(self) -> None:
        self.open_new_tab()

    def switch_tab(self, idx: int) -> None:
        if not (0 <= idx < len(self.tabs)):
            return
        self.active_tab_idx = idx
        self._rebuild_tab_bar()
        tab = self.tabs[idx]
        if tab.kind == "new_tab":
            self._refresh_launcher_categories()
            self._refresh_launcher()
            self.body_stack.setCurrentIndex(1)
            return
        self.body_stack.setCurrentIndex(0)
        self.cb_drive_view.setVisible(tab.kind == "drive")
        self.ed_filter.clear()
        if tab.kind == "local":
            self.lbl_quota.setText(self._local_disk_free(tab.local_path))
            self._build_local_tree()
            self._load_local_dir(tab.local_path, push_history=False)
        else:
            self._sync_drive_view_combo(tab)
            self._build_drive_tree(tab.remote)
            self._load_drive_current(push_history=False)

    # ------------------------------------------------------------ Local Explorer
    @staticmethod
    def _local_disk_free(path: str) -> str:
        try:
            usage = shutil.disk_usage(path or "C:\\")
            return f"{format_size(usage.free)} trống"
        except OSError:
            return ""

    def _build_local_tree(self) -> None:
        self.dir_tree.clear()
        home = Path.home()
        quick = [
            ("Desktop", str(home / "Desktop")),
            ("Documents", str(home / "Documents")),
            ("Downloads", str(home / "Downloads")),
            ("Videos", str(home / "Videos")),
            ("Pictures", str(home / "Pictures")),
        ]
        for name, p in quick:
            if os.path.isdir(p):
                node = QTreeWidgetItem([name])
                node.setIcon(0, icons.folder_icon("#5ea1ff", 16))
                node.setData(0, Qt.UserRole, {"kind": "local", "path": p})
                node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                self.dir_tree.addTopLevelItem(node)

        for drv in _windows_drives():
            node = QTreeWidgetItem([f"Ổ đĩa ({drv.rstrip('\\')})"])
            node.setIcon(0, icons.disk_icon(16))
            node.setData(0, Qt.UserRole, {"kind": "local", "path": drv})
            node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self.dir_tree.addTopLevelItem(node)

    def _load_local_dir(self, path: str, *, push_history: bool = True, notify_link: bool = False) -> None:
        tab = self.current_tab()
        if not tab or tab.kind != "local":
            return
        if not os.path.isdir(path):
            self.status_message.emit(f"Không tìm thấy thư mục: {path}")
            return
        if push_history and tab.local_path and tab.local_path != path:
            tab.history.append({"local_path": tab.local_path})
        tab.local_path = os.path.normpath(path)
        self.ed_path.setText(tab.local_path)
        self.lbl_quota.setText(self._local_disk_free(tab.local_path))

        entries: list[dict] = []
        try:
            with os.scandir(tab.local_path) as it:
                for sc in it:
                    if not self.config.show_hidden and sc.name.startswith("."):
                        continue
                    try:
                        st = sc.stat()
                        is_dir = sc.is_dir()
                        dt = datetime.fromtimestamp(st.st_mtime)
                        entries.append(
                            {
                                "name": sc.name,
                                "is_dir": is_dir,
                                "size": 0 if is_dir else st.st_size,
                                "mod_display": dt.strftime("%d/%m/%Y %H:%M:%S"),
                                "mod_sort": dt.strftime("%Y-%m-%d %H:%M:%S"),
                                "kind": "Thư mục" if is_dir else (f"{Path(sc.name).suffix[1:].upper()} File" if Path(sc.name).suffix else "Tệp"),
                                "id": "",
                                "full_path": sc.path,
                                "detail": "" if is_dir else probe_local_media_detail(sc.path),
                                "owner": "",
                            }
                        )
                    except OSError:
                        continue
        except OSError as exc:
            self.status_message.emit(f"Không đọc được thư mục: {exc}")

        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        self._all_entries = entries
        self._render_entries()
        if notify_link:
            self.folder_changed.emit(self.side, os.path.basename(tab.local_path))

    # ------------------------------------------------------------ Google Drive Explorer
    def _sync_drive_view_combo(self, tab: PaneTabState) -> None:
        self.cb_drive_view.blockSignals(True)
        idx = self.cb_drive_view.findData(tab.drive_mode)
        self.cb_drive_view.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_drive_view.blockSignals(False)

    @staticmethod
    def _tree_node_cache_key(remote: str, meta: dict) -> tuple:
        mode = meta.get("mode", "my_drive")
        return DriveCache.make_key(
            remote,
            mode,
            meta.get("team_drive", "") if mode == "team_drive" else "",
            meta.get("folder_id", "") if mode in ("folder_id", "shared_owner") else "",
            meta.get("rel_path", ""),
        )

    def _populate_tree_children(self, parent_node: QTreeWidgetItem, remote: str, meta: dict, dirs: list[DriveEntry]) -> None:
        parent_node.setData(0, Qt.UserRole + 1, True)
        if not dirs:
            parent_node.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            return
        tab = self.current_tab()
        expanded_set = tab.expanded_keys if (tab and tab.kind == "drive") else set()
        for d in sorted(dirs, key=lambda x: natural_sort_key(x.name)):
            c = QTreeWidgetItem([d.name])
            c.setIcon(0, icons.folder_icon("#f0be4b", 16))
            if meta.get("mode") in ("shared_with_me", "shared_owner") and not meta.get("folder_id") or meta.get("mode") == "shared_owner":
                cmeta = {
                    "kind": "drive",
                    "mode": "folder_id",
                    "folder_id": d.item_id,
                    "folder_label": d.name,
                    "rel_path": "",
                    "team_drive": "",
                }
            else:
                sub_rel = f"{meta.get('rel_path', '').strip('/')}/{d.name}".strip("/")
                cmeta = {
                    "kind": "drive",
                    "mode": meta.get("mode", "my_drive"),
                    "folder_id": meta.get("folder_id", ""),
                    "folder_label": meta.get("folder_label", ""),
                    "rel_path": sub_rel,
                    "team_drive": meta.get("team_drive", ""),
                    "team_drive_name": meta.get("team_drive_name", ""),
                }
            c.setData(0, Qt.UserRole, cmeta)
            c.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            parent_node.addChild(c)
            ckey = self._tree_node_cache_key(remote, cmeta)
            cached_sub = self.cache.get_dirs(ckey)
            if cached_sub is not None and ckey in expanded_set:
                self._populate_tree_children(c, remote, cmeta, cached_sub)
                c.setExpanded(True)

    def _restore_cached_root_expansion(self, node: QTreeWidgetItem, remote: str) -> None:
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        meta = node.data(0, Qt.UserRole) or {}
        key = self._tree_node_cache_key(remote, meta)
        cached = self.cache.get_dirs(key)
        if cached is not None and key in tab.expanded_keys:
            self._populate_tree_children(node, remote, meta, cached)
            node.setExpanded(True)

    def _build_drive_tree(self, remote: str, *, force: bool = False) -> None:
        self.dir_tree.blockSignals(True)
        self.dir_tree.clear()

        if self._is_nas_remote(remote):
            nas_node = QTreeWidgetItem([f"NAS: {self.config.get_account_meta(remote).label()}"])
            nas_node.setIcon(0, icons.nas_icon(16))
            nas_node.setData(0, Qt.UserRole, {"kind": "drive", "mode": "my_drive", "rel_path": "", "team_drive": ""})
            nas_node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self.dir_tree.addTopLevelItem(nas_node)
            self._restore_cached_root_expansion(nas_node, remote)
            self.dir_tree.blockSignals(False)
            self.lbl_quota.setText("NAS Xpenology")
            return

        my_node = QTreeWidgetItem(["Drive của tôi"])
        my_node.setIcon(0, icons.gdrive_icon(16))
        my_node.setData(0, Qt.UserRole, {"kind": "drive", "mode": "my_drive", "rel_path": "", "team_drive": ""})
        my_node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.dir_tree.addTopLevelItem(my_node)

        sh_node = QTreeWidgetItem(["Được chia sẻ với tôi"])
        sh_node.setIcon(0, icons.folder_icon("#4f8cff", 16))
        sh_node.setData(0, Qt.UserRole, {"kind": "drive", "mode": "shared_with_me", "rel_path": "", "team_drive": ""})
        sh_node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.dir_tree.addTopLevelItem(sh_node)

        cached_roots = None if force else self.cache.get_roots(remote)
        cached_owners = None if force else self.cache.get_shared_owners(remote)
        if cached_roots is not None:
            drives, about = cached_roots
            for sd in drives:
                sd_node = self._append_shared_drive_node(sd)
                self._restore_cached_root_expansion(sd_node, remote)
            if cached_owners:
                self._append_shared_owner_nodes(remote, cached_owners)
            self._restore_cached_root_expansion(my_node, remote)
            self._restore_cached_root_expansion(sh_node, remote)
            if about is not None:
                qtxt = f"{format_size(about.free)} trống" if about.total > 0 else f"Đã dùng {format_size(about.used)}"
                self.lbl_quota.setText(qtxt)
            else:
                self.lbl_quota.setText("")
            self.dir_tree.blockSignals(False)
            return

        self._restore_cached_root_expansion(my_node, remote)
        self._restore_cached_root_expansion(sh_node, remote)
        self.dir_tree.blockSignals(False)
        self.lbl_quota.setText("Đang kiểm tra dung lượng…")

        w = _DriveRootsWorker(self.rclone, remote)
        self._roots_worker = w
        self._track_worker(w)
        w.done.connect(self._on_drive_roots_loaded)
        w.err.connect(self._on_drive_roots_err)
        w.start()

    def _append_shared_drive_node(self, sd: SharedDrive) -> QTreeWidgetItem:
        node = QTreeWidgetItem([sd.name])
        node.setIcon(0, icons.gdrive_icon(16))
        node.setData(
            0,
            Qt.UserRole,
            {"kind": "drive", "mode": "team_drive", "rel_path": "", "team_drive": sd.id, "team_drive_name": sd.name},
        )
        node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.dir_tree.addTopLevelItem(node)
        return node

    def _append_shared_owner_nodes(self, remote: str, owners_map: dict[str, list[DriveEntry]]) -> None:
        """Thêm các nhánh '<tên chủ> Drive' (chủ tài khoản đã chia sẻ với mình) vào cột nhỏ kiểu 2 khung."""
        existing = {self.dir_tree.topLevelItem(i).text(0) for i in range(self.dir_tree.topLevelItemCount())}
        for owner_label, items in owners_map.items():
            key = DriveCache.make_key(remote, "shared_owner", "", owner_label, "")
            self.cache.set_entries(key, items)
            dirs = [e for e in items if e.is_dir]
            self.cache.set_dirs(key, dirs)
            if owner_label in existing:
                continue
            node = QTreeWidgetItem([owner_label])
            node.setIcon(0, icons.gdrive_icon(16))
            meta = {
                "kind": "drive",
                "mode": "shared_owner",
                "folder_id": owner_label,
                "folder_label": owner_label,
                "rel_path": "",
                "team_drive": "",
            }
            node.setData(0, Qt.UserRole, meta)
            if dirs:
                node.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            else:
                node.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            self.dir_tree.addTopLevelItem(node)
            self._restore_cached_root_expansion(node, remote)

    def _on_drive_roots_loaded(
        self,
        remote: str,
        drives: list[SharedDrive],
        about: DriveAbout | None,
        owners_map: dict[str, list[DriveEntry]] | None = None,
    ) -> None:
        self.cache.set_roots(remote, drives, about)
        if owners_map is not None:
            self.cache.set_shared_owners(remote, owners_map)
        qtxt = ""
        if about is not None:
            qtxt = f"{format_size(about.free)} trống" if about.total > 0 else f"Đã dùng {format_size(about.used)}"
        tab = self.current_tab()
        if not tab or tab.kind != "drive" or tab.remote != remote:
            return
        self.lbl_quota.setText(qtxt)
        existing_ids = set()
        for i in range(self.dir_tree.topLevelItemCount()):
            d = self.dir_tree.topLevelItem(i).data(0, Qt.UserRole) or {}
            if d.get("team_drive"):
                existing_ids.add(d["team_drive"])
        self.dir_tree.blockSignals(True)
        for sd in drives:
            if sd.id not in existing_ids:
                sd_node = self._append_shared_drive_node(sd)
                self._restore_cached_root_expansion(sd_node, remote)
        if owners_map:
            self._append_shared_owner_nodes(remote, owners_map)
        self.dir_tree.blockSignals(False)

    def _on_drive_roots_err(self, remote: str, err: str) -> None:
        tab = self.current_tab()
        if tab and tab.kind == "drive" and tab.remote == remote:
            self.lbl_quota.setText("Chưa xác thực")
            self.status_message.emit(f"[{remote}] {err}")

    def _apply_drive_entries(self, items: list[DriveEntry]) -> None:
        entries: list[dict] = []
        for e in items:
            d_disp, d_sort = _fmt_iso_date(e.mod_time)
            entries.append(
                {
                    "name": e.name,
                    "is_dir": e.is_dir,
                    "size": 0 if e.is_dir else e.size,
                    "mod_display": d_disp,
                    "mod_sort": d_sort,
                    "kind": e.kind_label,
                    "id": e.item_id,
                    "full_path": e.path,
                    "detail": getattr(e, "detail", "") or "",
                    "owner": getattr(e, "owner", "") or "",
                }
            )
        self._all_entries = entries
        self._render_entries()

    def _load_drive_current(self, *, push_history: bool = False, notify_link: bool = False, force: bool = False) -> None:
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        self.ed_path.setText(tab.display_path())
        self._sync_drive_view_combo(tab)

        key = tab.cache_key()
        if not force:
            cached = self.cache.get_entries(key)
            if cached is not None:
                self._apply_drive_entries(cached)
                if notify_link and tab.rel_path:
                    self.folder_changed.emit(self.side, tab.rel_path.strip("/").split("/")[-1])
                return
            if tab.drive_mode == "shared_owner":
                owners_map = self.cache.get_shared_owners(tab.remote)
                if owners_map is not None and tab.root_folder_id in owners_map:
                    items = owners_map.get(tab.root_folder_id, [])
                    self.cache.set_entries(key, items)
                    self._apply_drive_entries(items)
                    return

        self.file_list.clear()
        loading = QTreeWidgetItem(["Đang tải dữ liệu…", "", "", ""])
        self.file_list.addTopLevelItem(loading)
        self.lbl_count.setText("Đang tải…")

        self._req_seq += 1
        req_id = self._req_seq
        self._req_keys[req_id] = key

        if tab.drive_mode == "shared_owner":
            owner_lbl = tab.root_folder_id or tab.root_folder_label
            ow = _SharedOwnerRefreshWorker(req_id, self.rclone, tab.remote, owner_lbl)
            ow.done.connect(self._on_shared_owner_loaded)
            ow.err.connect(self._on_drive_list_err)
            self._list_worker = ow
            self._track_worker(ow)
            ow.start()
            return

        worker = _DriveListWorker(
            req_id,
            self.rclone,
            tab.remote,
            tab.rel_path,
            folder_id=tab.root_folder_id if tab.drive_mode == "folder_id" else "",
            shared_with_me=(tab.drive_mode == "shared_with_me" and not tab.root_folder_id),
            team_drive=tab.team_drive_id if tab.drive_mode == "team_drive" else "",
        )
        worker.done.connect(self._on_drive_list_done)
        worker.err.connect(self._on_drive_list_err)
        self._list_worker = worker
        self._track_worker(worker)
        worker.start()

        if notify_link and tab.rel_path:
            self.folder_changed.emit(self.side, tab.rel_path.strip("/").split("/")[-1])

    def _on_shared_owner_loaded(
        self,
        req_id: int,
        remote: str,
        owner_label: str,
        owners_map: dict[str, list[DriveEntry]],
    ) -> None:
        self._req_keys.pop(req_id, None)
        self.cache.set_shared_owners(remote, owners_map)
        for ol, its in owners_map.items():
            k = DriveCache.make_key(remote, "shared_owner", "", ol, "")
            self.cache.set_entries(k, its)
            self.cache.set_dirs(k, [e for e in its if e.is_dir])
        if req_id != self._req_seq:
            return
        self._apply_drive_entries(owners_map.get(owner_label, []))

    def _on_drive_list_done(self, req_id: int, items: list[DriveEntry]) -> None:
        key = self._req_keys.pop(req_id, None)
        if key is not None:
            self.cache.set_entries(key, items)
        if req_id != self._req_seq:
            return
        self._apply_drive_entries(items)

    def _on_drive_list_err(self, req_id: int, err: str) -> None:
        if req_id != self._req_seq:
            return
        self._all_entries = []
        self.file_list.clear()
        item = QTreeWidgetItem([f"Lỗi: {err}", "", "", ""])
        item.setForeground(0, Qt.red)
        self.file_list.addTopLevelItem(item)
        self.lbl_count.setText("Lỗi kết nối")
        self.status_message.emit(err)

    def _render_entries(self) -> None:
        q = self.ed_filter.text().strip().lower()
        self.file_list.setSortingEnabled(False)
        self.file_list.clear()
        shown = 0
        for e in self._all_entries:
            if q and q not in e["name"].lower():
                continue
            row = _SortableItem(
                [
                    e["name"],
                    "" if e["is_dir"] else format_size(e["size"]),
                    e["mod_display"],
                    e["kind"],
                    e.get("detail", ""),
                ]
            )
            row.setIcon(0, icons.file_icon(e["name"], e["is_dir"]))
            row.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            row.setData(0, Qt.UserRole, e)
            self.file_list.addTopLevelItem(row)
            shown += 1
        self.file_list.setSortingEnabled(True)
        self._update_status_counts()

    def _apply_filter(self) -> None:
        self._render_entries()

    def _update_status_counts(self) -> None:
        total = self.file_list.topLevelItemCount()
        sel = self.selected_entries()
        if sel:
            sel_size = sum(int(e.get("size") or 0) for e in sel if not e.get("is_dir"))
            extra = f" ({format_size(sel_size)})" if sel_size > 0 else ""
            self.lbl_count.setText(f"{total} mục  ·  Đang chọn {len(sel)}{extra}")
        else:
            self.lbl_count.setText(f"{total} mục")

    # ------------------------------------------------------------ Tree expand & click
    def _on_tree_expanded(self, node: QTreeWidgetItem) -> None:
        if node.data(0, Qt.UserRole + 1):
            return
        node.setData(0, Qt.UserRole + 1, True)
        meta = node.data(0, Qt.UserRole) or {}
        if meta.get("kind") == "local":
            p = meta.get("path", "")
            added = 0
            try:
                dirs = sorted(
                    (sc for sc in os.scandir(p) if sc.is_dir() and (self.config.show_hidden or not sc.name.startswith("."))),
                    key=lambda s: natural_sort_key(s.name),
                )
                for sc in dirs:
                    child = QTreeWidgetItem([sc.name])
                    child.setIcon(0, icons.folder_icon("#f0be4b", 16))
                    child.setData(0, Qt.UserRole, {"kind": "local", "path": sc.path})
                    child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                    node.addChild(child)
                    added += 1
            except OSError:
                pass
            if added == 0:
                node.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            return

        # Google Drive lazy expand (ưu tiên cache phiên chạy — 0 gọi API nếu đã tải!)
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        mode = meta.get("mode", "my_drive")
        rel = meta.get("rel_path", "")
        td = meta.get("team_drive", "")
        fid = meta.get("folder_id", "")
        key = self._tree_node_cache_key(tab.remote, meta)
        tab.expanded_keys.add(key)

        cached = self.cache.get_dirs(key)
        if cached is not None:
            self._populate_tree_children(node, tab.remote, meta, cached)
            return

        worker = _DriveListWorker(
            0,
            self.rclone,
            tab.remote,
            rel,
            folder_id=fid,
            shared_with_me=(mode == "shared_with_me" and not fid),
            team_drive=td if mode == "team_drive" else "",
            dirs_only=True,
        )

        def _done(_rid: int, dirs: list[DriveEntry], parent_node: QTreeWidgetItem = node, m=meta, k=key, rem=tab.remote) -> None:
            self.cache.set_dirs(k, dirs)
            try:
                if parent_node.treeWidget() is not None:
                    self._populate_tree_children(parent_node, rem, m, dirs)
            except RuntimeError:
                pass

        worker.done.connect(_done)
        self._track_worker(worker)
        worker.start()

    def _on_tree_collapsed(self, node: QTreeWidgetItem) -> None:
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        meta = node.data(0, Qt.UserRole) or {}
        if meta.get("kind") == "drive":
            tab.expanded_keys.discard(self._tree_node_cache_key(tab.remote, meta))

    def _on_tree_clicked(self, node: QTreeWidgetItem) -> None:
        meta = node.data(0, Qt.UserRole) or {}
        tab = self.current_tab()
        if not tab:
            return
        if meta.get("kind") == "local":
            self._load_local_dir(meta.get("path", ""), notify_link=True)
            return
        if tab.kind == "drive":
            self._push_drive_history(tab)
            tab.drive_mode = meta.get("mode", "my_drive")
            tab.rel_path = meta.get("rel_path", "")
            tab.team_drive_id = meta.get("team_drive", "")
            tab.team_drive_name = meta.get("team_drive_name", "")
            tab.root_folder_id = meta.get("folder_id", "")
            tab.root_folder_label = meta.get("folder_label", "")
            self._load_drive_current(notify_link=True)

    # ------------------------------------------------------------ Navigation
    @staticmethod
    def _push_drive_history(tab: PaneTabState) -> None:
        tab.history.append(
            {
                "drive_mode": tab.drive_mode,
                "rel_path": tab.rel_path,
                "team_drive_id": tab.team_drive_id,
                "team_drive_name": tab.team_drive_name,
                "root_folder_id": tab.root_folder_id,
                "root_folder_label": tab.root_folder_label,
            }
        )

    def _on_drive_view_picked(self, index: int) -> None:
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        val = self.cb_drive_view.itemData(index)
        if val == "paste_link":
            link, ok = QInputDialog.getText(
                self,
                "Mở thư mục Google Drive bằng Link / ID",
                "Dán link folder Google Drive (…/folders/ID) hoặc Folder ID:",
            )
            if ok and link.strip():
                fid = folder_id_from_link(link)
                if not fid:
                    QMessageBox.warning(self, "Link không hợp lệ", "Không nhận dạng được Folder ID trong đường dẫn.")
                else:
                    self._push_drive_history(tab)
                    tab.drive_mode = "folder_id"
                    tab.root_folder_id = fid
                    tab.root_folder_label = f"Link ({fid[:8]}…)"
                    tab.rel_path = ""
                    tab.team_drive_id = ""
                    self._load_drive_current()
            self._sync_drive_view_combo(tab)
            return
        self._push_drive_history(tab)
        tab.drive_mode = str(val)
        tab.rel_path = ""
        tab.team_drive_id = ""
        tab.root_folder_id = ""
        self._load_drive_current()

    def _on_path_entered(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        raw = self.ed_path.text().strip()
        if not raw:
            return
        if tab.kind == "local":
            if os.path.isdir(raw):
                self._load_local_dir(raw, notify_link=True)
            else:
                self.status_message.emit(f"Thư mục không tồn tại: {raw}")
        else:
            # Nếu người dùng dán link Google Drive vào thẳng thanh địa chỉ
            if "drive.google.com" in raw or (len(raw) >= 18 and " " not in raw and "/" not in raw and ">" not in raw):
                fid = folder_id_from_link(raw)
                if fid:
                    self._push_drive_history(tab)
                    tab.drive_mode = "folder_id"
                    tab.root_folder_id = fid
                    tab.root_folder_label = f"Link ({fid[:8]}…)"
                    tab.rel_path = ""
                    tab.team_drive_id = ""
                    self._load_drive_current()
                    return
            # Nếu nhập dạng đường dẫn tương đối
            if ">" not in raw:
                self._push_drive_history(tab)
                tab.rel_path = raw.strip("/")
                self._load_drive_current()

    def _on_file_double_clicked(self, item: QTreeWidgetItem) -> None:
        e = item.data(0, Qt.UserRole)
        if not isinstance(e, dict):
            return
        tab = self.current_tab()
        if not tab:
            return
        if tab.kind == "local":
            p = e.get("full_path", "")
            if e.get("is_dir"):
                self._load_local_dir(p, notify_link=True)
            elif p and os.path.isfile(p):
                os.startfile(p) if sys.platform == "win32" else webbrowser.open(Path(p).as_uri())
        else:
            if e.get("is_dir"):
                self._push_drive_history(tab)
                if tab.drive_mode in ("shared_with_me", "shared_owner") and e.get("id"):
                    tab.drive_mode = "folder_id"
                    tab.root_folder_id = e["id"]
                    tab.root_folder_label = e["name"]
                    tab.rel_path = ""
                else:
                    tab.rel_path = f"{tab.rel_path.strip('/')}/{e['name']}".strip("/")
                self._load_drive_current(notify_link=True)

    def try_enter_subfolder(self, folder_name: str) -> None:
        """Dùng cho chế độ khóa điều hướng (🔗) giữa 2 khung."""
        if not folder_name:
            return
        for e in self._all_entries:
            if e.get("is_dir") and e["name"].lower() == folder_name.lower():
                tab = self.current_tab()
                if not tab:
                    return
                if tab.kind == "local":
                    self._load_local_dir(e["full_path"], notify_link=False)
                else:
                    self._push_drive_history(tab)
                    if tab.drive_mode == "shared_with_me" and not tab.root_folder_id and e.get("id"):
                        tab.drive_mode = "folder_id"
                        tab.root_folder_id = e["id"]
                        tab.root_folder_label = e["name"]
                        tab.rel_path = ""
                    else:
                        tab.rel_path = f"{tab.rel_path.strip('/')}/{e['name']}".strip("/")
                    self._load_drive_current(notify_link=False)
                break

    def _go_back(self) -> None:
        tab = self.current_tab()
        if not tab or not tab.history:
            return
        prev = tab.history.pop()
        if tab.kind == "local":
            self._load_local_dir(prev["local_path"], push_history=False)
        else:
            tab.drive_mode = prev["drive_mode"]
            tab.rel_path = prev["rel_path"]
            tab.team_drive_id = prev["team_drive_id"]
            tab.team_drive_name = prev["team_drive_name"]
            tab.root_folder_id = prev["root_folder_id"]
            tab.root_folder_label = prev["root_folder_label"]
            self._load_drive_current()

    def _go_up(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        if tab.kind == "local":
            parent = os.path.dirname(tab.local_path.rstrip("\\/"))
            if parent and parent != tab.local_path:
                self._load_local_dir(parent)
        else:
            rel = tab.rel_path.strip("/")
            if rel:
                self._push_drive_history(tab)
                parts = rel.split("/")[:-1]
                tab.rel_path = "/".join(parts)
                self._load_drive_current()

    def _on_refresh_clicked(self) -> None:
        full_remote = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        self.refresh(force=True, clear_remote_cache=full_remote)

    def refresh(self, *, force: bool = True, clear_remote_cache: bool = False) -> None:
        tab = self.current_tab()
        if not tab:
            return
        if tab.kind == "local":
            self._load_local_dir(tab.local_path, push_history=False)
        else:
            if clear_remote_cache:
                self.cache.invalidate_remote(tab.remote)
                self._build_drive_tree(tab.remote, force=True)
            elif force:
                self.cache.invalidate_key(tab.cache_key())
            self._load_drive_current(force=force)

    # ------------------------------------------------------------ Selection & File Ops
    def selected_entries(self) -> list[dict]:
        out: list[dict] = []
        for it in self.file_list.selectedItems():
            d = it.data(0, Qt.UserRole)
            if isinstance(d, dict) and d.get("name"):
                out.append(d)
        return out

    def summarize_context(self, compact: bool = True) -> str:
        tab = self.current_tab()
        if not tab:
            return ""
        side_label = "Trái" if self.side == "left" else "Phải"
        if compact:
            from ..core.ai_bridge import compact_entries_summary
            return compact_entries_summary(side_label, tab.display_path(), self._all_entries)
        head = f"Khung {side_label}: {tab.display_path()} ({len(self._all_entries)} mục)"
        sample = [
            f"  - [{'DIR' if e['is_dir'] else 'FILE'}] {e['name']}" + ("" if e["is_dir"] else f" ({format_size(e['size'])})")
            for e in self._all_entries[:60]
        ]
        if len(self._all_entries) > 60:
            sample.append(f"  ... và {len(self._all_entries) - 60} mục khác")
        return "\n".join([head, *sample])

    def _mkdir(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        name, ok = QInputDialog.getText(self, "Tạo thư mục mới", "Tên thư mục:")
        name = name.strip()
        if not ok or not name:
            return
        if tab.kind == "local":
            try:
                os.makedirs(os.path.join(tab.local_path, name), exist_ok=True)
                self.refresh()
                self.status_message.emit(f"Đã tạo thư mục: {name}")
            except OSError as exc:
                QMessageBox.critical(self, "Lỗi tạo thư mục", str(exc))
        else:
            rel = f"{tab.rel_path.strip('/')}/{name}".strip("/")
            self._run_drive_op(
                lambda: self.rclone.mkdir(
                    tab.remote,
                    rel,
                    folder_id=tab.root_folder_id if tab.drive_mode == "folder_id" else "",
                    shared_with_me=(tab.drive_mode == "shared_with_me" and not tab.root_folder_id),
                    team_drive=tab.team_drive_id if tab.drive_mode == "team_drive" else "",
                ),
                f"Đã tạo thư mục trên Drive: {name}",
            )

    def _rename_selected(self) -> None:
        tab = self.current_tab()
        sel = self.selected_entries()
        if not tab or len(sel) != 1:
            self.status_message.emit("Hãy chọn đúng 1 mục để đổi tên.")
            return
        entry = sel[0]
        new_name, ok = QInputDialog.getText(self, "Đổi tên", "Tên mới:", text=entry["name"])
        new_name = new_name.strip()
        if not ok or not new_name or new_name == entry["name"]:
            return
        if tab.kind == "local":
            try:
                os.rename(entry["full_path"], os.path.join(tab.local_path, new_name))
                self.refresh()
                self.status_message.emit(f"Đã đổi tên thành: {new_name}")
            except OSError as exc:
                QMessageBox.critical(self, "Lỗi đổi tên", str(exc))
        else:
            old_rel = f"{tab.rel_path.strip('/')}/{entry['name']}".strip("/")
            new_rel = f"{tab.rel_path.strip('/')}/{new_name}".strip("/")
            self._run_drive_op(
                lambda: self.rclone.rename_item(
                    tab.remote,
                    old_rel,
                    new_rel,
                    folder_id=tab.root_folder_id if tab.drive_mode == "folder_id" else "",
                    shared_with_me=(tab.drive_mode == "shared_with_me" and not tab.root_folder_id),
                    team_drive=tab.team_drive_id if tab.drive_mode == "team_drive" else "",
                ),
                f"Đã đổi tên trên Drive thành: {new_name}",
            )

    def _delete_selected(self) -> None:
        tab = self.current_tab()
        sel = self.selected_entries()
        if not tab or not sel:
            return
        names = "\n".join(f"• {e['name']}" for e in sel[:8])
        if len(sel) > 8:
            names += f"\n… và {len(sel) - 8} mục khác"
        ans = QMessageBox.question(
            self,
            "Xác nhận xóa",
            f"Bạn có chắc muốn xóa {len(sel)} mục sau?\n\n{names}",
        )
        if ans != QMessageBox.Yes:
            return

        if tab.kind == "local":
            for e in sel:
                try:
                    if e["is_dir"]:
                        shutil.rmtree(e["full_path"])
                    else:
                        os.remove(e["full_path"])
                except OSError as exc:
                    QMessageBox.warning(self, "Lỗi xóa", f"{e['name']}: {exc}")
            self.refresh()
            self.status_message.emit(f"Đã xóa {len(sel)} mục trên máy.")
        else:
            def _do_del() -> None:
                for e in sel:
                    rel = f"{tab.rel_path.strip('/')}/{e['name']}".strip("/")
                    self.rclone.delete_item(
                        tab.remote,
                        rel,
                        bool(e["is_dir"]),
                        folder_id=tab.root_folder_id if tab.drive_mode == "folder_id" else "",
                        shared_with_me=(tab.drive_mode == "shared_with_me" and not tab.root_folder_id),
                        team_drive=tab.team_drive_id if tab.drive_mode == "team_drive" else "",
                    )

            self._run_drive_op(_do_del, f"Đã xóa {len(sel)} mục trên Google Drive.")

    def _run_drive_op(self, fn, ok_msg: str) -> None:
        self.status_message.emit("Đang thực hiện trên Google Drive…")
        w = _DriveOpWorker(fn, ok_msg)
        self._op_worker = w
        self._track_worker(w)
        w.done.connect(lambda msg: (self.status_message.emit(msg), self.refresh()))
        w.err.connect(lambda err: QMessageBox.critical(self, "Lỗi Google Drive", err))
        w.start()

    def _open_external(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        if tab.kind == "local":
            sel = self.selected_entries()
            target = sel[0]["full_path"] if sel else tab.local_path
            if sys.platform == "win32":
                if os.path.isfile(target):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
                else:
                    os.startfile(target)
        else:
            sel = self.selected_entries()
            fid = sel[0].get("id", "") if sel else tab.root_folder_id
            if fid:
                url = (
                    f"https://drive.google.com/drive/folders/{fid}"
                    if (not sel or sel[0].get("is_dir"))
                    else f"https://drive.google.com/file/d/{fid}/view"
                )
                webbrowser.open(url)
            else:
                webbrowser.open("https://drive.google.com/drive/my-drive")

    # ------------------------------------------------------------ Favourites, View Mode, Clipboard, Properties, Share
    def _apply_view_mode(self, icon_px: int, show_all_cols: bool) -> None:
        self.file_list.setIconSize(QSize(icon_px, icon_px))
        for col in (1, 2, 3, 4):
            self.file_list.setColumnHidden(col, not show_all_cols and icon_px >= 22)

    def _populate_fav_menu(self) -> None:
        self.fav_menu.clear()
        act_add = self.fav_menu.addAction("★  Lưu thư mục đang mở vào Favourite")
        act_add.triggered.connect(self.add_current_to_favourites)
        self.fav_menu.addSeparator()
        favs = self.config.favourites or []
        if not favs:
            empty = self.fav_menu.addAction("(Chưa có địa chỉ Favourite nào)")
            empty.setEnabled(False)
            return
        for idx, fav in enumerate(favs):
            ico = icons.pc_icon(15) if fav.get("kind") == "local" else icons.gdrive_icon(15)
            act = self.fav_menu.addAction(ico, fav.get("title") or fav.get("local_path") or fav.get("remote", "?"))
            act.triggered.connect(lambda _=False, f=fav: self._jump_to_favourite(f))
        self.fav_menu.addSeparator()
        del_menu = self.fav_menu.addMenu("Xóa khỏi Favourite")
        for idx, fav in enumerate(favs):
            del_menu.addAction(fav.get("title", f"Mục #{idx + 1}")).triggered.connect(
                lambda _=False, i=idx: self._remove_favourite(i)
            )

    def add_current_to_favourites(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        default_title = tab.display_path()
        title, ok = QInputDialog.getText(self, "Lưu vào Favourite (★)", "Tên gợi nhớ cho địa chỉ này:", text=default_title)
        if not ok or not title.strip():
            return
        item = {
            "title": title.strip(),
            "kind": tab.kind,
            "remote": tab.remote,
            "local_path": tab.local_path,
            "drive_mode": tab.drive_mode,
            "rel_path": tab.rel_path,
            "team_drive_id": tab.team_drive_id,
            "team_drive_name": tab.team_drive_name,
            "root_folder_id": tab.root_folder_id,
            "root_folder_label": tab.root_folder_label,
        }
        self.config.favourites.append(item)
        self.config.save()
        self.status_message.emit(f"Đã lưu vào Favourite: {title.strip()}")

    def _jump_to_favourite(self, fav: dict) -> None:
        if fav.get("kind") == "local":
            p = fav.get("local_path", "")
            tab = self.current_tab()
            if tab and tab.kind == "local":
                self._load_local_dir(p)
            else:
                self.open_local_tab(p)
            return
        rem = fav.get("remote", "")
        if not rem:
            return
        self.open_drive_tab(rem)
        tab = self.current_tab()
        if not tab or tab.kind != "drive":
            return
        self._push_drive_history(tab)
        tab.drive_mode = fav.get("drive_mode", "my_drive")
        tab.rel_path = fav.get("rel_path", "")
        tab.team_drive_id = fav.get("team_drive_id", "")
        tab.team_drive_name = fav.get("team_drive_name", "")
        tab.root_folder_id = fav.get("root_folder_id", "")
        tab.root_folder_label = fav.get("root_folder_label", "")
        self._load_drive_current()

    def _remove_favourite(self, idx: int) -> None:
        if 0 <= idx < len(self.config.favourites):
            removed = self.config.favourites.pop(idx)
            self.config.save()
            self.status_message.emit(f"Đã xóa khỏi Favourite: {removed.get('title', '')}")

    def copy_selected(self) -> None:
        self._set_clipboard("copy")

    def cut_selected(self) -> None:
        self._set_clipboard("move")

    def _set_clipboard(self, mode: str) -> None:
        tab = self.current_tab()
        sel = self.selected_entries()
        if not tab or not sel:
            self.status_message.emit("Hãy chọn file/thư mục trước khi Sao chép / Cắt.")
            return
        _INTERNAL_CLIPBOARD["side"] = self.side
        _INTERNAL_CLIPBOARD["mode"] = mode
        _INTERNAL_CLIPBOARD["entries"] = list(sel)
        if tab.kind == "local":
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(e["full_path"]) for e in sel if e.get("full_path") and os.path.exists(e["full_path"])])
            QApplication.clipboard().setMimeData(md)
        verb = "Đã Cắt (Ctrl+X)" if mode == "move" else "Đã Sao chép (Ctrl+C)"
        self.status_message.emit(f"{verb} {len(sel)} mục. Chọn khung đích rồi bấm Ctrl+V để Dán.")

    def paste_clipboard(self) -> None:
        # 1. Nếu có mục từ khung bên kia trong Rdrive
        src_side = _INTERNAL_CLIPBOARD.get("side")
        entries = _INTERNAL_CLIPBOARD.get("entries") or []
        mode = _INTERNAL_CLIPBOARD.get("mode", "copy")
        if src_side and src_side != self.side and entries:
            self.transfer_requested.emit(src_side, entries, mode)
            if mode == "move":
                _INTERNAL_CLIPBOARD["entries"] = []
            return
        # 2. Nếu người dùng bấm Ctrl+C trên file/folder ngoài Windows Explorer rồi Ctrl+V vào khung Rdrive
        md = QApplication.clipboard().mimeData()
        if md and md.hasUrls():
            paths = [os.path.normpath(u.toLocalFile()) for u in md.urls() if u.isLocalFile() and os.path.exists(u.toLocalFile())]
            if paths:
                self.external_drop_requested.emit(self.side, paths)
                return
        self.status_message.emit("Clipboard trống hoặc đang dán vào cùng khung nguồn.")

    def share_link_selected(self, entry: dict | None = None) -> None:
        tab = self.current_tab()
        if not tab:
            return
        if entry is None:
            sel = self.selected_entries()
            entry = sel[0] if sel else None
        if tab.kind == "local":
            p = entry["full_path"] if entry else tab.local_path
            QApplication.clipboard().setText(p)
            self.status_message.emit(f"Đã sao chép đường dẫn: {p}")
            return
        name = entry["name"] if entry else ""
        is_dir = bool(entry.get("is_dir")) if entry else True
        fid = (entry.get("id") if entry else tab.root_folder_id) or ""
        spec, _ = tab.rclone_spec(name)
        try:
            url = self.rclone.create_share_link(spec, fid, is_dir)
            QApplication.clipboard().setText(url)
            self.status_message.emit(f"Đã sao chép liên kết chia sẻ: {url}")
            QMessageBox.information(self, "Chia sẻ liên kết", f"Đã sao chép liên kết vào Clipboard:\n\n{url}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Chia sẻ liên kết", str(exc))

    def show_properties_selected(self, entry: dict | None = None) -> None:
        tab = self.current_tab()
        if not tab:
            return
        if entry is None:
            sel = self.selected_entries()
            entry = sel[0] if sel else None
        if entry:
            name = entry["name"]
            is_dir = bool(entry.get("is_dir"))
            size = int(entry.get("size") or 0)
            mod_disp = entry.get("mod_display", "")
            kind_lbl = entry.get("kind", "")
            fid = entry.get("id", "")
            detail = entry.get("detail", "")
            owner = entry.get("owner", "")
            spec = entry["full_path"] if tab.kind == "local" else tab.rclone_spec(name)[0]
        else:
            name = os.path.basename(tab.local_path) or tab.local_path if tab.kind == "local" else tab.display_path()
            is_dir = True
            size = 0
            mod_disp = ""
            kind_lbl = "Thư mục"
            fid = tab.root_folder_id
            detail = ""
            owner = ""
            spec = tab.local_path if tab.kind == "local" else tab.rclone_spec()[0]

        PropertiesDialog(
            self,
            self.rclone,
            name=name,
            spec=spec,
            is_local=(tab.kind == "local"),
            is_dir=is_dir,
            size=size,
            mod_display=mod_disp,
            kind_label=kind_lbl,
            item_id=fid,
            detail=detail,
            owner=owner,
        ).exec()

    def _show_tree_context_menu(self, pos) -> None:
        """Menu chuột phải ở cột cây thư mục nhỏ (DirTree — Mục #12, chuẩn)."""
        node = self.dir_tree.itemAt(pos)
        if not node:
            return
        self._on_tree_clicked(node)
        menu = QMenu(self)
        menu.addAction("Mở Rộng").triggered.connect(lambda: node.setExpanded(not node.isExpanded()))
        menu.addSeparator()
        menu.addAction("★  Lưu vào Favourite").triggered.connect(self.add_current_to_favourites)
        menu.addAction("Dán vào đây (Ctrl+V)").triggered.connect(self.paste_clipboard)
        menu.addSeparator()
        menu.addAction("Thư mục Mới…").triggered.connect(self._mkdir)
        menu.addAction("Chia sẻ liên kết…").triggered.connect(lambda: self.share_link_selected(None))
        menu.addAction("Tính Chất (Xem dung lượng)…").triggered.connect(lambda: self.show_properties_selected(None))
        menu.exec(self.dir_tree.viewport().mapToGlobal(pos))

    def _show_context_menu(self, pos) -> None:
        tab = self.current_tab()
        if not tab:
            return
        sel = self.selected_entries()
        menu = QMenu(self)
        opp = "Khung Phải" if self.side == "left" else "Khung Trái"

        if sel:
            menu.addAction("Mở").triggered.connect(lambda: self._on_file_double_clicked(self.file_list.currentItem()))
            if tab.kind == "local":
                menu.addAction("Mở trong Windows Explorer").triggered.connect(self._open_external)
            menu.addSeparator()
            menu.addAction(f"Sao chép (Upload/Download) sang {opp}").triggered.connect(
                lambda: self.transfer_requested.emit(self.side, sel, "copy")
            )
            menu.addAction(f"Di chuyển (Move) sang {opp}").triggered.connect(
                lambda: self.transfer_requested.emit(self.side, sel, "move")
            )
            menu.addSeparator()
            menu.addAction("Cắt (Ctrl+X)").triggered.connect(self.cut_selected)
            menu.addAction("Sao Chép (Ctrl+C)").triggered.connect(self.copy_selected)

        act_paste = menu.addAction("Dán (Ctrl+V)")
        act_paste.triggered.connect(self.paste_clipboard)
        menu.addSeparator()

        if sel:
            if len(sel) == 1:
                menu.addAction("Sửa Tên (F2)").triggered.connect(self._rename_selected)
            menu.addAction("Xóa (Delete)").triggered.connect(self._delete_selected)
            menu.addSeparator()
            menu.addAction("Chia sẻ liên kết…").triggered.connect(lambda: self.share_link_selected(sel[0]))
            if tab.kind == "drive":
                menu.addAction("Mở trên Trình Duyệt Web").triggered.connect(self._open_external)
            else:
                paths = [e["full_path"] for e in sel if e.get("full_path")]
                menu.addAction(icons.ribbon_icon("syau", 15), "Gửi sang SyAu → Tab Sync Audio").triggered.connect(
                    lambda: self.send_to_syau_sync.emit(paths)
                )
                menu.addAction(icons.ribbon_icon("syau", 15), "Gửi sang SyAu → Tab Converter").triggered.connect(
                    lambda: self.send_to_syau_convert.emit(paths)
                )
            menu.addSeparator()

        menu.addAction("★  Lưu thư mục hiện tại vào Favourite").triggered.connect(self.add_current_to_favourites)
        menu.addAction("Thư mục Mới (Ctrl+Shift+N)…").triggered.connect(self._mkdir)
        menu.addAction("Làm mới (F5)").triggered.connect(lambda: self.refresh(force=True))
        menu.addSeparator()
        menu.addAction("Tính Chất (Alt+Enter — Xem dung lượng)…").triggered.connect(
            lambda: self.show_properties_selected(sel[0] if sel else None)
        )
        menu.exec(self.file_list.viewport().mapToGlobal(pos))

    def export_tabs_state(self) -> list[dict]:
        """Lưu trạng thái toàn bộ các tab đang mở và đúng thư mục đang đứng (Mục #2)."""
        out: list[dict] = []
        for t in self.tabs:
            if t.kind == "new_tab":
                continue
            out.append(
                {
                    "kind": t.kind,
                    "remote": t.remote,
                    "title": t.title,
                    "local_path": t.local_path,
                    "drive_mode": t.drive_mode,
                    "rel_path": t.rel_path,
                    "team_drive_id": t.team_drive_id,
                    "team_drive_name": t.team_drive_name,
                    "root_folder_id": t.root_folder_id,
                    "root_folder_label": t.root_folder_label,
                }
            )
        return out

    def restore_tabs_state(self, raw_tabs: list[dict], active_idx: int, valid_remotes: set[str]) -> bool:
        """Khôi phục đúng các tab và thư mục đang mở trước khi tắt app."""
        restored: list[PaneTabState] = []
        for d in raw_tabs or []:
            if not isinstance(d, dict):
                continue
            kind = d.get("kind", "local")
            if kind == "local":
                lp = d.get("local_path", "")
                if lp and os.path.isdir(lp):
                    restored.append(PaneTabState(kind="local", title="This PC", local_path=lp))
            elif kind == "drive":
                rem = d.get("remote", "")
                if rem and rem in valid_remotes:
                    meta = self.config.get_account_meta(rem)
                    restored.append(
                        PaneTabState(
                            kind="drive",
                            remote=rem,
                            title=meta.label(),
                            drive_mode=d.get("drive_mode", "my_drive"),
                            rel_path=d.get("rel_path", ""),
                            team_drive_id=d.get("team_drive_id", ""),
                            team_drive_name=d.get("team_drive_name", ""),
                            root_folder_id=d.get("root_folder_id", ""),
                            root_folder_label=d.get("root_folder_label", ""),
                        )
                    )
        if not restored:
            return False
        self.tabs = restored
        self.active_tab_idx = -1
        self.switch_tab(min(max(0, active_idx), len(self.tabs) - 1))
        return True

    def shutdown(self) -> None:
        for w in list(self._live_workers):
            if w is not None and w.isRunning():
                w.wait(1500)
                if w.isRunning():
                    w.terminate()
                    w.wait(500)
