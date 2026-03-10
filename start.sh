#!/usr/bin/env bash
# start.sh — Boot all Phone Bridge services
# Usage: ./start.sh [--no-asterisk] [--no-sms] [--no-ai]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
LOG_DIR="$SCRIPT_DIR/logs"

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }

# ─── Flags ───────────────────────────────────────────────────────────────────
START_ASTERISK=true
START_SMS=true
START_AI=true

for arg in "$@"; do
  case "$arg" in
    --no-asterisk) START_ASTERISK=false ;;
    --no-sms)      START_SMS=false ;;
    --no-ai)       START_AI=false ;;
    --help|-h)
      echo "Usage: $0 [--no-asterisk] [--no-sms] [--no-ai]"
      exit 0 ;;
  esac
done

# ─── Load .env ───────────────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
  ok "Loaded $ENV_FILE"
else
  warn ".env not found — using existing environment. Copy .env.example to .env and fill in values."
fi

# ─── Preflight checks ────────────────────────────────────────────────────────
echo ""
echo "── Preflight ─────────────────────────────────────────────────────────────"

MISSING=()
[[ -z "${ANTHROPIC_API_KEY:-}" ]]  && MISSING+=("ANTHROPIC_API_KEY")
[[ -z "${DEEPGRAM_API_KEY:-}" ]]   && MISSING+=("DEEPGRAM_API_KEY")
[[ -z "${ELEVENLABS_API_KEY:-}" ]] && MISSING+=("ELEVENLABS_API_KEY")

if [[ ${#MISSING[@]} -gt 0 ]]; then
  for m in "${MISSING[@]}"; do
    warn "Not set: $m (some services may fail)"
  done
else
  ok "API keys present"
fi

# Check Docker
if ! command -v docker &>/dev/null; then
  fail "docker not found — install Docker Desktop"
  exit 1
fi
ok "Docker found"

# Check Python deps
if ! python3 -c "import flask, requests, anthropic" &>/dev/null 2>&1; then
  warn "Some Python deps missing — running: pip3 install -r requirements.txt"
  pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet
fi
ok "Python deps ready"

mkdir -p "$LOG_DIR"

# ─── Service 1: Asterisk (Docker) ────────────────────────────────────────────
if $START_ASTERISK; then
  echo ""
  echo "── Asterisk ──────────────────────────────────────────────────────────────"
  if docker ps --format '{{.Names}}' | grep -q "^asterisk-bridge$"; then
    ok "Asterisk already running"
  else
    if docker ps -a --format '{{.Names}}' | grep -q "^asterisk-bridge$"; then
      docker start asterisk-bridge &>/dev/null
      ok "Asterisk restarted (existing container)"
    else
      ASTERISK_SIP_SECRET="${ASTERISK_SIP_SECRET:-phonebridge123}"
      ASTERISK_MANAGER_SECRET="${ASTERISK_MANAGER_SECRET:-phonebridge123}"
      docker run -d \
        --name asterisk-bridge \
        --network host \
        -e "ASTERISK_SIP_SECRET=$ASTERISK_SIP_SECRET" \
        -e "ASTERISK_MANAGER_SECRET=$ASTERISK_MANAGER_SECRET" \
        -v "$SCRIPT_DIR/config/asterisk:/etc/asterisk" \
        andrius/asterisk:latest \
        &>> "$LOG_DIR/asterisk.log"
      ok "Asterisk started (Docker)"
    fi
  fi
  HOST_IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "unknown")
  echo "   SIP server: $HOST_IP:5060"
  echo "   Linphone: server=$HOST_IP  user=android-phone  pass=\$ASTERISK_SIP_SECRET"
fi

# ─── Service 2: SMS Gateway server ───────────────────────────────────────────
if $START_SMS; then
  echo ""
  echo "── SMS Gateway ───────────────────────────────────────────────────────────"
  SMS_PORT="${SERVER_PORT:-3001}"
  if lsof -ti :"$SMS_PORT" &>/dev/null; then
    ok "SMS gateway already listening on :$SMS_PORT"
  else
    if [[ -z "${PHONE_IP:-}" ]] || [[ "$PHONE_IP" == "192.168.1.X" ]]; then
      warn "PHONE_IP not set — SMS gateway will start but phone calls will fail until PHONE_IP is set"
    fi
    nohup python3 "$SCRIPT_DIR/src/sms_gateway.py" \
      >> "$LOG_DIR/sms-gateway.log" 2>&1 &
    SMS_PID=$!
    sleep 1
    if kill -0 "$SMS_PID" 2>/dev/null; then
      ok "SMS gateway started (pid $SMS_PID) on :$SMS_PORT"
      echo "$SMS_PID" > "$LOG_DIR/sms-gateway.pid"
    else
      fail "SMS gateway failed to start — check $LOG_DIR/sms-gateway.log"
    fi
  fi
fi

# ─── Service 3: AI handler smoke test ────────────────────────────────────────
if $START_AI; then
  echo ""
  echo "── AI Layer ──────────────────────────────────────────────────────────────"
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    warn "ANTHROPIC_API_KEY not set — AI handler will fail at runtime"
  else
    if python3 -c "import anthropic; anthropic.Anthropic(api_key='$ANTHROPIC_API_KEY')" &>/dev/null 2>&1; then
      ok "AI handler (Claude Haiku) ready"
    else
      warn "AI handler import check failed — verify anthropic package is installed"
    fi
  fi
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo "  Phone Bridge started. Services:"
$START_ASTERISK && echo "  • Asterisk SIP   → docker container 'asterisk-bridge'"
$START_SMS      && echo "  • SMS Gateway    → http://localhost:${SERVER_PORT:-3001}"
$START_AI       && echo "  • AI Layer       → Claude ${AI_MODEL:-haiku-4-5}"
echo ""
echo "  Logs: $LOG_DIR/"
echo ""
if [[ -z "${PHONE_IP:-}" ]] || [[ "$PHONE_IP" == "192.168.1.X" ]]; then
  echo -e "${YELLOW}  Next step:${NC} Set PHONE_IP in .env once the phone is on the network,"
  echo   "  then run: python3 src/connect-test.py"
else
  echo   "  Phone IP set: $PHONE_IP"
  echo   "  Run: python3 src/connect-test.py  to verify connectivity"
fi
echo "─────────────────────────────────────────────────────────────────────────"
