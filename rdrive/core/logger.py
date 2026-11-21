"""Hệ thống ghi nhật ký bền vững (logs/rdrive.log + logs/crash.log) và bắt lỗi an toàn cho pythonw.exe."""
from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOGS_DIR

LOG_FILE = LOGS_DIR / "rdrive.log"
CRASH_FILE = LOGS_DIR / "crash.log"

_INITIALIZED = False
_CRASH_FH = None


def setup_logging() -> logging.Logger:
    """Khởi tạo logger quay vòng file + faulthandler (an toàn 100% dưới pythonw.exe khi stderr=None)."""
    global _INITIALIZED, _CRASH_FH
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("rdrive")
    if _INITIALIZED:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(str(LOG_FILE), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Chỉ gắn StreamHandler nếu stderr thực sự tồn tại (pythonw.exe có stderr=None)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    # Ghi dấu vết crash native (C++/Qt) vào logs/crash.log
    try:
        _CRASH_FH = open(CRASH_FILE, "a", encoding="utf-8")
        faulthandler.enable(file=_CRASH_FH, all_threads=True)
    except Exception:
        pass

    # Bắt exception chưa xử lý trong các luồng phụ
    def _thread_excepthook(args) -> None:
        tb = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        logger.error("THREAD EXCEPTION (%s):\n%s", getattr(args.thread, "name", "?"), tb)

    threading.excepthook = _thread_excepthook

    # Chuyển thông điệp Qt warning/critical vào log
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def _qt_handler(mode, _ctx, message) -> None:
            if mode == QtMsgType.QtWarningMsg:
                logger.warning("[Qt] %s", message)
            elif mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                logger.error("[Qt] %s", message)

        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass

    _INITIALIZED = True
    return logger


def get_logger() -> logging.Logger:
    if not _INITIALIZED:
        return setup_logging()
    return logging.getLogger("rdrive")


def log_dir_path() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR
