# Phone Bridge Changelog

## 2026-03-10 — iMessage group chat reply via chat_id

**Author:** Friday

- send_imessage_to_chat(chat_id): replies into existing thread via --chat-id
- handle_imsg_message: always use chat_id when present (1:1 and group)
- Validated: imsg send --chat-id works for both 1:1 and group threads
- Research confirmed: gateway cannot do group MMS; imsg --chat-id is correct path


## 2026-03-10 — Group MMS reply to all participants

**Author:** Friday

- mac_bridge.py: get all group participants from mms/{id}/addr
- Reply sent to all participants via gateway multi-recipient API
- Excludes own Android number from recipient list


## 2026-03-10 — MMS/group text support + fix openclaw:jarvis routing

**Author:** Friday

- mac_bridge.py: added MMS poll (content://mms) for group text messages
- Group text body from content://mms/{id}/part, sender from type=137 addr
- Reverted model to anthropic/claude-haiku-4-5 (openclaw:jarvis pointed to wrong session)


## 2026-03-10 — Route SMS exclusively to Jarvis session

**Author:** Friday

- mac_bridge.py: model changed to openclaw:jarvis (targets agent:jarvis:main only)
- No more Builder/Friday agents intercepting SMS replies
- Verified: openclaw:jarvis responds correctly


## 2026-03-10 — Fix AI persona in mac_bridge.py

**Author:** Friday

- Stronger system prompt: plain text, 160 chars, no role leakage


## 2026-03-10 — sms_gateway.py clean rebuild

**Author:** Friday

- No subprocess, no --json flag — direct HTTP to OpenClaw
- Rate limit: 1 reply per sender per 60s
- Dedup: ignore duplicate bodies within 30s  
- 160 char cap on all replies
- Markdown stripped from AI output
- Tested: simulated webhook → AI reply → SMS sent ✅


## 2026-03-10 — Fix SMS spam bug

**Author:** Jarvis  
**Type:** Bugfix

### Changes
- sms_gateway.py: safely extract only text content from OpenClaw response (prevent raw JSON being sent as SMS)
- sms_gateway.py: hard 320-char limit on all SMS replies
- run.sh: replaced dangerous UI-tap send method with android-sms-gateway REST API
- run.sh: switched model to openclaw:jarvis with explicit no-markdown instruction


## 2026-03-10 — Route AI through Jarvis session

**Author:** Jarvis  
**Type:** Feature

### Changes
- `src/ai_handler.py`: switched AI model from `anthropic/claude-sonnet-4-5` to `openclaw:jarvis`
- All inbound SMS and voice calls now route through Jarvis's OpenClaw session
- Confirmed working: test SMS reply came from Jarvis persona

## 2026-03-09 — Phase 1 SMS Setup (Issue #7)

**Author:** Friday  
**Type:** Feature

### Changes
- Downloaded android-sms-gateway APK v1.54.0 to `setup/android-sms-gateway.apk`
- Created `setup/sms-api.py` — Python wrapper for local server API (send/list/health)
- Created `setup/SETUP.md` — step-by-step installation guide

### Architecture Clarification
android-sms-gateway runs ON the Android phone (local HTTP server mode).
No Docker required. Phone serves REST API at http://<phone-ip>:8080.

### Next Steps
1. @michael installs APK on Android phone
2. Configure app: enable local server, set user/pass, note phone IP
3. Run: `PHONE_IP=<ip> python3 setup/sms-api.py` to verify
4. Test SMS send via API

---

## 2026-03-09 — Issue #8: SMS Gateway integration layer

**Author:** Friday  
**Type:** Feature

### Changes
- `src/sms_gateway.py` — Flask server with:
  - `POST /send` — outbound SMS via android-sms-gateway
  - `POST /webhook/sms` — inbound SMS receiver (configure as webhook in app)
  - `GET /health` — checks phone gateway reachability
  - AI handler stub (pluggable for Claude Haiku in #12)
- `requirements.txt` — flask, requests

### Next
- @michael installs APK, configures webhook URL to `http://<mac-ip>:3001/webhook/sms`
- Run: `pip install -r requirements.txt && PHONE_IP=<ip> python3 src/sms_gateway.py`

---

## 2026-03-09 — Issue #9: Asterisk SIP server setup

**Author:** Friday  
**Type:** Feature

### Changes
- `config/asterisk/sip.conf` — SIP peer: android-phone (user: android-phone, pass: phonebridge123)
- `config/asterisk/extensions.conf` — inbound → AGI handler, outbound → SIP dial
- `config/asterisk/manager.conf` — AMI enabled on 127.0.0.1:5038
- `config/asterisk/docker-run.sh` — one-command Asterisk Docker startup

### Next
- Run `config/asterisk/docker-run.sh`
- Install Linphone on Android, register with Mac's local IP, user: android-phone

---

## 2026-03-09 — Issue #10: Deepgram STT pipeline

**Author:** Friday  
**Type:** Feature

### Changes
- `src/stt_deepgram.py`:
  - `transcribe_audio_file()` — batch transcription of WAV/PCM files
  - `transcribe_stream()` — async streaming STT for live call audio
  - `test_connection()` — validates DEEPGRAM_API_KEY
- Config: nova-2 model, 8kHz (Asterisk format), 300ms endpointing

### Next
- Set `DEEPGRAM_API_KEY` env var and run to validate

---

## 2026-03-09 — Issue #11: ElevenLabs TTS pipeline

**Author:** Friday  
**Type:** Feature

### Changes
- `src/tts_elevenlabs.py`:
  - `synthesize()` — text → MP3 via ElevenLabs turbo model (~200-400ms)
  - `synthesize_for_asterisk()` — converts to 8kHz μ-law WAV via ffmpeg
  - `list_voices()` — enumerate available voices
  - `test_connection()` — validates ELEVENLABS_API_KEY
- Model: `eleven_turbo_v2` (lowest latency)

### Next
- Set `ELEVENLABS_API_KEY` env var, pick voice ID, run test

---

## 2026-03-09 — Issue #12: Claude Haiku AI layer

**Author:** Friday  
**Type:** Feature

### Changes
- `src/ai_handler.py` (199 lines):
  - `respond()` — Claude Haiku call with SMS/voice context
  - `handle_sms()` — SMS conversation handler with per-number history
  - `handle_call_turn()` — voice conversation handler with per-call history
  - `run_agi()` — Asterisk AGI script (record → STT → AI → TTS → speak loop)
- `src/sms_gateway.py` updated — AI stub replaced with real `ai_handler.handle_sms()` call
- Model: `claude-haiku-4-5`, 256 max tokens (low latency)

### Integration
- SMS path: webhook → handle_sms() → Claude Haiku → send reply
- Voice path: Asterisk AGI → record → Deepgram STT → handle_call_turn() → ElevenLabs TTS → playback

---
