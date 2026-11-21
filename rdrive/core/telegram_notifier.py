"""Thông báo Telegram cho Rdrive — lưu Bot Token an toàn trong Windows Credential Manager, gửi ở luồng nền."""
from __future__ import annotations

import ctypes
import html
import json
import sys
import threading
import urllib.error
import urllib.request
from ctypes import wintypes

from .logger import get_logger

log = get_logger()

_CRED_TARGET = "Rdrive_Telegram_BotToken"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


# ------------------------------------------------------------ Windows Credential Manager
if sys.platform == "win32":
    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None


def save_bot_token(token: str) -> bool:
    """Lưu Telegram Bot Token vào Windows Credential Manager (tuyệt đối không lưu ra config.json)."""
    tok = (token or "").strip()
    if not tok:
        return delete_bot_token()
    if sys.platform != "win32":
        return False
    try:
        blob = tok.encode("utf-16-le")
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = _CRED_TARGET
        cred.UserName = "RdriveTelegramBot"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = buf
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        ok = bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))
        if not ok:
            log.warning("Telegram CredWriteW that bai (err=%s)", ctypes.get_last_error())
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("Khong luu duoc Telegram token vao Credential Manager: %s", exc)
        return False


def load_bot_token() -> str:
    """Đọc Telegram Bot Token từ Windows Credential Manager."""
    if sys.platform != "win32":
        return ""
    pcred = ctypes.POINTER(_CREDENTIALW)()
    try:
        if not _advapi32.CredReadW(_CRED_TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            return ""
        try:
            size = int(pcred.contents.CredentialBlobSize)
            if size <= 0 or not pcred.contents.CredentialBlob:
                return ""
            raw = ctypes.string_at(pcred.contents.CredentialBlob, size)
            return raw.decode("utf-16-le", "ignore").strip()
        finally:
            _advapi32.CredFree(pcred)
    except Exception:  # noqa: BLE001
        return ""


def delete_bot_token() -> bool:
    """Xóa Telegram Bot Token khỏi Windows Credential Manager."""
    if sys.platform != "win32":
        return True
    try:
        _advapi32.CredDeleteW(_CRED_TARGET, _CRED_TYPE_GENERIC, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def mask_token(token: str) -> str:
    """Che token khi hiển thị trạng thái (không bao giờ in nguyên token)."""
    t = (token or "").strip()
    if len(t) <= 10:
        return "****" if t else ""
    return f"{t[:6]}…{t[-4:]}"


# ------------------------------------------------------------ Telegram HTTP API (luồng nền)
def send_telegram_sync(token: str, chat_id: str, html_text: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Gửi 1 tin nhắn Telegram (gọi từ luồng nền). Trả về (ok, message_khong_chua_token)."""
    tok = (token or "").strip()
    cid = (chat_id or "").strip()
    if not tok:
        return False, "Chưa nhập Bot Token."
    if not cid:
        return False, "Chưa nhập Chat ID."
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": cid,
            "text": html_text[:3900],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            if data.get("ok"):
                return True, "Đã gửi thông báo Telegram thành công!"
            return False, str(data.get("description") or "Telegram trả về lỗi không xác định.")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace") or "{}")
            desc = str(body.get("description") or f"HTTP {exc.code}")
        except Exception:  # noqa: BLE001
            desc = f"HTTP {exc.code}"
        return False, f"Lỗi Telegram ({exc.code}): {desc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Không kết nối được tới Telegram: {type(exc).__name__}"


def fetch_latest_chat_id(token: str, timeout: float = 10.0) -> tuple[bool, str, str]:
    """Tự động lấy Chat ID mới nhất từ getUpdates sau khi người dùng nhắn /start cho bot.
    Trả về (ok, chat_id, display_label_or_error).
    """
    tok = (token or "").strip()
    if not tok:
        return False, "", "Hãy dán Bot Token trước rồi bấm Tự lấy Chat ID."
    url = f"https://api.telegram.org/bot{tok}/getUpdates?limit=20"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        if not data.get("ok"):
            return False, "", str(data.get("description") or "Bot Token không hợp lệ.")
        updates = data.get("result") or []
        for upd in reversed(updates):
            msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is not None:
                name = " ".join(
                    p for p in [chat.get("first_name"), chat.get("last_name"), chat.get("title")] if p
                ).strip()
                uname = f"@{chat['username']}" if chat.get("username") else ""
                label = " ".join(p for p in [name, uname] if p).strip() or str(cid)
                return True, str(cid), label
        return (
            False,
            "",
            "Chưa thấy tin nhắn nào gửi cho bot.\nHãy mở Telegram, tìm bot của bạn rồi bấm Start (hoặc nhắn 'hi'), sau đó bấm nút này lại!",
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 404):
            return False, "", "Bot Token không đúng. Hãy kiểm tra lại mã lấy từ @BotFather."
        return False, "", f"Lỗi Telegram HTTP {exc.code}."
    except Exception as exc:  # noqa: BLE001
        return False, "", f"Không kết nối được tới Telegram: {type(exc).__name__}"


class TelegramNotifier:
    """Bộ gửi thông báo Telegram bất đồng bộ (luôn chạy luồng nền, không bao giờ chặn UI)."""

    def __init__(self, config) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._session_done = 0
        self._session_err = 0
        self._session_bytes = 0
        self._session_peak_bps = 0.0
        self._session_elapsed = 0.0

    def is_ready(self) -> bool:
        return bool(
            getattr(self.config, "telegram_enabled", False)
            and (getattr(self.config, "telegram_chat_id", "") or "").strip()
            and load_bot_token()
        )

    def send_async(self, html_text: str) -> None:
        if not getattr(self.config, "telegram_enabled", False):
            return
        chat_id = (getattr(self.config, "telegram_chat_id", "") or "").strip()
        token = load_bot_token()
        if not token or not chat_id:
            return

        def _worker() -> None:
            ok, msg = send_telegram_sync(token, chat_id, html_text, timeout=10.0)
            if not ok:
                log.warning("Telegram gui that bai: %s", msg)

        threading.Thread(target=_worker, name="rdrive-telegram", daemon=True).start()

    def record_job_done(self, title: str, total_bytes: int, elapsed: float, peak_bps: float, summary: str) -> None:
        with self._lock:
            self._session_done += 1
            self._session_bytes += max(0, int(total_bytes))
            self._session_elapsed += max(0.0, float(elapsed))
            if peak_bps > self._session_peak_bps:
                self._session_peak_bps = peak_bps
        if getattr(self.config, "telegram_notify_each", False):
            self.send_async(
                f"✅ <b>Rdrive — Hoàn tất</b>\n"
                f"📄 {html.escape(title)}\n"
                f"📊 {html.escape(summary)}"
            )

    def record_job_error(self, title: str, err_msg: str, stats_str: str = "") -> None:
        with self._lock:
            self._session_err += 1
        if getattr(self.config, "telegram_notify_error", True):
            extra = f"\n⏱ {html.escape(stats_str)}" if stats_str else ""
            self.send_async(
                f"❌ <b>Rdrive — Lỗi truyền tải</b>\n"
                f"📄 {html.escape(title)}{extra}\n"
                f"💬 <code>{html.escape(err_msg[:300])}</code>"
            )

    def notify_stall(self, title: str, detail: str) -> None:
        if getattr(self.config, "telegram_notify_stall", True):
            self.send_async(
                f"⚠️ <b>Rdrive — Cảnh báo nghẽn / tụt tốc</b>\n"
                f"📄 {html.escape(title)}\n"
                f"📉 {html.escape(detail[:300])}"
            )

    def flush_queue_summary(self) -> None:
        """Gửi báo cáo tổng kết khi toàn bộ hàng đợi truyền tải chạy xong."""
        with self._lock:
            done = self._session_done
            err = self._session_err
            total_b = self._session_bytes
            peak_bps = self._session_peak_bps
            elapsed = self._session_elapsed
            self._session_done = 0
            self._session_err = 0
            self._session_bytes = 0
            self._session_peak_bps = 0.0
            self._session_elapsed = 0.0

        if (done + err) == 0 or not getattr(self.config, "telegram_notify_done", True):
            return

        def _fmt_sz(b: int) -> str:
            if b >= 1024**3:
                return f"{b / (1024**3):.2f} GB"
            if b >= 1024**2:
                return f"{b / (1024**2):.1f} MB"
            if b >= 1024:
                return f"{b / 1024:.1f} KB"
            return f"{b} B"

        def _fmt_spd(bps: float) -> str:
            if bps >= 1024 * 1024:
                return f"{bps / (1024 * 1024):.2f} MB/s"
            if bps >= 1024:
                return f"{bps / 1024:.1f} KB/s"
            return f"{bps:.0f} B/s"

        avg_bps = (total_b / elapsed) if elapsed > 0.1 and total_b > 0 else 0.0
        icon = "🎉" if err == 0 else "⚠️"
        self.send_async(
            f"{icon} <b>Rdrive — Hoàn tất hàng đợi truyền tải</b>\n"
            f"✅ Thành công: <b>{done}</b>   ·   ❌ Lỗi: <b>{err}</b>\n"
            f"💾 Tổng dung lượng: <b>{_fmt_sz(total_b)}</b>\n"
            f"🚀 Tốc độ TB: <b>{_fmt_spd(avg_bps)}</b>   ·   Đỉnh: <b>{_fmt_spd(peak_bps)}</b>"
        )
