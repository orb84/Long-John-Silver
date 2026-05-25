@echo off
REM ─── LJS Launcher (Windows) ──────────────────────────────────
REM Creates a virtual environment if needed, installs dependencies,
REM and starts the server. Port defaults to 8088.
REM
REM Usage:
REM   run.bat                    # Start on port 8088
REM   run.bat 9000               # Start on custom port
REM   run.bat install            # Install/reinstall dependencies only
REM   run.bat update             # Update dependencies
REM ────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion

set "VENV_DIR=.venv"
set "PORT=%~1"
set "ACTION="

if "%PORT%"=="install" (
    set "ACTION=install"
    set "PORT="
)
if "%PORT%"=="update" (
    set "ACTION=update"
    set "PORT="
)

REM Port priority: CLI arg > LJS_PORT env > default 8088
if "%PORT%"=="" (
    if "%LJS_PORT%"=="" (
        set "PORT=8088"
    ) else (
        set "PORT=%LJS_PORT%"
    )
)

REM ─── Create virtual environment ────────────────────────────────
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [LJS] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [LJS] ERROR: Failed to create virtual environment. Is Python 3.10+ installed?
        exit /b 1
    )
    echo [LJS] Virtual environment created at %VENV_DIR%
)

REM ─── Activate ──────────────────────────────────────────────────
call "%VENV_DIR%\Scripts\activate.bat"

REM ─── Install / update dependencies ─────────────────────────────
set "NEEDS_INSTALL=0"

if not exist "%VENV_DIR%\.deps_installed" (
    set "NEEDS_INSTALL=1"
)

REM Check if requirements.txt is newer than .deps_installed
if exist "%VENV_DIR%\.deps_installed" (
    for %%F in (requirements.txt) do (
        for %%D in ("%VENV_DIR%\.deps_installed") do (
            if %%~tF gtr %%~tD set "NEEDS_INSTALL=1"
        )
    )
)

if "%ACTION%"=="update" set "NEEDS_INSTALL=1"

if "!NEEDS_INSTALL!"=="1" (
    echo [LJS] Installing dependencies...
    pip install --upgrade pip --quiet
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [LJS] ERROR: Failed to install dependencies.
        exit /b 1
    )

    echo [LJS] Optional bridges:
    echo     pip install discord.py           [Discord bot]
    echo     pip install python-telegram-bot  [Telegram bot]

    type nul > "%VENV_DIR%\.deps_installed"
    echo [LJS] Dependencies installed.
)

REM Playwright Chromium browser — always ensure it's installed (idempotent, skips if present)
"%VENV_DIR%\Scripts\python.exe" -c "import playwright" 2>nul && (
    echo [LJS] Ensuring Playwright Chromium browser is installed...
    "%VENV_DIR%\Scripts\python.exe" -m playwright install chromium
    if errorlevel 1 (
        echo [LJS] WARNING: Playwright Chromium install failed - torrent search may be limited.
    )
)

if "%ACTION%"=="install" (
    echo [LJS] Dependencies installed. Run run.bat to start the server.
    exit /b 0
)

if "%ACTION%"=="update" (
    echo [LJS] Dependencies updated.
    exit /b 0
)

REM ─── Ensure data directories exist ─────────────────────────────
if not exist "data" mkdir data
if not exist "config" mkdir config
if not exist "downloads" mkdir downloads

REM ─── Check for config ──────────────────────────────────────────
if not exist "config\settings.yaml" (
    echo [LJS] No config found - first run will launch setup wizard.
)

REM ─── Launch ────────────────────────────────────────────────────
set "LJS_PORT=%PORT%"
set "LJS_ALLOW_INSECURE_DEV=1"
echo [LJS] Starting LJS on port %PORT%...
echo [LJS] Open http://localhost:%PORT% in your browser.

python main.py