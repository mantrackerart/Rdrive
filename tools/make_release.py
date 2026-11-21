"""Đóng gói bản phát hành: chỉ lấy file trong DANH SÁCH CHO PHÉP (không bao giờ lấy data/, logs/, bin/, config).
Chạy:  python tools/make_release.py   ->  dist/Rdrive-<phiên bản>.zip
Sau đó tạo Release trên GitHub (tag v<phiên bản>) và đính kèm file zip này.

Bước quét chỉ dùng mẫu chung (email, IP mạng nội bộ, đường dẫn người dùng Windows, khóa/token),
không chứa tên tài khoản hay thiết bị cụ thể nào."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILES = ["Rdrive.pyw", "Rdrive.bat", "Rdrive_debug.bat", "setup.bat", "README.md", "NOTICE.md", "requirements.txt",
               "tools/setup.ps1"]
ALLOW_DIRS = ["rdrive"]
# Mẫu chung: bắt địa chỉ thật trông như dữ liệu cá nhân. Ví dụ chuẩn (example.com, 192.168.1.10) được bỏ qua.
PATTERNS = [
    r"[A-Za-z]:\\\\?Users\\\\?[^\\\s\"']+",                       # đường dẫn thư mục người dùng Windows
    r"/(?:home|Users)/[A-Za-z0-9._-]+/",                          # đường dẫn thư mục người dùng Linux/macOS
    r"[A-Za-z0-9._%+-]+@(?!example\.com)[A-Za-z0-9.-]+\.[a-z]{2,}",  # email thật
    r"\b192\.168\.(?!1\.10\b)\d{1,3}\.\d{1,3}\b",                 # IP mạng nội bộ (trừ ví dụ 192.168.1.10)
    r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    r"client_secret\s*=\s*[\"'][^\"']+[\"']",                     # client secret viết cứng
    r"AIza[0-9A-Za-z_-]{20,}",                                    # khóa API Google
    r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b",                           # token bot Telegram
]
TEXT_SUFFIXES = {".py", ".md", ".bat", ".ps1", ".pyw", ".txt", ".json"}


def main() -> int:
    ver = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "rdrive" / "__init__.py").read_text(encoding="utf-8")).group(1)
    files = [ROOT / f for f in ALLOW_FILES if (ROOT / f).is_file()]
    for d in ALLOW_DIRS:
        files += [p for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    bad = []
    for p in files:
        if p.suffix.lower() in TEXT_SUFFIXES:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for pat in PATTERNS:
                for m in re.finditer(pat, text):
                    bad.append(f"{p.relative_to(ROOT)}: '{m.group(0)[:60]}'")
    if bad:
        print("DỪNG — phát hiện thông tin cá nhân có thể lộ:\n  " + "\n  ".join(bad))
        return 1
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    zp = out / f"Rdrive-{ver}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(files):
            zf.write(p, p.relative_to(ROOT).as_posix())
    print(f"OK: {zp} ({len(files)} file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
