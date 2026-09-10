@echo off
REM Double-click this file to start the proxy agent. No commands to type.
cd /d "%~dp0"

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
