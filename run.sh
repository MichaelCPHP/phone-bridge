#!/usr/bin/env bash
# Phone Bridge — unified Mac-side launcher
# Phone = modem. Mac = everything.
#
# Starts mac_bridge.py which handles:
#   - iMessage + SMS via imsg watch (Mac Messages.app)
#   - Android SMS via ADB poll (fallback)
#   - AI replies via OpenClaw (Claude)
#
# Usage: bash run.sh
# Stop:  Ctrl+C

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export ADB_SERIAL="${ADB_SERIAL:-ZY22K45948}"
export PHONE_IP="${PHONE_IP:-192.168.1.40}"
export PHONE_PORT="${PHONE_PORT:-8080}"
export SMS_USER="${SMS_USER:-sms}"
export SMS_PASS="${SMS_PASS:-smspass1}"
export OPENCLAW_URL="${OPENCLAW_URL:-http://localhost:18789}"
export OPENCLAW_TOKEN="${OPENCLAW_TOKEN:-dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91}"
export AI_MODEL="${AI_MODEL:-anthropic/claude-haiku-4-5}"
export IMSG_BIN="${IMSG_BIN:-/opt/homebrew/bin/imsg}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "=== Phone Bridge Starting ==="

# 1. ADB check (USB preferred, WiFi fallback)
log "Checking Android..."
if ! adb -s "$ADB_SERIAL" shell echo ok >/dev/null 2>&1; then
    ADB_SERIAL="192.168.1.40:5555"
    adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
    adb -s "$ADB_SERIAL" shell echo ok >/dev/null 2>&1 || { log "❌ ADB not connected"; exit 1; }
fi
MODEL=$(adb -s "$ADB_SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
log "✅ Android: $MODEL ($ADB_SERIAL)"

# 2. Google Messages must stay as default (writes inbound to DB)
DEFAULT=$(adb -s "$ADB_SERIAL" shell cmd role get-role-holders android.app.role.SMS 2>/dev/null | tr -d '\r')
if [ "$DEFAULT" != "com.google.android.apps.messaging" ]; then
    log "Restoring Google Messages as default SMS app..."
    adb -s "$ADB_SERIAL" shell cmd role set-bypassing-role-qualification true 2>/dev/null || true
    adb -s "$ADB_SERIAL" shell cmd role add-role-holder android.app.role.SMS \
        com.google.android.apps.messaging 0 2>/dev/null || true
fi
log "✅ SMS app: $(adb -s "$ADB_SERIAL" shell cmd role get-role-holders android.app.role.SMS 2>/dev/null | tr -d '\r')"

# 3. Kill stale processes
pkill -f mac_bridge.py     2>/dev/null || true
pkill -f sms_adb_monitor.py 2>/dev/null || true
pkill -f sms_gateway.py    2>/dev/null || true
sleep 1

# 4. Start unified bridge
log "Starting unified Mac bridge..."
nohup python3 src/mac_bridge.py > /tmp/pb-monitor.log 2>&1 &
BRIDGE_PID=$!
sleep 3
kill -0 "$BRIDGE_PID" 2>/dev/null || { log "❌ Bridge failed — check /tmp/pb-monitor.log"; exit 1; }
log "✅ Bridge running: pid=$BRIDGE_PID"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      PHONE BRIDGE LIVE                   ║"
echo "║                                          ║"
echo "║  Android : +1-702-946-9526               ║"
echo "║  iMessage: via Mac Messages.app (imsg)   ║"
echo "║  SMS     : via ADB poll (every 3s)       ║"
echo "║  AI      : Claude Haiku (OpenClaw)       ║"
echo "║                                          ║"
echo "║  Text either number to test              ║"
echo "║  Logs: tail -f /tmp/pb-monitor.log       ║"
echo "║  Stop: Ctrl+C                            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

cleanup() {
    log "Shutting down..."
    kill "$BRIDGE_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

tail -f /tmp/pb-monitor.log
