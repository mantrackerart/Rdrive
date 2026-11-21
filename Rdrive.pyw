"""Điểm khởi động ứng dụng Rdrive (tự ghi log khi lỗi khởi động)."""
import os
import sys
import traceback
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _write_boot_log(text: str) -> None:
    try:
        logs = Path(ROOT) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "rdrive.log", "a", encoding="utf-8") as fh:
            fh.write("\n[BOOT ERROR]\n" + text + "\n")
    except Exception:
        pass


def _alert(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Rdrive", 0x10)
    except Exception:  # noqa: BLE001
        if sys.stderr is not None:
            print(text)


try:
    import PySide6  # noqa: F401
    from rdrive.app import main
except Exception:  # noqa: BLE001
    tb = traceback.format_exc()
    _write_boot_log(tb)
    _alert(
        "Rdrive không khởi động được (thiếu thư viện hoặc lỗi môi trường).\n"
        "Hãy chạy setup.bat trong thư mục Rdrive để tự tải phần còn thiếu.\n\n"
        + tb[-1000:]
    )
    sys.exit(1)

main()
