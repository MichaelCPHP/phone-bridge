# Phone Bridge — Working State

**LAST CONFIRMED WORKING: 2026-03-10 ~13:30 PDT**
**STATUS: TWO-WAY SMS CONFIRMED ✅ | VOICE: IN PROGRESS**

This document is the source of truth for the working configuration.
**Before making changes, read this file. After making changes, update it.**

---

## What Works Right Now

### ✅ SMS — Fully Working (Both Directions)

- **Inbound**: iPhone texts Android number `+17029469526` → `me.capcom.smsgateway` app on Android fires webhook → ADB reverse tunnel (port 3001) → Mac webhook server (`sms_gateway.py`) → OpenClaw AI generates reply → ADB forward tunnel (port 18080) → SMS gateway sends reply → iPhone receives it
- **Outbound**: Mac calls `POST http://localhost:18080/messages` → ADB forward → phone's SMS gateway → sends SMS via SIM
- **AI**: OpenClaw gateway (local, `anthropic/claude-haiku-4-5`), no cloud API keys
- **Round-trip**: ~15-30s (AI response time dominates)

### 🔜 Voice Calls — In Progress
- Asterisk running in Docker, PJSIP endpoint `android-phone` configured
- Linphone installed on Android
- **Needed**: Configure Linphone SIP account (see below)

---

## Architecture (Current)

```
iPhone/Caller
     │ SMS via cellular
     ▼
Android (Motorola Razr 2024 — MODEM ONLY)
├── me.capcom.smsgateway  ← receives SMS, fires webhook
│        │ HTTP POST to 127.0.0.1:3001 (ADB reverse tunnel)
│        ▼
├── ADB USB + WiFi (192.168.1.40:5555)
│        │ port 3001 → Mac:3001  (inbound webhook)
│        │ port 18080 → phone:8080 (outbound API)
│        ▼
Mac (192.168.1.235) — ALL LOGIC RUNS HERE
├── sms_gateway.py    ← Flask webhook server (port 3001)
├── ai_handler.py     ← OpenClaw gateway client
├── agi_server.py     ← FastAGI for voice calls (port 4573)
├── stt_whisper.py    ← faster-whisper STT (local)
└── tts_kokoro.py     ← Kokoro 82M TTS (local)
         │
         ▼
Docker: asterisk-bridge  ← SIP server (port 5060)
         │ FastAGI calls agi://192.168.1.235:4573
         ▼
Linphone on Android  ← SIP client (registers to Asterisk)
```

**The phone is a radio/modem only. No custom code runs on it.**

---

## Critical Configuration — DO NOT CHANGE WITHOUT TESTING

### Android Setup
| Setting | Value | How to verify |
|---------|-------|---------------|
| Default SMS app | `me.capcom.smsgateway` | Settings → Apps → Default apps → SMS app |
| ADB USB debugging | ON | Developer options |
| ADB WiFi | `192.168.1.40:5555` | `adb -s ZY22K45948 shell getprop ro.product.model` |
| SMS gateway running | Local server ON, port 8080 | Open app, check "Local server" toggle |

### ADB
```bash
ADB=~/Library/Android/sdk/platform-tools/adb
SERIAL=ZY22K45948

# Always use -s ZY22K45948 (USB + WiFi both show as connected)
$ADB -s $SERIAL devices   # verify connected

# ADB tunnels (must be re-run after phone reboot or ADB restart):
$ADB -s $SERIAL forward tcp:18080 tcp:8080    # outbound: Mac → phone SMS API
$ADB -s $SERIAL reverse tcp:3001 tcp:3001     # inbound: phone → Mac webhook

# Verify tunnels:
$ADB -s $SERIAL forward --list
$ADB -s $SERIAL reverse --list
```

### SMS Gateway (on Android phone)
- **Package**: `me.capcom.smsgateway`
- **Local URL**: `http://192.168.1.40:8080` (WiFi) OR `http://localhost:18080` (ADB tunnel)
- **Auth**: `sms` / `smspass1`
- **Webhook registered**: `http://127.0.0.1:3001/webhook/sms` (event: sms:received)
- **Send endpoint**: `POST /messages`
- **Health**: `GET /health`

```bash
# Test phone reachability:
curl http://localhost:18080/health -u sms:smspass1

# Send test SMS:
curl -X POST http://localhost:18080/messages -u sms:smspass1 \
  -H "Content-Type: application/json" \
  -d '{"message":"Test","phoneNumbers":["+19495772413"]}'

# Check registered webhooks:
curl http://localhost:18080/webhooks -u sms:smspass1
```

### Mac Services
```bash
# Gateway server (port 3001) — MUST be running:
ps aux | grep sms_gateway  # check PID
tail -f /tmp/sms_gw_fresh.log  # live log

# Restart if down:
cd "/Volumes/T9 Drive 1/projects/phone-bridge"
nohup /opt/homebrew/Caskroom/miniconda/base/bin/python3 src/sms_gateway.py \
  > /tmp/sms_gw_fresh.log 2>&1 &
```

### OpenClaw (AI)
- **URL**: `http://localhost:18789/v1/chat/completions`
- **Token**: `dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91`
- **Model**: `anthropic/claude-haiku-4-5`
- **Timeout**: 90s (slow under heavy load — normal)

```bash
# Test directly:
curl -s http://localhost:18789/v1/chat/completions \
  -H "Authorization: Bearer dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91" \
  -H "Content-Type: application/json" \
  -d '{"model":"anthropic/claude-haiku-4-5","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

### iPhone
- iMessage should be **ON** (messages to Android number go as SMS automatically)
- No special setup needed — just text `+17029469526`

---

## How to Start the Bridge

```bash
cd "/Volumes/T9 Drive 1/projects/phone-bridge"

# 1. Verify ADB
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 devices

# 2. Re-establish tunnels (after any reboot or ADB disconnect)
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 forward tcp:18080 tcp:8080
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 reverse tcp:3001 tcp:3001

# 3. Start gateway server
nohup /opt/homebrew/Caskroom/miniconda/base/bin/python3 src/sms_gateway.py \
  > /tmp/sms_gw_fresh.log 2>&1 &
echo "Gateway PID: $!"

# 4. Verify
curl -s http://localhost:3001/health
```

Or use the phone_control.py helper:
```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python3 src/phone_control.py setup
/opt/homebrew/Caskroom/miniconda/base/bin/python3 src/phone_control.py status
```

---

## What MUST NOT Be Changed Without Testing

1. **Default SMS app on Android** — must stay `me.capcom.smsgateway`. If changed, inbound webhook breaks.
2. **ADB tunnels** — `forward tcp:18080 tcp:8080` and `reverse tcp:3001 tcp:3001` must be active.
3. **sms_gateway.py send URL** — must use `http://localhost:18080` (ADB tunnel). NOT `192.168.1.X`.
4. **OpenClaw token** — don't change without updating `.env` and restarting gateway.
5. **Webhook URL** — registered on phone as `http://127.0.0.1:3001/webhook/sms`. Re-register if webhook vanishes.

---

## Troubleshooting

### No inbound SMS received
```bash
# Check webhook is registered:
curl http://localhost:18080/webhooks -u sms:smspass1

# Check gateway is running:
curl http://localhost:3001/health

# Check ADB reverse tunnel:
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 reverse --list

# Re-register webhook:
/opt/homebrew/Caskroom/miniconda/base/bin/python3 src/phone_control.py webhook
```

### SMS send failing
```bash
# Check ADB forward tunnel:
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 forward --list

# Test send directly:
curl -X POST http://localhost:18080/messages -u sms:smspass1 \
  -H "Content-Type: application/json" \
  -d '{"message":"Test","phoneNumbers":["+19495772413"]}'

# Re-establish tunnels if needed:
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 forward tcp:18080 tcp:8080
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 reverse tcp:3001 tcp:3001
```

### ADB disconnected
```bash
# USB: plug in cable, re-authorize on phone
~/Library/Android/sdk/platform-tools/adb -s ZY22K45948 devices

# WiFi fallback:
~/Library/Android/sdk/platform-tools/adb connect 192.168.1.40:5555
```

### AI not responding
```bash
tail -20 /tmp/sms_gw_fresh.log
# Check for "No response from OpenClaw" or timeout errors
# OpenClaw may be slow under load — 90s timeout is normal
```

---

## Voice Calls Setup (In Progress)

### Asterisk
```bash
# Status:
docker ps --filter name=asterisk

# Check PJSIP endpoints:
docker exec asterisk-bridge asterisk -rx "pjsip show endpoints"
# Should show android-phone (Unavailable until Linphone registers)
```

### Linphone SIP Account (One-time setup on phone)
On the Android, open Linphone:
1. Menu → Assistant → Use SIP account
2. **Username**: `android-phone`
3. **Password**: `phonebridge123`
4. **Domain**: `192.168.1.235`
5. **Transport**: `UDP`
6. Tap Login → wait for green "Registered" indicator

### AGI Server (voice AI)
```bash
# Start AGI server (handles calls):
cd "/Volumes/T9 Drive 1/projects/phone-bridge"
nohup /opt/homebrew/Caskroom/miniconda/base/bin/python3 src/agi_server.py \
  > /tmp/agi_server.log 2>&1 &

# Monitor:
tail -f /tmp/agi_server.log
```

---

## Key Files

| File | Purpose | Change Risk |
|------|---------|-------------|
| `src/sms_gateway.py` | Flask webhook + AI + SMS send | HIGH — test after any change |
| `src/ai_handler.py` | OpenClaw API client | MEDIUM |
| `src/phone_control.py` | ADB control automation | LOW |
| `src/agi_server.py` | Voice call AGI server | MEDIUM |
| `src/stt_whisper.py` | faster-whisper STT | LOW (not used in SMS) |
| `src/tts_kokoro.py` | Kokoro TTS | LOW (not used in SMS) |
| `config/asterisk/` | Asterisk PJSIP config | HIGH — restart Asterisk after |
| `.env` | All secrets/config | HIGH — source before running |

---

## Environment Variables (`.env`)

```bash
ADB_SERIAL=ZY22K45948
OPENCLAW_GATEWAY_URL=http://localhost:18789
OPENCLAW_GATEWAY_TOKEN=dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91
OPENCLAW_MODEL=anthropic/claude-haiku-4-5
PHONE_IP=192.168.1.40
SMS_GATEWAY_USER=sms
SMS_GATEWAY_PASS=smspass1
WHISPER_MODEL=tiny.en
KOKORO_VOICE=af_heart
KOKORO_SPEED=1.0
```

---

## Confirmed E2E Test (2026-03-10 ~13:30 PDT)

```
iPhone (+19495772413) → "This is a test"
  → Android SMS gateway (me.capcom.smsgateway) fires webhook
  → ADB reverse tunnel → Mac:3001
  → sms_gateway.py processes → ai_handler.py calls OpenClaw
  → Reply: "✅ Test received!" (or similar)
  → ADB forward tunnel → phone:8080 → SMS gateway sends
  → iPhone receives reply ✅

Total latency: ~15-30s
```

---

## Next Steps (Future Work — Don't Break SMS While Doing These)

- [ ] **Voice calls**: Configure Linphone SIP → test call → AGI server handles it
- [ ] **Faster AI**: Optimize OpenClaw response time or switch to faster model
- [ ] **Auto-restart**: LaunchAgent/supervisor so bridge survives reboots
- [ ] **iMessage support**: Use `imsg` CLI on Mac for Apple-to-Apple messaging
- [ ] **Outbound calls**: Asterisk originate → Linphone → cellular
- [ ] **Persistent webhook**: Re-register webhook automatically if phone reboots
- [ ] **SMS AI persona**: Update system prompt to say "AI phone assistant", not Builder role
