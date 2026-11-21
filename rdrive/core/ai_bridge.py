"""Cầu nối AI quản lý thư mục & backup: hỗ trợ cả Local AI (Ollama / LM Studio) và Cloud AI (Gemini / OpenAI)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import AIConfig
from .rclone_client import format_size


class AIError(RuntimeError):
    """Lỗi khi gọi Local AI hoặc Cloud AI."""


def estimate_tokens(text: str) -> int:
    """Ước lượng nhanh số token (~3.5 ký tự/token cho tiếng Việt + tên file)."""
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


def compact_entries_summary(side_label: str, path: str, entries: list[dict], max_groups: int = 18) -> str:
    """Nén danh sách file thành tóm tắt siêu gọn theo mẫu tên + đuôi file (tiết kiệm 75–90% token)."""
    dirs = [e["name"] for e in entries if e.get("is_dir")]
    files = [e for e in entries if not e.get("is_dir")]

    lines = [f"[{side_label}] {path} ({len(dirs)} dir, {len(files)} file)"]
    if dirs:
        shown = ", ".join(dirs[:12])
        more = f" +{len(dirs) - 12}" if len(dirs) > 12 else ""
        lines.append(f"  DIRS: {shown}{more}")

    if files:
        groups: dict[str, list[int]] = defaultdict(list)
        for f in files:
            name = f.get("name", "")
            p = Path(name)
            ext = p.suffix.lower()
            stem = p.stem
            # Thay chuỗi số/ngày lặp (vd DJI_01_20261119_080954 -> DJI_*) để gom nhóm
            pat = re.sub(r"[\d_-]{2,}.*$", "*", stem)
            key = f"{pat}{ext}" if pat and pat != "*" else f"*{ext or '(noext)'}"
            groups[key].append(int(f.get("size") or 0))

        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), -sum(kv[1])))
        parts = []
        for pat, sizes in ranked[:max_groups]:
            total_sz = format_size(sum(sizes))
            parts.append(f"{pat} x{len(sizes)} ({total_sz})" if len(sizes) > 1 else f"{pat} ({total_sz})")
        if len(ranked) > max_groups:
            parts.append(f"+{len(ranked) - max_groups} nhóm khác")
        lines.append("  FILES: " + "; ".join(parts))

    return "\n".join(lines)


def _http_json(url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 45.0) -> dict[str, Any]:
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise AIError(f"HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise AIError(f"Không kết nối được tới {url}: {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIError(str(exc)) from exc


def list_local_models(cfg: AIConfig) -> list[str]:
    """Lấy danh sách model đang có trên Local AI (Ollama hoặc LM Studio / OpenAI-compatible)."""
    base = cfg.local_url.rstrip("/")
    if cfg.local_provider == "ollama":
        data = _http_json(f"{base}/api/tags", timeout=8.0)
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    # LM Studio / OpenAI-compatible
    v1 = base if base.endswith("/v1") else f"{base}/v1"
    data = _http_json(f"{v1}/models", timeout=8.0)
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def test_connection(cfg: AIConfig) -> str:
    """Kiểm tra kết nối nhanh tới Local AI hoặc Cloud AI."""
    if cfg.mode == "local":
        models = list_local_models(cfg)
        if models:
            return f"Kết nối Local AI thành công ({len(models)} model: {', '.join(models[:4])}{'…' if len(models) > 4 else ''})."
        return "Đã kết nối Local AI (chưa thấy model nào được tải)."
    reply = ask_ai(cfg, "Trả lời đúng 1 từ: OK")
    return f"Kết nối Cloud AI thành công! Phản hồi: {reply[:80]}"


def ask_ai(cfg: AIConfig, prompt: str, context: str = "") -> str:
    """Gửi yêu cầu phân tích / đề xuất quản lý thư mục tới Local AI hoặc Cloud AI."""
    full_user = f"{context}\n\nYêu cầu: {prompt}" if context.strip() else prompt
    system = cfg.system_prompt.strip()

    if cfg.mode == "local":
        base = cfg.local_url.rstrip("/")
        if cfg.local_provider == "ollama":
            data = _http_json(
                f"{base}/api/chat",
                payload={
                    "model": cfg.local_model,
                    "stream": False,
                    "options": {"num_predict": max(64, int(cfg.max_output_tokens or 512)), "temperature": 0.2},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": full_user},
                    ],
                },
                timeout=90.0,
            )
            return str((data.get("message") or {}).get("content") or "").strip()
        v1 = base if base.endswith("/v1") else f"{base}/v1"
        data = _http_json(
            f"{v1}/chat/completions",
            payload={
                "model": cfg.local_model,
                "max_tokens": max(64, int(cfg.max_output_tokens or 512)),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": full_user},
                ],
                "temperature": 0.2,
            },
            timeout=90.0,
        )
        choices = data.get("choices") or []
        if choices:
            return str((choices[0].get("message") or {}).get("content") or "").strip()
        return ""

    # Cloud AI
    api_key = cfg.cloud_api_key.strip()
    if not api_key:
        raise AIError("Chưa nhập API Key cho Cloud AI.")

    if cfg.cloud_provider == "gemini":
        model = cfg.cloud_model.strip() or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": full_user}]}],
            "generationConfig": {
                "maxOutputTokens": max(64, int(cfg.max_output_tokens or 512)),
                "temperature": 0.2,
            },
        }
        data = _http_json(url, payload=payload, timeout=60.0)
        cands = data.get("candidates") or []
        if cands:
            parts = ((cands[0].get("content") or {}).get("parts")) or []
            return "".join(str(p.get("text") or "") for p in parts).strip()
        raise AIError("Gemini không trả về nội dung.")

    # OpenAI / Custom OpenAI-compatible Cloud endpoint
    base = (cfg.cloud_url.strip() or "https://api.openai.com/v1").rstrip("/")
    data = _http_json(
        f"{base}/chat/completions",
        payload={
            "model": cfg.cloud_model.strip() or "gpt-4o-mini",
            "max_tokens": max(64, int(cfg.max_output_tokens or 512)),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": full_user},
            ],
            "temperature": 0.2,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    choices = data.get("choices") or []
    if choices:
        return str((choices[0].get("message") or {}).get("content") or "").strip()
    return ""
