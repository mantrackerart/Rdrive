"""Cửa sổ chính độc lập của ứng dụng Rdrive (kèm ghi log bền vững & bắt lỗi an toàn cho pythonw.exe)."""
from __future__ import annotations

import os
import subprocess
import sys
import traceback

from PySide6.QtCore import QSettings, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSystemTrayIcon

from . import APP_NAME, __version__
from .core.logger import LOG_FILE, setup_logging
from .ui import icons, theme
from .ui.main_widget import RdriveWidget

INSTANCE = "Rdrive-single-instance"
log = setup_logging()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("RdriveWindow")
        self.setWindowTitle(f"{APP_NAME} v{__version__} — Google Drive Backup & Explorer (rclone)")
        self.setWindowIcon(icons.app_icon(64))
        self.resize(1380, 840)
        self.setMinimumSize(1040, 620)
        self.store = QSettings("Rdrive", "Rdrive")
        self._quitting = False

        self.widget = RdriveWidget(self, embedded_in_syau=False)
        self.setCentralWidget(self.widget)
        self.widget.status.connect(lambda m: self.statusBar().showMessage(m, 8000))

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icons.app_icon(64), self)
            self.tray.setToolTip(f"{APP_NAME} — Google Drive & Mount Manager")
            tmenu = QMenu(self)
            tmenu.addAction("Mở Rdrive").triggered.connect(self.bring_to_front)
            tmenu.addAction("Mount Cloud thành Ổ Đĩa…").triggered.connect(lambda: (self.bring_to_front(), self.widget.open_mount()))
            tmenu.addSeparator()
            tmenu.addAction("Thoát hẳn Rdrive").triggered.connect(self.quit_app)
            self.tray.setContextMenu(tmenu)
            self.tray.activated.connect(
                lambda reason: self.bring_to_front()
                if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
                else None
            )
            self.tray.show()

        btn_log = QPushButton("Mở file log")
        btn_log.setFlat(True)
        btn_log.setStyleSheet("padding: 2px 10px; font-weight: 400;")
        btn_log.setToolTip(f"{LOG_FILE}\nGửi file rdrive.log này khi cần kiểm tra lỗi.")
        btn_log.clicked.connect(self._open_log)
        self.statusBar().addPermanentWidget(btn_log)

        ver_lbl = QLabel(f"  {APP_NAME} v{__version__}  ")
        ver_lbl.setObjectName("Muted")
        self.statusBar().addPermanentWidget(ver_lbl)

        geo = self.store.value("win/geometry")
        if geo:
            self.restoreGeometry(geo)
        log.info("=== Rdrive %s khoi dong | python=%s | rclone=%s", __version__, sys.version.split()[0], self.widget.rclone.exe)

    def _open_log(self) -> None:
        if sys.platform == "win32" and LOG_FILE.is_file():
            subprocess.Popen(["explorer", "/select,", os.path.normpath(str(LOG_FILE))])

    def bring_to_front(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event) -> None:
        if not self._quitting and self.tray is not None and self.widget.config.minimize_to_tray:
            event.ignore()
            self.widget.save_state()
            self.hide()
            return
        if self.widget.transfers.active_count() > 0:
            ans = QMessageBox.question(
                self,
                APP_NAME,
                "Đang có tác vụ upload/download trong hàng đợi. Dừng tất cả và thoát Rdrive?",
            )
            if ans != QMessageBox.Yes:
                self._quitting = False
                event.ignore()
                return
        self.widget.shutdown()
        if self.tray is not None:
            self.tray.hide()
        self.store.setValue("win/geometry", self.saveGeometry())
        log.info("=== Rdrive thoat")
        super().closeEvent(event)


def _excepthook(etype, value, tb) -> None:
    text = "".join(traceback.format_exception(etype, value, tb))
    log.error("LOI KHONG MONG MUON:\n%s", text)
    if sys.stderr is not None:
        try:
            sys.stderr.write(text)
        except Exception:
            pass
    if QApplication.instance():
        QMessageBox.critical(
            None,
            APP_NAME,
            f"Có lỗi xảy ra:\n{value}\n\nChi tiết đã được lưu vào:\n{LOG_FILE}",
        )


def main() -> None:
    setup_logging()
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    # Nếu Rdrive đã mở sẵn thì đưa cửa sổ đó lên trước
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if sock.waitForConnected(250):
        sock.write(b"show")
        sock.waitForBytesWritten(250)
        sock.disconnectFromServer()
        log.info("Rdrive da chay -> dua cua so cu len truoc")
        return

    QLocalServer.removeServer(INSTANCE)
    server = QLocalServer(app)
    server.listen(INSTANCE)

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    app.setWindowIcon(icons.app_icon(64))

    win = MainWindow()

    def _on_other() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnectFromServer()
        win.bring_to_front()

    server.newConnection.connect(_on_other)
    if "--tray" in sys.argv and win.tray is not None:
        log.info("Khoi dong ngam o khay he thong (--tray)")
    else:
        win.show()
    sys.exit(app.exec())
