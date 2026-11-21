"""Tab 'Speedtest' lấy nguồn từ Speedtest.net (Ookla CLI) + Test đa luồng 16 luồng (Load-Balance 2 WAN) + Tự lưu log so sánh."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import DATA_DIR, ROOT_DIR
from ..core.logger import get_logger
from ..core.rclone_client import NO_WINDOW

log = get_logger()
SPEEDTEST_HISTORY_FILE = DATA_DIR / "speedtest_history.json"
OOKLA_ZIP_URL = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"


def _ensure_ookla_cli(on_status=None) -> str:
    bin_dir = ROOT_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "speedtest.exe"
    if exe.is_file():
        return str(exe)
    raise RuntimeError("Chưa có bin\\speedtest.exe. Tự tải Speedtest CLI từ speedtest.net/apps/cli (theo giấy phép của Ookla) rồi đặt vào thư mục bin, hoặc dùng chế độ Đa luồng.")


class _SpeedtestWorker(QThread):
    status = Signal(str)
    done = Signal(dict)
    err = Signal(str)

    def __init__(self, mode: str = "ookla", streams: int = 16) -> None:
        super().__init__()
        self.mode = mode      # "ookla" | "multistream"
        self.streams = streams

    def run(self) -> None:
        try:
            if self.mode == "ookla":
                exe = _ensure_ookla_cli(self.status.emit)
                self.status.emit("Đang đo tốc độ qua máy chủ Speedtest.net (Ping / Download / Upload)…")
                p = subprocess.run(
                    [exe, "--accept-license", "--accept-gdpr", "--format=json"],
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    timeout=120,
                    creationflags=NO_WINDOW,
                )
                stdout = p.stdout.decode("utf-8", "replace").strip()
                # Tìm dòng JSON kết quả
                obj = None
                for ln in reversed(stdout.splitlines()):
                    s = ln.strip()
                    if s.startswith("{") and s.endswith("}"):
                        try:
                            cand = json.loads(s)
                            if "download" in cand and "upload" in cand:
                                obj = cand
                                break
                        except json.JSONDecodeError:
                            pass
                if not obj:
                    raise RuntimeError(p.stderr.decode("utf-8", "replace")[:300] or "Speedtest.net CLI không trả về JSON.")
                dl_bps = float((obj.get("download") or {}).get("bandwidth") or 0)  # bytes/sec!
                ul_bps = float((obj.get("upload") or {}).get("bandwidth") or 0)    # bytes/sec!
                ping_ms = float((obj.get("ping") or {}).get("latency") or 0)
                jitter_ms = float((obj.get("ping") or {}).get("jitter") or 0)
                srv = obj.get("server") or {}
                isp = str(obj.get("isp") or "")
                url = str((obj.get("result") or {}).get("url") or "")
                self.done.emit(
                    {
                        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                        "source": f"Speedtest.net ({srv.get('name', '?')} - {srv.get('location', '')})",
                        "isp": isp,
                        "ping_ms": round(ping_ms, 1),
                        "jitter_ms": round(jitter_ms, 1),
                        "dl_mbps": round(dl_bps * 8 / 1_000_000, 1),
                        "dl_mbs": round(dl_bps / (1024 * 1024), 2),
                        "ul_mbps": round(ul_bps * 8 / 1_000_000, 1),
                        "ul_mbs": round(ul_bps / (1024 * 1024), 2),
                        "url": url,
                    }
                )
                return

            # Chế độ Multi-Stream (16 luồng song song như IDM để gộp 2 đường truyền Load-Balance)
            self.status.emit(f"Đang test tải đa luồng ({self.streams} luồng song song như IDM) để đo gộp 2 gói mạng Load-Balance…")
            t0 = time.perf_counter()
            req_ping = urllib.request.Request("https://speed.cloudflare.com/__down?bytes=64", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_ping, timeout=10) as r:
                r.read()
            ping_ms = (time.perf_counter() - t0) * 1000.0

            chunk_bytes = 25 * 1024 * 1024  # 25 MB x 16 luồng = 400 MB

            def _pull(_i: int) -> int:
                u = f"https://speed.cloudflare.com/__down?bytes={chunk_bytes}"
                rq = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                got = 0
                with urllib.request.urlopen(rq, timeout=25) as resp:
                    while True:
                        buf = resp.read(256 * 1024)
                        if not buf:
                            break
                        got += len(buf)
                return got

            start_dl = time.perf_counter()
            with ThreadPoolExecutor(max_workers=self.streams) as pool:
                total_dl = sum(pool.map(_pull, range(self.streams)))
            dt_dl = max(0.05, time.perf_counter() - start_dl)
            dl_bps = total_dl / dt_dl

            # Upload 8 luồng x 4 MB
            ul_payload = b"0" * (4 * 1024 * 1024)

            def _push(_i: int) -> int:
                rq = urllib.request.Request("https://speed.cloudflare.com/__up", data=ul_payload, headers={"User-Agent": "Mozilla/5.0"}, method="POST")
                with urllib.request.urlopen(rq, timeout=20) as resp:
                    resp.read()
                return len(ul_payload)

            start_ul = time.perf_counter()
            with ThreadPoolExecutor(max_workers=min(8, self.streams)) as pool:
                total_ul = sum(pool.map(_push, range(min(8, self.streams))))
            dt_ul = max(0.05, time.perf_counter() - start_ul)
            ul_bps = total_ul / dt_ul

            self.done.emit(
                {
                    "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "source": f"IDM Multi-Stream ({self.streams} luồng gộp Load-Balance)",
                    "isp": "Multi-WAN",
                    "ping_ms": round(ping_ms, 1),
                    "jitter_ms": 0.0,
                    "dl_mbps": round(dl_bps * 8 / 1_000_000, 1),
                    "dl_mbs": round(dl_bps / (1024 * 1024), 2),
                    "ul_mbps": round(ul_bps * 8 / 1_000_000, 1),
                    "ul_mbs": round(ul_bps / (1024 * 1024), 2),
                    "url": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc))


class SpeedtestView(QWidget):
    """Tab Speedtest chính thức từ Speedtest.net + Test đa luồng IDM + Lưu lịch sử so sánh."""

    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[dict] = []
        self._live_workers: list[QThread] = []
        self._load_history()
        self._build_ui()
        self._render_table()

    def _load_history(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not SPEEDTEST_HISTORY_FILE.is_file():
            self._records = []
            return
        try:
            with open(SPEEDTEST_HISTORY_FILE, "r", encoding="utf-8") as fh:
                self._records = json.load(fh) or []
        except Exception:
            self._records = []

    def _save_history(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SPEEDTEST_HISTORY_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._records[-500:], fh, ensure_ascii=False, indent=2)
        os.replace(tmp, SPEEDTEST_HISTORY_FILE)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Thẻ đồng hồ đo lớn
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.lbl_ping = self._metric_card(cards, "PING / ĐỘ TRỄ", "— ms")
        self.lbl_dl = self._metric_card(cards, "DOWNLOAD (TẢI XUỐNG)", "— Mbps  (— MB/s)")
        self.lbl_ul = self._metric_card(cards, "UPLOAD (TẢI LÊN)", "— Mbps  (— MB/s)")
        self.lbl_diff = self._metric_card(cards, "SO SÁNH VỚI LẦN TRƯỚC", "Chưa có dữ liệu")
        root.addLayout(cards)

        # Thanh nút bấm
        bar = QHBoxLayout()
        self.btn_ookla = QPushButton("🚀 Đo Speedtest.net (Ookla chính thức)")
        self.btn_ookla.setObjectName("PrimaryBtn")
        self.btn_ookla.clicked.connect(lambda: self._start_test("ookla"))

        self.btn_multi = QPushButton("⚡ Đo Đa Luồng IDM (16 luồng — Gộp 2 WAN Load-Balance)")
        self.btn_multi.clicked.connect(lambda: self._start_test("multistream"))

        self.btn_clear = QPushButton("Xóa lịch sử Speedtest")
        self.btn_clear.clicked.connect(self._clear_history)

        bar.addWidget(self.btn_ookla)
        bar.addWidget(self.btn_multi)
        bar.addStretch(1)
        bar.addWidget(self.btn_clear)
        root.addLayout(bar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.lbl_state = QLabel("Mọi lần đo đều tự động lưu vào data/speedtest_history.json và logs/rdrive.log để so sánh.")
        self.lbl_state.setObjectName("Muted")
        root.addWidget(self.lbl_state)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Thời gian",
            "Nguồn / Máy chủ",
            "ISP",
            "Ping",
            "Download (Mbps · MB/s)",
            "Upload (Mbps · MB/s)",
            "So với lần trước",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

    @staticmethod
    def _metric_card(layout: QHBoxLayout, title: str, init_val: str) -> QLabel:
        box = QFrame()
        box.setObjectName("PaneFrame")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        t = QLabel(title)
        t.setObjectName("Muted")
        v = QLabel(init_val)
        v.setStyleSheet("font-size: 13pt; font-weight: 700; color: #ffffff;")
        bl.addWidget(t)
        bl.addWidget(v)
        layout.addWidget(box, 1)
        return v

    def _start_test(self, mode: str) -> None:
        self.btn_ookla.setEnabled(False)
        self.btn_multi.setEnabled(False)
        self.progress.setVisible(True)
        w = _SpeedtestWorker(mode=mode, streams=16)
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.status.connect(self.lbl_state.setText)
        w.done.connect(self._on_done)
        w.err.connect(self._on_err)
        w.start()

    def _on_done(self, rec: dict) -> None:
        self.btn_ookla.setEnabled(True)
        self.btn_multi.setEnabled(True)
        self.progress.setVisible(False)

        diff_txt = "Lần đo đầu tiên"
        if self._records:
            prev = self._records[-1]
            p_dl = float(prev.get("dl_mbps") or 0)
            p_ul = float(prev.get("ul_mbps") or 0)
            d_dl = rec["dl_mbps"] - p_dl
            d_ul = rec["ul_mbps"] - p_ul
            diff_txt = f"DL: {d_dl:+.1f} Mbps  ·  UL: {d_ul:+.1f} Mbps"

        rec["diff"] = diff_txt
        self._records.append(rec)
        self._save_history()

        log.info(
            "SPEEDTEST [%s]: Ping=%sms | DL=%s Mbps (%s MB/s) | UL=%s Mbps (%s MB/s) | %s",
            rec["source"],
            rec["ping_ms"],
            rec["dl_mbps"],
            rec["dl_mbs"],
            rec["ul_mbps"],
            rec["ul_mbs"],
            diff_txt,
        )

        self.lbl_ping.setText(f"{rec['ping_ms']} ms")
        self.lbl_dl.setText(f"{rec['dl_mbps']} Mbps  ({rec['dl_mbs']} MB/s)")
        self.lbl_ul.setText(f"{rec['ul_mbps']} Mbps  ({rec['ul_mbs']} MB/s)")
        self.lbl_diff.setText(diff_txt)
        self.lbl_state.setText(f"Đo hoàn tất lúc {rec['timestamp']} ({rec['source']}). Đã tự lưu vào log.")
        self._render_table()

    def _on_err(self, msg: str) -> None:
        self.btn_ookla.setEnabled(True)
        self.btn_multi.setEnabled(True)
        self.progress.setVisible(False)
        self.lbl_state.setText(f"Lỗi đo tốc độ: {msg}")
        log.error("SPEEDTEST ERROR: %s", msg)

    def _render_table(self) -> None:
        self.table.setRowCount(0)
        for rec in reversed(self._records):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(rec.get("timestamp", "")))
            self.table.setItem(row, 1, QTableWidgetItem(rec.get("source", "")))
            self.table.setItem(row, 2, QTableWidgetItem(rec.get("isp", "")))
            self.table.setItem(row, 3, QTableWidgetItem(f"{rec.get('ping_ms', 0)} ms"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{rec.get('dl_mbps', 0)} Mbps  ({rec.get('dl_mbs', 0)} MB/s)"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{rec.get('ul_mbps', 0)} Mbps  ({rec.get('ul_mbs', 0)} MB/s)"))
            self.table.setItem(row, 6, QTableWidgetItem(rec.get("diff", "")))

    def _clear_history(self) -> None:
        if QMessageBox.question(self, "Xóa lịch sử Speedtest", "Xóa toàn bộ lịch sử Speedtest đã lưu?") == QMessageBox.Yes:
            self._records.clear()
            self._save_history()
            self._render_table()

    def shutdown(self) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.terminate()
                w.wait(400)
