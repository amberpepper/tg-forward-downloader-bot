from __future__ import annotations

import hashlib
import hmac
import mimetypes
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from telegram import User

try:
    from .config import Settings
    from .models import DownloadJob
except ImportError:
    from config import Settings
    from models import DownloadJob

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])")
PROGRESS_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def clean_username(value: str | None) -> str:
    return (value or "").strip().lstrip("@").lower()


def normalize_username(value: str | None) -> str | None:
    cleaned = clean_username(value)
    return cleaned or None


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    return name or "file.bin"


def is_video_file(path: Path) -> bool:
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("video/"):
        return True
    return path.suffix.lower() in {
        ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".mpeg", ".mpg", ".3gp", ".ts",
    }


def get_preview_type(path: str | Path | None) -> str:
    lower = str(path or "").lower()
    if re.search(r"\.(mp4|m4v|mov|mkv|webm|avi|flv|wmv|mpeg|mpg|3gp|ts)$", lower):
        return "video"
    if re.search(r"\.(jpg|jpeg|png|gif|webp|bmp|svg)$", lower):
        return "image"
    if re.search(r"\.(mp3|m4a|aac|wav|ogg|flac|opus)$", lower):
        return "audio"
    if re.search(r"\.pdf$", lower):
        return "pdf"
    if re.search(r"\.(txt|log|json|md|csv|xml|yml|yaml|html|htm)$", lower):
        return "text"
    return ""


def find_default_preview_index(files: list[str] | None) -> int:
    for index, path in enumerate(files or []):
        if get_preview_type(path):
            return index
    return -1


def format_bytes(num_bytes: int) -> str:
    value = float(max(0, num_bytes))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{int(num_bytes)}B"


def sanitize_terminal_output(text: str) -> str:
    if not text:
        return ""
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_progress_update(text: str) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    lines = [line.strip() for line in sanitize_terminal_output(text).splitlines() if line.strip()]
    if not lines:
        return None, None
    for line in reversed(lines):
        matches = PROGRESS_PERCENT_RE.findall(line)
        if matches:
            value = min(100.0, max(0.0, float(matches[-1])))
            return value, line
    tail = lines[-1]
    if "done!" in tail.lower():
        return 100.0, tail
    return None, tail


def describe_process_failure(label: str, returncode: int, output_text: str = "") -> str:
    output_lower = (output_text or "").lower()
    if returncode in {137, -9} or "killed" in output_lower or "sigkill" in output_lower:
        return f"{label} 被系统杀死（退出码 {returncode}），疑似内存不足/OOM 被系统杀死"
    if returncode == 143:
        return f"{label} 被终止（退出码 {returncode}）"
    if returncode < 0:
        return f"{label} 被信号终止（退出码 {returncode}）"
    return f"{label}退出码 {returncode}"


def is_telegram_message_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"t.me", "telegram.me"}:
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    parts = [item for item in path.split("/") if item]
    if len(parts) < 2:
        return False
    return parts[0] == "c" or parts[-1].isdigit()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_admin_user(user: User, settings: Settings) -> bool:
    username = normalize_username(user.username)
    return user.id in settings.admin_user_ids or (username is not None and username in settings.admin_usernames)


def parse_allow_input(args: list[str]) -> tuple[int | None, str | None]:
    if not args:
        return None, None
    raw = args[0].strip()
    if not raw:
        return None, None
    if raw.lstrip("-").isdigit():
        return int(raw), None
    return None, clean_username(raw)


def build_session_cookie(username: str, secret_key: str, hours: int) -> str:
    expires = int(time.time()) + hours * 3600
    payload = f"{username}|{expires}"
    signature = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def verify_session_cookie(cookie: str | None, secret_key: str) -> bool:
    if not cookie:
        return False
    try:
        username, expires_raw, signature = cookie.split("|", 2)
        payload = f"{username}|{expires_raw}"
        expected = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        if int(expires_raw) < int(time.time()):
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def build_public_file_signature(job_id: str, file_index: int, expires: int, secret_key: str) -> str:
    payload = f"{job_id}|{file_index}|{expires}"
    return hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_public_file_signature(job_id: str, file_index: int, expires: int, signature: str | None, secret_key: str) -> bool:
    if not signature:
        return False
    if int(expires) < int(time.time()):
        return False
    expected = build_public_file_signature(job_id, file_index, expires, secret_key)
    return hmac.compare_digest(signature, expected)



def job_to_public_dict(job: DownloadJob) -> dict[str, Any]:
    data = asdict(job)
    data["files"] = job.files or []
    data["can_cancel"] = job.status in {"queued", "downloading"}
    data["can_retry"] = job.status in {"failed", "cancelled", "success"}
    return data

