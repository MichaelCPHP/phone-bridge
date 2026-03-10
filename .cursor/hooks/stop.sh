#!/bin/bash
# OpenClaw CCPM Hook: Remind about open issues on stop
# Installed by openclaw-vscode extension

SESSION_FILE="/tmp/ccpm-session.json"

if [ -f "$SESSION_FILE" ]; then
  ISSUE=$(python3 -c "import json; d=json.load(open('$SESSION_FILE')); print(d.get('issue','?'))" 2>/dev/null || echo "?")
  PROJECT=$(python3 -c "import json; d=json.load(open('$SESSION_FILE')); print(d.get('project','?'))" 2>/dev/null || echo "?")
  echo "CCPM: Issue #$ISSUE on $PROJECT is still open. Close with: ccpm-close --force 'summary'" >&2
fi

exit 0
