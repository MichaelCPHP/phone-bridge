#!/usr/bin/env bash
# Phone Bridge — persistent launcher with auto-restart
set -e
cd "$(dirname "$0")"

ADB_SERIAL="${ADB_SERIAL:-192.168.1.40:5555}"
export PHONE_IP="${PHONE_IP:-192.168.1.40}"
export SMS_GATEWAY_USER="${SMS_GATEWAY_USER:-sms}"
export SMS_GATEWAY_PASS="${SMS_GATEWAY_PASS:-smspass1}"

echo "🚀 Phone Bridge starting..."

# Verify ADB
if ! adb -s "$ADB_SERIAL" shell echo ok 2>/dev/null | grep -q ok; then
  echo "❌ ADB not connected. Run: adb connect $ADB_SERIAL"
  exit 1
fi
echo "✅ ADB connected: $ADB_SERIAL"

# Refresh ADB reverse tunnel
adb -s "$ADB_SERIAL" reverse tcp:3001 tcp:3001 2>/dev/null && echo "✅ ADB reverse tunnel active"

# Verify gateway
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://$PHONE_IP:8080/health --max-time 5 2>/dev/null)
[[ "$HTTP" == "200" ]] && echo "✅ SMS Gateway healthy" || echo "⚠️  SMS Gateway unreachable (HTTP $HTTP)"

# Auto-restart loop
restart_sms_server() {
  while true; do
    echo "[$(date +%H:%M:%S)] Starting SMS webhook server..."
    python3 src/sms_gateway.py 2>&1 | tee -a /tmp/sms_gateway.log
    echo "[$(date +%H:%M:%S)] SMS server crashed — restarting in 3s..."
    sleep 3
  done
}

restart_imessage_bridge() {
  while true; do
    echo "[$(date +%H:%M:%S)] Starting iMessage bridge..."
    PHONE_IP=$PHONE_IP SMS_GATEWAY_USER=$SMS_GATEWAY_USER SMS_GATEWAY_PASS=$SMS_GATEWAY_PASS \
      python3 src/mac_bridge.py 2>&1 | tee -a /tmp/mac_bridge.log
    echo "[$(date +%H:%M:%S)] iMessage bridge crashed — restarting in 3s..."
    sleep 3
  done
}

# Kill existing
pkill -f "sms_gateway.py|mac_bridge.py" 2>/dev/null; sleep 1

# Start both with auto-restart
restart_sms_server &
sleep 2
restart_imessage_bridge &

echo ""
echo "✅ Phone Bridge LIVE"
echo "   SMS webhook: http://localhost:3001/webhook/sms"
echo "   iMessage: watching Mac Messages.app"
echo "   Logs: /tmp/sms_gateway.log | /tmp/mac_bridge.log"
echo ""
echo "Press Ctrl+C to stop"
wait
