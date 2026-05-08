#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"
APP_FILE="${APP_FILE:-$ROOT_DIR/app/main.py}"

COLOR_RESET='\033[0m'
COLOR_BLUE='\033[34m'
COLOR_GREEN='\033[32m'
COLOR_YELLOW='\033[33m'
COLOR_RED='\033[31m'

log() { echo -e "${COLOR_BLUE}==>${COLOR_RESET} $*"; }
ok() { echo -e "${COLOR_GREEN}[OK]${COLOR_RESET} $*"; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $*"; }
err() { echo -e "${COLOR_RED}[ERR]${COLOR_RESET} $*" >&2; }

require_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "$path" ]]; then
    err "$label 不存在: $path"
    exit 1
  fi
}

check_env_value() {
  local key="$1"
  local value
  value="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ -z "$value" ]]; then
    err ".env 里缺少 $key"
    exit 1
  fi
}

show_runtime_hint() {
  local bot_token web_port web_host
  bot_token="$(grep -E '^[[:space:]]*BOT_TOKEN=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  web_host="$(grep -E '^[[:space:]]*WEB_HOST=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  web_port="$(grep -E '^[[:space:]]*WEB_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  web_host="${web_host:-0.0.0.0}"
  web_port="${web_port:-8090}"

  echo
  ok "启动检查通过"
  [[ -n "$bot_token" ]] && ok "BOT_TOKEN 已配置"
  ok "Web: http://${web_host}:${web_port}"
  echo
}

main() {
  log "项目目录: $ROOT_DIR"
  require_file "$ENV_FILE" ".env"
  require_file "$APP_FILE" "入口文件"

  if [[ ! -x "$PYTHON_BIN" ]]; then
    err "未找到虚拟环境 Python: $PYTHON_BIN"
    echo "先执行：bash \"$ROOT_DIR/scripts/install.sh\""
    exit 1
  fi

  check_env_value "BOT_TOKEN"
  check_env_value "WEB_ADMIN_USERNAME"
  check_env_value "WEB_ADMIN_PASSWORD"
  check_env_value "WEB_SECRET_KEY"

  show_runtime_hint
  log "启动 bot"
  cd "$ROOT_DIR"
  exec "$PYTHON_BIN" "$APP_FILE"
}

main "$@"
