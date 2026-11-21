"""Hàng đợi truyền tải nền (Upload / Download / Drive-to-Drive Backup / Sync) dựa trên rclone."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue

from PySide6.QtCore import QObject, Signal

from .config import DATA_DIR, AppConfig
from .history import HistoryStore
from .logger import get_logger
from .rclone_client import RcloneClient, RcloneError
from .telegram_notifier import TelegramNotifier

log = get_logger()

QUEUE_FILE = DATA_DIR / "queue.json"     # tac vu chua xong, de mo lai app van con
_KEEP_FIELDS = ("id", "title", "command", "src", "dst", "direction", "extra_args", "state", "percent",
                "bytes_done", "bytes_total", "error", "created_at")


@dataclass
class TransferJob:
    id: str
    title: str
    command: str          # "copy" | "copyto" | "move" | "moveto" | "sync"
    src: str
    dst: str
    direction: str        # "upload" | "download" | "cloud" | "local" | "sync"
    extra_args: list[str] = field(default_factory=list)
    state: str = "queued"  # "queued" | "running" | "done" | "error" | "cancelled"
    percent: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    speed: float = 0.0
    eta: float | None = None
    current_file: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


class TransferManager(QObject):
    """Quản lý hàng đợi truyền tải bằng luồng nền, phát tín hiệu an toàn cho Qt UI."""

    job_added = Signal(object)       # TransferJob
    job_updated = Signal(object)     # TransferJob
    job_completed = Signal(object)   # TransferJob (để làm mới đúng thư mục đích)
    log_message = Signal(str)
    queue_idle = Signal()

    def __init__(
        self,
        rclone: RcloneClient,
        config: AppConfig,
        history: HistoryStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self.history = history
        self.telegram = TelegramNotifier(config)
        self._jobs: dict[str, TransferJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._frozen = False                 # dang thoat: giu nguyen file hang doi da ghi
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, name="rdrive-transfer-scheduler", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------ luu / khoi phuc hang doi
    def _persist(self, running_as_paused: bool = False) -> None:
        """Ghi cac tac vu chua xong (cho, dang chay, tam dung, loi) ra data/queue.json.
        Goi khi trang thai doi (khong goi moi lan cap nhat tien do)."""
        if self._frozen:
            return
        with self._lock:
            out = []
            for jid in self._order:
                j = self._jobs.get(jid)
                if j is None or j.state in ("done", "cancelled"):
                    continue
                d = {k: getattr(j, k) for k in _KEEP_FIELDS}
                if running_as_paused and d["state"] in ("running", "queued"):
                    d["state"] = "paused"
                out.append(d)
        try:
            QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = QUEUE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, QUEUE_FILE)
        except OSError:
            log.exception("Khong ghi duoc %s", QUEUE_FILE)

    def restore_saved(self) -> int:
        """Nap lai tac vu chua xong tu lan truoc. Tac vu dang chay / dang cho -> 'Tam dung' (bam Tiep tuc
        de chay lai, tranh tu dong upload ngay luc mo app). Tra ve so tac vu nap lai."""
        try:
            with open(QUEUE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return 0
        n = 0
        for d in data if isinstance(data, list) else []:
            try:
                job = TransferJob(**{k: d[k] for k in _KEEP_FIELDS if k in d})
            except TypeError:
                continue
            if job.id in self._jobs:
                continue
            if job.state in ("running", "queued"):
                job.state = "paused"
            job.speed, job.eta = 0.0, None
            with self._lock:
                self._jobs[job.id] = job
                self._order.append(job.id)
            self.job_added.emit(job)
            self.job_updated.emit(job)
            n += 1
        if n:
            log.info("Nap lai %d tac vu chua xong tu lan truoc (dang Tam dung)", n)
            self.log_message.emit(f"[QUEUE] Nạp lại {n} tác vụ chưa xong từ lần trước — bấm ▶ Tiếp Tục để chạy.")
        return n

    def jobs(self) -> list[TransferJob]:
        with self._lock:
            return [self._jobs[jid] for jid in self._order if jid in self._jobs]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.state in ("queued", "running"))

    def enqueue(
        self,
        *,
        title: str,
        command: str,
        src: str,
        dst: str,
        direction: str,
        extra_args: list[str] | None = None,
    ) -> TransferJob:
        job = TransferJob(
            id=uuid.uuid4().hex[:10],
            title=title,
            command=command,
            src=src,
            dst=dst,
            direction=direction,
            extra_args=list(extra_args or []),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
        self.job_added.emit(job)
        self.log_message.emit(f"[QUEUE] {title} ({src} → {dst})")
        self._persist()
        return job

    def pause_job(self, job_id: str) -> None:
        """Tạm dừng tác vụ: dừng tiến trình rclone hiện tại, khi Tiếp tục sẽ chạy lại nối tiếp."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state not in ("queued", "running"):
                return
            was_running = job.state == "running"
            job.state = "paused"
            job.speed = 0.0
            if was_running:
                job.cancel_event.set()
        self.job_updated.emit(job)
        self._persist()

    def resume_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state != "paused":
                return
            job.cancel_event = threading.Event()
            job.state = "queued"
            job.error = ""
        self.job_updated.emit(job)
        self._persist()

    def cancel_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if job.state in ("queued", "running", "paused"):
                job.cancel_event.set()
                if job.state in ("queued", "paused"):
                    job.state = "cancelled"
                    job.error = "Đã hủy"
                    self.job_updated.emit(job)
        self._persist()

    def retry_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state not in ("error", "cancelled", "paused"):
                return
            job.cancel_event = threading.Event()
            job.state = "queued"
            job.error = ""
            job.percent = 0.0
            job.speed = 0.0
        self.job_updated.emit(job)
        self._persist()

    def move_job(self, job_id: str, delta: int) -> bool:
        """Di chuyển thứ tự ưu tiên trong hàng đợi (delta = -1 Move Up, +1 Move Down)."""
        with self._lock:
            if job_id not in self._order:
                return False
            idx = self._order.index(job_id)
            new_idx = max(0, min(len(self._order) - 1, idx + delta))
            if new_idx == idx:
                return False
            self._order[idx], self._order[new_idx] = self._order[new_idx], self._order[idx]
            return True

    def clear_finished(self) -> None:
        with self._lock:
            remove_ids = [jid for jid, j in self._jobs.items() if j.state in ("done", "cancelled")]
            for jid in remove_ids:
                self._jobs.pop(jid, None)
                if jid in self._order:
                    self._order.remove(jid)

    def clear_failed(self) -> None:
        with self._lock:
            remove_ids = [jid for jid, j in self._jobs.items() if j.state == "error"]
            for jid in remove_ids:
                self._jobs.pop(jid, None)
                if jid in self._order:
                    self._order.remove(jid)
        self._persist()

    def shutdown(self) -> None:
        self._persist(running_as_paused=True)   # ghi truoc khi huy: lan mo sau con thay de chay tiep
        self._frozen = True
        self._stop.set()
        with self._lock:
            for job in self._jobs.values():
                if job.state in ("queued", "running"):
                    job.cancel_event.set()

    def _max_concurrency(self, direction: str) -> int:
        if direction == "download":
            return max(1, int(getattr(self.config, "download_threads", 3) or 3))
        return max(1, int(getattr(self.config, "upload_threads", 3) or 3))

    def _loop(self) -> None:
        """Bộ điều phối đa luồng: tự động bốc nhiều file Upload và Download chạy song song theo giới hạn cài đặt."""
        while not self._stop.is_set():
            to_start: list[TransferJob] = []
            with self._lock:
                running_ul = sum(1 for j in self._jobs.values() if j.state == "running" and j.direction != "download")
                running_dl = sum(1 for j in self._jobs.values() if j.state == "running" and j.direction == "download")
                max_ul = self._max_concurrency("upload")
                max_dl = self._max_concurrency("download")

                for jid in self._order:
                    job = self._jobs.get(jid)
                    if not job or job.state != "queued":
                        continue
                    if job.cancel_event.is_set():
                        job.state = "cancelled"
                        job.error = "Đã hủy"
                        continue
                    if job.direction == "download":
                        if running_dl < max_dl:
                            job.state = "running"
                            running_dl += 1
                            to_start.append(job)
                    else:
                        if running_ul < max_ul:
                            job.state = "running"
                            running_ul += 1
                            to_start.append(job)

            for job in to_start:
                threading.Thread(
                    target=self._execute_job,
                    args=(job,),
                    name=f"rdrive-job-{job.id}",
                    daemon=True,
                ).start()

            self._stop.wait(0.25)

    def _execute_job(self, job: TransferJob) -> None:
        self.job_updated.emit(job)
        self.log_message.emit(f"[START] {job.title} | {job.src} -> {job.dst}")
        log.info("TRANSFER START: %s | %s -> %s", job.title, job.src, job.dst)

        t_start = time.monotonic()
        last_speed_log = t_start
        stall_since: float | None = None
        stall_warned = False
        stall_count = 0
        peak_speed = 0.0
        min_speed = float("inf")
        last_pct = 0.0

        def _fmt_spd(bps: float) -> str:
            if bps >= 1024 * 1024:
                return f"{bps / (1024 * 1024):.2f} MB/s"
            if bps >= 1024:
                return f"{bps / 1024:.1f} KB/s"
            return f"{bps:.0f} B/s"

        def _fmt_sz(b: int) -> str:
            if b >= 1024**3:
                return f"{b / (1024**3):.2f} GB"
            if b >= 1024**2:
                return f"{b / (1024**2):.1f} MB"
            if b >= 1024:
                return f"{b / 1024:.1f} KB"
            return f"{b} B"

        def _on_prog(st: dict, j: TransferJob = job) -> None:
            nonlocal last_speed_log, stall_since, stall_warned, stall_count, peak_speed, min_speed, last_pct
            j.bytes_done = st["bytes"]
            j.bytes_total = st["total_bytes"]
            j.speed = float(st["speed"] or 0.0)
            j.eta = st["eta"]
            j.percent = float(st["percent"] or 0.0)
            if st.get("current_file"):
                j.current_file = st["current_file"]

            now = time.monotonic()
            elapsed = max(0.1, now - t_start)
            avg_spd = j.bytes_done / elapsed if j.bytes_done > 0 else 0.0

            if j.speed > peak_speed:
                peak_speed = j.speed
            if j.speed > 0 and j.speed < min_speed:
                min_speed = j.speed
            last_pct = j.percent

            # Phát hiện nghẽn / tụt tốc đột ngột (VD đang chạy >2 MB/s bỗng tụt về <300 KB/s lâu hơn 8 giây)
            is_stalled = (peak_speed >= 2 * 1024 * 1024 and j.speed < 300 * 1024) or (
                j.percent >= 88.0 and j.speed < 200 * 1024 and elapsed > 10.0
            )
            if is_stalled:
                if stall_since is None:
                    stall_since = now
                    stall_warned = False
                elif not stall_warned and (now - stall_since) >= 8.0:
                    stall_warned = True
                    stall_count += 1
                    hint = (
                        "Google Drive đang ghép chunk cuối / kiểm tra MD5 hoặc bị nghẽn luồng cuối"
                        if j.percent >= 88.0
                        else "có thể nghẽn mạng / Google bóp băng thông tạm thời / chuyển WAN"
                    )
                    warn_msg = (
                        f"[CẢNH BÁO NGHẼN #{stall_count}] {j.title} tụt xuống {_fmt_spd(j.speed)} "
                        f"tại {j.percent:.1f}% ({_fmt_sz(j.bytes_done)}/{_fmt_sz(j.bytes_total)}) | "
                        f"Đỉnh trước đó: {_fmt_spd(peak_speed)} — {hint}"
                    )
                    log.warning(warn_msg)
                    self.log_message.emit(warn_msg)
                    self.telegram.notify_stall(j.title, warn_msg)
            else:
                stall_since = None
                stall_warned = False

            # Ghi mẫu tốc độ chi tiết định kỳ mỗi 10 giây vào Nhật ký (Log) & logs/rdrive.log
            if (now - last_speed_log) >= 10.0:
                last_speed_log = now
                spd_msg = (
                    f"[SPEED] {j.title} | {j.percent:.1f}% ({_fmt_sz(j.bytes_done)}/{_fmt_sz(j.bytes_total)}) | "
                    f"Hiện tại: {_fmt_spd(j.speed)} | Đỉnh: {_fmt_spd(peak_speed)} | TB: {_fmt_spd(avg_spd)}"
                )
                log.info(spd_msg)
                self.log_message.emit(spd_msg)

            self.job_updated.emit(j)

        def _on_log(msg: str) -> None:
            self.log_message.emit(f"[rclone] {msg}")

        extra = list(job.extra_args)
        if self.config.ignore_existing and job.command in ("copy", "copyto") and "--ignore-existing" not in extra:
            extra.append("--ignore-existing")
        # Da luong tren 1 file (--multi-thread-streams) chi co tac dung khi TAI VE may (download).
        # Upload len Google Drive luon la 1 luong/file (resumable upload tuan tu) -> nhanh hon nho chay
        # nhieu file cung luc (upload_threads), khong phai nho co nay.
        if (self.config.multi_thread_download and job.direction == "download"
                and "--multi-thread-streams" not in extra):
            streams = max(4, min(64, int(getattr(self.config, "idm_streams", 16) or 16)))
            extra += [
                "--multi-thread-streams",
                str(streams),
                "--multi-thread-cutoff",
                "16M",
                "--multi-thread-chunk-size",
                "32M",
            ]
        if self.config.retries and "--retries" not in extra:
            extra += ["--retries", str(max(1, int(self.config.retries)))]

        sub_transfers = (
            self._max_concurrency(job.direction)
            if job.command in ("copy", "move", "sync")
            else 1
        )

        try:
            self.rclone.run_transfer(
                job.command,
                job.src,
                job.dst,
                extra_args=extra,
                transfers=sub_transfers,
                checkers=self.config.checkers,
                drive_chunk_mb=self.config.drive_chunk_mb,
                drive_pacer_ms=getattr(self.config, "drive_pacer_ms", 100),
                bwlimit=self.config.effective_bwlimit(),
                on_progress=_on_prog,
                on_log=_on_log,
                cancel_event=job.cancel_event,
            )
            elapsed = max(0.1, time.monotonic() - t_start)
            total_b = job.bytes_total or job.bytes_done
            avg_spd = total_b / elapsed if total_b > 0 else 0.0
            min_str = _fmt_spd(min_speed) if min_speed < float("inf") else "—"
            summary = (
                f"Xong sau {elapsed:.1f}s | TB: {_fmt_spd(avg_spd)} | "
                f"Đỉnh: {_fmt_spd(peak_speed)} | Thấp nhất: {min_str} | Nghẽn: {stall_count} lần"
            )
            job.percent = 100.0
            job.speed = 0.0
            job.eta = 0
            job.state = "done"
            log.info("TRANSFER DONE: %s | %s (%s -> %s)", job.title, summary, job.src, job.dst)
            self.log_message.emit(f"[DONE] {job.title} — {summary}")
            self.telegram.record_job_done(job.title, total_b, elapsed, peak_speed, summary)
            self.job_completed.emit(job)
            if self.history is not None:
                self.history.record(
                    job.direction,
                    job.title,
                    status="ok",
                    src=job.src,
                    dst=job.dst,
                    size_bytes=total_b,
                    detail=summary,
                )
        except RcloneError as exc:
            elapsed = max(0.1, time.monotonic() - t_start)
            err_stats = f"sau {elapsed:.1f}s tại {last_pct:.1f}% | Đỉnh: {_fmt_spd(peak_speed)} | Nghẽn: {stall_count} lần"
            if job.state == "paused":
                job.speed = 0.0
                self.log_message.emit(f"[PAUSED] {job.title} ({err_stats})")
            elif job.cancel_event.is_set():
                job.state = "cancelled"
                job.error = "Đã hủy"
                log.info("TRANSFER CANCEL: %s (%s)", job.title, err_stats)
                self.log_message.emit(f"[CANCEL] {job.title} ({err_stats})")
                if self.history is not None:
                    self.history.record(job.direction, job.title, status="cancelled", src=job.src, dst=job.dst, detail=f"Người dùng hủy ({err_stats})")
            else:
                job.state = "error"
                job.error = str(exc)
                log.error("TRANSFER ERROR: %s (%s) | %s", job.title, err_stats, exc)
                self.log_message.emit(f"[ERROR] {job.title} ({err_stats}): {exc}")
                self.telegram.record_job_error(job.title, str(exc), err_stats)
                if self.history is not None:
                    self.history.record(job.direction, job.title, status="error", src=job.src, dst=job.dst, detail=f"{err_stats} | {str(exc)[:180]}")
        except Exception as exc:  # noqa: BLE001
            job.state = "error"
            job.error = str(exc)
            log.exception("TRANSFER UNEXPECTED ERROR: %s", job.title)
            self.log_message.emit(f"[ERROR] {job.title}: {exc}")
            self.telegram.record_job_error(job.title, str(exc))
            if self.history is not None:
                self.history.record(job.direction, job.title, status="error", src=job.src, dst=job.dst, detail=str(exc)[:240])

        self.job_updated.emit(job)
        self._persist()
        if self.active_count() == 0:
            self.telegram.flush_queue_summary()
            self.queue_idle.emit()
