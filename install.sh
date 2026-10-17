#!/usr/bin/env bash
# editor-ai-kit installer
# Copies the six skills from plugins/editor-ai-kit/skills/ into ~/.claude/skills/
# on the founder's computer (the brain). Safe to run again: each skill folder
# is replaced with the fresh copy, nothing else in ~/.claude/skills is touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/plugins/editor-ai-kit/skills"
DEST_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

if [ ! -d "$SRC_DIR" ]; then
  echo "error: $SRC_DIR not found. Run this from inside the editor-ai-kit folder." >&2
  exit 1
fi

mkdir -p "$DEST_DIR"

installed=0
for skill_dir in "$SRC_DIR"/*/; do
  [ -f "$skill_dir/SKILL.md" ] || continue
  name="$(basename "$skill_dir")"
  dest="$DEST_DIR/$name"
  rm -rf "$dest"
  cp -R "$skill_dir" "$dest"
  find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  echo "installed: $name"
  installed=$((installed + 1))
done

if [ "$installed" -eq 0 ]; then
  echo "error: no skills found under $SRC_DIR" >&2
  exit 1
fi

echo ""
echo "Done. $installed skills are in $DEST_DIR"

# Friendly checks, never fatal
if ! command -v python3 >/dev/null 2>&1; then
  echo "heads up: python3 not found. Install Python 3.9 or newer."
elif ! python3 -c "import PIL, numpy" >/dev/null 2>&1; then
  echo "heads up: motion-render needs Pillow and numpy:  python3 -m pip install --user pillow numpy"
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "heads up: ffmpeg not found on PATH. Mac: brew install ffmpeg. Linux: sudo apt install ffmpeg."
fi

echo ""
echo "Next: start Claude Code and say \"run brand-style\" to teach it your look,"
echo "then \"run telegram-setup\" to give your editors the chat."
