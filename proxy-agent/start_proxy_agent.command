#!/bin/bash
# Double-click this file to start the proxy agent. No commands to type.
cd "$(dirname "$0")"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "This needs Python 3.10 or newer (found: $(python3 --version 2>&1))."
  echo "Install a current Python from https://www.python.org/downloads/ and try again."
  read -p "Press Enter to close..."
  exit 1
fi

if [ ! -d venv ]; then
  echo "Setting up (first run only)..."
  python3 -m venv venv
fi

source venv/bin/activate
python3 -m pip install -q --upgrade pip
pip install -q -r requirements.txt

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
fi

echo "Starting AOS8-10-Migrator proxy agent... look for its icon in the menu bar."
python3 tray.py
