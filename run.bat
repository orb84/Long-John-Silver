@echo off
REM ─── LJS Launcher (Windows) ──────────────────────────────────
REM Creates a virtual environment if needed, installs dependencies,
REM ensures optional runtime helpers such as FFmpeg, and starts LJS.
REM
REM Usage:
REM   run.bat                         Start on port 8088
REM   run.bat 9000                    Start on custom port
REM   run.bat install                 Install/reinstall dependencies only
REM   run.bat update                  Update dependencies
REM   run.bat doctor                  Print launcher diagnostics
REM   run.bat install-python          Install Python 3.11 with winget
REM   run.bat install-ffmpeg          Install FFmpeg with winget/choco/scoop
REM   run.bat reset-venv              Remove .venv and recreate on next start
REM
REM Advanced:
REM   set LJS_PYTHON=C:\Path\To\python.exe
REM   set LJS_AUTO_INSTALL_PYTHON=0    Disable Python auto-install
REM   set LJS_AUTO_INSTALL_FFMPEG=0    Disable FFmpeg auto-install
REM   set LJS_VENV_DIR=.venv-alt
REM ────────────────────────────────────────────────────────────────

setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "VENV_DIR=%LJS_VENV_DIR%"
if "%VENV_DIR%"=="" set "VENV_DIR=.venv"
set "RAW_ARG=%~1"
set "PORT=%RAW_ARG%"
set "ACTION="
set "PYTHON_BIN="
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "MIN_PYTHON=3.10"
set "PREFERRED_WINGET_PYTHON=%LJS_WINGET_PYTHON%"
if "%PREFERRED_WINGET_PYTHON%"=="" set "PREFERRED_WINGET_PYTHON=Python.Python.3.11"

if /I "%RAW_ARG%"=="install" (set "ACTION=install" & set "PORT=")
if /I "%RAW_ARG%"=="-i" (set "ACTION=install" & set "PORT=")
if /I "%RAW_ARG%"=="update" (set "ACTION=update" & set "PORT=")
if /I "%RAW_ARG%"=="-u" (set "ACTION=update" & set "PORT=")
if /I "%RAW_ARG%"=="doctor" (set "ACTION=doctor" & set "PORT=")
if /I "%RAW_ARG%"=="--doctor" (set "ACTION=doctor" & set "PORT=")
if /I "%RAW_ARG%"=="install-python" (set "ACTION=install-python" & set "PORT=")
if /I "%RAW_ARG%"=="install-ffmpeg" (set "ACTION=install-ffmpeg" & set "PORT=")
if /I "%RAW_ARG%"=="reset-venv" (set "ACTION=reset-venv" & set "PORT=")
if /I "%RAW_ARG%"=="help" goto :usage
if /I "%RAW_ARG%"=="--help" goto :usage
if /I "%RAW_ARG%"=="-h" goto :usage

REM Port priority: CLI arg > LJS_PORT env > default 8088
if "%PORT%"=="" (
    if "%LJS_PORT%"=="" (
        set "PORT=8088"
    ) else (
        set "PORT=%LJS_PORT%"
    )
)

if /I "%ACTION%"=="reset-venv" (
    echo [LJS] Removing %VENV_DIR%...
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
    echo [LJS] Virtual environment removed. Run run.bat to recreate it.
    exit /b 0
)

if /I "%ACTION%"=="install-python" (
    call :install_python_windows || exit /b 1
    call :pick_python || exit /b 1
    call :python_version_path "!PYTHON_BIN!"
    echo [LJS] Python ready: !PY_VERSION! at !PYTHON_BIN!
    exit /b 0
)

REM Python is needed for every other action, including doctor.
call :pick_python || exit /b 1
call :python_version_path "%PYTHON_BIN%"
echo [LJS] Using Python !PY_VERSION! at %PYTHON_BIN%

if /I "%ACTION%"=="install-ffmpeg" (
    call :ensure_ffmpeg 1
    exit /b %ERRORLEVEL%
)

if /I "%ACTION%"=="doctor" (
    call :doctor
    exit /b 0
)

REM ─── Create / validate virtual environment ─────────────────────
if exist "%VENV_PYTHON%" (
    call :is_python_path_310_plus "%VENV_PYTHON%"
    if errorlevel 1 (
        call :python_version_path "%VENV_PYTHON%"
        echo [LJS] WARNING: Existing virtual environment uses Python !PY_VERSION!, but LJS needs Python %MIN_PYTHON%+.
        echo [LJS] Recreating %VENV_DIR%...
        rmdir /s /q "%VENV_DIR%"
    )
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [LJS] Creating virtual environment...
    "%PYTHON_BIN%" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [LJS] ERROR: Failed to create virtual environment with %PYTHON_BIN%.
        echo [LJS] Try: run.bat reset-venv
        exit /b 1
    )
    echo [LJS] Virtual environment created at %VENV_DIR%
)

if not exist "%VENV_PYTHON%" (
    echo [LJS] ERROR: Virtual environment was created but %VENV_PYTHON% was not found.
    exit /b 1
)

call :is_python_path_310_plus "%VENV_PYTHON%"
if errorlevel 1 (
    call :python_version_path "%VENV_PYTHON%"
    echo [LJS] ERROR: Virtual environment is still using Python !PY_VERSION!.
    echo [LJS] Remove %VENV_DIR% and retry after installing Python %MIN_PYTHON%+.
    exit /b 1
)

call :python_version_path "%VENV_PYTHON%"
echo [LJS] Virtualenv Python: !PY_VERSION!

REM ─── Install / update dependencies ─────────────────────────────
if not exist "requirements.txt" (
    echo [LJS] ERROR: requirements.txt not found in %SCRIPT_DIR%
    exit /b 1
)

set "NEEDS_INSTALL=0"
if not exist "%VENV_DIR%\.deps_installed" set "NEEDS_INSTALL=1"
if exist "%VENV_DIR%\.deps_installed" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-Item 'requirements.txt').LastWriteTimeUtc -gt (Get-Item '%VENV_DIR%\.deps_installed').LastWriteTimeUtc) { exit 0 } else { exit 1 }" >nul 2>nul
    if not errorlevel 1 set "NEEDS_INSTALL=1"
)
if /I "%ACTION%"=="update" set "NEEDS_INSTALL=1"
if /I "%ACTION%"=="install" set "NEEDS_INSTALL=1"

if "%NEEDS_INSTALL%"=="1" (
    echo [LJS] Installing dependencies...
    "%VENV_PYTHON%" -m pip install --upgrade pip --quiet
    if errorlevel 1 exit /b 1
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [LJS] ERROR: Failed to install dependencies.
        exit /b 1
    )
    echo [LJS] Optional bridges:
    echo     %VENV_PYTHON% -m pip install discord.py           [Discord bot]
    echo     %VENV_PYTHON% -m pip install python-telegram-bot  [Telegram bot]
    type nul > "%VENV_DIR%\.deps_installed"
    echo [LJS] Dependencies installed.
)

REM Playwright Chromium browser — idempotent and skipped if Playwright is absent.
"%VENV_PYTHON%" -c "import playwright" >nul 2>nul
if not errorlevel 1 (
    echo [LJS] Ensuring Playwright Chromium browser is installed...
    "%VENV_PYTHON%" -m playwright install chromium
    if errorlevel 1 echo [LJS] WARNING: Playwright Chromium install failed - browser-backed search may be limited.
)

REM FFmpeg is optional for the app, but needed for Music/Audiobook conversion.
call :ensure_ffmpeg 0

if /I "%ACTION%"=="install" (
    echo [LJS] Dependencies installed. Run run.bat to start the server.
    exit /b 0
)

if /I "%ACTION%"=="update" (
    echo [LJS] Dependencies updated.
    exit /b 0
)

REM ─── Ensure runtime directories exist ───────────────────────────
if not exist "data" mkdir data
if not exist "config" mkdir config
if not exist "config\categories" mkdir config\categories
if not exist "downloads" mkdir downloads

if not exist "config\settings.local.yaml" (
    echo [LJS] No local config found - LJS will create config\settings.local.yaml from the template and launch setup.
)

REM ─── Launch ────────────────────────────────────────────────────
set "LJS_PORT=%PORT%"
set "LJS_ALLOW_INSECURE_DEV=1"
echo [LJS] Starting LJS on port %PORT%...
echo [LJS] Open http://localhost:%PORT% in your browser.

for /f "tokens=14" %%A in ('ipconfig ^| findstr /R /C:"IPv4 Address" /C:"Indirizzo IPv4" 2^>nul') do (
    echo [LJS] LAN candidate: http://%%A:%PORT%
)

"%VENV_PYTHON%" main.py
exit /b %ERRORLEVEL%

:usage
echo LJS Windows launcher
echo.
echo Usage:
echo   run.bat                         Start on port 8088
echo   run.bat 9000                    Start on custom port
echo   run.bat install                 Install/reinstall dependencies only
echo   run.bat update                  Update dependencies
echo   run.bat doctor                  Print diagnostics
echo   run.bat install-python          Install Python 3.11 with winget
echo   run.bat install-ffmpeg          Install FFmpeg with winget/choco/scoop
echo   run.bat reset-venv              Remove .venv
echo.
exit /b 0

:doctor
echo [LJS] Doctor diagnostics
echo [LJS] Script dir: %SCRIPT_DIR%
echo [LJS] Venv dir: %VENV_DIR%
call :python_version_path "%PYTHON_BIN%"
echo [LJS] Selected Python: !PY_VERSION! at %PYTHON_BIN%
if exist "%VENV_PYTHON%" (
    call :python_version_path "%VENV_PYTHON%"
    echo [LJS] Venv Python: !PY_VERSION! at %VENV_PYTHON%
) else (
    echo [LJS] Venv Python: missing
)
where winget >nul 2>nul && echo [LJS] winget: available || echo [LJS] winget: not found
where choco >nul 2>nul && echo [LJS] Chocolatey: available || echo [LJS] Chocolatey: not found
where scoop >nul 2>nul && echo [LJS] Scoop: available || echo [LJS] Scoop: not found
call :find_ffmpeg
if defined FFMPEG_EXE (
    echo [LJS] FFmpeg: %FFMPEG_EXE%
) else (
    echo [LJS] FFmpeg: missing
)
exit /b 0

:pick_python
if defined LJS_PYTHON (
    if exist "%LJS_PYTHON%" (
        call :try_python_path "%LJS_PYTHON%" && exit /b 0
    ) else (
        call :try_python_cmd "%LJS_PYTHON%" && exit /b 0
    )
    echo [LJS] ERROR: LJS_PYTHON is set to '%LJS_PYTHON%', but it is not Python %MIN_PYTHON%+.
    exit /b 1
)

REM Prefer versions with broad dependency support before trying newer aliases.
call :try_python_cmd "py -3.12" && exit /b 0
call :try_python_cmd "py -3.11" && exit /b 0
call :try_python_cmd "py -3.10" && exit /b 0
call :try_python_cmd "python3.12" && exit /b 0
call :try_python_cmd "python3.11" && exit /b 0
call :try_python_cmd "python3.10" && exit /b 0
call :try_python_path "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" && exit /b 0
call :try_python_path "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" && exit /b 0
call :try_python_path "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" && exit /b 0
call :try_python_path "%ProgramFiles%\Python312\python.exe" && exit /b 0
call :try_python_path "%ProgramFiles%\Python311\python.exe" && exit /b 0
call :try_python_path "%ProgramFiles%\Python310\python.exe" && exit /b 0
call :try_python_cmd "python" && exit /b 0
call :try_python_cmd "py -3.13" && exit /b 0
call :try_python_cmd "python3.13" && exit /b 0

if /I "%LJS_AUTO_INSTALL_PYTHON%"=="0" (
    echo [LJS] ERROR: Python %MIN_PYTHON%+ was not found and auto-install is disabled.
    exit /b 1
)

call :install_python_windows || exit /b 1

REM Re-run discovery after install because winget may alter PATH only for new shells.
call :try_python_cmd "py -3.12" && exit /b 0
call :try_python_cmd "py -3.11" && exit /b 0
call :try_python_cmd "py -3.10" && exit /b 0
call :try_python_path "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" && exit /b 0
call :try_python_path "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" && exit /b 0
call :try_python_path "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" && exit /b 0
call :try_python_path "%ProgramFiles%\Python312\python.exe" && exit /b 0
call :try_python_path "%ProgramFiles%\Python311\python.exe" && exit /b 0
call :try_python_path "%ProgramFiles%\Python310\python.exe" && exit /b 0
call :try_python_cmd "python" && exit /b 0

echo [LJS] ERROR: Python installation finished, but Python %MIN_PYTHON%+ was still not found.
echo [LJS] Open a new Command Prompt or set LJS_PYTHON to python.exe and rerun.
exit /b 1

:try_python_cmd
set "PY_CMD=%~1"
%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%P in (`%PY_CMD% -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_BIN=%%P"
if "%PYTHON_BIN%"=="" exit /b 1
exit /b 0

:try_python_path
if "%~1"=="" exit /b 1
if not exist "%~1" exit /b 1
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%P in (`"%~1" -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_BIN=%%P"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=%~1"
exit /b 0

:is_python_path_310_plus
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
exit /b %ERRORLEVEL%

:python_version_path
set "PY_VERSION=unknown"
if exist "%~1" (
    for /f "usebackq delims=" %%V in (`"%~1" -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2^>nul`) do set "PY_VERSION=%%V"
)
exit /b 0

:install_python_windows
where winget >nul 2>nul
if errorlevel 1 (
    echo [LJS] ERROR: Python %MIN_PYTHON%+ is required and winget was not found.
    echo [LJS] Install Python 3.11+ from python.org, or install App Installer/winget, then rerun.
    echo [LJS] You may also set LJS_PYTHON=C:\Path\To\python.exe
    exit /b 1
)

echo [LJS] Python %MIN_PYTHON%+ was not found. Installing %PREFERRED_WINGET_PYTHON% with winget...
winget install -e --id %PREFERRED_WINGET_PYTHON% --scope user --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo [LJS] User-scope Python install failed; retrying with winget default scope...
    winget install -e --id %PREFERRED_WINGET_PYTHON% --accept-source-agreements --accept-package-agreements
)
if errorlevel 1 (
    echo [LJS] ERROR: winget could not install Python.
    exit /b 1
)
exit /b 0

:ensure_ffmpeg
set "FFMPEG_REQUIRED=%~1"
call :find_ffmpeg
if defined FFMPEG_EXE (
    for %%D in ("%FFMPEG_EXE%") do set "PATH=%%~dpD;%PATH%"
    echo [LJS] FFmpeg found: %FFMPEG_EXE%
    exit /b 0
)

if /I "%LJS_AUTO_INSTALL_FFMPEG%"=="0" (
    echo [LJS] WARNING: FFmpeg is missing. Audio conversion workflows will be unavailable.
    if "%FFMPEG_REQUIRED%"=="1" exit /b 1
    exit /b 0
)

call :install_ffmpeg_windows
set "FFMPEG_INSTALL_RC=%ERRORLEVEL%"
call :find_ffmpeg
if defined FFMPEG_EXE (
    for %%D in ("%FFMPEG_EXE%") do set "PATH=%%~dpD;%PATH%"
    echo [LJS] FFmpeg installed/found: %FFMPEG_EXE%
    exit /b 0
)

echo [LJS] WARNING: FFmpeg could not be found after install attempt. Audio conversion workflows will be unavailable.
if "%FFMPEG_REQUIRED%"=="1" exit /b 1
exit /b 0

:find_ffmpeg
set "FFMPEG_EXE="
for /f "usebackq delims=" %%F in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$cmd=Get-Command ffmpeg.exe -ErrorAction SilentlyContinue; if($cmd){$cmd.Source; exit}; $roots=@($env:LOCALAPPDATA+'\Microsoft\WinGet\Packages',$env:LOCALAPPDATA+'\Programs',$env:USERPROFILE+'\scoop\shims',$env:ProgramData+'\chocolatey\bin'); foreach($r in $roots){ if($r -and (Test-Path $r)){ $hit=Get-ChildItem -Path $r -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue ^| Select-Object -First 1; if($hit){$hit.FullName; exit} } }" 2^>nul`) do set "FFMPEG_EXE=%%F"
exit /b 0

:install_ffmpeg_windows
where winget >nul 2>nul
if not errorlevel 1 (
    echo [LJS] Installing FFmpeg with winget...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    if not errorlevel 1 exit /b 0
    echo [LJS] winget FFmpeg install failed; trying fallback package managers if available...
)
where choco >nul 2>nul
if not errorlevel 1 (
    echo [LJS] Installing FFmpeg with Chocolatey...
    choco install ffmpeg -y
    if not errorlevel 1 exit /b 0
)
where scoop >nul 2>nul
if not errorlevel 1 (
    echo [LJS] Installing FFmpeg with Scoop...
    scoop install ffmpeg
    if not errorlevel 1 exit /b 0
)
echo [LJS] No supported Windows package manager could install FFmpeg automatically.
echo [LJS] Install manually or run: winget install -e --id Gyan.FFmpeg
exit /b 1
