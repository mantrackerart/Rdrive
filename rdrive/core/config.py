"""Quản lý cấu hình bền vững cho Rdrive (data/config.json)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"
BACKUP_FILE = DATA_DIR / "config.json.bak"
_SAVE_LOCK = threading.Lock()


@dataclass
class AccountMeta:
    """Thông tin hiển thị bổ sung cho mỗi remote rclone (không lưu mật khẩu/token ở đây)."""
    remote: str
    display_name: str = ""
    email: str = ""
    note: str = ""
    category: str = ""
    default_view: str = "my_drive"   # "my_drive" | "shared_with_me" | "shared_drives"
    color: str = "#4f8cff"
    capacity_gb: int = 0             # NAS: dung lượng tối đa nhập tay (GB), 0 = tự đọc

    def label(self) -> str:
        return self.display_name.strip() or self.remote


@dataclass
class AIConfig:
    """Cấu hình kết nối AI quản lý (Local AI hoặc Cloud AI)."""
    mode: str = "local"              # "local" | "cloud"
    # Local AI (Ollama / LM Studio / OpenAI-compatible)
    local_provider: str = "ollama"   # "ollama" | "lmstudio" | "openai_compat"
    local_url: str = "http://127.0.0.1:11434"
    local_model: str = "qwen2.5:7b"
    # Cloud AI (Gemini / OpenAI / Custom)
    cloud_provider: str = "gemini"   # "gemini" | "openai" | "custom"
    cloud_url: str = ""
    cloud_model: str = "gemini-2.5-flash"
    cloud_api_key: str = ""
    compact_mode: bool = True        # Gom nhóm file theo mẫu để tiết kiệm ~80% token
    max_output_tokens: int = 512     # Giới hạn độ dài phản hồi để tránh tốn token
    system_prompt: str = (
        "Trợ lý Rdrive (rclone/Google Drive). Trả lời tiếng Việt cực ngắn gọn, rõ bước, không lan man."
    )


@dataclass
class AutoRule:
    """Nền tảng quy tắc tự động (sẽ mở rộng chi tiết theo yêu cầu người dùng)."""
    id: str = ""
    name: str = ""
    enabled: bool = True
    kind: str = "backup"             # "backup" | "sort" | "sync"
    source: str = ""
    destination: str = ""
    schedule: str = "manual"         # "manual" | "watch" | "hourly" | "daily"
    use_ai: bool = False
    note: str = ""


@dataclass
class AppConfig:
    rclone_path: str = ""            # "" = tự tìm rclone.exe
    transfers: int = 6               # Số luồng Upload cùng lúc (và --transfers khi copy thư mục)
    upload_threads: int = 6          # Số file Upload chạy song song trong hàng đợi
    download_threads: int = 6        # Số file Download chạy song song trong hàng đợi
    checkers: int = 8                # --checkers
    retries: int = 6                 # --retries (thử lại khi thất bại)
    drive_chunk_mb: int = 256        # --drive-chunk-size (M — lũy thừa của 2: 64/128/256/512)
    use_rcd: bool = True             # duyệt Drive qua 1 rclone thường trực (rclone rcd) — nhanh hơn; False = cách cũ
    drive_pacer_ms: int = 100        # --drive-pacer-min-sleep (ms). 100 = mặc định rclone; thấp hơn dễ bị Google trả 403 rate limit
    bwlimit: str = ""                # legacy / custom bwlimit
    dl_limit_enabled: bool = False
    dl_limit_kbps: int = 0           # KB/s
    ul_limit_enabled: bool = False
    ul_limit_kbps: int = 0           # KB/s
    multi_thread_download: bool = True
    idm_streams: int = 16            # Số luồng đa kết nối trên 1 file lớn (IDM Mode cho mạng 1-2 Gbps)
    auto_refresh_after_upload: bool = True
    ignore_existing: bool = False    # bỏ qua file đã tồn tại khi copy
    show_hidden: bool = False
    auto_clear_done: bool = False
    minimize_to_tray: bool = False
    autostart_windows: bool = False
    # Logs (luôn tự động lưu)
    detailed_log: bool = True
    log_dir: str = ""
    log_rotate_mb: int = 10
    # Proxy
    proxy_enabled: bool = False
    proxy_url: str = ""
    proxy_user: str = ""
    proxy_pass: str = ""
    # SyAu
    syau_dir: str = ""   # trống = tự tìm thư mục SyAu cạnh Rdrive (nếu có)
    syau_server_url: str = "http://127.0.0.1:47811"
    update_repo: str = ""            # GitHub "chủ/kho" chứa bản phát hành (để trống = tắt tự cập nhật)
    update_auto_check: bool = True
    last_left_path: str = ""
    last_right_remote: str = ""
    left_tabs_state: list[dict] = field(default_factory=list)
    right_tabs_state: list[dict] = field(default_factory=list)
    active_left_tab: int = 0
    active_right_tab: int = 0
    favourites: list[dict] = field(default_factory=list)       # [{title, kind, remote, local_path, drive_mode, rel_path, team_drive_id, root_folder_id}]
    mounts: list[dict] = field(default_factory=list)           # [{letter, remote, subpath, label, auto_mount}]
    hotkeys: dict[str, str] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    accounts: dict[str, dict] = field(default_factory=dict)   # remote_name -> AccountMeta dict
    ai: dict = field(default_factory=lambda: asdict(AIConfig()))
    auto_rules: list[dict] = field(default_factory=list)
    # Telegram (Bot Token lưu riêng trong Windows Credential Manager — không lưu vào config.json)
    telegram_enabled: bool = False
    telegram_chat_id: str = ""
    telegram_notify_done: bool = True
    telegram_notify_each: bool = False
    telegram_notify_error: bool = True
    telegram_notify_stall: bool = True

    def effective_bwlimit(self) -> str:
        """Tính chuỗi --bwlimit cho rclone (hỗ trợ giới hạn riêng Upload:Download dạng 'UL:DL')."""
        ul = f"{self.ul_limit_kbps}K" if (self.ul_limit_enabled and self.ul_limit_kbps > 0) else "off"
        dl = f"{self.dl_limit_kbps}K" if (self.dl_limit_enabled and self.dl_limit_kbps > 0) else "off"
        if ul != "off" or dl != "off":
            return f"{ul}:{dl}"
        return self.bwlimit.strip()

    def get_account_meta(self, remote: str) -> AccountMeta:
        raw = self.accounts.get(remote) or {}
        known = {k for k in AccountMeta.__dataclass_fields__}
        data = {k: v for k, v in raw.items() if k in known}
        data["remote"] = remote
        return AccountMeta(**data)

    def set_account_meta(self, meta: AccountMeta) -> None:
        self.accounts[meta.remote] = asdict(meta)

    def remove_account_meta(self, remote: str) -> None:
        self.accounts.pop(remote, None)

    def get_ai(self) -> AIConfig:
        raw = self.ai or {}
        known = {k for k in AIConfig.__dataclass_fields__}
        return AIConfig(**{k: v for k, v in raw.items() if k in known})

    def set_ai(self, ai_cfg: AIConfig) -> None:
        self.ai = asdict(ai_cfg)

    def get_rules(self) -> list[AutoRule]:
        out: list[AutoRule] = []
        known = {k for k in AutoRule.__dataclass_fields__}
        for r in self.auto_rules:
            if isinstance(r, dict):
                out.append(AutoRule(**{k: v for k, v in r.items() if k in known}))
        return out

    def set_rules(self, rules: list[AutoRule]) -> None:
        self.auto_rules = [asdict(r) for r in rules]

    def save(self) -> None:
        """Ghi config.json an toan: ghi file tam roi doi ten; giu ban truoc o config.json.bak.
        Co khoa vi nhieu cho (luong nen, hop thoai) cung goi save()."""
        with _SAVE_LOCK:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, ensure_ascii=False, indent=2)
            if CONFIG_FILE.is_file():
                try:
                    shutil.copyfile(CONFIG_FILE, BACKUP_FILE)
                except OSError:
                    pass
            os.replace(tmp, CONFIG_FILE)

    @classmethod
    def _read(cls, path: Path) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError("config khong phai doi tuong JSON")
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    @classmethod
    def load(cls) -> "AppConfig":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.is_file():
            if BACKUP_FILE.is_file():          # mat config.json (vd tat may giua luc ghi) -> lay ban sao luu
                try:
                    return cls._read(BACKUP_FILE)
                except Exception:  # noqa: BLE001
                    pass
            cfg = cls()
            cfg.save()
            return cfg
        try:
            return cls._read(CONFIG_FILE)
        except Exception as exc:  # noqa: BLE001
            # KHONG tra ve cau hinh trang roi ghi de: cat file hong sang ten khac de con cuu duoc,
            # roi thu ban sao luu. (Truoc day: mat het Favourite, Mount, thong tin tai khoan.)
            bad = CONFIG_FILE.with_name(f"config.loi-{time.strftime('%Y%m%d-%H%M%S')}.json")
            try:
                shutil.copyfile(CONFIG_FILE, bad)
            except OSError:
                pass
            logging.getLogger("rdrive").error("config.json hong (%s) - da cat ra %s, dung ban sao luu", exc, bad.name)
            if BACKUP_FILE.is_file():
                try:
                    return cls._read(BACKUP_FILE)
                except Exception:  # noqa: BLE001
                    pass
            return cls()
