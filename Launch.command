#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  scripts/setup-mac.sh
fi
exec .venv/bin/python app.py "$@"
