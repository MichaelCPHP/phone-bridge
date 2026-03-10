#!/usr/bin/env bash
# Phone Bridge — single-command ADB SMS bridge
set -e

ADB_SERIAL="192.168.1.40:5555"
GATEWAY_URL="http://localhost:18789/v1/chat/completions"
GATEWAY_TOKEN="dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91"
POLL_SEC=3

# ── Verify ADB ─────────────────────────────────────────────────────────────
echo "🔌 Checking ADB connection..."
if ! adb -s "$ADB_SERIAL" shell echo ok 2>/dev/null | grep -q ok; then
  echo "❌ ADB not connected. Run: adb connect $ADB_SERIAL"
  exit 1
fi
echo "✅ ADB connected: $ADB_SERIAL"

# ── Verify OpenClaw gateway ─────────────────────────────────────────────────
echo "🔌 Checking OpenClaw gateway..."
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL" 2>/dev/null || echo "000")
if [[ "$HTTP" != "200" && "$HTTP" != "405" && "$HTTP" != "401" ]]; then
  echo "❌ Gateway not reachable (HTTP $HTTP)"
  exit 1
fi
echo "✅ OpenClaw gateway OK"

# ── Get last SMS id ─────────────────────────────────────────────────────────
LAST_ID=$(adb -s "$ADB_SERIAL" shell "content query --uri content://sms --projection '_id' --sort 'date DESC'" 2>/dev/null | head -1 | grep -o '_id=[0-9]*' | cut -d= -f2)
LAST_ID=${LAST_ID:-0}
echo "📱 Starting poll (last SMS id: $LAST_ID)"
echo ""

# ── Poll loop ───────────────────────────────────────────────────────────────
while true; do
  # Query for new inbound SMS
  NEW=$(adb -s "$ADB_SERIAL" shell "content query --uri content://sms --projection '_id,address,body' --where '_id > $LAST_ID AND type=1' --sort 'date ASC'" 2>/dev/null)

  if [[ -n "$NEW" && "$NEW" != "No result found." ]]; then
    while IFS= read -r line; do
      if [[ "$line" == Row:* ]]; then
        ID=$(echo "$line" | grep -o '_id=[0-9]*' | cut -d= -f2)
        FROM=$(echo "$line" | grep -o 'address=[^,]*' | cut -d= -f2)
        BODY=$(echo "$line" | grep -o 'body=.*' | cut -d= -f2-)

        echo "[$(date +%H:%M:%S)] 📨 SMS from $FROM: $BODY"

        # Get AI reply
        BODY_ESCAPED=$(echo "$BODY" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))" 2>/dev/null || echo '"Hey"')
        REPLY=$(curl -s "$GATEWAY_URL" \
          -H "Authorization: Bearer $GATEWAY_TOKEN" \
          -H "Content-Type: application/json" \
          -d "{\"model\":\"openclaw:main\",\"messages\":[{\"role\":\"system\",\"content\":\"You are Jarvis, a helpful AI. Reply via SMS. Be concise, max 160 chars.\"},{\"role\":\"user\",\"content\":$BODY_ESCAPED}],\"max_tokens\":100}" \
          2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'].strip())" 2>/dev/null)

        REPLY=${REPLY:-"Got your message! How can I help?"}
        echo "[$(date +%H:%M:%S)] 🤖 Reply: $REPLY"

        # Send reply via ADB
        REPLY_SAFE=$(echo "$REPLY" | tr -d "'" | head -c 160)
        adb -s "$ADB_SERIAL" shell "am start -a android.intent.action.SENDTO -d 'smsto:$FROM' --es 'sms_body' '$REPLY_SAFE'" 2>/dev/null
        sleep 2
        adb -s "$ADB_SERIAL" shell "input tap 985 2419" 2>/dev/null
        echo "[$(date +%H:%M:%S)] ✅ Reply sent to $FROM"

        LAST_ID=$ID
      fi
    done <<< "$NEW"
  fi

  sleep "$POLL_SEC"
done
