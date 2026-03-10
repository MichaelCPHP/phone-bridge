# Phone Bridge — Architecture Decision Record

## Decision: Option A (Hosted APIs) for MVP

**Date:** 2026-03-09  
**Decision authority:** Jarvis (delegated by Michael)

---

## Problem
Build an AI phone bridge on an Android device that can:
- Make/receive real phone calls with AI voice
- Send/receive SMS
- Respond in near-real-time (<2s round-trip)

## Why VoiceBox Was Rejected
- CPU-only PyTorch (Mac Mini)
- Benchmark: 104,294ms for a single TTS phrase → completely unusable for real-time
- Model instability (auto-switching 0.6B ↔ 1.7B during test)

---

## Architecture

```
[Android Phone]
    ↕ (SIP via Linphone + Asterisk)         ← voice calls
    ↕ (android-sms-gateway REST API)         ← SMS

[Mac Bridge Server — Node.js]
    ↓ inbound audio stream
    
[Deepgram Nova-2 API]          ← STT, streaming, ~300-500ms
    ↓ transcript text
    
[Claude Haiku API]             ← AI response, ~500ms
    ↓ response text
    
[ElevenLabs / OpenAI TTS API]  ← TTS, ~200-400ms
    ↓ audio stream
    
[Back to Android via SIP/Linphone]
```

### Estimated Round-Trip
| Step | Latency |
|------|---------|
| STT (Deepgram streaming) | 300–500ms |
| AI (Claude Haiku) | 300–600ms |
| TTS (ElevenLabs/OpenAI) | 200–400ms |
| **Total** | **~800ms–1.5s** ✅ |

---

## Components

### Phase 1 — SMS (implement first, faster)
- **android-sms-gateway**: Docker + APK on Android
- REST API for send/receive SMS
- Mac bridge subscribes to webhook for inbound SMS
- AI processes and auto-replies

### Phase 2 — Voice Calls
- **Asterisk** on Mac as SIP server
- **Linphone** on Android as SIP client
- Inbound call → Asterisk → Mac bridge → Deepgram STT → Claude → ElevenLabs TTS → Asterisk → Linphone

### Android Requirements
- Android 8+ (for android-sms-gateway compatibility)
- USB or WiFi connection to Mac
- Linphone app (free, SIP client)
- android-sms-gateway APK

---

## API Keys Needed
- [ ] Deepgram API key (STT)
- [ ] ElevenLabs API key (TTS) — OR OpenAI TTS (already have key?)
- [ ] Twilio or VoIP.ms account for SIP DID (real phone number) — optional for WiFi-only

---

## Long-Term (Option C)
- Keep VoiceBox for non-realtime: SMS readback, notifications, agent voice logging
- Replace live call path with hosted APIs (this architecture)

---

## Implementation Order
1. `[IMPL-SMS]` android-sms-gateway setup + Mac bridge API
2. `[IMPL-VOICE]` Asterisk + Linphone SIP stack
3. `[IMPL-AI]` Claude Haiku integration with Deepgram + ElevenLabs
4. `[E2E]` Full call flow test: inbound call → AI response → caller hears reply
