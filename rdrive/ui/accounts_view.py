"""Màn hình Quản lý Tài khoản phong cách 2 khung (Danh mục + Thẻ tài khoản + Dung lượng + Đăng xuất / Import / Export)."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AccountMeta, AppConfig
from ..core.history import HistoryStore
from ..core.logger import get_logger
from ..core.rclone_client import DriveAbout, RcloneClient, RcloneError, format_size
from . import icons
from .accounts_dialog import GoogleLoginDialog
from .nas_dialog import NasDialog

log = get_logger()


class _AccountProbeWorker(QThread):
    """Kiểm tra trạng thái đăng nhập, dung lượng (rclone about) và tên/email Google cho từng remote."""

    probed = Signal(str, str, object, str, str, str)
    # remote, status("online"|"signed_out"|"error"), DriveAbout|None, display_name, email, err_msg

    def __init__(self, rclone: RcloneClient, remotes: list[str]) -> None:
        super().__init__()
        self.rclone = rclone
        self.remotes = remotes

    def run(self) -> None:
        dump: dict = {}
        try:
            dump = self.rclone.config_dump()
        except Exception:
            dump = {}

        for name in self.remotes:
            if self.isInterruptionRequested():
                return
            sec = dump.get(name) or {}
            rtype = str(sec.get("type") or "drive").lower()
            if rtype != "drive":
                # NAS Xpenology / Synology (SMB / SFTP / WebDAV / FTP) — không dùng Google OAuth token
                try:
                    self.rclone.lsjson(name, "", dirs_only=True, timeout=12.0)
                    ab = None
                    try:
                        ab = self.rclone.about(name)
                    except Exception:
                        ab = None
                    if ab is None or ab.total <= 0:
                        try:
                            from ..core.nas_usage import nas_about
                            first = ""
                            try:
                                dirs = self.rclone.lsjson(name, "", dirs_only=True, timeout=12.0)
                                first = dirs[0].name if dirs else ""
                            except Exception:
                                pass
                            ab = nas_about(sec, first) or ab
                        except Exception:
                            pass
                    self.probed.emit(name, "online", ab, "", "", "")
                except RcloneError as exc:
                    self.probed.emit(name, "error", None, "", "", str(exc))
                continue

            tok = str(sec.get("token") or "").strip()
            if not tok or tok == "{}":
                self.probed.emit(name, "signed_out", None, "", "", "Chưa đăng nhập / đã đăng xuất")
                continue
            try:
                ab = self.rclone.about(name)
                dname, email = self.rclone.fetch_google_profile(name)
                self.probed.emit(name, "online", ab, dname, email, "")
            except RcloneError as exc:
                self.probed.emit(name, "error", None, "", "", str(exc))


class _AccountCard(QFrame):
    """Thẻ tài khoản Google Drive kiểu 2 khung: Icon + Tên + Email + Kích thước / Free + Thanh dung lượng."""

    clicked = Signal(str)
    double_clicked = Signal(str)
    context_requested = Signal(str, object)

    def __init__(
        self,
        remote: str,
        meta: AccountMeta,
        parent: QWidget | None = None,
        remote_type: str = "drive",
    ) -> None:
        super().__init__(parent)
        self.remote = remote
        self.remote_type = (remote_type or "drive").lower()
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("AccountCard")
        self.setStyleSheet(
            """
            QFrame#AccountCard {
                background: #1b1e25;
                border: 1px solid #2b303c;
                border-radius: 7px;
            }
            QFrame#AccountCard:hover {
                background: #212631;
                border-color: #3d4658;
            }
            QFrame#AccountCard[selected="true"] {
                background: #252c3a;
                border: 1px solid #ff8a3d;
            }
            """
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        ico = QLabel()
        card_icon = icons.nas_icon(30) if self.remote_type != "drive" else icons.gdrive_icon(30)
        ico.setPixmap(card_icon.pixmap(30, 30))
        ico.setFixedSize(34, 34)
        lay.addWidget(ico, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(5)

        # Hàng 1: GoogleDrive / NAS | Tên hiển thị | Email / Địa chỉ | Badge trạng thái
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        type_text = "GoogleDrive" if self.remote_type == "drive" else f"NAS ({self.remote_type.upper()})"
        lbl_type = QLabel(type_text)
        lbl_type.setStyleSheet(
            "font-weight: 700; color: #5ea1ff;" if self.remote_type != "drive" else "font-weight: 700; color: #ffffff;"
        )
        self.lbl_name = QLabel(meta.label())
        self.lbl_name.setStyleSheet("font-weight: 600; color: #e6e8ec;")
        self.lbl_email = QLabel(meta.email or f"({remote}:)")
        self.lbl_email.setStyleSheet("color: #8b93a1; font-size: 9pt;")

        self.lbl_cat = QLabel(meta.category)
        self.lbl_cat.setStyleSheet(
            "background: #262c38; color: #9db7e8; border: 1px solid #364155; "
            "border-radius: 8px; padding: 1px 7px; font-size: 8pt;"
        )
        self.lbl_cat.setVisible(bool(meta.category))
        self._capacity_gb = int(getattr(meta, "capacity_gb", 0) or 0)

        self.lbl_status = QLabel("Đang kiểm tra…")
        self.lbl_status.setObjectName("BadgeWarn")

        r1.addWidget(lbl_type)
        r1.addWidget(self.lbl_name)
        r1.addWidget(self.lbl_email)
        r1.addWidget(self.lbl_cat)
        r1.addStretch(1)
        r1.addWidget(self.lbl_status)
        body.addLayout(r1)

        # Hàng 2: Kích Thước ...  Free ...  [==== thanh dung lượng ====]
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        self.lbl_quota = QLabel("Kích Thước: —   ·   Free: —")
        self.lbl_quota.setStyleSheet("color: #aeb6c4; font-size: 8.8pt;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setFixedWidth(180)
        self.bar.setStyleSheet(
            "QProgressBar { background: #2d323e; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #4f8cff; border-radius: 3px; }"
        )
        r2.addWidget(self.lbl_quota)
        r2.addWidget(self.bar)
        r2.addStretch(1)
        body.addLayout(r2)

        lay.addLayout(body, 1)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def update_meta(self, meta: AccountMeta) -> None:
        self.lbl_name.setText(meta.label())
        self.lbl_email.setText(meta.email or f"({self.remote}:)")
        self.lbl_cat.setText(meta.category)
        self.lbl_cat.setVisible(bool(meta.category))
        self._capacity_gb = int(getattr(meta, "capacity_gb", 0) or 0)

    def update_probe(self, status: str, about: DriveAbout | None, err: str) -> None:
        if status == "online":
            self.lbl_status.setText("Đã kết nối" if self.remote_type != "drive" else "Đã đăng nhập")
            self.lbl_status.setObjectName("BadgeGood")
            cap = int(getattr(self, "_capacity_gb", 0) or 0) * 1024 ** 3
            if about and about.total <= 0 and cap > 0 and self.remote_type != "drive":
                self.lbl_quota.setText(f"Tổng {format_size(cap)} (nhập tay)")
                self.bar.setVisible(False)
            elif about and about.total > 0:
                pct = int(round(min(100, max(0, (about.used / about.total) * 100))))
                self.lbl_quota.setText(
                    f"Đã dùng {format_size(about.used)} ({pct}%)    Trống {format_size(about.free)}    Tổng {format_size(about.total)}"
                )
                self.bar.setValue(pct)
                color = "#e5484d" if pct >= 95 else ("#f5b841" if pct >= 80 else "#4f8cff")
                self.bar.setStyleSheet(
                    "QProgressBar { background: #2d323e; border: none; border-radius: 3px; }"
                    f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
                )
                self.bar.setVisible(True)
            elif about and self.remote_type == "drive":
                self.lbl_quota.setText(f"Đã dùng  {format_size(about.used)}    (Không giới hạn)")
                self.bar.setVisible(False)
            elif about:
                self.lbl_quota.setText("Đã kết nối NAS — chưa đọc được dung lượng (nhập tay trong Cấu hình NAS)")
                self.bar.setVisible(False)
            elif self.remote_type != "drive":
                self.lbl_quota.setText("Đã kết nối NAS Xpenology — sẵn sàng duyệt & đồng bộ")
                self.bar.setVisible(False)
        elif status == "signed_out":
            self.lbl_status.setText("Đã đăng xuất")
            self.lbl_status.setObjectName("BadgeWarn")
            self.lbl_quota.setText("Tài khoản đã đăng xuất — bấm 'Đăng nhập lại' để kết nối")
            self.bar.setValue(0)
            self.bar.setVisible(False)
        else:
            self.lbl_status.setText("Lỗi kết nối NAS" if self.remote_type != "drive" else "Cần đăng nhập lại")
            self.lbl_status.setObjectName("BadgeBad")
            self.lbl_quota.setText(err[:120] if err else "Không kết nối được")
            self.bar.setValue(0)
            self.bar.setVisible(False)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.remote)
        elif event.button() == Qt.RightButton:
            self.clicked.emit(self.remote)
            self.context_requested.emit(self.remote, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.remote)
        super().mouseDoubleClickEvent(event)


class AccountsView(QWidget):
    """Màn hình Tài Khoản toàn khung kiểu 2 khung (Sidebar danh mục + Thẻ tài khoản + Bảng thao tác/Đăng xuất)."""

    accounts_changed = Signal()
    account_removed_or_signed_out = Signal(str)     # remote_name -> để Explorer đóng tab đang mở
    open_remote_requested = Signal(str, str)        # remote_name, "left" | "right"
    status_message = Signal(str)

    def __init__(
        self,
        rclone: RcloneClient,
        config: AppConfig,
        history: HistoryStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self.history = history

        self._remotes: list[str] = []
        self._remote_types: dict[str, str] = {}
        self._cards: dict[str, _AccountCard] = {}
        self._probe_cache: dict[str, tuple[str, DriveAbout | None, str]] = {}
        self._selected_remote: str = ""
        self._active_category: str = "__ALL__"
        self._live_workers: list[QThread] = []

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Thanh công cụ con phía trên (kiểu 2 khung: Add Category | Add account | Tìm Kiếm | Import | Export)
        bar = QFrame()
        bar.setObjectName("NavBar")
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(8, 6, 10, 6)
        blay.setSpacing(8)

        btn_add_cat = QToolButton()
        btn_add_cat.setObjectName("IconBtn")
        btn_add_cat.setText("+ Thêm danh mục")
        btn_add_cat.clicked.connect(self._add_category)

        btn_add_acc = QPushButton("+ Thêm tài khoản Google")
        btn_add_acc.setObjectName("PrimaryBtn")
        btn_add_acc.setIcon(icons.gdrive_icon(16))
        btn_add_acc.setStyleSheet("padding: 5px 12px;")
        btn_add_acc.clicked.connect(self.add_account)

        btn_add_nas = QPushButton("+ Thêm NAS Xpenology")
        btn_add_nas.setIcon(icons.nas_icon(16))
        btn_add_nas.setStyleSheet("padding: 5px 12px;")
        btn_add_nas.clicked.connect(self.add_nas)

        btn_refresh = QToolButton()
        btn_refresh.setObjectName("IconBtn")
        btn_refresh.setText("↻ Kiểm tra lại")
        btn_refresh.clicked.connect(self.reload_accounts)

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Tìm Kiếm tài khoản / email...")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMaximumWidth(250)
        self.ed_search.textChanged.connect(self._apply_filter)

        btn_import = QToolButton()
        btn_import.setObjectName("IconBtn")
        btn_import.setText("⇩ Import")
        btn_import.setToolTip("Nhập danh sách tài khoản từ file JSON đã xuất")
        btn_import.clicked.connect(self._import_accounts)

        btn_export = QToolButton()
        btn_export.setObjectName("IconBtn")
        btn_export.setText("⇧ Export")
        btn_export.setToolTip("Xuất sao lưu danh sách tài khoản (rclone + ghi chú)")
        btn_export.clicked.connect(self._export_accounts)

        blay.addWidget(btn_add_cat)
        blay.addWidget(btn_add_acc)
        blay.addWidget(btn_add_nas)
        blay.addWidget(btn_refresh)
        blay.addStretch(1)
        blay.addWidget(self.ed_search)
        blay.addWidget(btn_import)
        blay.addWidget(btn_export)
        root.addWidget(bar)

        # 3 cột kiểu 2 khung: [Sidebar danh mục] | [Danh sách thẻ tài khoản] | [Chi tiết & Đăng xuất]
        split = QSplitter(Qt.Horizontal)

        # Cột 1: Danh mục
        self.cat_list = QListWidget()
        self.cat_list.setStyleSheet(
            "QListWidget { background: #15181e; border: none; border-right: 1px solid #2d323e; padding: 6px; }"
            "QListWidget::item { padding: 7px 8px; border-radius: 5px; }"
            "QListWidget::item:selected { background: #26344d; color: #ffffff; }"
            "QListWidget::item:hover { background: #1f2430; }"
        )
        self.cat_list.currentItemChanged.connect(self._on_category_changed)
        self.cat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._category_menu)
        split.addWidget(self.cat_list)

        # Cột 2: Danh sách thẻ tài khoản (ScrollArea)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #14161b; border: none; }")
        self.cards_host = QWidget()
        self.cards_host.setObjectName("CardsHost")
        self.cards_host.setStyleSheet("QWidget#CardsHost { background: #14161b; }")
        self.cards_lay = QVBoxLayout(self.cards_host)
        self.cards_lay.setContentsMargins(12, 12, 12, 12)
        self.cards_lay.setSpacing(8)
        self.cards_lay.addStretch(1)
        scroll.setWidget(self.cards_host)
        split.addWidget(scroll)

        # Cột 3: Chi tiết & Thao tác tài khoản đang chọn
        self.detail_box = QFrame()
        self.detail_box.setStyleSheet("QFrame { background: #171a21; border-left: 1px solid #2d323e; }")
        dlay = QVBoxLayout(self.detail_box)
        dlay.setContentsMargins(16, 16, 16, 16)
        dlay.setSpacing(10)

        self.d_title = QLabel("Chọn 1 tài khoản")
        self.d_title.setStyleSheet("font-size: 12pt; font-weight: 700; border: none;")
        self.d_email = QLabel("Bấm vào thẻ tài khoản bên trái để xem chi tiết hoặc đăng xuất.")
        self.d_email.setObjectName("Muted")
        self.d_email.setWordWrap(True)
        self.d_email.setStyleSheet("border: none; color: #8b93a1;")
        dlay.addWidget(self.d_title)
        dlay.addWidget(self.d_email)

        form = QFormLayout()
        form.setSpacing(6)
        self.d_remote = QLabel("—")
        self.d_status = QLabel("—")
        self.d_total = QLabel("—")
        self.d_used = QLabel("—")
        self.d_free = QLabel("—")
        for lbl in (self.d_remote, self.d_status, self.d_total, self.d_used, self.d_free):
            lbl.setStyleSheet("border: none;")
        self.cb_cat = QComboBox()
        self.cb_cat.activated.connect(self._assign_category_from_combo)

        form.addRow("Remote:", self.d_remote)
        form.addRow("Trạng thái:", self.d_status)
        form.addRow("Danh mục:", self.cb_cat)
        form.addRow("Tổng dung lượng:", self.d_total)
        form.addRow("Đã sử dụng:", self.d_used)
        form.addRow("Còn trống:", self.d_free)
        dlay.addLayout(form)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background: #2d323e; max-height: 1px; border: none;")
        dlay.addWidget(sep)

        open_row = QHBoxLayout()
        self.btn_open_left = QPushButton("Mở Khung Trái")
        self.btn_open_right = QPushButton("Mở Khung Phải")
        self.btn_open_left.clicked.connect(lambda: self._open_selected("left"))
        self.btn_open_right.clicked.connect(lambda: self._open_selected("right"))
        open_row.addWidget(self.btn_open_left)
        open_row.addWidget(self.btn_open_right)
        dlay.addLayout(open_row)

        self.btn_relogin = QPushButton("Đăng nhập / Đăng nhập lại (Google)")
        self.btn_relogin.setObjectName("PrimaryBtn")
        self.btn_relogin.setIcon(icons.gdrive_icon(16))
        self.btn_relogin.clicked.connect(self._reconnect_selected)
        dlay.addWidget(self.btn_relogin)

        self.btn_signout = QPushButton("Đăng xuất tài khoản (Sign Out)")
        self.btn_signout.setToolTip("Thu hồi phiên đăng nhập Google nhưng giữ lại thẻ tài khoản để đăng nhập lại sau")
        self.btn_signout.clicked.connect(self._signout_selected)
        dlay.addWidget(self.btn_signout)

        self.btn_edit = QPushButton("Sửa tên hiển thị / Email")
        self.btn_edit.clicked.connect(self._edit_selected)
        dlay.addWidget(self.btn_edit)

        self.btn_delete = QPushButton("Xóa hẳn tài khoản khỏi máy")
        self.btn_delete.setObjectName("DangerBtn")
        self.btn_delete.clicked.connect(self._delete_selected)
        dlay.addWidget(self.btn_delete)

        dlay.addStretch(1)

        self.lbl_conf = QLabel("")
        self.lbl_conf.setWordWrap(True)
        self.lbl_conf.setStyleSheet("color: #7c8594; font-size: 8pt; border: none;")
        dlay.addWidget(self.lbl_conf)

        split.addWidget(self.detail_box)
        split.setSizes([195, 760, 310])
        root.addWidget(split, 1)

        self._set_detail_enabled(False)

    # ------------------------------------------------------------ Categories
    def _rebuild_categories(self) -> None:
        self.cat_list.blockSignals(True)
        self.cat_list.clear()

        it_all = QListWidgetItem(icons.ribbon_icon("accounts", 16), "Tất Cả")
        it_all.setData(Qt.UserRole, "__ALL__")
        self.cat_list.addItem(it_all)

        it_gd = QListWidgetItem(icons.gdrive_icon(16), "GoogleDrive")
        it_gd.setData(Qt.UserRole, "__GDRIVE__")
        self.cat_list.addItem(it_gd)

        for cat in self.config.categories:
            it = QListWidgetItem(icons.folder_icon("#f0be4b", 15), cat)
            it.setData(Qt.UserRole, cat)
            self.cat_list.addItem(it)

        # Chọn lại danh mục đang active
        picked = 0
        for i in range(self.cat_list.count()):
            if self.cat_list.item(i).data(Qt.UserRole) == self._active_category:
                picked = i
                break
        self.cat_list.setCurrentRow(picked)
        self.cat_list.blockSignals(False)

        # Cập nhật dropdown gán danh mục bên cột chi tiết
        self.cb_cat.blockSignals(True)
        self.cb_cat.clear()
        self.cb_cat.addItem("(Không phân loại)", "")
        for cat in self.config.categories:
            self.cb_cat.addItem(cat, cat)
        self.cb_cat.blockSignals(False)

    def _add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Thêm danh mục", "Tên danh mục (VD: Công Ty, Backup, Cá Nhân):")
        name = name.strip()
        if not ok or not name:
            return
        if name not in self.config.categories:
            self.config.categories.append(name)
            self.config.save()
        self._rebuild_categories()

    def _category_menu(self, pos) -> None:
        it = self.cat_list.itemAt(pos)
        if not it:
            return
        val = it.data(Qt.UserRole)
        if val in ("__ALL__", "__GDRIVE__"):
            return
        menu = QMenu(self)
        act_del = menu.addAction(f"Xóa danh mục '{val}'")
        act_del.triggered.connect(lambda: self._delete_category(val))
        menu.exec(self.cat_list.mapToGlobal(pos))

    def _delete_category(self, cat: str) -> None:
        if cat in self.config.categories:
            self.config.categories.remove(cat)
            for rem in list(self.config.accounts.keys()):
                meta = self.config.get_account_meta(rem)
                if meta.category == cat:
                    meta.category = ""
                    self.config.set_account_meta(meta)
            self.config.save()
            self._active_category = "__ALL__"
            self._rebuild_categories()
            self._apply_filter()

    def _on_category_changed(self, cur: QListWidgetItem | None) -> None:
        self._active_category = cur.data(Qt.UserRole) if cur else "__ALL__"
        self._apply_filter()

    def _assign_category_from_combo(self, index: int) -> None:
        if not self._selected_remote:
            return
        cat = str(self.cb_cat.itemData(index) or "")
        meta = self.config.get_account_meta(self._selected_remote)
        meta.category = cat
        self.config.set_account_meta(meta)
        self.config.save()
        card = self._cards.get(self._selected_remote)
        if card:
            card.update_meta(meta)
        self._apply_filter()

    # ------------------------------------------------------------ Load & Probe
    def reload_accounts(self) -> None:
        self._rebuild_categories()
        try:
            rem_objs = self.rclone.list_remotes(only_drive=True)
            remotes = [r.name for r in rem_objs]
            self._remote_types = {r.name: r.kind for r in rem_objs}
            self.lbl_conf.setText(f"rclone.conf: {self.rclone.config_file()}")
        except RcloneError as exc:
            remotes = []
            self._remote_types = {}
            self.lbl_conf.setText(f"Lỗi rclone: {exc}")

        self._remotes = remotes
        self._cards.clear()

        while self.cards_lay.count():
            item = self.cards_lay.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        if not remotes:
            empty = QLabel("Chưa có tài khoản Google Drive nào.\nBấm '+ Thêm tài khoản Google' phía trên để đăng nhập.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #8b93a1; padding: 40px;")
            self.cards_lay.addWidget(empty)
            self.cards_lay.addStretch(1)
            self._selected_remote = ""
            self._set_detail_enabled(False)
            return

        for rem in remotes:
            meta = self.config.get_account_meta(rem)
            rtype = self._remote_types.get(rem, "drive")
            card = _AccountCard(rem, meta, self.cards_host, remote_type=rtype)
            card.clicked.connect(self._select_remote)
            card.double_clicked.connect(lambda r=rem: self.open_remote_requested.emit(r, "right"))
            card.context_requested.connect(self._show_card_menu)
            if rem in self._probe_cache:
                st, ab, err = self._probe_cache[rem]
                card.update_probe(st, ab, err)
            self._cards[rem] = card
            self.cards_lay.addWidget(card)
        self.cards_lay.addStretch(1)

        if self._selected_remote not in self._cards:
            self._select_remote(remotes[0])
        else:
            self._select_remote(self._selected_remote)
        self._apply_filter()

        # Chạy probe nền an toàn
        w = _AccountProbeWorker(self.rclone, list(remotes))
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.probed.connect(self._on_probed)
        w.start()

    def _on_probed(self, remote: str, status: str, about: DriveAbout | None, dname: str, email: str, err: str) -> None:
        self._probe_cache[remote] = (status, about, err)
        meta = self.config.get_account_meta(remote)
        changed = False
        if dname and (not meta.display_name or meta.display_name == remote):
            meta.display_name = dname
            changed = True
        if email and not meta.email:
            meta.email = email
            changed = True
        if changed:
            self.config.set_account_meta(meta)
            self.config.save()
            self.accounts_changed.emit()

        card = self._cards.get(remote)
        if card:
            card.update_meta(meta)
            card.update_probe(status, about, err)
        if self._selected_remote == remote:
            self._refresh_detail()

    def _apply_filter(self) -> None:
        q = self.ed_search.text().strip().lower()
        for rem, card in self._cards.items():
            meta = self.config.get_account_meta(rem)
            rtype = self._remote_types.get(rem, "drive")
            cat_ok = (
                self._active_category == "__ALL__"
                or (self._active_category == "__GDRIVE__" and rtype == "drive")
                or meta.category == self._active_category
            )
            txt_ok = not q or (q in f"{rem} {meta.display_name} {meta.email} {meta.category} {rtype}".lower())
            card.setVisible(cat_ok and txt_ok)

    # ------------------------------------------------------------ Selection & Detail
    def _set_detail_enabled(self, on: bool) -> None:
        for b in (self.btn_open_left, self.btn_open_right, self.btn_relogin, self.btn_signout, self.btn_edit, self.btn_delete, self.cb_cat):
            b.setEnabled(on)

    def _select_remote(self, remote: str) -> None:
        self._selected_remote = remote
        for r, card in self._cards.items():
            card.set_selected(r == remote)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        rem = self._selected_remote
        if not rem or rem not in self._cards:
            self._set_detail_enabled(False)
            return
        self._set_detail_enabled(True)
        meta = self.config.get_account_meta(rem)
        self.d_title.setText(meta.label())
        self.d_email.setText(meta.email or "(Chưa có email — tự lấy khi đăng nhập)")
        self.d_remote.setText(f"{rem}:")

        self.cb_cat.blockSignals(True)
        idx = self.cb_cat.findData(meta.category)
        self.cb_cat.setCurrentIndex(max(0, idx))
        self.cb_cat.blockSignals(False)

        st, ab, err = self._probe_cache.get(rem, ("checking", None, ""))
        is_nas = self._remote_types.get(rem, "drive") != "drive"
        if is_nas:
            self.btn_relogin.setText("Cấu hình / Kết nối lại NAS")
            self.btn_relogin.setIcon(icons.nas_icon(16))
            self.btn_signout.setVisible(False)
        else:
            self.btn_relogin.setText("Đăng nhập / Đăng nhập lại (Google)")
            self.btn_relogin.setIcon(icons.gdrive_icon(16))
            self.btn_signout.setVisible(True)

        if st == "online":
            self.d_status.setText("Đã kết nối NAS" if is_nas else "Đã đăng nhập")
            self.d_status.setStyleSheet("color: #3ecf8e; font-weight: 600; border: none;")
            self.btn_signout.setEnabled(not is_nas)
            self.btn_open_left.setEnabled(True)
            self.btn_open_right.setEnabled(True)
            if ab:
                self.d_total.setText(format_size(ab.total) if ab.total > 0 else ("Chưa rõ" if is_nas else "Không giới hạn"))
                self.d_used.setText(format_size(ab.used))
                self.d_free.setText(format_size(ab.free) if ab.total > 0 else "—")
            elif is_nas:
                self.d_total.setText("NAS LAN")
                self.d_used.setText("—")
                self.d_free.setText("Sẵn sàng")
        elif st == "signed_out":
            self.d_status.setText("Đã đăng xuất")
            self.d_status.setStyleSheet("color: #f5b841; font-weight: 600; border: none;")
            self.btn_signout.setEnabled(False)
            self.btn_open_left.setEnabled(False)
            self.btn_open_right.setEnabled(False)
            self.d_total.setText("—")
            self.d_used.setText("—")
            self.d_free.setText("—")
        elif st == "error":
            self.d_status.setText("Lỗi kết nối NAS" if is_nas else "Cần đăng nhập lại")
            self.d_status.setStyleSheet("color: #ff5d6c; font-weight: 600; border: none;")
            self.btn_signout.setEnabled(not is_nas)
            self.btn_open_left.setEnabled(is_nas)
            self.btn_open_right.setEnabled(is_nas)
            self.d_total.setText("—")
            self.d_used.setText("—")
            self.d_free.setText(err[:90] if err else "—")
        else:
            self.d_status.setText("Đang kiểm tra…")
            self.d_status.setStyleSheet("color: #8b93a1; border: none;")

    # ------------------------------------------------------------ Account Actions
    def add_nas(self) -> None:
        dlg = NasDialog(self, self.rclone, self.config)
        if dlg.exec() == NasDialog.Accepted:
            if dlg.created_remote:
                self.reload_accounts()
                self._select_remote(dlg.created_remote)
                self.accounts_changed.emit()
                self.status_message.emit(f"Đã kết nối NAS Xpenology: {dlg.created_remote}")
            elif dlg.unc_path:
                self.open_remote_requested.emit(f"__UNC__{dlg.unc_path}", "left")

    def add_account(self) -> None:
        try:
            existing = {r.name for r in self.rclone.list_remotes(only_drive=False)}
        except RcloneError as exc:
            QMessageBox.critical(self, "Lỗi rclone", str(exc))
            return
        dlg = GoogleLoginDialog(self, self.rclone, self.config, existing)
        if dlg.exec() == GoogleLoginDialog.Accepted and dlg.created_remote:
            rem = dlg.created_remote
            if self.history is not None:
                self.history.record("login", f"Đăng nhập Google Drive: {rem}", status="ok")
            log.info("ACCOUNT LOGIN: %s", rem)
            self._probe_cache.pop(rem, None)
            self.reload_accounts()
            self._select_remote(rem)
            self.accounts_changed.emit()
            self.status_message.emit(f"Đã thêm & đăng nhập tài khoản Google: {rem}")

    def _reconnect_selected(self) -> None:
        rem = self._selected_remote
        if not rem:
            return
        if self._remote_types.get(rem, "drive") != "drive":
            dlg = NasDialog(self, self.rclone, self.config, edit_remote=rem)
            if dlg.exec() == NasDialog.Accepted:
                self._probe_cache.pop(rem, None)
                self.reload_accounts()
                self._select_remote(rem)
                self.accounts_changed.emit()
                self.status_message.emit(f"Đã cập nhật kết nối NAS: {rem}")
            return
        dlg = GoogleLoginDialog(self, self.rclone, self.config, set(self._remotes), reconnect_remote=rem)
        if dlg.exec() == GoogleLoginDialog.Accepted:
            if self.history is not None:
                self.history.record("login", f"Đăng nhập lại Google Drive: {rem}", status="ok")
            log.info("ACCOUNT RELOGIN: %s", rem)
            self._probe_cache.pop(rem, None)
            self.reload_accounts()
            self.accounts_changed.emit()
            self.status_message.emit(f"Đã đăng nhập lại tài khoản: {rem}")

    def _signout_selected(self) -> None:
        rem = self._selected_remote
        if not rem:
            return
        meta = self.config.get_account_meta(rem)
        ans = QMessageBox.question(
            self,
            "Đăng xuất tài khoản Google",
            f"Đăng xuất tài khoản '{meta.label()}' ({rem}:)?\n\n"
            "• Phiên đăng nhập OAuth sẽ được thu hồi ngay lập tức.\n"
            "• Thẻ tài khoản vẫn được giữ lại để bạn bấm 'Đăng nhập lại' bất cứ lúc nào.\n"
            "• Dữ liệu trên Google Drive KHÔNG bị ảnh hưởng.",
        )
        if ans != QMessageBox.Yes:
            return
        try:
            self.rclone.disconnect_remote(rem)
            self._probe_cache[rem] = ("signed_out", None, "Đã đăng xuất")
            if self.history is not None:
                self.history.record("login", f"Đăng xuất Google Drive: {meta.label()} ({rem})", status="ok")
            log.info("ACCOUNT SIGN OUT: %s", rem)
            self.account_removed_or_signed_out.emit(rem)
            self.accounts_changed.emit()
            card = self._cards.get(rem)
            if card:
                card.update_probe("signed_out", None, "")
            self._refresh_detail()
            self.status_message.emit(f"Đã đăng xuất tài khoản: {meta.label()}")
        except RcloneError as exc:
            QMessageBox.critical(self, "Lỗi đăng xuất", str(exc))

    def _edit_selected(self) -> None:
        rem = self._selected_remote
        if not rem:
            return
        meta = self.config.get_account_meta(rem)
        name, ok = QInputDialog.getText(self, "Sửa tên hiển thị", f"Tên hiển thị cho '{rem}':", text=meta.display_name or rem)
        if not ok:
            return
        email, ok2 = QInputDialog.getText(self, "Sửa Email / Ghi chú", f"Email hoặc ghi chú cho '{rem}':", text=meta.email)
        if not ok2:
            return
        meta.display_name = name.strip()
        meta.email = email.strip()
        self.config.set_account_meta(meta)
        self.config.save()
        card = self._cards.get(rem)
        if card:
            card.update_meta(meta)
        self._refresh_detail()
        self.accounts_changed.emit()

    def _delete_selected(self) -> None:
        rem = self._selected_remote
        if not rem:
            return
        meta = self.config.get_account_meta(rem)
        ans = QMessageBox.question(
            self,
            "Xóa hẳn tài khoản",
            f"Xóa hẳn tài khoản '{meta.label()}' ({rem}:) khỏi rclone.conf?\n"
            "(Chỉ xóa kết nối trên máy này, KHÔNG xóa dữ liệu trên Google Drive.)",
        )
        if ans != QMessageBox.Yes:
            return
        try:
            self.rclone.delete_remote(rem)
            self.config.remove_account_meta(rem)
            self.config.save()
            self._probe_cache.pop(rem, None)
            if self.history is not None:
                self.history.record("login", f"Xóa tài khoản khỏi máy: {meta.label()} ({rem})", status="ok")
            log.info("ACCOUNT DELETE: %s", rem)
            self.account_removed_or_signed_out.emit(rem)
            self.reload_accounts()
            self.accounts_changed.emit()
            self.status_message.emit(f"Đã xóa tài khoản: {meta.label()}")
        except RcloneError as exc:
            QMessageBox.critical(self, "Lỗi xóa tài khoản", str(exc))

    def _open_selected(self, side: str) -> None:
        if self._selected_remote:
            self.open_remote_requested.emit(self._selected_remote, side)

    def _show_card_menu(self, remote: str, global_pos) -> None:
        self._select_remote(remote)
        menu = QMenu(self)
        menu.addAction("Mở ở Khung Phải").triggered.connect(lambda: self.open_remote_requested.emit(remote, "right"))
        menu.addAction("Mở ở Khung Trái").triggered.connect(lambda: self.open_remote_requested.emit(remote, "left"))
        menu.addSeparator()
        menu.addAction("Đăng nhập / Đăng nhập lại…").triggered.connect(self._reconnect_selected)
        menu.addAction("Đăng xuất (Sign Out)").triggered.connect(self._signout_selected)
        menu.addAction("Sửa tên / Email…").triggered.connect(self._edit_selected)
        menu.addSeparator()
        menu.addAction("Xóa hẳn tài khoản").triggered.connect(self._delete_selected)
        menu.exec(global_pos)

    # ------------------------------------------------------------ Import / Export
    def _export_accounts(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Xuất cấu hình tài khoản Rdrive", "rdrive_accounts.json", "JSON (*.json)")
        if not path:
            return
        try:
            dump = self.rclone.config_dump()
            drive_remotes = {k: v for k, v in dump.items() if isinstance(v, dict) and v.get("type") == "drive"}
            payload = {
                "app": "Rdrive",
                "categories": self.config.categories,
                "accounts_meta": self.config.accounts,
                "rclone_drive": drive_remotes,
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            self.status_message.emit(f"Đã xuất {len(drive_remotes)} tài khoản ra {path}")
            QMessageBox.information(self, "Export thành công", f"Đã xuất {len(drive_remotes)} tài khoản Google Drive.\n(Lưu ý: file chứa token đăng nhập, hãy bảo mật cẩn thận!)")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Lỗi Export", str(exc))

    def _import_accounts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Nhập cấu hình tài khoản Rdrive", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            for c in payload.get("categories") or []:
                if c and c not in self.config.categories:
                    self.config.categories.append(c)
            for k, v in (payload.get("accounts_meta") or {}).items():
                self.config.accounts[k] = v
            self.config.save()

            existing = {r.name for r in self.rclone.list_remotes(only_drive=False)}
            imported = 0
            for name, params in (payload.get("rclone_drive") or {}).items():
                if name not in existing and isinstance(params, dict):
                    self.rclone.import_drive_remote(name, params)
                    imported += 1
            self.reload_accounts()
            self.accounts_changed.emit()
            self.status_message.emit(f"Đã nhập {imported} tài khoản mới từ {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Lỗi Import", str(exc))

    def shutdown(self) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.requestInterruption()
                w.wait(1200)
                if w.isRunning():
                    w.terminate()
                    w.wait(400)
