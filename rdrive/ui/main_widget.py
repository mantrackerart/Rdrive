"""Widget chính của Rdrive (dùng chung cho cả bản độc lập và khi nhúng vào Tab 3 của SyAu)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core.config import AppConfig, ROOT_DIR
from ..core.drive_cache import DriveCache
from ..core.history import HistoryStore
from ..core.logger import get_logger, log_dir_path
from ..core.rclone_client import RcloneClient, RcloneError
from ..core.syau_bridge import SyAuBridge
from ..core.transfer_manager import TransferManager

log = get_logger()
from . import icons, theme
from .accounts_dialog import GoogleLoginDialog
from .accounts_view import AccountsView
from .ai_dialog import AIDialog
from .auto_dialog import AutoDialog
from .changelog_dialog import ChangelogDialog, ensure_changelog_file
from .explorer_pane import ExplorerPane
from .keymap_dialog import KeymapDialog, get_hotkey
from .mount_dialog import MountDialog, stop_all_mounts
from .search_view import SearchView
from .settings_dialog import SettingsDialog
from .speedtest_view import SpeedtestView
from .sync_dialog import SyncDialog
from .transfer_panel import TransferPanel


class RdriveWidget(QWidget):
    """Giao diện 2 khung phong cách 2 khung chạy trên nền rclone.

    Có thể dùng độc lập trong `rdrive.app.MainWindow` hoặc nhúng trực tiếp làm Tab 3 của SyAu.
    """

    status = Signal(str)
    _update_found = Signal(object, bool, str)   # (UpdateInfo|None, silent, loi)
    _syau_ping = Signal(object)          # ket qua ping SyAu tu luong nen

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        embedded_in_syau: bool = False,
        on_send_sync: Callable[[list[str]], None] | None = None,
        on_send_convert: Callable[[list[str]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RdriveRoot")
        self.setStyleSheet(theme.QSS)

        self.embedded_in_syau = embedded_in_syau
        self.config = AppConfig.load()
        self.history = HistoryStore(self)
        self.drive_cache = DriveCache()
        self.rclone = RcloneClient(self.config.rclone_path)
        self.rclone.use_rcd = bool(getattr(self.config, "use_rcd", True))
        self.transfers = TransferManager(self.rclone, self.config, self.history, self)
        self.syau = SyAuBridge(
            server_url=self.config.syau_server_url,
            on_send_sync=on_send_sync,
            on_send_convert=on_send_convert,
            embedded=embedded_in_syau,
        )
        self._link_nav = False
        self._shortcuts: list[QShortcut] = []
        ensure_changelog_file()

        self._build_ui()
        self._bind_shortcuts()
        QTimer.singleShot(80, self._bootstrap)

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1) Top Ribbon Bar (giống thanh công cụ trên cùng của app 2 khung)
        ribbon = QFrame()
        ribbon.setObjectName("RibbonBar")
        rlay = QHBoxLayout(ribbon)
        rlay.setContentsMargins(8, 5, 10, 5)
        rlay.setSpacing(4)

        self.btn_explorer = self._ribbon_btn("Khám Phá", "explorer", "Quay lại giao diện duyệt 2 khung (Trái / Phải)")
        self.btn_explorer.setCheckable(True)
        self.btn_explorer.setChecked(True)

        self.btn_accounts = self._ribbon_btn("Tài Khoản", "accounts", "Quản lý, đăng nhập & đăng xuất nhiều tài khoản Google Drive")
        self.btn_accounts.setCheckable(True)

        self.btn_search = self._ribbon_btn("Tìm Kiếm", "explorer", "Tìm file siêu nhanh trong Google Drive chỉ định")
        self.btn_search.setCheckable(True)

        self.btn_speedtest = self._ribbon_btn("Speedtest", "sync", "Đo tốc độ mạng từ Speedtest.net & Đa luồng 16 luồng IDM, tự lưu log so sánh")
        self.btn_speedtest.setCheckable(True)

        self.btn_sync = self._ribbon_btn("Đồng bộ", "sync", "So sánh (Compare) & Đồng bộ / Backup chuẩn")
        self.btn_mount = self._ribbon_btn("Mount Ổ Đĩa", "explorer", "Gắn Cloud / Thư mục bất kỳ thành ổ đĩa Windows & quản lý chạy ngầm")
        self.btn_auto = self._ribbon_btn("Auto / Lịch", "auto", "Tự động backup & sắp xếp thư mục (Scheduler)")
        self.btn_ai = self._ribbon_btn("AI Quản Lý", "ai", "Liên kết Local AI (Ollama/LM Studio) hoặc Cloud AI (Gemini/OpenAI)")
        self.btn_settings = self._ribbon_btn("Tùy Chọn", "settings", "Cấu hình tốc độ, Logs, Proxy, Phím tắt, rclone & SyAu")
        self.btn_changelog = self._ribbon_btn("📋 Biên bản", "explorer", "Xem Biên bản cập nhật & Tài liệu bàn giao cho AI Agent (CHANGELOG_AGENT.md)")
        self.btn_update = self._ribbon_btn("⟳ Cập nhật", "sync", "Kiểm tra bản Rdrive mới trên GitHub và tự cập nhật")

        self.btn_view = self._ribbon_btn("Xem ▾", "explorer", "Chế độ 1 khung / 2 khung, Hàng đợi, Cửa sổ mới, Logs")
        view_menu = QMenu(self)
        view_menu.addAction("Một Ô Cửa (Chỉ Khung Phải / Trái)").triggered.connect(lambda: self._set_dual_pane(False))
        view_menu.addAction("Hai Ô Cửa (Mặc định)").triggered.connect(lambda: self._set_dual_pane(True))
        view_menu.addAction("⚖  Chia đều 2 khung Trái / Phải (50 / 50)").triggered.connect(self.balance_panes)
        view_menu.addSeparator()
        self.act_toggle_queue = view_menu.addAction("Hiện Hàng đợi & Lịch sử")
        self.act_toggle_queue.setCheckable(True)
        self.act_toggle_queue.setChecked(True)
        self.act_toggle_queue.toggled.connect(self._toggle_queue_panel)
        view_menu.addSeparator()
        view_menu.addAction("Tùy chỉnh Phím tắt (Custom Hotkeys)…").triggered.connect(self.open_keymap)
        view_menu.addAction("Cửa Sổ Mới").triggered.connect(self._launch_standalone)
        view_menu.addAction("Mở thư mục Logs").triggered.connect(lambda: sys.platform == "win32" and os.startfile(str(log_dir_path())))
        self.btn_view.setMenu(view_menu)
        self.btn_view.setPopupMode(QToolButton.InstantPopup)

        self.btn_explorer.clicked.connect(self.show_explorer)
        self.btn_accounts.clicked.connect(self.open_accounts)
        self.btn_search.clicked.connect(self.open_search)
        self.btn_speedtest.clicked.connect(self.open_speedtest)
        self.btn_sync.clicked.connect(self.open_sync)
        self.btn_mount.clicked.connect(self.open_mount)
        self.btn_auto.clicked.connect(self.open_auto)
        self.btn_ai.clicked.connect(self.open_ai)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_changelog.clicked.connect(self.open_changelog)
        self.btn_update.clicked.connect(self._manual_update_check)

        for b in (
            self.btn_explorer,
            self.btn_accounts,
            self.btn_search,
            self.btn_speedtest,
            self.btn_sync,
            self.btn_mount,
            self.btn_auto,
            self.btn_ai,
            self.btn_settings,
            self.btn_view,
            self.btn_changelog,
            self.btn_update,
        ):
            rlay.addWidget(b)

        rlay.addStretch(1)

        # Trạng thái liên kết SyAu + rclone
        self.lbl_syau = QLabel()
        self.lbl_syau.setObjectName("BadgeGood" if self.embedded_in_syau else "BadgeWarn")
        self.lbl_syau.setText("SyAu: Tab 3 đã liên kết" if self.embedded_in_syau else "SyAu: Đang kiểm tra…")
        rlay.addWidget(self.lbl_syau)

        if self.embedded_in_syau:
            btn_pop = QToolButton()
            btn_pop.setObjectName("RibbonBtn")
            btn_pop.setText("Mở cửa sổ riêng")
            btn_pop.setToolTip("Mở Rdrive trong cửa sổ độc lập")
            btn_pop.clicked.connect(self._launch_standalone)
            rlay.addWidget(btn_pop)

        self.lbl_rclone = QLabel("rclone: đang kiểm tra…")
        self.lbl_rclone.setObjectName("BadgeWarn")
        rlay.addWidget(self.lbl_rclone)

        root.addWidget(ribbon)

        # 2) Khung chính: Dual-pane Explorer + Transfer Drawer ở dưới
        self.vsplit = QSplitter(Qt.Vertical)

        center_wrap = QWidget()
        clay = QHBoxLayout(center_wrap)
        clay.setContentsMargins(6, 6, 6, 4)
        clay.setSpacing(0)

        self.left_pane = ExplorerPane("left", self.rclone, self.config, self.drive_cache, self)
        self.right_pane = ExplorerPane("right", self.rclone, self.config, self.drive_cache, self)

        # Thanh nút ở giữa 2 khung (kiểu 2 khung: 🔗, ▶, ◀, ⇄, ⚖)
        self.mid_bar = QWidget()
        self.mid_bar.setFixedWidth(44)
        mlay = QVBoxLayout(self.mid_bar)
        mlay.setContentsMargins(2, 40, 2, 10)
        mlay.setSpacing(8)

        self.btn_link = QToolButton()
        self.btn_link.setObjectName("CenterAction")
        self.btn_link.setText("🔗")
        self.btn_link.setCheckable(True)
        self.btn_link.setToolTip("Khóa điều hướng 2 khung (vào cùng tên thư mục ở cả 2 bên)")
        self.btn_link.toggled.connect(self._on_link_toggled)

        btn_lr = QToolButton()
        btn_lr.setObjectName("CenterAction")
        btn_lr.setText("▶")
        btn_lr.setToolTip("Sao chép / Upload từ Khung Trái → Khung Phải")
        btn_lr.clicked.connect(lambda: self._transfer_from_side("left", "copy"))

        btn_rl = QToolButton()
        btn_rl.setObjectName("CenterAction")
        btn_rl.setText("◀")
        btn_rl.setToolTip("Sao chép / Download từ Khung Phải → Khung Trái")
        btn_rl.clicked.connect(lambda: self._transfer_from_side("right", "copy"))

        btn_sync_mid = QToolButton()
        btn_sync_mid.setObjectName("CenterAction")
        btn_sync_mid.setText("⇄")
        btn_sync_mid.setToolTip("Mở hộp thoại Đồng bộ & Backup Trái ↔ Phải")
        btn_sync_mid.clicked.connect(self.open_sync)

        btn_balance = QToolButton()
        btn_balance.setObjectName("CenterAction")
        btn_balance.setText("⚖")
        btn_balance.setToolTip("Chia đều 2 khung Trái / Phải về tỷ lệ 50 / 50 (khi bị lệch)")
        btn_balance.clicked.connect(self.balance_panes)

        for b in (self.btn_link, btn_lr, btn_rl, btn_sync_mid, btn_balance):
            b.setFixedSize(36, 34)
            mlay.addWidget(b, 0, Qt.AlignHCenter)
        mlay.addStretch(1)

        # Dùng QSplitter ngang để người dùng kéo chỉnh độ rộng Khung Trái ↔ Khung Phải tùy ý
        right_wrap = QWidget()
        rwlay = QHBoxLayout(right_wrap)
        rwlay.setContentsMargins(0, 0, 0, 0)
        rwlay.setSpacing(4)
        rwlay.addWidget(self.mid_bar)
        rwlay.addWidget(self.right_pane, 1)

        self.hsplit = QSplitter(Qt.Horizontal)
        self.hsplit.setHandleWidth(6)
        self.hsplit.setChildrenCollapsible(False)
        self.hsplit.addWidget(self.left_pane)
        self.hsplit.addWidget(right_wrap)
        self.hsplit.setStretchFactor(0, 1)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setSizes([660, 704])
        clay.addWidget(self.hsplit, 1)

        self.transfer_panel = TransferPanel(self.transfers, self.history, self)
        self.transfers.restore_saved()          # tac vu chua xong tu lan truoc (hien o trang thai Tam dung)
        self.transfer_panel.setMinimumHeight(110)

        self.vsplit.addWidget(center_wrap)
        self.vsplit.addWidget(self.transfer_panel)
        self.vsplit.setStretchFactor(0, 4)
        self.vsplit.setStretchFactor(1, 1)
        self.vsplit.setSizes([560, 170])

        # Màn hình Quản lý Tài khoản toàn khung kiểu 2 khung
        self.accounts_view = AccountsView(self.rclone, self.config, self.history, self)
        self.accounts_view.accounts_changed.connect(self._on_accounts_changed)
        self.accounts_view.account_removed_or_signed_out.connect(self._on_account_signed_out)
        self.accounts_view.open_remote_requested.connect(self._open_remote_on_side)
        self.accounts_view.status_message.connect(self._emit_status)

        # Màn hình Tìm Kiếm trong Google Drive chỉ định (kiểu 2 khung)
        self.search_view = SearchView(self.rclone, self.config, self)
        self.search_view.set_right_pane_getter(self.right_pane.current_tab)
        self.search_view.download_requested.connect(self._download_from_search)
        self.search_view.open_folder_requested.connect(self._open_folder_from_search)
        self.search_view.status_message.connect(self._emit_status)

        # Màn hình Speedtest.net + Đa luồng IDM
        self.speedtest_view = SpeedtestView(self)
        self.speedtest_view.status_message.connect(self._emit_status)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.vsplit)          # 0: Khám Phá
        self.pages.addWidget(self.accounts_view)   # 1: Tài Khoản
        self.pages.addWidget(self.search_view)     # 2: Tìm Kiếm
        self.pages.addWidget(self.speedtest_view)  # 3: Speedtest
        root.addWidget(self.pages, 1)

        # Kết nối tín hiệu 2 khung
        for pane in (self.left_pane, self.right_pane):
            pane.transfer_requested.connect(self._enqueue_entries)
            pane.external_drop_requested.connect(self._on_external_drop)
            pane.status_message.connect(self._emit_status)
            pane.add_account_requested.connect(self.quick_login_google)
            pane.send_to_syau_sync.connect(self._send_to_syau_sync)
            pane.send_to_syau_convert.connect(self._send_to_syau_convert)
            pane.folder_changed.connect(self._on_pane_folder_changed)
            pane.tab_move_requested.connect(self._on_tab_move_requested)

        self.transfer_panel.schedule_job_requested.connect(self._schedule_transfer_job)
        self.transfer_panel.fix_account_requested.connect(self._fix_account_token)
        self.transfers.job_completed.connect(self._on_job_completed)
        self.transfers.queue_idle.connect(self._on_queue_idle)

    @staticmethod
    def _ribbon_btn(text: str, icon_kind: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("RibbonBtn")
        b.setText(text)
        b.setIcon(icons.gdrive_icon(18) if icon_kind == "gdrive" else icons.ribbon_icon(icon_kind, 18))
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setToolTip(tip)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return b

    def _active_pane(self) -> ExplorerPane:
        fw = self.focusWidget()
        if fw and (fw is self.right_pane or self.right_pane.isAncestorOf(fw)):
            return self.right_pane
        return self.left_pane

    def _bind_shortcuts(self) -> None:
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts.clear()

        bindings = [
            ("refresh", self.refresh_all),
            ("copy", lambda: self._active_pane().copy_selected()),
            ("cut", lambda: self._active_pane().cut_selected()),
            ("paste", lambda: self._active_pane().paste_clipboard()),
            ("rename", lambda: self._active_pane()._rename_selected()),
            ("mkdir", lambda: self._active_pane()._mkdir()),
            ("delete", lambda: self._active_pane()._delete_selected()),
            ("properties", lambda: self._active_pane().show_properties_selected()),
            ("new_tab", lambda: self._active_pane()._show_add_tab_menu()),
            ("search", self.open_search),
            ("sync", self.open_sync),
        ]
        for aid, fn in bindings:
            key = get_hotkey(self.config, aid)
            if key:
                sc = QShortcut(QKeySequence(key), self)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    def open_keymap(self) -> None:
        dlg = KeymapDialog(self, self.config)
        if dlg.exec() == KeymapDialog.Accepted:
            self._bind_shortcuts()
            self._emit_status("Đã cập nhật phím tắt tùy chỉnh.")

    # ------------------------------------------------------------ Bootstrap
    def _bootstrap(self) -> None:
        # 1. Kiểm tra rclone
        remotes = []
        try:
            ver = self.rclone.version()
            self.lbl_rclone.setText(ver or "rclone: sẵn sàng")
            self.lbl_rclone.setObjectName("BadgeGood")
            self._restyle(self.lbl_rclone)
            remotes = self.rclone.list_remotes(only_drive=False)
        except RcloneError as exc:
            self.lbl_rclone.setText("Chưa có rclone")
            self.lbl_rclone.setObjectName("BadgeBad")
            self._restyle(self.lbl_rclone)
            self._emit_status(str(exc))

        valid_names = {r.name for r in remotes}

        # 2. Khôi phục đúng các tab và thư mục đang mở trước khi tắt app (Mục #2)
        if not self.left_pane.restore_tabs_state(self.config.left_tabs_state, self.config.active_left_tab, valid_names):
            left_start = self.config.last_left_path
            if not left_start or not os.path.isdir(left_start):
                cand = str(Path.home() / "Desktop")
                left_start = cand if os.path.isdir(cand) else str(Path.home())
            self.left_pane.open_local_tab(left_start)

        if not self.right_pane.restore_tabs_state(self.config.right_tabs_state, self.config.active_right_tab, valid_names):
            if remotes:
                preferred = self.config.last_right_remote
                pick = next((r for r in remotes if r.name == preferred), remotes[0])
                self.right_pane.open_drive_tab(pick.name)
                for extra in remotes[:3]:
                    if extra.name != pick.name:
                        meta = self.config.get_account_meta(extra.name)
                        from .explorer_pane import PaneTabState
                        self.right_pane.tabs.append(PaneTabState(kind="drive", remote=extra.name, title=meta.label()))
                self.right_pane._rebuild_tab_bar()
            else:
                self.right_pane.open_local_tab(str(Path.home()))
                self._emit_status("Chưa có tài khoản Google Drive nào. Vào mục 'Tài Khoản' để thêm!")

        # 4. Kiểm tra kết nối SyAu nếu chạy standalone: ở luồng nền (không làm đứng giao diện khi SyAu
        #    chưa chạy) và lặp lại mỗi 20 giây (mở SyAu sau thì nhãn tự chuyển sang "Đã kết nối").
        self._update_found.connect(self._offer_update)
        if self.config.update_repo and self.config.update_auto_check and not self.embedded_in_syau:
            QTimer.singleShot(8000, lambda: self.check_for_update(silent=True))
        if not self.embedded_in_syau:
            self._syau_ping.connect(self._show_syau_state)
            self._ping_syau()
            self._syau_timer = QTimer(self)
            self._syau_timer.timeout.connect(self._ping_syau)
            self._syau_timer.start(20000)

    def check_for_update(self, silent: bool = False) -> None:
        """Hỏi GitHub có bản mới không (luồng nền). silent=True: chỉ báo khi có bản mới."""
        import threading
        from ..core import updater
        repo = self.config.update_repo

        def work() -> None:
            try:
                self._update_found.emit(updater.check_latest(repo), silent, "")
            except Exception as exc:  # mạng lỗi, repo sai...
                self._update_found.emit(None, silent, str(exc)[:200])

        threading.Thread(target=work, name="rdrive-update-check", daemon=True).start()

    def _manual_update_check(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        if not self.config.update_repo:
            QMessageBox.information(self, "Cập nhật", "Chưa cấu hình kho GitHub.\nVào Tùy Chọn → 'Kho cập nhật GitHub' và nhập chủ/kho (VD: ten-ban/rdrive-releases).")
            return
        self.check_for_update(silent=False)

    def _offer_update(self, info, silent: bool, err: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        from ..core import updater
        if err:
            if not silent:
                QMessageBox.warning(self, "Cập nhật", f"Không kiểm tra được bản mới:\n{err}")
            return
        if info is None:
            if not silent:
                QMessageBox.information(self, "Cập nhật", f"Bạn đang dùng bản mới nhất (v{__version__}).")
            return
        r = QMessageBox.question(
            self, "Có bản cập nhật",
            f"Có Rdrive v{info.version} (đang dùng v{__version__}).\n\n{info.notes}\n\n"
            "Cập nhật ngay? Rdrive sẽ tắt và tự mở lại. Cài đặt và dữ liệu của bạn được giữ nguyên.\n"
            "(Hãy đợi các tác vụ đang chạy xong trước khi cập nhật.)",
        )
        if r != QMessageBox.Yes:
            return
        try:
            self._emit_status("Đang tải bản cập nhật…")
            zp = updater.download(info)
            updater.apply_and_restart(zp, ROOT_DIR)
        except Exception as exc:
            QMessageBox.warning(self, "Cập nhật", f"Cập nhật thất bại:\n{exc}")
            return
        self.window().close()

    def _ping_syau(self) -> None:
        import threading
        threading.Thread(target=lambda: self._syau_ping.emit(self.syau.ping()), name="rdrive-syau-ping",
                         daemon=True).start()

    def _show_syau_state(self, info) -> None:
        if info:
            self.lbl_syau.setText(f"SyAu: Đã kết nối (v{info.get('version', '?')})")
            self.lbl_syau.setObjectName("BadgeGood")
        else:
            self.lbl_syau.setText("SyAu: Chưa chạy")
            self.lbl_syau.setObjectName("BadgeWarn")
        self._restyle(self.lbl_syau)

    @staticmethod
    def _restyle(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)
        w.adjustSize()
        w.updateGeometry()

    def _emit_status(self, msg: str) -> None:
        self.status.emit(msg)

    # ------------------------------------------------------------ Actions
    def refresh_all(self) -> None:
        self.left_pane.refresh()
        self.right_pane.refresh()

    def _toggle_queue_panel(self, visible: bool) -> None:
        self.transfer_panel.setVisible(visible)

    def balance_panes(self) -> None:
        """Chia đều 2 khung Trái / Phải về đúng tỷ lệ 50/50 và cân lại cột cây thư mục."""
        total = max(600, self.hsplit.width())
        half = (total - 44) // 2
        self.hsplit.setSizes([half, half + 44])
        for pane in (self.left_pane, self.right_pane):
            pw = max(300, pane.width())
            tree_w = min(220, max(150, int(pw * 0.28)))
            pane.splitter.setSizes([tree_w, max(150, pw - tree_w)])
        self._emit_status("Đã chia đều 2 khung Trái / Phải về tỷ lệ 50 / 50.")

    def _on_link_toggled(self, on: bool) -> None:
        self._link_nav = on
        self._emit_status("Đã BẬT khóa điều hướng 2 khung." if on else "Đã TẮT khóa điều hướng 2 khung.")

    def _on_pane_folder_changed(self, side: str, folder_name: str) -> None:
        if not self._link_nav or not folder_name:
            return
        other = self.right_pane if side == "left" else self.left_pane
        other.try_enter_subfolder(folder_name)

    def _on_tab_move_requested(self, src_side: str, src_idx: int, dst_side: str, dst_idx: int) -> None:
        """Di chuyển 1 tab giữa Khung Trái ↔ Khung Phải an toàn (giữ nguyên toàn bộ logic Upload/Download 2 chiều)."""
        if src_side == dst_side:
            return
        src_pane = self.left_pane if src_side == "left" else self.right_pane
        dst_pane = self.left_pane if dst_side == "left" else self.right_pane
        if not (0 <= src_idx < len(src_pane.tabs)):
            return

        moved_tab = src_pane.tabs.pop(src_idx)
        # Nếu khung nguồn hết tab, hiện màn hình chọn nhanh (Launcher kiểu 2 khung) thay vì khung trắng
        if not src_pane.tabs:
            src_pane.show_launcher()
        else:
            new_src_idx = min(max(0, src_pane.active_tab_idx), len(src_pane.tabs) - 1)
            src_pane.active_tab_idx = -1
            src_pane.switch_tab(new_src_idx)

        dst_idx = max(0, min(dst_idx, len(dst_pane.tabs)))
        dst_pane.tabs.insert(dst_idx, moved_tab)
        dst_pane.active_tab_idx = -1
        dst_pane.switch_tab(dst_idx)

        side_lbl = "Khung Trái" if dst_side == "left" else "Khung Phải"
        self._emit_status(f"Đã chuyển tab '{moved_tab.title}' sang {side_lbl}.")

    def _set_page(self, idx: int) -> None:
        self.pages.setCurrentIndex(idx)
        self.btn_explorer.setChecked(idx == 0)
        self.btn_accounts.setChecked(idx == 1)
        self.btn_search.setChecked(idx == 2)
        self.btn_speedtest.setChecked(idx == 3)

    def show_explorer(self) -> None:
        self._set_page(0)

    def open_accounts(self) -> None:
        self._set_page(1)
        self.accounts_view.reload_accounts()

    def open_search(self) -> None:
        self._set_page(2)
        self.search_view.refresh_scopes()

    def open_speedtest(self) -> None:
        self._set_page(3)

    def open_mount(self) -> None:
        rt = self.right_pane.current_tab()
        spec = rt.rclone_spec()[0] if (rt and rt.kind == "drive") else ""
        MountDialog(self, self.rclone, self.config, current_right_spec=spec).exec()

    def open_changelog(self) -> None:
        ChangelogDialog(self, self.rclone).exec()

    def _on_external_drop(self, target_side: str, local_paths: list[str]) -> None:
        """Nhận file/folder kéo thả hoặc Ctrl+V từ Windows Explorer vào Khung Trái hoặc Khung Phải."""
        dst_pane = self.left_pane if target_side == "left" else self.right_pane
        dst_tab = dst_pane.current_tab()
        if not dst_tab or not local_paths:
            return
        direction = "upload" if dst_tab.kind == "drive" else "local"
        if not self.transfer_panel.isVisible():
            self.act_toggle_queue.setChecked(True)
        for p in local_paths:
            name = os.path.basename(p.rstrip("\\/"))
            if not name:
                continue
            is_dir = os.path.isdir(p)
            dst_spec, dst_flags = dst_tab.rclone_spec(name)
            cmd = "copy" if is_dir else "copyto"
            verb = "Upload" if direction == "upload" else "Sao chép"
            self.transfers.enqueue(
                title=f"{verb}: {name}",
                command=cmd,
                src=p,
                dst=dst_spec,
                direction=direction,
                extra_args=dst_flags,
            )
        self._emit_status(f"Đã thêm {len(local_paths)} mục kéo thả / dán từ Windows vào hàng đợi.")

    def _schedule_transfer_job(self, job) -> None:
        from ..core.config import AutoRule
        import uuid

        rules = self.config.get_rules()
        rules.append(
            AutoRule(
                id=uuid.uuid4().hex[:8],
                name=job.title,
                enabled=True,
                kind="backup",
                source=job.src,
                destination=job.dst,
                schedule="daily",
            )
        )
        self.config.set_rules(rules)
        self.config.save()
        self._emit_status(f"Đã đưa '{job.title}' vào Auto / Lịch.")
        self.open_auto()

    def _fix_account_token(self, remote: str) -> None:
        if not remote:
            return
        try:
            existing = {r.name for r in self.rclone.list_remotes(only_drive=False)}
        except RcloneError:
            existing = {remote}
        dlg = GoogleLoginDialog(self, self.rclone, self.config, existing, reconnect_remote=remote)
        if dlg.exec() == GoogleLoginDialog.Accepted:
            self.drive_cache.invalidate_remote(remote)
            self._emit_status(f"Đã đăng nhập lại {remote} — đang chạy lại tác vụ!")

    def _on_job_completed(self, job) -> None:
        """Tự động làm mới đúng thư mục đích sau khi upload/download xong (Mục #2)."""
        if self.config.auto_clear_done:
            self.transfer_panel._clear_done()
        if not self.config.auto_refresh_after_upload:
            return
        for pane in (self.left_pane, self.right_pane):
            tab = pane.current_tab()
            if not tab:
                continue
            cur_spec, _ = tab.rclone_spec()
            if job.dst.startswith(cur_spec):
                pane.refresh(force=True)

    def _set_dual_pane(self, dual: bool) -> None:
        self.show_explorer()
        self.left_pane.setVisible(dual)
        self.mid_bar.setVisible(dual)
        self._emit_status("Chế độ Hai Ô Cửa" if dual else "Chế độ Một Ô Cửa (rộng)")

    def _download_from_search(self, remote: str, item_id: str, parent_id: str, name: str, is_dir: bool) -> None:
        lt = self.left_pane.current_tab()
        dst_dir = lt.local_path if (lt and lt.kind == "local" and lt.local_path) else str(Path.home() / "Downloads")
        if is_dir:
            src = f"{remote},root_folder_id={item_id}:"
            dst = os.path.join(dst_dir, name)
            cmd = "copy"
        else:
            src = f"{remote},root_folder_id={parent_id}:{name}" if parent_id else f"{remote}:{name}"
            dst = os.path.join(dst_dir, name)
            cmd = "copyto"
        self.show_explorer()
        self.transfer_panel.setVisible(True)
        self.act_toggle_queue.setChecked(True)
        self.transfers.enqueue(
            title=f"Download: {name}",
            command=cmd,
            src=src,
            dst=dst,
            direction="download",
        )
        self._emit_status(f"Đã thêm '{name}' vào hàng đợi tải về {dst_dir}")

    def _open_folder_from_search(self, remote: str, folder_id: str, label: str) -> None:
        self.show_explorer()
        self.right_pane.open_drive_folder_id(remote, folder_id, label)

    def quick_login_google(self) -> None:
        try:
            existing = {r.name for r in self.rclone.list_remotes(only_drive=False)}
        except RcloneError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        dlg = GoogleLoginDialog(self, self.rclone, self.config, existing)
        if dlg.exec() == GoogleLoginDialog.Accepted and dlg.created_remote:
            self._on_accounts_changed()
            if self.pages.currentIndex() == 1:
                self.accounts_view.reload_accounts()
            else:
                self.right_pane.open_drive_tab(dlg.created_remote)
            log.info("LOGIN GOOGLE OK: %s", dlg.created_remote)
            self.history.record("login", f"Đăng nhập Google Drive: {dlg.created_remote}", status="ok")
            self._emit_status(f"Đã đăng nhập tài khoản Google Drive: {dlg.created_remote}")

    def _on_accounts_changed(self) -> None:
        self.left_pane.refresh_tab_labels()
        self.right_pane.refresh_tab_labels()

    def _on_account_signed_out(self, remote: str) -> None:
        self.drive_cache.invalidate_remote(remote)
        self.left_pane.close_remote_tabs(remote)
        self.right_pane.close_remote_tabs(remote)

    def _open_remote_on_side(self, remote: str, side: str) -> None:
        self.show_explorer()
        pane = self.left_pane if side == "left" else self.right_pane
        if remote.startswith("__UNC__"):
            pane.open_local_tab(remote.replace("__UNC__", "", 1))
        else:
            pane.open_drive_tab(remote)

    def _transfer_from_side(self, side: str, mode: str) -> None:
        src_pane = self.left_pane if side == "left" else self.right_pane
        sel = src_pane.selected_entries()
        if not sel:
            self._emit_status("Hãy chọn ít nhất 1 file hoặc thư mục trước khi truyền tải.")
            return
        self._enqueue_entries(side, sel, mode)

    def _enqueue_entries(self, src_side: str, entries: list[dict], mode: str) -> None:
        src_pane = self.left_pane if src_side == "left" else self.right_pane
        dst_pane = self.right_pane if src_side == "left" else self.left_pane
        src_tab = src_pane.current_tab()
        dst_tab = dst_pane.current_tab()
        if not src_tab or not dst_tab:
            self._emit_status("Hãy mở 1 ổ đĩa / tài khoản ở cả 2 khung trước khi truyền tải file.")
            return

        if src_tab.kind == "local" and dst_tab.kind == "drive":
            direction = "upload"
        elif src_tab.kind == "drive" and dst_tab.kind == "local":
            direction = "download"
        elif src_tab.kind == "drive" and dst_tab.kind == "drive":
            direction = "cloud"
        else:
            direction = "local"

        if not self.transfer_panel.isVisible():
            self.act_toggle_queue.setChecked(True)

        for e in entries:
            name = e["name"]
            is_dir = bool(e.get("is_dir"))
            src_spec, src_flags = src_tab.rclone_spec(name)
            dst_spec, dst_flags = dst_tab.rclone_spec(name)
            if is_dir:
                cmd = "move" if mode == "move" else "copy"
            else:
                cmd = "moveto" if mode == "move" else "copyto"
            verb = "Di chuyển" if mode == "move" else ("Upload" if direction == "upload" else "Download" if direction == "download" else "Sao chép")
            self.transfers.enqueue(
                title=f"{verb}: {name}",
                command=cmd,
                src=src_spec,
                dst=dst_spec,
                direction=direction,
                extra_args=[*src_flags, *dst_flags],
            )
        self._emit_status(f"Đã thêm {len(entries)} mục vào hàng đợi truyền tải.")

    def open_sync(self) -> None:
        lt = self.left_pane.current_tab()
        rt = self.right_pane.current_tab()
        if not lt or not rt:
            return
        lspec, _ = lt.rclone_spec()
        rspec, _ = rt.rclone_spec()
        dlg = SyncDialog(
            self,
            self.rclone,
            self.config,
            lt.display_path(),
            lspec,
            lt.kind == "local",
            rt.display_path(),
            rspec,
            rt.kind == "local",
        )
        if dlg.exec() == SyncDialog.Accepted and dlg.selected_plans:
            self.show_explorer()
            if not self.transfer_panel.isVisible():
                self.act_toggle_queue.setChecked(True)
            for plan in dlg.selected_plans:
                self.transfers.enqueue(
                    title=plan["title"],
                    command=plan["command"],
                    src=plan["src"],
                    dst=plan["dst"],
                    direction="sync",
                    extra_args=plan["extra"],
                )
            self._emit_status(f"Đã bắt đầu {dlg.selected_plans[0]['title']}.")

    def open_auto(self) -> None:
        lt = self.left_pane.current_tab()
        rt = self.right_pane.current_tab()
        lhint = lt.rclone_spec()[0] if lt else ""
        rhint = rt.rclone_spec()[0] if rt else ""
        AutoDialog(self, self.config, lhint, rhint).exec()

    def open_ai(self) -> None:
        compact_ctx = self.left_pane.summarize_context(compact=True) + "\n" + self.right_pane.summarize_context(compact=True)
        full_ctx = self.left_pane.summarize_context(compact=False) + "\n\n" + self.right_pane.summarize_context(compact=False)
        AIDialog(self, self.config, folder_context=compact_ctx, full_context=full_ctx).exec()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.config)
        if dlg.exec() == SettingsDialog.Accepted:
            self.rclone.exe = self.config.rclone_path or self.rclone.exe
            self.rclone.use_rcd = bool(getattr(self.config, "use_rcd", True))
            self.syau.server_url = self.config.syau_server_url.rstrip("/")
            self.refresh_all()
            self._emit_status("Đã lưu tùy chọn Rdrive.")

    # ------------------------------------------------------------ SyAu bridge
    def _send_to_syau_sync(self, paths: list[str]) -> None:
        ok, msg = self.syau.send_to_sync(paths)
        self.history.record("syau", f"Gửi {len(paths)} mục sang SyAu Sync", status="ok" if ok else "error", detail=msg)
        self._emit_status(msg)
        if not ok:
            QMessageBox.information(self, "Liên kết SyAu", msg)

    def _send_to_syau_convert(self, paths: list[str]) -> None:
        ok, msg = self.syau.send_to_convert(paths)
        self.history.record("syau", f"Gửi {len(paths)} mục sang SyAu Converter", status="ok" if ok else "error", detail=msg)
        self._emit_status(msg)
        if not ok:
            QMessageBox.information(self, "Liên kết SyAu", msg)

    def _launch_standalone(self) -> None:
        py = sys.executable
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(pyw):
            py = pyw
        entry = str(ROOT_DIR / "Rdrive.pyw")
        if os.path.isfile(entry):
            subprocess.Popen([py, entry])

    def _on_queue_idle(self) -> None:
        self.drive_cache.save_to_disk()
        self._emit_status("Đã hoàn tất tất cả tác vụ trong hàng đợi truyền tải.")

    def save_state(self) -> None:
        lt = self.left_pane.current_tab()
        rt = self.right_pane.current_tab()
        if lt and lt.kind == "local" and lt.local_path:
            self.config.last_left_path = lt.local_path
        if rt and rt.kind == "drive" and rt.remote:
            self.config.last_right_remote = rt.remote
        self.config.left_tabs_state = self.left_pane.export_tabs_state()
        self.config.right_tabs_state = self.right_pane.export_tabs_state()
        self.config.active_left_tab = self.left_pane.active_tab_idx
        self.config.active_right_tab = self.right_pane.active_tab_idx
        self.config.save()
        self.drive_cache.save_to_disk()

    def shutdown(self) -> None:
        if getattr(self, "_syau_timer", None) is not None:
            self._syau_timer.stop()
        self.save_state()
        self.accounts_view.shutdown()
        self.search_view.shutdown()
        self.speedtest_view.shutdown()
        self.left_pane.shutdown()
        self.right_pane.shutdown()
        self.transfers.shutdown()
        stop_all_mounts()
        self.rclone.close()                       # tat rclone thuong truc
