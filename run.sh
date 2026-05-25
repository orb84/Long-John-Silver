#!/usr/bin/env bash
# ─── LJS Launcher (Linux / macOS) ───────────────────────────────
# Creates a virtual environment if needed, installs dependencies,
# and starts the server. Port defaults to 8088.
#
# Usage:
#   ./run.sh                    # Start on port 8088
#   ./run.sh 9000               # Start on custom port
#   LJS_PORT=9000 ./run.sh     # Port via environment variable
#   ./run.sh install            # Install/reinstall dependencies only
#   ./run.sh update             # Update dependencies
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
PORT="${1:-}"
ACTION=""

# Parse action arguments
if [ "$PORT" = "install" ] || [ "$PORT" = "-i" ]; then
    ACTION="install"
    PORT=""
elif [ "$PORT" = "update" ] || [ "$PORT" = "-u" ]; then
    ACTION="update"
    PORT=""
fi

# Port priority: CLI arg > LJS_PORT env > default 8088
if [ -z "$PORT" ]; then
    PORT="${LJS_PORT:-8088}"
fi

# ─── Colors ───────────────────────────────────────────────────
GREEN='\033[0;32m'
GOLD='\033[0;33m'
DIM='\033[0;2m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}⚓${NC} $1"; }
warn()  { echo -e "${GOLD}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }

# ─── Create virtual environment ──────────────────────────────
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    info "Creating virtual environment..."
    if ! python3 -m venv "$VENV_DIR"; then
        error "Failed to create virtual environment. Is Python 3.10+ installed?"
        exit 1
    fi
    if [ ! -f "$VENV_DIR/bin/activate" ]; then
        error "Virtual environment created but activate script not found."
        error "Try removing '$VENV_DIR' and running again."
        exit 1
    fi
    info "Virtual environment created at $VENV_DIR"
fi

# ─── Activate ────────────────────────────────────────────────
source "$VENV_DIR/bin/activate"

# ─── Install / update dependencies ────────────────────────────
NEEDS_INSTALL=0

if [ ! -f "$VENV_DIR/.deps_installed" ]; then
    NEEDS_INSTALL=1
elif [ "requirements.txt" -nt "$VENV_DIR/.deps_installed" ]; then
    NEEDS_INSTALL=1
fi

if [ "$ACTION" = "update" ]; then
    NEEDS_INSTALL=1
fi

if [ "$NEEDS_INSTALL" -eq 1 ] || [ "$ACTION" = "install" ]; then
    info "Installing dependencies..."
    pip install --upgrade pip --quiet
    pip install -r requirements.txt

    # Optional: install chat bridges
    echo -e "  ${DIM}Optional bridges:${NC}"
    echo -e "  ${DIM}pip install discord.py           # Discord bot${NC}"
    echo -e "  ${DIM}pip install python-telegram-bot  # Telegram bot${NC}"

    touch "$VENV_DIR/.deps_installed"
    info "Dependencies installed."
fi

# Playwright Chromium browser — always ensure it's installed (idempotent, skips if present)
if "$VENV_DIR/bin/python" -c "import playwright" 2>/dev/null; then
    info "Ensuring Playwright Chromium browser is installed..."
    "$VENV_DIR/bin/python" -m playwright install chromium || \
        warn "Playwright Chromium install failed — torrent search may be limited."
fi

if [ "$ACTION" = "install" ]; then
    info "Dependencies installed. Run ./run.sh to start the server."
    exit 0
fi

if [ "$ACTION" = "update" ]; then
    info "Dependencies updated."
    exit 0
fi

# ─── Ensure data directories exist ─────────────────────────────
mkdir -p data config downloads

# ─── Check for config ──────────────────────────────────────────
if [ ! -f "config/settings.yaml" ]; then
    info "No config found — first run will launch setup wizard."
fi

# ─── Launch ────────────────────────────────────────────────────
export LJS_PORT="$PORT"
export LJS_ALLOW_INSECURE_DEV=1
info "Starting LJS on port ${GOLD}${PORT}${NC}..."
info "Open ${GOLD}http://localhost:${PORT}${NC} in your browser."
if command -v hostname >/dev/null 2>&1; then
    LAN_IPS="$(hostname -I 2>/dev/null || true)"
    if [ -n "$LAN_IPS" ]; then
        for ip in $LAN_IPS; do
            case "$ip" in
                127.*|::1) ;;
                *) info "LAN candidate: ${GOLD}http://${ip}:${PORT}${NC}" ;;
            esac
        done
    fi
fi

python main.py