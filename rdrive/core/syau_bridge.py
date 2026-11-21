"""Cầu nối liên kết 2 chiều giữa Rdrive và ứng dụng SyAu (nhúng trực tiếp Tab 3 hoặc gọi qua HTTP 127.0.0.1:47811)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


class SyAuBridge:
    """Liên kết với SyAu:
    - Khi Rdrive chạy nhúng trong Tab 3 của SyAu: gọi trực tiếp callback vào SyncTab / ConvertTab.
    - Khi Rdrive chạy độc lập: ping và gửi file tới máy chủ nội bộ của SyAu (127.0.0.1:47811).
    """

    def __init__(
        self,
        *,
        server_url: str = "http://127.0.0.1:47811",
        on_send_sync: Callable[[list[str]], None] | None = None,
        on_send_convert: Callable[[list[str]], None] | None = None,
        embedded: bool = False,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self._on_send_sync = on_send_sync
        self._on_send_convert = on_send_convert
        self.embedded = embedded

    def ping(self) -> dict | None:
        if self.embedded:
            return {"app": "SyAu", "mode": "embedded"}
        try:
            req = urllib.request.Request(f"{self.server_url}/ping", method="GET")
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return None

    def send_to_sync(self, paths: list[str]) -> tuple[bool, str]:
        if not paths:
            return False, "Chưa chọn file/thư mục nào trên máy."
        if self._on_send_sync is not None:
            self._on_send_sync(paths)
            return True, f"Đã gửi {len(paths)} mục sang Tab Sync Audio của SyAu."
        return False, "Tính năng gửi trực tiếp vào Sync Audio hoạt động khi mở Rdrive ở Tab 3 của SyAu."

    def send_to_convert(self, paths: list[str]) -> tuple[bool, str]:
        if not paths:
            return False, "Chưa chọn file/thư mục nào trên máy."
        if self._on_send_convert is not None:
            self._on_send_convert(paths)
            return True, f"Đã gửi {len(paths)} mục sang Tab Converter của SyAu."
        # Gọi qua HTTP server của SyAu khi chạy standalone
        try:
            body = json.dumps({"paths": paths, "preset": ""}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.server_url}/convert/add",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            jobs = data.get("jobs") or []
            return True, f"Đã gửi {len(jobs) or len(paths)} mục sang SyAu Converter (HTTP)."
        except urllib.error.HTTPError as exc:
            return False, f"SyAu từ chối ({exc.code}): {exc.read().decode('utf-8', 'replace')[:180]}"
        except Exception:
            return False, "Không kết nối được SyAu (hãy mở app SyAu hoặc dùng Rdrive ngay ở Tab 3 của SyAu)."
