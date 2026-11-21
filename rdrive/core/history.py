"""Lưu lịch sử hoạt động bền vững cho Rdrive (data/history.json)."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from .config import DATA_DIR

HISTORY_FILE = DATA_DIR / "history.json"
MAX_HISTORY = 1000


@dataclass
class HistoryEntry:
    timestamp: str
    action: str           # upload | download | cloud | sync | move | mkdir | rename | delete | login | ai | syau | app | error
    status: str           # ok | error | cancelled | info
    title: str
    src: str = ""
    dst: str = ""
    size_bytes: int = 0
    detail: str = ""


class HistoryStore(QObject):
    """Lưu & đọc lịch sử các tác vụ upload/download/backup/đăng nhập/AI."""

    entry_added = Signal(object)   # HistoryEntry
    cleared = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._items: list[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not HISTORY_FILE.is_file():
            self._items = []
            return
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            known = {k for k in HistoryEntry.__dataclass_fields__}
            self._items = [
                HistoryEntry(**{k: v for k, v in item.items() if k in known})
                for item in (raw or [])
                if isinstance(item, dict)
            ][-MAX_HISTORY:]
        except Exception:
            self._items = []

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump([asdict(x) for x in self._items[-MAX_HISTORY:]], fh, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_FILE)

    def entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(reversed(self._items))

    def record(
        self,
        action: str,
        title: str,
        *,
        status: str = "ok",
        src: str = "",
        dst: str = "",
        size_bytes: int = 0,
        detail: str = "",
    ) -> HistoryEntry:
        entry = HistoryEntry(
            timestamp=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            action=action,
            status=status,
            title=title,
            src=src,
            dst=dst,
            size_bytes=int(size_bytes or 0),
            detail=detail,
        )
        with self._lock:
            self._items.append(entry)
            if len(self._items) > MAX_HISTORY:
                self._items = self._items[-MAX_HISTORY:]
            try:
                self._save()
            except Exception:
                pass
        self.entry_added.emit(entry)
        return entry

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            try:
                self._save()
            except Exception:
                pass
        self.cleared.emit()
