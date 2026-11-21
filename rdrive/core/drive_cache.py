"""Bộ nhớ đệm bền vững (Session + Disk Cache data/tree_cache.json) cho cây thư mục & danh sách file Google Drive.

- Lưu cache xuống đĩa (data/tree_cache.json) để tắt app bật lại vẫn hiển thị cây thư mục đã mở tức thì (0 ms).
- Chỉ gọi rclone khi thư mục chưa từng mở hoặc khi người dùng bấm F5 / ↻ hoặc sau khi upload xong vào đúng thư mục đó.
- Lưu cả danh sách chủ sở hữu đã chia sẻ file với mình (<tên tài khoản> Drive).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict

from .config import DATA_DIR
from .rclone_client import DriveAbout, DriveEntry, SharedDrive

CACHE_FILE = DATA_DIR / "tree_cache.json"
CACHE_TTL_S = 7 * 24 * 3600  # Giữ cache cây thư mục 7 ngày (hoặc tới khi người dùng bấm F5)


class DriveCache:
    """Cache dùng chung giữa cả Khung Trái, Khung Phải và tất cả các tab, tự lưu xuống đĩa."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._roots: dict[str, tuple[list[SharedDrive], DriveAbout | None]] = {}
        self._entries: dict[tuple, list[DriveEntry]] = {}
        self._dirs: dict[tuple, list[DriveEntry]] = {}
        self._shared_owners: dict[str, dict[str, list[DriveEntry]]] = {}  # remote -> {"owner Drive": [DriveEntry]}
        self._dirty = False
        self._load_from_disk()

    @staticmethod
    def make_key(remote: str, mode: str, team_drive: str = "", folder_id: str = "", rel_path: str = "") -> tuple:
        return (remote, mode, team_drive or "", folder_id or "", (rel_path or "").strip("/"))

    @staticmethod
    def _key_str(key: tuple) -> str:
        return "|".join(str(x) for x in key)

    @staticmethod
    def _parse_key(s: str) -> tuple:
        parts = s.split("|", 4)
        while len(parts) < 5:
            parts.append("")
        return tuple(parts)

    def _load_from_disk(self) -> None:
        if not CACHE_FILE.is_file():
            return
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if time.time() - float(raw.get("saved_at") or 0) > CACHE_TTL_S:
                return
            for rem, rdata in (raw.get("roots") or {}).items():
                sds = [SharedDrive(**d) for d in (rdata.get("drives") or []) if isinstance(d, dict)]
                ab_raw = rdata.get("about")
                ab = DriveAbout(**ab_raw) if isinstance(ab_raw, dict) else None
                self._roots[rem] = (sds, ab)
            known_e = {k for k in DriveEntry.__dataclass_fields__}
            for ks, items in (raw.get("entries") or {}).items():
                k = self._parse_key(ks)
                parsed = [DriveEntry(**{f: v for f, v in it.items() if f in known_e}) for it in items if isinstance(it, dict)]
                self._entries[k] = parsed
                self._dirs[k] = [e for e in parsed if e.is_dir]
            for ks, items in (raw.get("dirs") or {}).items():
                k = self._parse_key(ks)
                if k not in self._dirs:
                    self._dirs[k] = [DriveEntry(**{f: v for f, v in it.items() if f in known_e}) for it in items if isinstance(it, dict)]
            for rem, omap in (raw.get("shared_owners") or {}).items():
                self._shared_owners[rem] = {
                    ok: [DriveEntry(**{f: v for f, v in it.items() if f in known_e}) for it in oitems if isinstance(it, dict)]
                    for ok, oitems in (omap or {}).items()
                }
        except Exception:
            pass

    def save_to_disk(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": time.time(),
                "roots": {
                    rem: {
                        "drives": [asdict(d) for d in sds],
                        "about": asdict(ab) if ab else None,
                    }
                    for rem, (sds, ab) in self._roots.items()
                },
                "entries": {self._key_str(k): [asdict(e) for e in v] for k, v in list(self._entries.items())[-300:]},
                "dirs": {self._key_str(k): [asdict(e) for e in v] for k, v in list(self._dirs.items())[-500:]},
                "shared_owners": {
                    rem: {ok: [asdict(e) for e in oitems] for ok, oitems in omap.items()}
                    for rem, omap in self._shared_owners.items()
                },
            }
            try:
                tmp = CACHE_FILE.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                os.replace(tmp, CACHE_FILE)
                self._dirty = False
            except Exception:
                pass

    def get_roots(self, remote: str) -> tuple[list[SharedDrive], DriveAbout | None] | None:
        with self._lock:
            return self._roots.get(remote)

    def set_roots(self, remote: str, drives: list[SharedDrive], about: DriveAbout | None) -> None:
        with self._lock:
            self._roots[remote] = (list(drives), about)
            self._dirty = True

    def get_shared_owners(self, remote: str) -> dict[str, list[DriveEntry]] | None:
        with self._lock:
            v = self._shared_owners.get(remote)
            return dict(v) if v is not None else None

    def set_shared_owners(self, remote: str, owners_map: dict[str, list[DriveEntry]]) -> None:
        with self._lock:
            self._shared_owners[remote] = dict(owners_map)
            self._dirty = True

    def get_entries(self, key: tuple) -> list[DriveEntry] | None:
        with self._lock:
            v = self._entries.get(key)
            return list(v) if v is not None else None

    def set_entries(self, key: tuple, entries: list[DriveEntry]) -> None:
        with self._lock:
            items = list(entries)
            self._entries[key] = items
            self._dirs[key] = [e for e in items if e.is_dir]
            self._dirty = True

    def get_dirs(self, key: tuple) -> list[DriveEntry] | None:
        with self._lock:
            v = self._dirs.get(key)
            return list(v) if v is not None else None

    def set_dirs(self, key: tuple, dirs: list[DriveEntry]) -> None:
        with self._lock:
            self._dirs[key] = list(dirs)
            self._dirty = True

    def invalidate_key(self, key: tuple) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._dirs.pop(key, None)
            self._dirty = True

    def invalidate_remote(self, remote: str) -> None:
        with self._lock:
            self._roots.pop(remote, None)
            self._shared_owners.pop(remote, None)
            for k in [k for k in self._entries if k[0] == remote]:
                self._entries.pop(k, None)
            for k in [k for k in self._dirs if k[0] == remote]:
                self._dirs.pop(k, None)
            self._dirty = True

    def clear(self) -> None:
        with self._lock:
            self._roots.clear()
            self._entries.clear()
            self._dirs.clear()
            self._shared_owners.clear()
            self._dirty = True
