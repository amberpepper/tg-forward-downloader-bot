#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
ENV_EXAMPLE_FILE="${ENV_EXAMPLE_FILE:-$ROOT_DIR/.env.example}"
INSTALL_TDL="${INSTALL_TDL:-1}"
INSTALL_YTDLP="${INSTALL_YTDLP:-1}"
INSTALL_FFMPEG="${INSTALL_FFMPEG:-1}"
INSTALL_PYTHON_DEPS="${INSTALL_PYTHON_DEPS:-1}"
UPDATE_MODE="${UPDATE_MODE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -n "${INSTALL_BIN_DIR:-}" ]]; then
  BIN_DIR="$INSTALL_BIN_DIR"
elif [[ $EUID -eq 0 || -w /usr/local/bin ]]; then
  BIN_DIR="/usr/local/bin"
else
  BIN_DIR="$HOME/.local/bin"
fi

COLOR_RESET='\033[0m'
COLOR_BLUE='\033[34m'
COLOR_GREEN='\033[32m'
COLOR_YELLOW='\033[33m'
COLOR_RED='\033[31m'

log() { echo -e "${COLOR_BLUE}==>${COLOR_RESET} $*"; }
ok() { echo -e "${COLOR_GREEN}[OK]${COLOR_RESET} $*"; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $*"; }
err() { echo -e "${COLOR_RED}[ERR]${COLOR_RESET} $*" >&2; }

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

sudo_if_needed() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif has_cmd sudo; then
    sudo "$@"
  else
    err "需要 root 或 sudo 执行：$*"
    exit 1
  fi
}

apt_install_if_missing() {
  local packages=()
  for pkg in "$@"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      packages+=("$pkg")
    fi
  done
  if [[ ${#packages[@]} -eq 0 ]]; then
    return 0
  fi
  log "安装系统依赖: ${packages[*]}"
  sudo_if_needed apt-get update
  sudo_if_needed apt-get install -y "${packages[@]}"
}

ensure_base_deps() {
  if ! has_cmd "$PYTHON_BIN"; then
    err "未找到 $PYTHON_BIN"
    exit 1
  fi

  if has_cmd apt-get; then
    local pkgs=(ca-certificates curl wget tar)
    if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
      pkgs+=(python3-venv)
    fi
    apt_install_if_missing "${pkgs[@]}"
  else
    for cmd in curl wget tar; do
      if ! has_cmd "$cmd"; then
        err "缺少命令: $cmd，且当前系统不是 apt 环境，请手动安装"
        exit 1
      fi
    done
  fi
}

download_to_file() {
  local url="$1"
  local output="$2"
  if has_cmd curl; then
    curl -fsSL "$url" -o "$output"
  elif has_cmd wget; then
    wget -q "$url" -O "$output"
  else
    err "缺少 curl/wget"
    exit 1
  fi
}

fetch_latest_github_tag() {
  local repo="$1"
  "$PYTHON_BIN" - "$repo" <<'PY'
import json
import sys
import urllib.request

repo = sys.argv[1]
url = f"https://api.github.com/repos/{repo}/releases/latest"
req = urllib.request.Request(url, headers={"User-Agent": "tg-forward-downloader-installer"})
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.load(resp)
print(data["tag_name"])
PY
}

tdl_asset_name() {
  local os_name arch_name
  case "$(uname -s)" in
    Linux) os_name="Linux" ;;
    Darwin) os_name="MacOS" ;;
    *)
      err "tdl 暂不支持当前系统: $(uname -s)"
      exit 1
      ;;
  esac

  case "$(uname -m)" in
    x86_64) arch_name="64bit" ;;
    i686) arch_name="32bit" ;;
    armv5*) arch_name="armv5" ;;
    armv6*) arch_name="armv6" ;;
    armv7*) arch_name="armv7" ;;
    arm64|aarch64*) arch_name="arm64" ;;
    *)
      err "tdl 暂不支持当前架构: $(uname -m)"
      exit 1
      ;;
  esac

  printf 'tdl_%s_%s.tar.gz' "$os_name" "$arch_name"
}

ensure_bin_dir() {
  mkdir -p "$BIN_DIR"
  if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "当前 PATH 不包含 $BIN_DIR"
    warn "建议执行: export PATH=\"$BIN_DIR:\$PATH\""
  fi
}

create_env_file_if_missing() {
  if [[ -f "$ENV_FILE" ]]; then
    ok ".env 已存在，保留当前配置"
    return
  fi
  cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
  ok "已生成 .env: $ENV_FILE"
}

upsert_env_value() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -Eq "^[[:space:]]*${key}=" "$file"; then
    "$PYTHON_BIN" - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
text = path.read_text(encoding="utf-8")
text = re.sub(rf"(?m)^[ \t]*{re.escape(key)}=.*$", f"{key}={value}", text)
path.write_text(text, encoding="utf-8")
PY
    ok "已更新 $key=$value"
    return
  fi
  printf '\n%s=%s\n' "$key" "$value" >>"$file"
  ok "已追加 $key=$value"
}

install_python_deps() {
  if [[ "$INSTALL_PYTHON_DEPS" != "1" ]]; then
    warn "跳过 Python 依赖安装"
    return
  fi
  log "创建虚拟环境: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"
  ok "Python 依赖安装完成"
}

install_tdl() {
  if [[ "$INSTALL_TDL" != "1" ]]; then
    warn "跳过 tdl 安装"
    return
  fi

  ensure_bin_dir
  local version asset url tmpdir
  version="$(fetch_latest_github_tag "iyear/tdl")"
  asset="$(tdl_asset_name)"
  url="https://github.com/iyear/tdl/releases/download/${version}/${asset}"
  tmpdir="$(mktemp -d)"

  log "安装 tdl: $version"
  download_to_file "$url" "$tmpdir/$asset"
  tar -xzf "$tmpdir/$asset" -C "$tmpdir"
  install -m 0755 "$tmpdir/tdl" "$BIN_DIR/tdl"
  rm -rf "$tmpdir"
  ok "tdl 已安装到 $BIN_DIR/tdl"
}

install_yt_dlp() {
  if [[ "$INSTALL_YTDLP" != "1" ]]; then
    warn "跳过 yt-dlp 安装"
    return
  fi

  ensure_bin_dir
  local tmpfile
  tmpfile="$(mktemp)"

  log "安装 yt-dlp"
  download_to_file "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp" "$tmpfile"
  install -m 0755 "$tmpfile" "$BIN_DIR/yt-dlp"
  rm -f "$tmpfile"
  ok "yt-dlp 已安装到 $BIN_DIR/yt-dlp"
}

install_ffmpeg() {
  if [[ "$INSTALL_FFMPEG" != "1" ]]; then
    warn "跳过 ffmpeg 安装"
    return
  fi

  if [[ "$UPDATE_MODE" != "1" ]] && has_cmd ffmpeg; then
    ok "ffmpeg 已存在: $(command -v ffmpeg)"
    return
  fi

  if has_cmd apt-get; then
    log "$([[ "$UPDATE_MODE" == "1" ]] && echo '更新' || echo '安装') ffmpeg"
    sudo_if_needed apt-get update
    sudo_if_needed apt-get install -y ffmpeg
    ok "ffmpeg 已就绪"
  else
    warn "当前系统不是 apt 环境，跳过 ffmpeg，请手动安装"
  fi
}

main() {
  log "项目目录: $ROOT_DIR"
  ensure_base_deps
  create_env_file_if_missing
  install_python_deps
  install_tdl
  install_yt_dlp
  install_ffmpeg

  if [[ -x "$BIN_DIR/tdl" ]]; then
    upsert_env_value "TDL_BIN" "$BIN_DIR/tdl" "$ENV_FILE"
  fi
  if [[ -x "$BIN_DIR/yt-dlp" ]]; then
    upsert_env_value "YT_DLP_BIN" "$BIN_DIR/yt-dlp" "$ENV_FILE"
  fi
  if has_cmd ffmpeg; then
    upsert_env_value "FFMPEG_BIN" "$(command -v ffmpeg)" "$ENV_FILE"
  fi

  echo
  ok "安装完成"
  echo "下一步："
  echo "  1. 编辑 $ENV_FILE，填好 BOT_TOKEN / 后台账号等配置"
  echo "  2. 启动虚拟环境: source \"$VENV_DIR/bin/activate\""
  echo "  3. 运行程序: python3 \"$ROOT_DIR/app/main.py\""
}

main "$@"
