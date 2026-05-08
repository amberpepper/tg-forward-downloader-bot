from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    bot_token: str
    download_root: Path
    db_path: Path
    default_webhook_url: str | None
    default_hook_script: str | None
    tdl_bin: str
    yt_dlp_bin: str
    ffmpeg_bin: str
    tdl_cmd: str
    url_downloader_cmd: str
    telegram_reply_on_finish: bool
    max_concurrent_jobs: int
    require_allowlist: bool
    admin_user_ids: set[int]
    admin_usernames: set[str]
    initial_allowed_user_ids: set[int]
    initial_allowed_usernames: set[str]
    web_enabled: bool
    web_host: str
    web_port: int
    web_admin_username: str
    web_admin_password: str
    web_secret_key: str
    web_session_hours: int
    web_public_base_url: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        load_env_files()
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("BOT_TOKEN 未设置")

        tdl_bin = first_env("TDL_BIN") or "tdl"
        yt_dlp_bin = first_env("YT_DLP_BIN", "YTDLP_BIN") or "yt-dlp"
        ffmpeg_bin = first_env("FFMPEG_BIN") or "ffmpeg"
        tdl_cmd = apply_binary_override(
            first_env("TDL_CMD") or f"{shlex.quote(tdl_bin)} dl -u {{url}} -d {{output_dir}}",
            tdl_bin,
            "tdl",
        )
        url_downloader_cmd = apply_binary_override(
            first_env("URL_DOWNLOADER_CMD")
            or f"{shlex.quote(yt_dlp_bin)} -o {{output_template}} {{url}}",
            yt_dlp_bin,
            "yt-dlp",
        )

        return cls(
            bot_token=bot_token,
            download_root=Path(os.getenv("DOWNLOAD_ROOT", "./downloads")).resolve(),
            db_path=Path(os.getenv("DB_PATH", "./data/app.db")).resolve(),
            default_webhook_url=os.getenv("DEFAULT_WEBHOOK_URL") or None,
            default_hook_script=os.getenv("DEFAULT_HOOK_SCRIPT") or None,
            tdl_bin=tdl_bin,
            yt_dlp_bin=yt_dlp_bin,
            ffmpeg_bin=ffmpeg_bin,
            tdl_cmd=tdl_cmd,
            url_downloader_cmd=url_downloader_cmd,
            telegram_reply_on_finish=os.getenv("TELEGRAM_REPLY_ON_FINISH", "true").lower() in {"1", "true", "yes", "on"},
            max_concurrent_jobs=max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1"))),
            require_allowlist=os.getenv("REQUIRE_ALLOWLIST", "true").lower() in {"1", "true", "yes", "on"},
            admin_user_ids=parse_int_set(os.getenv("ADMIN_USER_IDS", "")),
            admin_usernames=parse_name_set(os.getenv("ADMIN_USERNAMES", "")),
            initial_allowed_user_ids=parse_int_set(os.getenv("ALLOWED_USER_IDS", "")),
            initial_allowed_usernames=parse_name_set(os.getenv("ALLOWED_USERNAMES", "")),
            web_enabled=os.getenv("WEB_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
            web_host=os.getenv("WEB_HOST", "0.0.0.0"),
            web_port=int(os.getenv("WEB_PORT", "8090")),
            web_admin_username=os.getenv("WEB_ADMIN_USERNAME", "admin"),
            web_admin_password=os.getenv("WEB_ADMIN_PASSWORD", "change-this-password"),
            web_secret_key=os.getenv("WEB_SECRET_KEY", "change-this-secret-key"),
            web_session_hours=max(1, int(os.getenv("WEB_SESSION_HOURS", "24"))),
            web_public_base_url=os.getenv("WEB_PUBLIC_BASE_URL", "").strip() or None,
        )



def parse_int_set(raw: str) -> set[int]:
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        result.add(int(item))
    return result


def parse_name_set(raw: str) -> set[str]:
    return {item.strip().lstrip("@").lower() for item in raw.split(",") if item.strip()}


def load_env_file(path: Path, override: bool = False) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = value.strip()


def load_env_files(override: bool = False) -> None:
    candidates = [
        Path.cwd() / ".env",
        PROJECT_ROOT / ".env",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate.resolve())
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        load_env_file(candidate, override=override)


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def set_env_value(path: Path, key: str, value: str | None) -> None:
    lines: list[str] = []
    found = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith(f"{key}="):
            found = True
            if value:
                new_lines.append(f"{key}={value}")
            continue
        new_lines.append(raw)
    if not found and value:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def set_runtime_env_value(key: str, value: str | None) -> None:
    if value is None or value == "":
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def parse_bool_env_like(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def apply_runtime_app_settings(settings: Settings, values: dict[str, str] | None) -> Settings:
    values = values or {}
    if "default_webhook_url" in values:
        settings.default_webhook_url = values.get("default_webhook_url", "").strip() or None
    if "default_hook_script" in values:
        settings.default_hook_script = values.get("default_hook_script", "").strip() or None
    if "telegram_reply_on_finish" in values:
        settings.telegram_reply_on_finish = parse_bool_env_like(values.get("telegram_reply_on_finish"), settings.telegram_reply_on_finish)
    if "require_allowlist" in values:
        settings.require_allowlist = parse_bool_env_like(values.get("require_allowlist"), settings.require_allowlist)
    if "max_concurrent_jobs" in values:
        try:
            settings.max_concurrent_jobs = max(1, int(str(values.get("max_concurrent_jobs") or settings.max_concurrent_jobs)))
        except Exception:
            pass
    return settings


def apply_binary_override(command: str, binary: str, default_binary: str) -> str:
    command = command.strip()
    if not command:
        return command
    binary = (binary or "").strip()
    if not binary:
        return command
    pattern = rf"^({re.escape(default_binary)})(?=\s|$)"
    return re.sub(pattern, shlex.quote(binary), command, count=1)
