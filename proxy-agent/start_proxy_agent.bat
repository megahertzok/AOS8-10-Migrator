@echo off
REM Double-click this file to start the proxy agent. No commands to type.
cd /d "%~dp0"

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
if errorlevel 1 (
    echo This needs Python 3.10 or newer.
    echo Install a current Python from https://www.python.org/downloads/ and try again.
    pause
    exit /b 1
)

if not exist venv (
    echo Setting up ^(first run only^)...
    python -m venv venv
)

call venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

if not exist config.yaml (
    copy config.example.yaml config.yaml
)

echo Starting AOS8-10-Migrator proxy agent... look for its icon in the system tray.
python tray.py
pause
