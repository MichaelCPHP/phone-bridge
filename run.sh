#!/bin/bash
# Phone Bridge — Mac-side controller
# Android = SIM modem only. All logic runs here.
# Usage: bash run.sh
# Stop:  Ctrl+C

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Config ────────────────────────────────────────────────────────────────────
ADB_SERIAL="${ADB_SERIAL:-ZY22K45948}"
PHONE_IP="${PHONE_IP:-192.168.1.40}"
PHONE_PORT="${PHONE_PORT:-8080}"
SMS_USER="${SMS_USER:-sms}"
SMS_PASS="${SMS_PASS:-smspass1}"
OPENCLAW_URL="${OPENCLAW_URL:-http://localhost:18789}"
OPENCLAW_TOKEN="${OPENCLAW_TOKEN:-dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91}"
AI_MODEL="${AI_MODEL:-anthropic/claude-haiku-4-5}"
POLL_INTERVAL="${POLL_INTERVAL:-3}"

export PHONE_IP PHONE_PORT SMS_USER SMS_PASS ADB_SERIAL \
       OPENCLAW_URL OPENCLAW_TOKEN AI_MODEL

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { log "ERROR: $*"; exit 1; }
ok()   { log "✅ $*"; }
warn() { log "⚠️  $*"; }

log "=== Phone Bridge Starting (Mac-side) ==="

# ── 1. ADB ────────────────────────────────────────────────────────────────────
log "Connecting to Android..."
if ! adb -s "$ADB_SERIAL" shell echo ok >/dev/null 2>&1; then
    log "USB not found, trying WiFi ADB..."
    ADB_SERIAL="192.168.1.40:5555"
    adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
    adb -s "$ADB_SERIAL" shell echo ok >/dev/null 2>&1 || die "Cannot reach Android via USB or WiFi"
fi
MODEL=$(adb -s "$ADB_SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
ok "Android: $MODEL ($ADB_SERIAL)"

# ── 2. Ensure Google Messages is default (writes inbound SMS to DB) ───────────
DEFAULT=$(adb -s "$ADB_SERIAL" shell cmd role get-role-holders android.app.role.SMS 2>/dev/null | tr -d '\r')
if [ "$DEFAULT" != "com.google.android.apps.messaging" ]; then
    log "Setting Google Messages as default SMS app..."
    adb -s "$ADB_SERIAL" shell cmd role set-bypassing-role-qualification true 2>/dev/null || true
    adb -s "$ADB_SERIAL" shell cmd role add-role-holder android.app.role.SMS \
        com.google.android.apps.messaging 0 2>/dev/null || true
    DEFAULT=$(adb -s "$ADB_SERIAL" shell cmd role get-role-holders android.app.role.SMS 2>/dev/null | tr -d '\r')
fi
ok "Default SMS: $DEFAULT"

# ── 3. OpenClaw gateway check ─────────────────────────────────────────────────
if curl -sf "$OPENCLAW_URL/v1/models" \
        -H "Authorization: Bearer $OPENCLAW_TOKEN" \
        --max-time 3 >/dev/null 2>&1; then
    ok "OpenClaw gateway: online"
else
    warn "OpenClaw gateway unreachable — AI replies will fail"
fi

# ── 4. Kill stale processes ───────────────────────────────────────────────────
pkill -f sms_adb_monitor.py 2>/dev/null || true
pkill -f sms_gateway.py    2>/dev/null || true
sleep 1

# ── 5. Start ADB SMS monitor ──────────────────────────────────────────────────
log "Starting ADB SMS monitor..."
nohup python3 src/sms_adb_monitor.py > /tmp/pb-monitor.log 2>&1 &
MONITOR_PID=$!
sleep 2
if kill -0 "$MONITOR_PID" 2>/dev/null; then
    ok "Monitor: pid=$MONITOR_PID (polling every ${POLL_INTERVAL}s)"
else
    die "Monitor failed to start — check /tmp/pb-monitor.log"
fi

# ── 6. Banner ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         PHONE BRIDGE LIVE                ║"
echo "║                                          ║"
echo "║  Number  : +1-702-946-9526               ║"
echo "║  Android : $MODEL"
echo "║  AI      : $AI_MODEL"
echo "║  Poll    : every ${POLL_INTERVAL}s                        ║"
echo "║                                          ║"
echo "║  Text the number above to test           ║"
echo "║  Logs: tail -f /tmp/pb-monitor.log       ║"
echo "║  Stop: Ctrl+C                            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

cleanup() {
    log "Shutting down..."
    kill "$MONITOR_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

tail -f /tmp/pb-monitor.log
