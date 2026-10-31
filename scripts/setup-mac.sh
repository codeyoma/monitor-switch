#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${MONITOR_SWITCH_PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Install Python 3.11 or newer first."'
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [ "$(uname -m)" = "arm64" ]; then
  HELPER=m1ddc
  REPOSITORY=https://github.com/waydabber/m1ddc.git
  REVISION=7339ceebb03f691b1eaeb8471dca301d215d19c8
else
  HELPER=ddcctl
  REPOSITORY=https://github.com/kfix/ddcctl.git
  REVISION=06c7ab6eba5b1c903678f8113a92cef990acaf90
fi
SOURCE=".tools/src/$HELPER"
mkdir -p .tools/src
if [ ! -d "$SOURCE/.git" ]; then
  git clone "$REPOSITORY" "$SOURCE"
fi
git -C "$SOURCE" checkout --detach "$REVISION"
.venv/bin/python scripts/patch_helpers.py "$HELPER" "$SOURCE"
make -C "$SOURCE"
if [ "$HELPER" = m1ddc ]; then
  cp "$SOURCE/m1ddc" ".tools/$HELPER"
else
  cp "$SOURCE/bin/release/ddcctl" ".tools/$HELPER"
fi
cp "$SOURCE/LICENSE" ".tools/$HELPER-LICENSE.txt"
printf '\nReady. Double-click Launch.command or run .venv/bin/python app.py\n'
