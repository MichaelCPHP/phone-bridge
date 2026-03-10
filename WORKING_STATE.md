# Phone Bridge — Working State (2026-03-10)

**STATUS: TWO-WAY SMS CONFIRMED WORKING**

This document captures the exact configuration that works. Do not change these
components without testing against this baseline first.

---

## What Works Right Now

- **Inbound SMS**: iPhone → +1-702-946-9526 (Android) → Google Messages writes to DB → Mac reads via ADB → Claude (OpenClaw) generates reply → Mac sends reply via Wi-Fi API → iPhone receives reply
- **Outbound SMS**: Mac → SMS Gateway Wi-Fi API → Android sends → iPhone receives
- **AI**: Claude Haiku via OpenClaw gateway (local, no cloud keys needed)
- **Round-trip time**: ~16 seconds (ADB poll 3s + OpenClaw ~13s)

**Confirmed working at 13:25 PDT, 2026-03-10.**

---

## Architecture

```
Mac (all logic)
├── run.sh              — single launch script
├── sms_adb_monitor.py  — polls content://sms every 3s (inbound)
├── ai_handler.py       — calls OpenClaw gateway → Claude Haiku
└── SMS send            — POST http://192.168.1.40:8080/messages

Android (modem/SIM only)
├── Google Messages     — DEFAULT SMS app (MUST stay as default)
│                         writes inbound SMS to content://sms DB
├── SMS Gateway app     — provides Wi-Fi HTTP API for outbound sends
└── ADB (USB)          — Mac reads DB, sends commands
```

**The phone is a radio/modem. It runs no custom logic. All AI runs on the Mac.**

---

## Critical Configuration

### Android SMS app
Google Messages MUST be the default SMS app. It writes every received SMS to
the `content://sms` content provider, which the Mac reads via ADB.

```bash
# Verify:
adb -s ZY22K45948 shell cmd role get-role-holders android.app.role.SMS
# Must return: com.google.android.apps.messaging

# Fix if wrong:
adb -s ZY22K45948 shell cmd role set-bypassing-role-qualification true
adb -s ZY22K45948 shell cmd role add-role-holder android.app.role.SMS \
    com.google.android.apps.messaging 0
```

### ADB Device
- **USB serial**: `ZY22K45948` (Motorola Razr 2024)
- **WiFi fallback**: `192.168.1.40:5555`
- Always use `-s ZY22K45948` (two ADB devices connected: USB + WiFi)

### SMS Gateway (outbound)
- **URL**: `http://192.168.1.40:8080`
- **Auth**: `sms` / `smspass1`
- **Send endpoint**: `POST /messages`
- **Status check**: `GET /messages/{id}`
- **Health**: `GET /`

### OpenClaw Gateway (AI)
- **URL**: `http://localhost:18789`
- **Token**: `dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91`
- **Model**: `anthropic/claude-haiku-4-5`
- **Endpoint**: `POST /v1/chat/completions`

### iPhone settings (caller side)
- iMessage must be **OFF** for messages to send as SMS (green bubble)
- Settings → Messages → iMessage → toggle OFF
- Or: long-press Send → "Send as Text Message"

---

## How to Start

```bash
cd /tmp/phone-bridge-work
bash run.sh
```

That's it. The script:
1. Verifies ADB connection
2. Ensures Google Messages is default
3. Checks OpenClaw gateway
4. Kills any stale processes
5. Starts `sms_adb_monitor.py` (polls every 3s)
6. Tails the live log

Press `Ctrl+C` to stop cleanly.

### Logs
```bash
tail -f /tmp/pb-monitor.log   # live inbound/outbound activity
tail -f /tmp/pb-server.log    # SMS server log (if running)
```

---

## Key Files

| File | Purpose |
|------|---------|
| `run.sh` | Single launch script — start here |
| `src/sms_adb_monitor.py` | Core loop: poll DB → AI → send reply |
| `src/ai_handler.py` | OpenClaw gateway client |
| `src/sms_gateway.py` | Flask webhook server (inbound from APK, optional) |
| `src/sms_adb.py` | ADB SMS send utility |
| `src/tts_kokoro.py` | Kokoro TTS (for future voice features) |
| `src/stt_voicebox.py` | Whisper-cpp STT (for future voice features) |
| `audio/tts-cache/` | Pre-generated TTS WAVs |
| `android/` | Custom APK source (not used for SMS, kept for reference) |

---

## What NOT to Change

1. **Do not change the default SMS app** away from Google Messages
2. **Do not uninstall SMS Gateway app** from the Android
3. **Do not change the ADB serial** — always use `ZY22K45948`
4. **Do not modify `sms_adb_monitor.py` poll logic** without testing
5. **Do not change OpenClaw token/URL** without updating `.env`

---

## Known Limitations (Future Work)

- First cold-start OpenClaw request may timeout (~17s) — subsequent requests are ~13s
- No iMessage support yet (requires Mac + `imsg` CLI with Full Disk Access)
- No call handling yet (requires Asterisk or similar)
- No LaunchAgent yet — must restart bridge manually after reboot
- AI persona says "Builder / SAPC board" — needs custom system prompt for phone assistant role

---

## Future Improvements (Do These Carefully)

- [ ] Fix AI persona system prompt (say "AI Assistant", not "Builder")
- [ ] Add LaunchAgent so bridge auto-starts on boot
- [ ] Add iMessage support via `imsg` CLI (Full Disk Access already granted)
- [ ] Add call handling via ADB (detect ring, answer, pipe audio)
- [ ] Add voice: whisper-cpp STT + Kokoro TTS pipeline
- [ ] Reduce OpenClaw cold-start timeout
- [ ] Make `.env` file for all config (instead of hardcoded defaults)

---

## Tested Flow (2026-03-10 13:24–13:25 PDT)

```
13:24:31  Michael → +1-702-946-9526: "This is a test when I toggled on iMessage on my iPhone..."
13:24:34  ADB monitor caught it (3s poll delay)
13:24:51  OpenClaw replied (cold start timeout, fallback sent)
13:24:52  Reply delivered to Michael's iPhone ✅

13:25:16  Michael → +1-702-946-9526: "What is your name in session ID and role?"
13:25:18  ADB monitor caught it
13:25:35  OpenClaw: "I'm Builder — a coding sub-agent on the SAPC board."
13:25:38  Reply delivered to Michael's iPhone ✅
```
