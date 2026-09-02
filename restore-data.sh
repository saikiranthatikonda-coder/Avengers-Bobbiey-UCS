#!/usr/bin/env bash
# ═══ Bobbiey UCS — restore the private migration bundle (macOS / Linux) ═══
#
# Puts your secrets/config/data back after cloning the repo on a new machine.
# NON-DESTRUCTIVE: anything it would replace is backed up to .restore-backup/
# first, and nothing is ever deleted.
#
#   ./restore-data.sh /Volumes/USB/bobbiey-migration
#   ./restore-data.sh /Volumes/USB/bobbiey-migration --new-node-identity
#
# See MIGRATION.md for the full procedure.

set -e
cd "$(dirname "$0")"
PROJ="$(pwd)"

BUNDLE="${1:-}"
NEW_NODE=""
[ "${2:-}" = "--new-node-identity" ] && NEW_NODE=1

if [ -z "$BUNDLE" ]; then
  echo "usage: ./restore-data.sh <path-to-bobbiey-migration> [--new-node-identity]"
  exit 1
fi

SRC="$BUNDLE/private-data"
if [ ! -d "$SRC" ]; then
  echo "[restore] ERROR: no 'private-data' folder inside $BUNDLE"
  echo "[restore] point at the folder that CONTAINS private-data/"
  exit 1
fi

BACKUP="$PROJ/.restore-backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"

echo "[restore] project : $PROJ"
echo "[restore] bundle  : $SRC"
echo "[restore] backups : $BACKUP"
echo

restored=0; backed=0; skipped=0
for f in "$SRC"/* "$SRC"/.[!.]*; do
  [ -f "$f" ] || continue
  name="$(basename "$f")"

  if [ -n "$NEW_NODE" ] && [ "$name" = "node_id.txt" ]; then
    echo "  skip     node_id.txt (new identity requested)"
    skipped=$((skipped+1)); continue
  fi

  # never overwrite without keeping a copy
  if [ -f "$PROJ/$name" ]; then
    cp "$PROJ/$name" "$BACKUP/$name"; backed=$((backed+1))
  fi
  cp "$f" "$PROJ/$name"
  echo "  restored $name"
  restored=$((restored+1))
done

echo
echo "[restore] $restored file(s) restored, $backed backed up, $skipped skipped"

echo
echo "[restore] verifying…"
for c in .env credentials.json token.json operator_memory.json; do
  if [ -f "$PROJ/$c" ]; then echo "  OK   $c"; else echo "  MISSING  $c"; fi
done

if [ -d "$BUNDLE/claude-context" ]; then
  echo
  echo "[restore] Claude memory files are in: $BUNDLE/claude-context"
  echo "[restore] launch Claude Code once in this project, then copy the *.md files into"
  echo "          ~/.claude/projects/<project-key>/memory/ and restart it."
fi

echo
echo "[restore] NEXT: edit .env and set CLAUDE_BIN for this machine (find it with: which claude)"
echo "[restore] then start the platform:  ./start-jarvis.sh"
