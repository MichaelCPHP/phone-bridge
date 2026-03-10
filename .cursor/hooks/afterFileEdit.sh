#!/bin/bash
# OpenClaw CCPM Hook: Track modified files for close reports
# Installed by openclaw-vscode extension

SESSION_FILE="/tmp/ccpm-session.json"
MODIFIED_FILE="/tmp/ccpm-modified-files.txt"

# Only track if CCPM session is active
if [ ! -f "$SESSION_FILE" ]; then
  exit 0
fi

# Read the file path from stdin (Cursor sends JSON with file info)
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('file',''))" 2>/dev/null || echo "")

if [ -n "$FILE_PATH" ]; then
  # Append if not already listed (dedup)
  if ! grep -qxF "$FILE_PATH" "$MODIFIED_FILE" 2>/dev/null; then
    echo "$FILE_PATH" >> "$MODIFIED_FILE"
  fi
fi

exit 0
