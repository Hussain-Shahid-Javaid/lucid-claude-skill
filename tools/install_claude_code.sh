#!/usr/bin/env bash
# Install / upgrade / remove the Lucid skill for Claude Code.
#   tools/install_claude_code.sh            # personal: ~/.claude/skills/lucid
#   tools/install_claude_code.sh --project  # this repo: .claude/skills/lucid
#   tools/install_claude_code.sh --uninstall [--project]
# Data in $LUCID_HOME (~/.lucid) is never touched by install/upgrade/uninstall.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/.claude/skills/lucid"
MODE=install
for a in "$@"; do
  case "$a" in
    --project) DEST="$(pwd)/.claude/skills/lucid" ;;
    --uninstall) MODE=uninstall ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
if [ "$MODE" = uninstall ]; then
  rm -rf "$DEST" && echo "removed $DEST (memory data in \${LUCID_HOME:-~/.lucid} kept)"; exit 0
fi
python3 "$ROOT/tools/build_skill.py" >/dev/null
mkdir -p "$(dirname "$DEST")"
TMP="$DEST.new.$$"
rm -rf "$TMP"; cp -R "$ROOT/skill/lucid" "$TMP"
rm -rf "$DEST.old"; [ -d "$DEST" ] && mv "$DEST" "$DEST.old"
mv "$TMP" "$DEST"; rm -rf "$DEST.old"
python3 "$DEST/scripts/lucid.py" init
echo "installed Lucid skill at $DEST — invoke with /lucid in Claude Code"
