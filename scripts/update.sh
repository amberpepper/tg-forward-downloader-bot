#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export INSTALL_PYTHON_DEPS="${INSTALL_PYTHON_DEPS:-0}"
export UPDATE_MODE="${UPDATE_MODE:-1}"

exec bash "$ROOT_DIR/scripts/install.sh"
