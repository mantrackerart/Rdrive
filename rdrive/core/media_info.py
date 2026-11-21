"""Đọc siêu nhanh thời lượng Video/Audio (MP4, MOV, M4A, WAV) bằng Python thuần (< 0.5 ms/file, không cần cài FFmpeg)."""
from __future__ import annotations

import os
import struct
import wave
from pathlib import Path


def format_duration(seconds: float | int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    total = int(round(float(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _parse_mp4_mvhd(data: bytes) -> float:
    """Tìm atom 'mvhd' trong buffer đầu/cuối file MP4/MOV để lấy duration chính xác."""
    idx = 0
    while True:
        pos = data.find(b"mvhd", idx)
        if pos < 4 or pos + 28 > len(data):
            return 0.0
        version = data[pos + 4]
        try:
            if version == 0:
                timescale, duration = struct.unpack(">II", data[pos + 16 : pos + 24])
                if timescale > 0 and 0 < duration < timescale * 86400 * 7:
                    return duration / timescale
            elif version == 1 and pos + 36 <= len(data):
                timescale, duration = struct.unpack(">IQ", data[pos + 24 : pos + 36])
                if timescale > 0 and 0 < duration < timescale * 86400 * 7:
                    return duration / timescale
        except struct.error:
            pass
        idx = pos + 4


def probe_local_media_detail(path: str) -> str:
    """Trả về chuỗi thời lượng (VD '00:43:00') cho file MP4/MOV/M4A/WAV trên ổ đĩa."""
    ext = Path(path).suffix.lower()
    if ext == ".wav":
        try:
            with wave.open(path, "rb") as wf:
                rate = wf.getframerate()
                frames = wf.getnframes()
                ch = wf.getnchannels()
                if rate > 0:
                    dur = format_duration(frames / rate)
                    return f"{dur} · {rate // 1000}kHz {ch}ch"
        except Exception:
            return ""
    elif ext in {".mp4", ".mov", ".m4a", ".m4v"}:
        try:
            sz = os.path.getsize(path)
            if sz < 64:
                return ""
            with open(path, "rb") as fh:
                head = fh.read(min(sz, 256 * 1024))
                sec = _parse_mp4_mvhd(head)
                if sec <= 0 and sz > 256 * 1024:
                    fh.seek(max(0, sz - 512 * 1024))
                    tail = fh.read(512 * 1024)
                    sec = _parse_mp4_mvhd(tail)
                if sec > 0:
                    return format_duration(sec)
        except Exception:
            return ""
    return ""
