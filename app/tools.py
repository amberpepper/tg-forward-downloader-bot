from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from typing import Any

try:
    from .config import PROJECT_ROOT, Settings, first_env
    from .utils import now_utc
except ImportError:
    from config import PROJECT_ROOT, Settings, first_env
    from utils import now_utc

def detect_tool(binary: str, version_arg_sets: list[list[str]], install_hint: str) -> dict[str, Any]:
    path = shutil.which(binary)
    if not path:
        return {
            "installed": False,
            "binary": binary,
            "path": None,
            "version": None,
            "hint": install_hint,
        }
    version = ""
    last_error = ""
    for version_args in version_arg_sets:
        try:
            result = subprocess.run(
                [path, *version_args],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            text = (result.stdout or result.stderr or "").strip().splitlines()
            first_line = text[0] if text else ""
            if result.returncode == 0:
                version = first_line
                break
            last_error = first_line or f"exit {result.returncode}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"detect failed: {exc}"
    if not version:
        version = last_error or "unknown"
    return {
        "installed": True,
        "binary": binary,
        "path": path,
        "version": version,
        "hint": install_hint,
    }


def get_tools_status(settings: Settings) -> dict[str, Any]:
    return {
        "updated_at": now_utc(),
        "tools": {
            "tdl": detect_tool(settings.tdl_bin, [["version"], ["--version"]], "可在 .env 里用 TDL_BIN 指定二进制路径"),
            "yt-dlp": detect_tool(settings.yt_dlp_bin, [["--version"]], "可在 .env 里用 YT_DLP_BIN 指定二进制路径"),
            "ffmpeg": detect_tool(settings.ffmpeg_bin, [["-version"], ["--version"]], "可在 .env 里用 FFMPEG_BIN 指定二进制路径"),
        },
    }


def normalize_tool_name(name: str) -> str:
    value = (name or "").strip().lower()
    if value in {"ytdlp", "yt_dlp", "yt-dlp"}:
        return "yt-dlp"
    return value


def build_tool_action_env(settings: Settings, tool_name: str, update: bool) -> dict[str, str]:
    env = os.environ.copy()
    env["ENV_FILE"] = str(PROJECT_ROOT / ".env")
    env["ENV_EXAMPLE_FILE"] = str(PROJECT_ROOT / ".env.example")
    env["VENV_DIR"] = str(PROJECT_ROOT / ".venv")
    env["PYTHON_BIN"] = shutil.which("python3") or sys.executable
    env["INSTALL_PYTHON_DEPS"] = "0"
    env["UPDATE_MODE"] = "1" if update else "0"
    env["INSTALL_TDL"] = "1" if tool_name == "tdl" else "0"
    env["INSTALL_YTDLP"] = "1" if tool_name == "yt-dlp" else "0"
    env["INSTALL_FFMPEG"] = "1" if tool_name == "ffmpeg" else "0"
    env["INSTALL_BIN_DIR"] = first_env("INSTALL_BIN_DIR") or str(PROJECT_ROOT / "bin")
    return env


async def run_tool_action_script(settings: Settings, tool_name: str, action: str) -> dict[str, Any]:
    script_name = "update.sh" if action == "update" else "install.sh"
    script_path = PROJECT_ROOT / "scripts" / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"script not found: {script_path}")
    env = build_tool_action_env(settings, tool_name, update=action == "update")
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script_path),
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="ignore")
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": output,
        "script": script_name,
    }

