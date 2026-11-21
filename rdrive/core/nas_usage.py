"""Đọc dung lượng NAS qua Windows (rclone about không trả được dung lượng của SMB)."""
from __future__ import annotations

import os
import shutil
import string
import sys

from .rclone_client import DriveAbout


def _mapped_unc(letter: str) -> str:
    """Trả về đường dẫn UNC của ổ mạng X: (rỗng nếu không phải ổ mạng)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(512)
        rc = ctypes.windll.mpr.WNetGetConnectionW(f"{letter}:", buf, ctypes.byref(size))
        return buf.value if rc == 0 else ""
    except Exception:
        return ""


def _usage(path: str) -> DriveAbout | None:
    try:
        du = shutil.disk_usage(path)
    except OSError:
        return None
    if du.total <= 0:
        return None
    return DriveAbout(total=int(du.total), used=int(du.used), free=int(du.free))


def nas_about(sec: dict, share_hint: str = "") -> DriveAbout | None:
    """Dung lượng thật của NAS SMB (chỉ Windows). Không đọc được thì trả None."""
    if sys.platform != "win32":
        return None
    if str(sec.get("type") or "").lower() != "smb":
        return None
    host = str(sec.get("host") or "").strip().lower()
    if not host:
        return None
    # 1) Ổ mạng đã gắn (X:) trỏ tới đúng NAS này
    for letter in string.ascii_uppercase:
        unc = _mapped_unc(letter)
        if unc and unc.lower().lstrip("\\").split("\\", 1)[0] == host:
            ab = _usage(f"{letter}:\\")
            if ab:
                return ab
    # 2) Đường dẫn UNC trực tiếp tới share (nếu biết tên share)
    if share_hint:
        return _usage(f"\\\\{host}\\{share_hint.strip('/\\')}")
    return None
