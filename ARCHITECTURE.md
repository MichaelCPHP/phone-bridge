# Phone Bridge — Architecture Decision Record

**Date:** 2026-03-09  
**Decision authority:** Jarvis (acting orchestrator)  
**Status:** APPROVED — moving to implementation

---

## Decision Summary

VoiceBox (CPU-only Qwen3-TTS) is **disqualified** for real-time calls.  
Benchmark: 104s to generate "Hello." on CPU — unusable for live conversation.

**Chosen stack: Hosted APIs for MVP (Option A)**

---

## Architecture

```
Android Phone
    │
    ├── SMS: android-sms-gateway app
    │       ↕ HTTP REST (send) / Webhook (receive)
    │
    └── Calls: Linphone SIP client
            ↕ SIP/RTP
            Asterisk SIP server (Docker, Mac)
                │
                ├── Inbound audio stream
                │       ↓
                │   Deepgram Nova-2 (WebSocket streaming STT)
                │       ↓ transcript
                │   Claude Haiku (AI response, <300ms)
                │       ↓ response text
                │   ElevenLabs Streaming TTS (<400ms first chunk)
                │       ↓ audio
                └── Back to caller via SIP/RTP

Mac Server (Node.js bridge)
    ├── /webhook/sms — receives incoming SMS from android-sms-gateway
    ├── /send/sms    — sends SMS via android-sms-gateway REST API
    ├── /call/outbound — triggers outbound call via Asterisk
    └── /call/inbound  — handles inbound call AI pipeline
```

---

## Component Decisions

### Android Bridge
- **SMS:** [android-sms-gateway](https://github.com/capcom6/android-sms-gateway) — lightweight app, REST API + webhooks
- **Calls:** Linphone SIP client on Android + local Asterisk (Docker) on Mac
- **Why not ADB:** Requires USB connection or ADB over WiFi (unreliable)
- **Why not Termux:** More setup complexity, no advantage for this stack

### STT
- **Deepgram Nova-2** — streaming WebSocket, ~300-500ms, phone-quality audio model
- ~~VoiceBox /transcribe~~ — disqualified (CPU too slow)

### TTS
- **ElevenLabs Streaming TTS** — <400ms first chunk, natural voice
- Fallback: OpenAI TTS API
- ~~VoiceBox /generate~~ — disqualified (104s on CPU)

### AI
- **Claude Haiku** (claude-haiku-4) — fastest Claude model, ~200-300ms
- System prompt: Jarvis persona — professional, concise, helpful

### Server
- **Node.js** — async I/O, good WebSocket/stream support
- Runs on Mac, bridges all components

---

## Latency Budget (Target: <2s total)

| Component | Target |
|-----------|--------|
| STT (Deepgram) | 300-500ms |
| AI (Claude Haiku) | 200-300ms |
| TTS first chunk (ElevenLabs) | 300-400ms |
| Network/routing overhead | 100-200ms |
| **Total** | **900ms–1.4s** ✅ |

---

## Issues / Task Assignments

| # | Title | Assignee |
|---|-------|----------|
| #8 | SMS Gateway integration | @friday |
| #9 | SIP call routing (Linphone + Asterisk) | @friday |
| #10 | Deepgram STT pipeline | @friday |
| #11 | ElevenLabs TTS pipeline | @friday |
| #12 | Claude Haiku AI conversation layer | @friday |
| #13 | E2E test suite | @friday |

---

## VoiceBox — Future Use

VoiceBox remains running and is suitable for:
- Non-realtime voice generation (notifications, summaries)
- SMS readback (read incoming SMS aloud)
- Pre-generated audio responses

Not suitable for: live call real-time conversation.
