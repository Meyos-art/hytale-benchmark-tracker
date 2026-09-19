#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Python 3.10 or later was not found. Install Python, then run this script again." >&2
    exit 1
fi

if [ ! -d .venv ]; then
    "$PYTHON" -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade "pip==26.2.0"
./.venv/bin/python -m pip install --editable .

if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "Created config.json from config.example.json."
fi

printf '\nInstallation complete.\n'
echo "1. Edit config.json."
echo "2. Add your Google service account JSON file."
echo "3. Run: ./.venv/bin/python log_to_sheets.py"
