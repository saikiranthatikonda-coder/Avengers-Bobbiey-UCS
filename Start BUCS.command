#!/bin/bash
# Start BUCS on macOS: double-click this file in Finder.
# (First time only: if macOS blocks it, Control-click it and choose Open, or allow it in
#  System Settings > Privacy & Security.) It runs start-jarvis.sh in this folder, which sets up
# Python 3.10+ dependencies on the first launch. The BUCS app for Mac (.dmg) needs no setup.
cd "$(dirname "$0")" || exit 1
# files unzipped from a download carry the quarantine flag; clear it for this BUCS folder only
xattr -dr com.apple.quarantine . 2>/dev/null || true
chmod +x ./start-jarvis.sh 2>/dev/null || true
./start-jarvis.sh
status=$?
if [ $status -ne 0 ]; then
  echo ""
  echo "BUCS stopped (exit code $status). Press Return to close this window."
  read -r _
fi
exit $status
