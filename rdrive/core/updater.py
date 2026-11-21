"""Tự kiểm tra và cập nhật Rdrive từ GitHub Releases (kho công khai chỉ chứa file zip phát hành)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import __version__

# Thư mục/tệp KHÔNG bao giờ bị ghi đè khi cập nhật (dữ liệu riêng của từng máy)
KEEP_DIRS = ["data", "logs", "bin", "runtime", ".venv"]
UA = {"User-Agent": "Rdrive-updater", "Accept": "application/vnd.github+json"}


@dataclass
class UpdateInfo:
    version: str
    notes: str
    url: str          # link tải file zip
    page: str = ""


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:4]) or (0,)


def check_latest(repo: str, timeout: float = 10.0) -> UpdateInfo | None:
    """Trả về UpdateInfo nếu GitHub có bản MỚI HƠN bản đang chạy, ngược lại None.
    Raise lỗi mạng cho người gọi tự xử lý."""
    repo = (repo or "").strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return None
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) <= parse_version(__version__):
        return None
    for a in data.get("assets") or []:
        name = str(a.get("name") or "")
        if name.lower().endswith(".zip") and str(a.get("browser_download_url") or "").startswith("https://"):
            return UpdateInfo(tag.lstrip("vV"), str(data.get("body") or "")[:1500], a["browser_download_url"],
                              str(data.get("html_url") or ""))
    return None


def download(info: UpdateInfo, timeout: float = 120.0) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rdrive_upd_"))
    dest = tmp / "update.zip"
    req = urllib.request.Request(info.url, headers={"User-Agent": "Rdrive-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    return dest


def apply_and_restart(zip_path: Path, root: Path) -> None:
    """Viết script .bat: chờ Rdrive tắt → giải nén → chép đè (trừ KEEP_DIRS) → mở lại. Gọi xong thì phải thoát app."""
    xd = " ".join(f'"{d}"' for d in KEEP_DIRS)
    work = zip_path.parent / "x"
    bat = zip_path.parent / "apply_update.bat"
    bat.write_text(
        "@echo off\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        f'powershell -NoProfile -Command "Expand-Archive -Path \'{zip_path}\' -DestinationPath \'{work}\' -Force"\r\n'
        f'set SRC={work}\r\n'
        # nếu zip bọc 1 thư mục duy nhất ở ngoài thì lấy vào trong
        'for /f "delims=" %%D in (\'dir /b /ad "%SRC%"\') do (if exist "%SRC%\\%%D\\Rdrive.pyw" set SRC=%SRC%\\%%D)\r\n'
        f'robocopy "%SRC%" "{root}" /E /XD {xd} /XF config.json /NFL /NDL /NJH /NJS >nul\r\n'
        f'start "" "{root}\\Rdrive.bat"\r\n',
        encoding="utf-8",
    )
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED | NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
