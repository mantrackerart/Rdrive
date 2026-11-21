"""rclone thuong truc (`rclone rcd`) de duyet Google Drive nhanh hon.

Truoc day moi lan mo 1 folder la bat 1 tien trinh rclone moi: khoi dong + doc rclone.conf + lam moi token
+ do lai tung tang duong dan theo ten ("2026" -> "quay thang 11" -> "quay 19.11", moi tang 1 lan hoi Google).
Voi rclone thuong truc, cac tai khoan (Fs) va bang "duong dan -> ID folder" duoc giu trong bo nho (1 gio),
ket noi HTTPS toi Google duoc giu san -> mo folder sau / mo lai folder cu nhanh hon nhieu.

- Chi nghe tren 127.0.0.1, cong ngau nhien, co user/mat khau ngau nhien moi lan mo.
- Chi dung cho thao tac DOC (liet ke folder, dung luong). Upload / download / xoa / doi ten van chay
  bang lenh rclone rieng nhu cu (giu nguyen tien do va nhat ky).
- Loi khoi dong / mat ket noi -> RcloneClient tu quay ve cach cu (bat rclone rieng), khong anh huong nguoi dung.
- Windows: gan tien trinh vao Job Object "kill on close" -> Rdrive tat dot ngot thi rclone rcd cung tat theo.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class RcdTransportError(RuntimeError):
    """Khong noi chuyen duoc voi rclone rcd (chua chay, da tat, het thoi gian) -> nen dung cach cu."""


class RcdCallError(RuntimeError):
    """rclone rcd tra loi loi that (vd folder khong ton tai, token het han) -> bao cho nguoi dung."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _KillOnCloseJob:
    """Job Object cua Windows: dong handle (Rdrive thoat / crash) thi Windows tu tat tien trinh con."""

    def __init__(self) -> None:
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                             "OtherOperationCount", "ReadTransferCount",
                                                             "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
            if ok:
                self.handle = job
                self._k32 = k32
        except Exception:  # noqa: BLE001
            self.handle = None

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        try:
            from ctypes import wintypes
            self._k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(int(proc._handle)))
        except Exception:  # noqa: BLE001
            pass


class RcdDaemon:
    """Mot tien trinh `rclone rcd` dung chung cho ca app. An toan khi goi tu nhieu luong."""

    MAX_FAILS = 3                       # khoi dong hong 3 lan -> thoi, dung cach cu cho het phien

    def __init__(self, log_file: Path, extra_flags: list[str] | None = None, logger=None) -> None:
        self.log_file = Path(log_file)
        self.extra_flags = list(extra_flags or [])
        self.log = logger
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._exe = ""
        self._url = ""
        self._auth = ""
        self._fails = 0
        self._job = _KillOnCloseJob()

    # ------------------------------------------------------------ khoi dong / dung
    def _info(self, msg: str, *args) -> None:
        if self.log is not None:
            self.log.info(msg, *args)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure(self, exe: str) -> bool:
        """Dam bao rcd dang chay voi dung rclone.exe. Tra ve False neu khong dung duoc (-> cach cu)."""
        with self._lock:
            if self.alive() and exe == self._exe:
                return True
            if self._fails >= self.MAX_FAILS or not exe:
                return False
            self._stop_locked()
            port = _free_port()
            user, pw = "rdrive", secrets.token_urlsafe(18)
            cmd = [exe, "rcd", "--rc-addr", f"127.0.0.1:{port}", "--rc-user", user, "--rc-pass", pw,
                   "--fs-cache-expire-duration", "1h", "--log-file", str(self.log_file), "--log-level", "NOTICE",
                   *self.extra_flags]
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            except OSError as exc:
                self._fails += 1
                self._info("RCD khong khoi dong duoc: %s", exc)
                return False
            self._job.add(proc)
            self._proc, self._exe = proc, exe
            self._url = f"http://127.0.0.1:{port}/"
            self._auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10.0:              # cho rcd san sang (thuong < 0,5 giay)
                if proc.poll() is not None:
                    break
                try:
                    self._post("rc/noop", {}, 2.0)
                    self._fails = 0
                    self._info("RCD rclone thuong truc san sang sau %.0f ms (cong %d, pid %d)",
                               (time.monotonic() - t0) * 1000, port, proc.pid)
                    return True
                except (RcdTransportError, RcdCallError):
                    time.sleep(0.15)
            self._fails += 1
            self._info("RCD khong san sang (lan %d) - dung cach cu. Xem %s", self._fails, self.log_file)
            self._stop_locked()
            return False

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            self._post("core/quit", {}, 2.0)
        except (RcdTransportError, RcdCallError):
            pass
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def restart(self) -> None:
        """Tai khoan vua them / dang nhap lai / xoa: bo cac Fs dang giu (token cu) bang cach khoi dong lai."""
        with self._lock:
            if self._proc is not None:
                self._info("RCD khoi dong lai (tai khoan vua thay doi)")
            self._stop_locked()

    # ------------------------------------------------------------ goi lenh
    def _post(self, method: str, params: dict, timeout: float) -> dict:
        req = urllib.request.Request(self._url + method, data=json.dumps(params).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": self._auth})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", "replace") or "{}")
            except ValueError:
                body = {}
            if exc.code in (401, 403, 404) and not body.get("error"):
                raise RcdTransportError(f"HTTP {exc.code}") from exc
            raise RcdCallError(str(body.get("error") or f"HTTP {exc.code}")) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RcdTransportError(str(exc)) from exc

    def call(self, exe: str, method: str, params: dict, timeout: float = 90.0) -> dict:
        if not self.ensure(exe):
            raise RcdTransportError("rclone rcd khong chay")
        try:
            return self._post(method, params, timeout)
        except RcdTransportError:
            if not self.alive():                 # rcd vua chet: lan sau tu bat lai
                with self._lock:
                    self._proc = None
            raise


def fs_string(remote: str, *, folder_id: str = "", shared_with_me: bool = False, team_drive: str = "") -> str:
    """Tuong duong cac co --drive-team-drive / --drive-root-folder-id / --drive-shared-with-me,
    viet thanh 'connection string' cua rclone: remote,team_drive=ID,root_folder_id=ID:"""
    params = []
    if team_drive:
        params.append(f"team_drive={team_drive}")
    if folder_id:
        params.append(f"root_folder_id={folder_id}")
    if shared_with_me:
        params.append("shared_with_me=true")
    return remote + ("," + ",".join(params) if params else "") + ":"
