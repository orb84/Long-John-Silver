#!/usr/bin/env bash
# ─── LJS Launcher (Linux / macOS) ───────────────────────────────
# Creates a virtual environment if needed, installs dependencies,
# and starts the server. Port defaults to 8088.
#
# Usage:
#   ./run.sh                    # Start on port 8088
#   ./run.sh 9000               # Start on custom port
#   LJS_PORT=9000 ./run.sh      # Port via environment variable
#   ./run.sh install            # Install/reinstall dependencies only
#   ./run.sh update             # Update dependencies
#   ./run.sh doctor             # Print launcher diagnostics
#   ./run.sh install-ffmpeg     # Install FFmpeg with the platform package manager
#   ./run.sh reset-venv         # Remove .venv and recreate on next start
#
# Advanced:
#   LJS_PYTHON=/path/to/python3.11 ./run.sh
#   LJS_AUTO_INSTALL_PYTHON=0 ./run.sh     # Disable Python auto-install
#   LJS_AUTO_INSTALL_HOMEBREW=0 ./run.sh   # Disable Homebrew prompt on macOS
#   LJS_AUTO_INSTALL_FFMPEG=0 ./run.sh     # Disable FFmpeg auto-install
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${LJS_VENV_DIR:-.venv}"
RAW_ARG="${1:-}"
PORT="${RAW_ARG}"
ACTION=""
PYTHON_BIN=""
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
PREFERRED_BREW_PYTHON="${LJS_BREW_PYTHON:-python@3.11}"

# ─── Colors ───────────────────────────────────────────────────
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    GOLD='\033[0;33m'
    DIM='\033[0;2m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    GOLD=''
    DIM=''
    RED=''
    NC=''
fi

info()  { printf "%b⚓%b %s\n" "$GREEN" "$NC" "$1"; }
warn()  { printf "%b⚠%b %s\n" "$GOLD" "$NC" "$1"; }
error() { printf "%b✗%b %s\n" "$RED" "$NC" "$1" >&2; }

usage() {
    cat <<USAGE
LJS launcher

Usage:
  ./run.sh [PORT]
  ./run.sh install
  ./run.sh update
  ./run.sh doctor
  ./run.sh install-ffmpeg
  ./run.sh reset-venv

Environment:
  LJS_PORT=8088
  LJS_PYTHON=/path/to/python3.11
  LJS_AUTO_INSTALL_PYTHON=0
  LJS_AUTO_INSTALL_HOMEBREW=0
  LJS_AUTO_INSTALL_FFMPEG=0
USAGE
}

# Parse action arguments.
case "$RAW_ARG" in
    "" ) ;;
    install|-i) ACTION="install"; PORT="" ;;
    update|-u) ACTION="update"; PORT="" ;;
    doctor|--doctor) ACTION="doctor"; PORT="" ;;
    install-ffmpeg|ffmpeg|--install-ffmpeg) ACTION="install-ffmpeg"; PORT="" ;;
    reset-venv|reset_venv|--reset-venv) ACTION="reset-venv"; PORT="" ;;
    help|-h|--help) usage; exit 0 ;;
    *) ;;
esac

# Port priority: CLI arg > LJS_PORT env > default 8088.
if [ -z "$PORT" ]; then
    PORT="${LJS_PORT:-8088}"
fi

validate_port() {
    local value="$1"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        error "Invalid port '$value'. Use a number, for example: ./run.sh 8088"
        exit 2
    fi
    if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
        error "Invalid port '$value'. Port must be between 1 and 65535."
        exit 2
    fi
}

if [ -z "$ACTION" ]; then
    validate_port "$PORT"
fi

# ─── Python discovery / install ───────────────────────────────
# LJS uses modern Python typing and Pydantic. Python 3.10+ is required.
# On macOS, /usr/bin/python3 can be Apple's older Python, so the launcher
# searches Homebrew, pyenv, and versioned binaries before creating .venv.

python_version() {
    "$1" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo "unknown"
}

is_python_310_plus() {
    "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

resolve_executable() {
    # Prints an executable path for either a command name or an absolute/relative path.
    if [ -x "$1" ]; then
        printf '%s\n' "$1"
        return 0
    fi
    command -v "$1" 2>/dev/null || true
}

consider_python() {
    local candidate_path
    candidate_path="$(resolve_executable "$1")"
    if [ -n "$candidate_path" ] && [ -x "$candidate_path" ] && is_python_310_plus "$candidate_path"; then
        PYTHON_BIN="$candidate_path"
        return 0
    fi
    return 1
}

pick_python_without_install() {
    if [ -n "${LJS_PYTHON:-}" ]; then
        if consider_python "$LJS_PYTHON"; then
            return 0
        fi
        error "LJS_PYTHON is set to '$LJS_PYTHON', but it is not Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+."
        error "Detected version: $(python_version "$LJS_PYTHON")"
        exit 1
    fi

    # Prefer stable, commonly supported versions before falling back to newer ones.
    local candidate
    for candidate in \
        python3.12 python3.11 python3.10 python3.13 python3 \
        /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3.13 \
        /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 /usr/local/bin/python3.13 \
        /opt/homebrew/opt/python@3.12/bin/python3.12 /opt/homebrew/opt/python@3.11/bin/python3.11 /opt/homebrew/opt/python@3.10/bin/python3.10 /opt/homebrew/opt/python@3.13/bin/python3.13 \
        /usr/local/opt/python@3.12/bin/python3.12 /usr/local/opt/python@3.11/bin/python3.11 /usr/local/opt/python@3.10/bin/python3.10 /usr/local/opt/python@3.13/bin/python3.13
    do
        if consider_python "$candidate"; then
            return 0
        fi
    done

    # pyenv support, if present.
    if command -v pyenv >/dev/null 2>&1; then
        for candidate in 3.12 3.11 3.10 3.13; do
            local pyenv_path
            pyenv_path="$(pyenv which python$candidate 2>/dev/null || true)"
            if [ -n "$pyenv_path" ] && consider_python "$pyenv_path"; then
                return 0
            fi
        done
    fi

    return 1
}

refresh_homebrew_shellenv() {
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
}

install_homebrew_if_allowed() {
    if command -v brew >/dev/null 2>&1; then
        return 0
    fi

    refresh_homebrew_shellenv
    if command -v brew >/dev/null 2>&1; then
        return 0
    fi

    if [ "${LJS_AUTO_INSTALL_HOMEBREW:-1}" != "1" ]; then
        return 1
    fi

    if [ "$(uname -s)" != "Darwin" ]; then
        return 1
    fi

    if [ ! -t 0 ]; then
        return 1
    fi

    warn "Homebrew is not installed. Homebrew is the safest way for this launcher to install Python on macOS."
    printf "Install Homebrew now? This will run the official Homebrew installer. [y/N] "
    local reply
    read -r reply || true
    case "$reply" in
        y|Y|yes|YES)
            info "Installing Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            ;;
        *)
            return 1
            ;;
    esac

    refresh_homebrew_shellenv
    command -v brew >/dev/null 2>&1
}

install_python_macos() {
    info "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ was not found. Attempting automatic install on macOS..."

    if ! install_homebrew_if_allowed; then
        error "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required, but neither Python nor Homebrew is available."
        error "Install Homebrew or Python 3.11+, then rerun this script."
        error "To disable automatic install attempts, run: LJS_AUTO_INSTALL_PYTHON=0 ./run.sh"
        return 1
    fi

    info "Installing ${PREFERRED_BREW_PYTHON} with Homebrew..."
    brew install "$PREFERRED_BREW_PYTHON"
    refresh_homebrew_shellenv
    pick_python_without_install
}

run_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        error "This install step needs root privileges, but sudo is not available."
        return 1
    fi
}

install_python_linux() {
    info "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ was not found. Attempting automatic install on Linux..."

    if command -v apt-get >/dev/null 2>&1; then
        warn "Using apt-get. You may be asked for your sudo password."
        run_sudo apt-get update
        run_sudo apt-get install -y python3.11 python3.11-venv python3.11-dev || \
            run_sudo apt-get install -y python3 python3-venv python3-dev
    elif command -v dnf >/dev/null 2>&1; then
        warn "Using dnf. You may be asked for your sudo password."
        run_sudo dnf install -y python3.11 python3.11-devel || run_sudo dnf install -y python3 python3-devel
    elif command -v yum >/dev/null 2>&1; then
        warn "Using yum. You may be asked for your sudo password."
        run_sudo yum install -y python3.11 python3.11-devel || run_sudo yum install -y python3 python3-devel
    elif command -v pacman >/dev/null 2>&1; then
        warn "Using pacman. You may be asked for your sudo password."
        run_sudo pacman -Sy --needed python
    elif command -v zypper >/dev/null 2>&1; then
        warn "Using zypper. You may be asked for your sudo password."
        run_sudo zypper install -y python311 python311-devel || run_sudo zypper install -y python3 python3-devel
    else
        error "No supported package manager found. Install Python 3.11+ manually, then rerun this script."
        return 1
    fi

    pick_python_without_install
}

install_python_if_missing() {
    if [ "${LJS_AUTO_INSTALL_PYTHON:-1}" != "1" ]; then
        return 1
    fi

    case "$(uname -s)" in
        Darwin)
            install_python_macos
            ;;
        Linux)
            install_python_linux
            ;;
        *)
            return 1
            ;;
    esac
}

pick_python() {
    if pick_python_without_install; then
        return 0
    fi

    if install_python_if_missing; then
        return 0
    fi

    error "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ was not found and could not be installed automatically."
    if [ "$(uname -s)" = "Darwin" ]; then
        error "On macOS, install it with: brew install ${PREFERRED_BREW_PYTHON}"
    else
        error "Install Python 3.11+ with your system package manager."
    fi
    exit 1
}

has_ffmpeg() {
    command -v ffmpeg >/dev/null 2>&1
}

install_ffmpeg_macos() {
    if ! install_homebrew_if_allowed; then
        error "FFmpeg is not installed and Homebrew is unavailable. Install it manually with: brew install ffmpeg"
        return 1
    fi
    info "Installing FFmpeg with Homebrew..."
    brew install ffmpeg
}

install_ffmpeg_linux() {
    if command -v apt-get >/dev/null 2>&1; then
        warn "Using apt-get to install FFmpeg. You may be asked for your sudo password."
        run_sudo apt-get update
        run_sudo apt-get install -y ffmpeg
    elif command -v dnf >/dev/null 2>&1; then
        warn "Using dnf to install FFmpeg. You may be asked for your sudo password."
        run_sudo dnf install -y ffmpeg
    elif command -v yum >/dev/null 2>&1; then
        warn "Using yum to install FFmpeg. You may be asked for your sudo password."
        run_sudo yum install -y ffmpeg
    elif command -v pacman >/dev/null 2>&1; then
        warn "Using pacman to install FFmpeg. You may be asked for your sudo password."
        run_sudo pacman -Sy --needed ffmpeg
    elif command -v zypper >/dev/null 2>&1; then
        warn "Using zypper to install FFmpeg. You may be asked for your sudo password."
        run_sudo zypper install -y ffmpeg
    else
        error "No supported package manager found. Install FFmpeg manually."
        return 1
    fi
}

install_ffmpeg() {
    if has_ffmpeg; then
        info "FFmpeg already available at $(command -v ffmpeg)"
        return 0
    fi
    case "$(uname -s)" in
        Darwin) install_ffmpeg_macos ;;
        Linux) install_ffmpeg_linux ;;
        *) error "Automatic FFmpeg install is not supported on this OS."; return 1 ;;
    esac
    if has_ffmpeg; then
        info "FFmpeg installed at $(command -v ffmpeg)"
        return 0
    fi
    error "FFmpeg install command finished, but ffmpeg is still not on PATH."
    return 1
}

warn_or_install_ffmpeg() {
    if has_ffmpeg; then
        return 0
    fi
    if [ "${LJS_AUTO_INSTALL_FFMPEG:-1}" = "1" ]; then
        info "FFmpeg is missing; installing it automatically for Music/Audiobook conversion workflows..."
        install_ffmpeg || warn "FFmpeg install failed — setup can continue, but audio conversion workflows will remain unavailable. Set LJS_AUTO_INSTALL_FFMPEG=0 to skip this attempt."
    else
        warn "FFmpeg is not installed. Setup can continue, but Music/Audiobook conversion workflows need it."
        warn "Run ./run.sh install-ffmpeg, or unset LJS_AUTO_INSTALL_FFMPEG=0 to let this launcher install it automatically."
    fi
}

if [ "$ACTION" = "reset-venv" ]; then
    warn "Removing $VENV_DIR..."
    rm -rf "$VENV_DIR"
    info "Virtual environment removed. Run ./run.sh to recreate it."
    exit 0
fi

if [ "$ACTION" = "install-ffmpeg" ]; then
    install_ffmpeg
    exit $?
fi

pick_python
info "Using Python $(python_version "$PYTHON_BIN") at $PYTHON_BIN"

if [ "$ACTION" = "doctor" ]; then
    info "Launcher diagnostics"
    echo "  Project: $SCRIPT_DIR"
    echo "  OS: $(uname -s) $(uname -m)"
    echo "  Python: $(python_version "$PYTHON_BIN") at $PYTHON_BIN"
    echo "  Venv: $VENV_DIR"
    if [ -f "$VENV_DIR/bin/python" ]; then
        echo "  Venv Python: $(python_version "$VENV_DIR/bin/python") at $VENV_DIR/bin/python"
    else
        echo "  Venv Python: not created yet"
    fi
    if command -v brew >/dev/null 2>&1; then
        echo "  Homebrew: $(command -v brew)"
    fi
    if has_ffmpeg; then
        echo "  FFmpeg: $(command -v ffmpeg)"
    else
        echo "  FFmpeg: missing (audio conversion workflows unavailable; run ./run.sh install-ffmpeg)"
    fi
    exit 0
fi

# ─── Create / validate virtual environment ─────────────────────
if [ -f "$VENV_DIR/bin/python" ]; then
    if ! is_python_310_plus "$VENV_DIR/bin/python"; then
        warn "Existing virtual environment uses Python $(python_version "$VENV_DIR/bin/python"), but LJS needs Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+."
        warn "Recreating $VENV_DIR with Python $(python_version "$PYTHON_BIN")..."
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    info "Creating virtual environment..."
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        error "Failed to create virtual environment with $PYTHON_BIN."
        if [ "$(uname -s)" = "Linux" ]; then
            error "If venv support is missing, install the venv package for your Python version."
        fi
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
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
VENV_PYTHON="$VENV_DIR/bin/python"

if ! is_python_310_plus "$VENV_PYTHON"; then
    error "Virtual environment is still using Python $(python_version "$VENV_PYTHON")."
    error "Remove '$VENV_DIR' and retry after installing Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+."
    exit 1
fi

info "Virtualenv Python: $($VENV_PYTHON --version)"

# Ensure pip exists even on minimal Python installs.
if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    info "Bootstrapping pip in virtual environment..."
    "$VENV_PYTHON" -m ensurepip --upgrade
fi

# ─── Install / update dependencies ────────────────────────────
NEEDS_INSTALL=0

if [ ! -f "requirements.txt" ]; then
    error "requirements.txt not found in $SCRIPT_DIR"
    exit 1
fi

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
    "$VENV_PYTHON" -m pip install --upgrade pip --quiet
    "$VENV_PYTHON" -m pip install -r requirements.txt

    # Optional: install chat bridges.
    printf "  %bOptional bridges:%b\n" "$DIM" "$NC"
    printf "  %b%s -m pip install discord.py           # Discord bot%b\n" "$DIM" "$VENV_PYTHON" "$NC"
    printf "  %b%s -m pip install python-telegram-bot  # Telegram bot%b\n" "$DIM" "$VENV_PYTHON" "$NC"

    touch "$VENV_DIR/.deps_installed"
    info "Dependencies installed."
fi

# Playwright Chromium browser — always ensure it's installed.
# This is idempotent and skips if Playwright is not installed.
if "$VENV_PYTHON" -c "import playwright" 2>/dev/null; then
    info "Ensuring Playwright Chromium browser is installed..."
    "$VENV_PYTHON" -m playwright install chromium || \
        warn "Playwright Chromium install failed — browser search may be limited."
fi

warn_or_install_ffmpeg

if [ "$ACTION" = "install" ]; then
    info "Dependencies installed. Run ./run.sh to start the server."
    exit 0
fi

if [ "$ACTION" = "update" ]; then
    info "Dependencies updated."
    exit 0
fi

# ─── Ensure runtime directories exist ───────────────────────────
mkdir -p data config config/categories downloads

# ─── Check for local config ─────────────────────────────────────
if [ ! -f "config/settings.local.yaml" ]; then
    info "No local config found — LJS will create config/settings.local.yaml from the template and launch setup."
fi

# ─── Launch ────────────────────────────────────────────────────
export LJS_PORT="$PORT"
export LJS_ALLOW_INSECURE_DEV="${LJS_ALLOW_INSECURE_DEV:-1}"
info "Starting LJS on port ${GOLD}${PORT}${NC}..."
info "Open ${GOLD}http://localhost:${PORT}${NC} in your browser."

# Linux LAN candidates.
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

# macOS LAN candidates.
if command -v ipconfig >/dev/null 2>&1; then
    for iface in en0 en1; do
        ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
        if [ -n "$ip" ]; then
            info "LAN candidate: ${GOLD}http://${ip}:${PORT}${NC}"
        fi
    done
fi

exec "$VENV_PYTHON" main.py
