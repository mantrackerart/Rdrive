"""Lớp giao tiếp trực tiếp với rclone.exe cho Rdrive (Google Drive đa tài khoản, Shared Drives, Shared with me, Transfer)."""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_AUTH_URL_RE = re.compile(r"(https?://127\.0\.0\.1:\d+/[^\s\"']+)")
_NATURAL_SPLIT_RE = re.compile(r"(\d+)")

_WIN_STRCMPLOGICAL = None
if sys.platform == "win32":
    try:
        import ctypes
        _WIN_STRCMPLOGICAL = ctypes.windll.shlwapi.StrCmpLogicalW
        _WIN_STRCMPLOGICAL.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        _WIN_STRCMPLOGICAL.restype = ctypes.c_int
    except Exception:
        _WIN_STRCMPLOGICAL = None


def natural_sort_key(text: str) -> list[tuple[int, int | str]]:
    """Khóa sắp xếp tự nhiên giống Windows Explorer (vd: 'quay 2.11' < 'quay 10.11', '4-IELTS' < '1013')."""
    parts = _NATURAL_SPLIT_RE.split((text or "").lower())
    key: list[tuple[int, int | str]] = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            key.append((0, int(p)))
        else:
            key.append((1, p))
    return key


def win_logical_less(a: str, b: str) -> bool:
    """So sánh 2 tên file/thư mục y hệt Windows Explorer (dùng StrCmpLogicalW gốc của Windows)."""
    if _WIN_STRCMPLOGICAL is not None:
        try:
            return _WIN_STRCMPLOGICAL(a or "", b or "") < 0
        except Exception:
            pass
    return natural_sort_key(a) < natural_sort_key(b)


class RcloneError(RuntimeError):
    """Lỗi khi gọi rclone."""


@dataclass
class DriveEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mod_time: str = ""
    mime_type: str = ""
    item_id: str = ""
    detail: str = ""
    owner: str = ""

    @property
    def kind_label(self) -> str:
        if self.is_dir:
            return "Thư mục"
        ext = Path(self.name).suffix.lower()
        if ext:
            return f"{ext[1:].upper()} File"
        if self.mime_type and "google-apps" in self.mime_type:
            return "Google Doc"
        return "Tệp"


@dataclass
class DriveAbout:
    total: int = 0
    used: int = 0
    free: int = 0
    trashed: int = 0
    other: int = 0


@dataclass
class RemoteInfo:
    name: str
    kind: str


@dataclass
class SharedDrive:
    id: str
    name: str


def find_rclone(hint: str = "") -> str:
    """Tìm rclone.exe trên máy (PATH, WinGet, Scoop, Program Files, thư mục app)."""
    root = Path(__file__).resolve().parent.parent.parent
    cands: list[str] = [hint] if hint else []
    cands += [
        shutil.which("rclone") or "",
        str(root / "tools" / "rclone.exe"),
        str(root / "bin" / "rclone.exe"),
    ]
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        cands.append(os.path.join(la, "Microsoft", "WinGet", "Links", "rclone.exe"))
        cands += sorted(
            glob.glob(
                os.path.join(la, "Microsoft", "WinGet", "Packages", "Rclone.Rclone*", "**", "rclone.exe"),
                recursive=True,
            ),
            reverse=True,
        )
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "rclone", "rclone.exe"))
    home = os.path.expanduser("~")
    cands += [
        os.path.join(home, "scoop", "shims", "rclone.exe"),
        r"C:\rclone\rclone.exe",
        os.path.join(home, "rclone", "rclone.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def sanitize_remote_name(name: str) -> str:
    """Chuẩn hóa tên remote hợp lệ cho rclone (chỉ giữ chữ, số, _, -, .)."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip())
    cleaned = cleaned.strip("_.-")
    if not cleaned:
        return ""
    if cleaned[0].isdigit():
        cleaned = "gd_" + cleaned
    return cleaned[:48]


def folder_id_from_link(text: str) -> str:
    """Trích xuất Google Drive folder ID từ link hoặc ID trần."""
    s = (text or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", s) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", s)
    if m:
        return m.group(1)
    return s if re.fullmatch(r"[A-Za-z0-9_-]{10,}", s) else ""


def format_size(num: int | float) -> str:
    if num is None or num < 0:
        return ""
    val = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:,.1f} {unit}"
        val /= 1024.0
    return f"{val:,.1f} TB"


class RcloneClient:
    """Wrapper an toàn, không mở cửa sổ console cho rclone.exe."""

    def __init__(self, exe: str = "") -> None:
        self.exe = exe or find_rclone()
        self.use_rcd = True              # duyet Drive qua rclone thuong truc (xem rclone_rcd.py)
        self._rcd = None

    # ------------------------------------------------------------ rclone thuong truc (rcd)
    def _daemon(self):
        if self._rcd is None:
            from .config import LOGS_DIR
            from .logger import get_logger
            from .rclone_rcd import RcdDaemon
            self._rcd = RcdDaemon(LOGS_DIR / "rcd.log",
                                  ["--drive-pacer-min-sleep", "100ms", "--drive-pacer-burst", "100"],
                                  logger=get_logger())
        return self._rcd

    def _rcd_call(self, method: str, params: dict, timeout: float):
        """Goi rclone thuong truc. Tra ve None neu khong dung duoc (-> goi cach cu).
        Loi that tu Google (folder khong co, token het han) -> RcloneError nhu cach cu."""
        if not self.use_rcd:
            return None
        from .rclone_rcd import RcdCallError, RcdTransportError
        try:
            return self._daemon().call(self.ensure_exe(), method, params, timeout)
        except RcdTransportError:
            return None
        except RcdCallError as exc:
            msg = str(exc)
            low = msg.lower()
            if "empty token found" in low or "token expired" in low or "invalid_grant" in low:
                raise RcloneError(f"Cần đăng nhập lại tài khoản Google ({msg[:220]})") from exc
            raise RcloneError(msg[:400]) from exc

    def restart_daemon(self) -> None:
        """Goi sau khi them / sua / xoa tai khoan (rcd dang giu token cu trong bo nho)."""
        if self._rcd is not None:
            self._rcd.restart()

    def close(self) -> None:
        if self._rcd is not None:
            self._rcd.stop()

    def ensure_exe(self) -> str:
        if not self.exe or not os.path.isfile(self.exe):
            self.exe = find_rclone()
        if not self.exe:
            raise RcloneError(
                "Chưa tìm thấy rclone.exe trên máy.\n"
                "Hãy cài rclone (winget install Rclone.Rclone) hoặc chọn đường dẫn trong Tùy Chọn."
            )
        return self.exe

    def _run(self, args: list[str], timeout: float = 60.0) -> str:
        # Them / sua / xoa / dang xuat tai khoan: rclone thuong truc dang giu token cu -> khoi dong lai
        if len(args) >= 2 and args[0] == "config" and args[1] in ("create", "update", "delete", "disconnect",
                                                                  "reconnect"):
            try:
                return self._run_once(args, timeout)
            finally:
                self.restart_daemon()
        return self._run_once(args, timeout)

    def _run_once(self, args: list[str], timeout: float = 60.0) -> str:
        exe = self.ensure_exe()
        try:
            p = subprocess.run(
                [exe, *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise RcloneError(f"rclone không phản hồi sau {int(timeout)} giây.") from exc
        except OSError as exc:
            raise RcloneError(str(exc)) from exc

        stdout = p.stdout.decode("utf-8", "replace")
        stderr = p.stderr.decode("utf-8", "replace")
        if p.returncode != 0:
            lines = [ln.strip() for ln in stderr.splitlines() if ln.strip() and "NOTICE:" not in ln]
            if not lines:
                lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
            msg = lines[-1] if lines else f"rclone thoát với mã lỗi {p.returncode}"
            if "empty token found" in stderr.lower() or "token expired" in stderr.lower() or "invalid_grant" in stderr.lower():
                raise RcloneError(f"Cần đăng nhập lại tài khoản Google ({msg[:220]})")
            raise RcloneError(msg[:400])
        return stdout

    def version(self) -> str:
        out = self._run(["version"], timeout=15)
        lines = out.splitlines()
        return lines[0].strip() if lines else ""

    def config_file(self) -> str:
        out = self._run(["config", "file"], timeout=15)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def list_remotes(self, only_drive: bool = True, include_nas: bool = True) -> list[RemoteInfo]:
        out = self._run(["listremotes", "--long"], timeout=20)
        res: list[RemoteInfo] = []
        allowed = {"drive", "smb", "sftp", "webdav", "ftp"} if include_nas else {"drive"}
        for line in out.splitlines():
            if ":" not in line:
                continue
            name, kind = line.split(":", 1)
            name, kind = name.strip(), kind.strip()
            if not only_drive or kind in allowed:
                res.append(RemoteInfo(name=name, kind=kind))
        return res

    def create_nas_remote(
        self,
        name: str,
        protocol: str,
        host: str,
        port: int,
        user: str,
        password: str,
        extra_path: str = "",
    ) -> None:
        """Tạo kết nối NAS Xpenology / Synology qua SMB, SFTP, WebDAV hoặc FTP."""
        kv: list[str] = []
        if protocol == "webdav":
            url = host if host.startswith(("http://", "https://")) else f"http://{host}:{port or 5005}/{extra_path.lstrip('/')}"
            kv += [f"url={url}", "vendor=other"]
        else:
            kv.append(f"host={host}")
            if port > 0:
                kv.append(f"port={port}")
        if user.strip():
            kv.append(f"user={user.strip()}")
        if password:
            kv.append(f"pass={password}")
        self._run(["config", "create", name, protocol, *kv, "--obscure", "--non-interactive"], timeout=30)

    def folder_size_info(self, spec: str) -> tuple[int, int]:
        """Trả về (số_file, tổng_bytes) của thư mục/file trên Drive hoặc NAS bằng `rclone size --json`."""
        out = self._run(["size", spec, "--json"], timeout=120)
        data = json.loads(out or "{}")
        return int(data.get("count") or 0), int(data.get("bytes") or 0)

    def create_share_link(self, spec: str, item_id: str = "", is_dir: bool = False) -> str:
        """Tạo/lấy link chia sẻ công khai qua `rclone link`, dự phòng về link Google Drive chuẩn."""
        try:
            out = self._run(["link", spec], timeout=25).strip()
            if out.startswith("http"):
                return out.splitlines()[-1].strip()
        except Exception:
            pass
        if item_id:
            return (
                f"https://drive.google.com/drive/folders/{item_id}?usp=sharing"
                if is_dir
                else f"https://drive.google.com/file/d/{item_id}/view?usp=sharing"
            )
        raise RcloneError("Không lấy được liên kết chia sẻ cho mục này.")

    def check_rclone_update(self) -> str:
        """Kiểm tra phiên bản rclone hiện tại so với bản mới nhất trên rclone.org."""
        try:
            out = self._run(["version", "--check"], timeout=20)
            return out.strip()
        except RcloneError as exc:
            return f"Phiên bản hiện tại: {self.version()}\n({exc})"

    def list_shared_by_owners(self, remote: str) -> dict[str, list[DriveEntry]]:
        """Lấy toàn bộ mục 'Được chia sẻ với tôi' và gom nhóm theo chủ sở hữu '<tên chủ> Drive' kiểu 2 khung."""
        import urllib.parse
        import urllib.request
        from collections import defaultdict
        from .media_info import format_duration

        try:
            self.about(remote)
        except Exception:
            pass

        cfg = self.config_dump().get(remote) or {}
        if cfg.get("type") != "drive":
            return {}
        raw_tok = str(cfg.get("token") or "").strip()
        if not raw_tok:
            return {}
        at = str(json.loads(raw_tok).get("access_token") or "").strip()
        if not at:
            return {}

        params = {
            "q": "sharedWithMe = true and trashed = false",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "pageSize": "300",
            "fields": "files(id,name,mimeType,size,modifiedTime,owners(displayName,emailAddress),sharingUser(displayName,emailAddress),videoMediaMetadata(width,height,durationMillis))",
        }
        url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"})
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return {}

        grouped: dict[str, list[DriveEntry]] = defaultdict(list)
        for f in data.get("files") or []:
            ow = (f.get("owners") or [{}])[0]
            sh = f.get("sharingUser") or {}
            email = str(ow.get("emailAddress") or sh.get("emailAddress") or "").strip()
            dname = str(ow.get("displayName") or sh.get("displayName") or "").strip()
            if email and "@" in email:
                owner_tag = f"{email.split('@', 1)[0]} Drive"
            elif dname:
                owner_tag = f"{dname} Drive"
            else:
                owner_tag = "Shared Drive"

            mime = str(f.get("mimeType") or "")
            is_dir = mime == "application/vnd.google-apps.folder"
            vmeta = f.get("videoMediaMetadata") or {}
            detail = ""
            if vmeta:
                dur_ms = int(vmeta.get("durationMillis") or 0)
                w, h = vmeta.get("width"), vmeta.get("height")
                parts = []
                if dur_ms > 0:
                    parts.append(format_duration(dur_ms / 1000.0))
                if w and h:
                    parts.append(f"{w}x{h}")
                detail = " · ".join(parts)

            name = str(f.get("name") or "")
            grouped[owner_tag].append(
                DriveEntry(
                    name=name,
                    path=name,
                    is_dir=is_dir,
                    size=0 if is_dir else int(f.get("size") or 0),
                    mod_time=str(f.get("modifiedTime") or ""),
                    mime_type=mime,
                    item_id=str(f.get("id") or ""),
                    detail=detail,
                    owner=email or dname,
                )
            )

        out_sorted: dict[str, list[DriveEntry]] = {}
        for k in sorted(grouped.keys(), key=natural_sort_key):
            items = grouped[k]
            items.sort(key=lambda e: (not e.is_dir, natural_sort_key(e.name)))
            out_sorted[k] = items
        return out_sorted

    def delete_remote(self, remote: str) -> None:
        self._run(["config", "delete", remote], timeout=20)

    def disconnect_remote(self, remote: str) -> None:
        """Đăng xuất tài khoản Google Drive: thu hồi OAuth token và xóa token trong rclone.conf (giữ lại thẻ tài khoản)."""
        try:
            self._run(["config", "disconnect", f"{remote}:"], timeout=25)
        except RcloneError:
            pass
        # Đảm bảo token được xóa trắng ngay cả khi token cũ đã hỏng/hết hạn
        self._run(["config", "update", remote, "token=", "--non-interactive"], timeout=20)

    def config_dump(self) -> dict:
        out = self._run(["config", "dump"], timeout=20)
        return json.loads(out or "{}")

    def has_token(self, remote: str) -> bool:
        try:
            cfg = self.config_dump().get(remote) or {}
            tok = str(cfg.get("token") or "").strip()
            return bool(tok and tok != "{}")
        except Exception:
            return False

    def fetch_google_profile(self, remote: str) -> tuple[str, str]:
        """Lấy (displayName, emailAddress) thật từ Google Drive API bằng access_token đang có trong rclone."""
        import urllib.request

        try:
            cfg = self.config_dump().get(remote) or {}
            raw_tok = str(cfg.get("token") or "").strip()
            if not raw_tok:
                return "", ""
            tok = json.loads(raw_tok)
            at = str(tok.get("access_token") or "").strip()
            if not at:
                return "", ""
            req = urllib.request.Request(
                "https://www.googleapis.com/drive/v3/about?fields=user(displayName,emailAddress)",
                headers={"Authorization": f"Bearer {at}"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            user = data.get("user") or {}
            return str(user.get("displayName") or "").strip(), str(user.get("emailAddress") or "").strip()
        except Exception:
            return "", ""

    def import_drive_remote(self, name: str, params: dict) -> None:
        """Khôi phục 1 remote Google Drive từ dữ liệu Export (JSON)."""
        kv = [f"{k}={v}" for k, v in params.items() if k != "type" and v is not None]
        self._run(["config", "create", name, "drive", *kv, "--non-interactive"], timeout=25)

    def lsjson_recursive(
        self,
        spec: str,
        *,
        with_hash: bool = False,
        include_pattern: str = "",
        exclude_patterns: list[str] | None = None,
        max_depth: int = 0,
        timeout: float = 180.0,
    ) -> list[dict]:
        """Liệt kê đệ quy danh sách file cho So sánh Đồng bộ & Tìm kiếm trong thư mục chỉ định."""
        args = ["lsjson", spec, "-R", "--files-only"]
        if with_hash:
            args += ["--hash", "--hash-type", "md5"]
        if max_depth > 0:
            args += ["--max-depth", str(max_depth)]
        if include_pattern:
            args += ["--include", include_pattern]
        for pat in (exclude_patterns or []):
            p = pat.strip()
            if p:
                args += ["--exclude", p]
        out = self._run(args, timeout=timeout)
        return json.loads(out or "[]")

    def search_google_drive(
        self,
        remote: str,
        keyword: str,
        *,
        team_drive_id: str = "",
        max_results: int = 200,
    ) -> list[dict]:
        """Tìm kiếm siêu nhanh phía máy chủ Google Drive API v3 (< 1 giây trên toàn bộ Drive chỉ định)."""
        import urllib.parse
        import urllib.request

        # Làm mới token nếu cần bằng lệnh about nhẹ
        try:
            self.about(remote, team_drive=team_drive_id)
        except RcloneError as exc:
            raise RcloneError(f"[{remote}] {exc}") from exc

        cfg = self.config_dump().get(remote) or {}
        raw_tok = str(cfg.get("token") or "").strip()
        if not raw_tok:
            raise RcloneError(f"[{remote}] Tài khoản chưa đăng nhập.")
        at = str(json.loads(raw_tok).get("access_token") or "").strip()
        if not at:
            raise RcloneError(f"[{remote}] Không tìm thấy access_token.")

        safe_kw = keyword.replace("\\", "\\\\").replace("'", "\\'")
        q_expr = f"name contains '{safe_kw}' and trashed = false" if safe_kw else "trashed = false"
        params = {
            "q": q_expr,
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "pageSize": str(min(500, max(10, max_results))),
            "fields": "files(id,name,mimeType,size,modifiedTime,parents,driveId)",
        }
        if team_drive_id:
            params["corpora"] = "drive"
            params["driveId"] = team_drive_id
        else:
            params["corpora"] = "allDrives"

        url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
        import time
        import urllib.error

        data = None
        last_err: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"})
            try:
                with urllib.request.urlopen(req, timeout=25.0) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                break
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code in (403, 429) and attempt < 2:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise RcloneError(f"[{remote}] Lỗi tìm kiếm Drive API: HTTP {exc.code}") from exc
            except Exception as exc:  # noqa: BLE001
                raise RcloneError(f"[{remote}] Lỗi tìm kiếm Drive API: {exc}") from exc
        if data is None:
            raise RcloneError(f"[{remote}] Lỗi tìm kiếm Drive API: {last_err}")

        files = data.get("files") or []
        # Giải mã tên thư mục cha (tối đa 25 parent ID khác nhau để giữ siêu nhanh)
        parent_names: dict[str, str] = {}
        unique_pids: list[str] = []
        for f in files:
            ps = f.get("parents") or []
            if ps and ps[0] not in parent_names and ps[0] not in unique_pids:
                unique_pids.append(ps[0])
        for pid in unique_pids[:25]:
            try:
                pu = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(pid)}?supportsAllDrives=true&fields=name"
                preq = urllib.request.Request(pu, headers={"Authorization": f"Bearer {at}"})
                with urllib.request.urlopen(preq, timeout=5.0) as presp:
                    pdata = json.loads(presp.read().decode("utf-8", "replace"))
                parent_names[pid] = str(pdata.get("name") or pid)
            except Exception:
                parent_names[pid] = ""

        out: list[dict] = []
        for f in files:
            mime = str(f.get("mimeType") or "")
            is_dir = mime == "application/vnd.google-apps.folder"
            ps = f.get("parents") or []
            pid = ps[0] if ps else ""
            out.append(
                {
                    "id": str(f.get("id") or ""),
                    "name": str(f.get("name") or ""),
                    "is_dir": is_dir,
                    "size": 0 if is_dir else int(f.get("size") or 0),
                    "mod_time": str(f.get("modifiedTime") or ""),
                    "mime_type": mime,
                    "parent_id": pid,
                    "parent_name": parent_names.get(pid, "") or ("Shared Drive" if f.get("driveId") else "Drive"),
                    "remote": remote,
                }
            )
        return out

    def authorize_google_drive(
        self,
        *,
        client_id: str = "",
        client_secret: str = "",
        scope: str = "drive",
        on_auth_url: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Mở trình duyệt đăng nhập Google OAuth qua `rclone authorize` và trả về chuỗi JSON token."""
        exe = self.ensure_exe()
        cmd = [exe, "authorize", "drive"]
        if client_id.strip() and client_secret.strip():
            cmd += [client_id.strip(), client_secret.strip()]
        if scope:
            cmd += ["--drive-scope", scope]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def read_stream(stream, sink: list[str]) -> None:
            if stream is None:
                return
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                sink.append(line)
                if on_log and line.strip():
                    on_log(line)
                m = _AUTH_URL_RE.search(line)
                if m and on_auth_url:
                    on_auth_url(m.group(1))
            stream.close()

        t_out = threading.Thread(target=read_stream, args=(proc.stdout, stdout_lines), daemon=True)
        t_err = threading.Thread(target=read_stream, args=(proc.stderr, stderr_lines), daemon=True)
        t_out.start()
        t_err.start()

        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RcloneError("Đã hủy đăng nhập Google.")
            threading.Event().wait(0.15)

        t_out.join(timeout=2)
        t_err.join(timeout=2)

        if proc.returncode != 0:
            err = next((ln for ln in reversed(stderr_lines) if ln.strip()), f"rclone authorize lỗi ({proc.returncode})")
            raise RcloneError(err[:350])

        full_out = "\n".join(stdout_lines)
        # Trích xuất JSON token nằm giữa "Paste the following" và "<---End paste" hoặc dòng JSON chứa access_token
        token_json = ""
        m = re.search(r"--->\s*(\{.*?\})\s*<---", full_out, re.DOTALL)
        if m:
            token_json = m.group(1).strip()
        else:
            for ln in stdout_lines:
                s = ln.strip()
                if s.startswith("{") and "access_token" in s and s.endswith("}"):
                    token_json = s
                    break
        if not token_json:
            raise RcloneError("Không đọc được token sau khi đăng nhập Google.")
        # Kiểm tra JSON hợp lệ
        json.loads(token_json)
        return token_json

    def create_or_update_drive_remote(
        self,
        remote: str,
        token_json: str,
        *,
        scope: str = "drive",
        client_id: str = "",
        client_secret: str = "",
        update_existing: bool = False,
    ) -> None:
        """Lưu remote Google Drive vào rclone.conf với token đã xác thực (không treo hỏi tương tác)."""
        kv: list[str] = [f"scope={scope}", f"token={token_json}", "config_change_team_drive=false"]
        if client_id.strip():
            kv.append(f"client_id={client_id.strip()}")
        if client_secret.strip():
            kv.append(f"client_secret={client_secret.strip()}")
        if update_existing:
            self._run(["config", "update", remote, *kv, "--non-interactive"], timeout=30)
        else:
            self._run(["config", "create", remote, "drive", *kv, "--non-interactive"], timeout=30)

    @staticmethod
    def build_drive_flags(
        *,
        folder_id: str = "",
        shared_with_me: bool = False,
        team_drive: str = "",
    ) -> list[str]:
        flags: list[str] = []
        if team_drive:
            flags += ["--drive-team-drive", team_drive]
        if folder_id:
            flags += ["--drive-root-folder-id", folder_id]
        if shared_with_me:
            flags += ["--drive-shared-with-me"]
        return flags

    def about(self, remote: str, team_drive: str = "") -> DriveAbout:
        from .rclone_rcd import fs_string
        data = self._rcd_call("operations/about", {"fs": fs_string(remote, team_drive=team_drive)}, 30.0)
        if data is None:
            args = ["about", f"{remote}:", "--json"]
            if team_drive:
                args += ["--drive-team-drive", team_drive]
            out = self._run(args, timeout=30)
            data = json.loads(out or "{}")
        return DriveAbout(
            total=int(data.get("total") or 0),
            used=int(data.get("used") or 0),
            free=int(data.get("free") or 0),
            trashed=int(data.get("trashed") or 0),
            other=int(data.get("other") or 0),
        )

    def list_shared_drives(self, remote: str) -> list[SharedDrive]:
        out = self._run(["backend", "drives", f"{remote}:"], timeout=45)
        data = json.loads(out or "[]")
        drives = [SharedDrive(id=d.get("id", ""), name=d.get("name", "")) for d in data if d.get("id")]
        return sorted(drives, key=lambda d: d.name.lower())

    def lsjson(
        self,
        remote: str,
        rel_path: str = "",
        *,
        folder_id: str = "",
        shared_with_me: bool = False,
        team_drive: str = "",
        dirs_only: bool = False,
        timeout: float = 90.0,
    ) -> list[DriveEntry]:
        from .rclone_rcd import fs_string
        rcd = self._rcd_call("operations/list", {
            "fs": fs_string(remote, folder_id=folder_id, shared_with_me=shared_with_me, team_drive=team_drive),
            "remote": rel_path.strip("/"),
            "opt": {"dirsOnly": bool(dirs_only)},
        }, timeout)
        if rcd is not None:
            raw = rcd.get("list") or []
        else:
            target = f"{remote}:{rel_path.strip('/')}" if rel_path.strip("/") else f"{remote}:"
            args = ["lsjson", target, "--max-depth", "1"]
            if dirs_only:
                args.append("--dirs-only")
            args += self.build_drive_flags(
                folder_id=folder_id,
                shared_with_me=shared_with_me,
                team_drive=team_drive,
            )
            out = self._run(args, timeout=timeout)
            raw = json.loads(out or "[]")
        entries: list[DriveEntry] = []
        for item in raw:
            entries.append(
                DriveEntry(
                    name=item.get("Name", ""),
                    path=item.get("Path", item.get("Name", "")),
                    is_dir=bool(item.get("IsDir", False)),
                    size=int(item.get("Size") or 0),
                    mod_time=str(item.get("ModTime") or ""),
                    mime_type=str(item.get("MimeType") or ""),
                    item_id=str(item.get("ID") or ""),
                )
            )
        entries.sort(key=lambda e: (not e.is_dir, natural_sort_key(e.name)))
        return entries

    list_dir = lsjson

    def mkdir(
        self,
        remote: str,
        rel_path: str,
        *,
        folder_id: str = "",
        shared_with_me: bool = False,
        team_drive: str = "",
    ) -> None:
        target = f"{remote}:{rel_path.strip('/')}"
        args = ["mkdir", target, *self.build_drive_flags(folder_id=folder_id, shared_with_me=shared_with_me, team_drive=team_drive)]
        self._run(args, timeout=45)

    def delete_item(
        self,
        remote: str,
        rel_path: str,
        is_dir: bool,
        *,
        folder_id: str = "",
        shared_with_me: bool = False,
        team_drive: str = "",
    ) -> None:
        target = f"{remote}:{rel_path.strip('/')}"
        cmd = "purge" if is_dir else "deletefile"
        args = [cmd, target, *self.build_drive_flags(folder_id=folder_id, shared_with_me=shared_with_me, team_drive=team_drive)]
        self._run(args, timeout=120)

    def rename_item(
        self,
        remote: str,
        old_rel_path: str,
        new_rel_path: str,
        *,
        folder_id: str = "",
        shared_with_me: bool = False,
        team_drive: str = "",
    ) -> None:
        src = f"{remote}:{old_rel_path.strip('/')}"
        dst = f"{remote}:{new_rel_path.strip('/')}"
        args = ["moveto", src, dst, *self.build_drive_flags(folder_id=folder_id, shared_with_me=shared_with_me, team_drive=team_drive)]
        self._run(args, timeout=90)

    def run_transfer(
        self,
        command: str,
        src: str,
        dst: str,
        *,
        extra_args: list[str] | None = None,
        transfers: int = 4,
        checkers: int = 8,
        drive_chunk_mb: int = 64,
        drive_pacer_ms: int = 100,
        bwlimit: str = "",
        dry_run: bool = False,
        on_progress: Callable[[dict], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[str]:
        """Chạy copy/copyto/move/moveto/sync với cập nhật tiến độ JSON thời gian thực."""
        exe = self.ensure_exe()
        extra = list(extra_args or [])
        cmd = [
            exe,
            command,
            src,
            dst,
            "--transfers",
            str(max(1, transfers)),
            "--checkers",
            str(max(1, checkers)),
            "--drive-chunk-size",
            f"{max(8, drive_chunk_mb)}M",
            "--buffer-size",
            "32M",
            # Nhip goi API Google Drive: 100 ms / burst 100 la mac dinh cua rclone. Ban truoc dat 10 ms / 200
            # -> goi qua day, Google tra 403 "rate limit exceeded" va rclone phai cho lau hon (cham hon).
            "--drive-pacer-min-sleep",
            f"{max(20, int(drive_pacer_ms or 100))}ms",
            "--drive-pacer-burst",
            "100",
            "--timeout",
            "5m",
            "--contimeout",
            "30s",
            "--stats",
            "0.6s",
            "--stats-log-level",
            "NOTICE",
            "--use-json-log",
            "--log-level",
            "NOTICE",
            "--low-level-retries",
            "10",
        ]
        if "--retries" not in extra:
            cmd += ["--retries", "3"]
        if bwlimit.strip():
            cmd += ["--bwlimit", bwlimit.strip()]
        if dry_run:
            cmd.append("--dry-run")
        if extra:
            cmd += extra

        # stdout KHONG doc o day -> phai la DEVNULL. De PIPE ma khong doc: khi rclone in day ong (~4-64 KB)
        # no se dung lai cho mai, tac vu treo o % cuoi va khong bao gio xong.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )

        # Theo dõi hủy từ luồng riêng để dừng ngay lập tức
        stop_watch = threading.Event()
        if cancel_event is not None:
            def _watcher() -> None:
                while not stop_watch.is_set() and proc.poll() is None:
                    if cancel_event.is_set():
                        try:
                            proc.terminate()
                        except OSError:
                            pass
                        break
                    stop_watch.wait(0.2)

            threading.Thread(target=_watcher, daemon=True).start()

        logs: list[str] = []
        errors: list[str] = []
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logs.append(line)
                if on_log:
                    on_log(line)
                continue

            level = str(obj.get("level") or "").lower()
            msg = str(obj.get("msg") or "").strip()
            stats = obj.get("stats")
            if isinstance(stats, dict) and on_progress:
                transferring = [t for t in (stats.get("transferring") or []) if isinstance(t, dict)]
                cur_name = str(transferring[0].get("name") or "") if transferring else ""

                # Với tác vụ đơn lẻ (copyto/moveto): đọc thẳng tiến độ file thật từ transferring[0]
                # để KHÔNG bị cộng dồn dung lượng sai (VD 1.31 GB -> 4.0 GB) khi rclone tự thử lại!
                if command in ("copyto", "moveto") and transferring:
                    cur = transferring[0]
                    transferred = int(cur.get("bytes") or 0)
                    total = int(cur.get("size") or 0)
                    speed = float(cur.get("speed") or cur.get("speedAvg") or stats.get("speed") or 0.0)
                    eta = cur.get("eta") if cur.get("eta") is not None else stats.get("eta")
                    pct = float(cur.get("percentage")) if cur.get("percentage") is not None else (
                        (transferred / total * 100.0) if total > 0 else 0.0
                    )
                else:
                    transferred = int(stats.get("bytes") or 0)
                    total = int(stats.get("totalBytes") or 0)
                    live_spd = sum(float(t.get("speed") or 0.0) for t in transferring)
                    speed = live_spd if live_spd > 0 else float(stats.get("speed") or 0.0)
                    eta = stats.get("eta")
                    pct = (transferred / total * 100.0) if total > 0 else 0.0

                on_progress(
                    {
                        "bytes": transferred,
                        "total_bytes": total,
                        "speed": speed,
                        "eta": eta,
                        "percent": min(100.0, max(0.0, pct)),
                        "transfers_done": int(stats.get("transfers") or 0),
                        "transfers_total": int(stats.get("totalTransfers") or 0),
                        "current_file": cur_name,
                    }
                )
            elif msg:
                # Lọc bỏ cảnh báo shared client_id dài dòng khỏi danh sách lỗi thật
                if "shared google drive client_id" not in msg.lower():
                    obj_name = obj.get("object")
                    pretty = f"{obj_name}: {msg}" if obj_name else msg
                    logs.append(pretty)
                    if on_log:
                        on_log(pretty)
                    if level in ("error", "critical"):
                        errors.append(pretty)

        proc.wait()
        stop_watch.set()

        if cancel_event is not None and cancel_event.is_set():
            raise RcloneError("Đã hủy tác vụ truyền tải.")
        if proc.returncode != 0:
            err = errors[-1] if errors else f"rclone {command} lỗi (exit {proc.returncode})"
            raise RcloneError(err[:400])
        return logs
