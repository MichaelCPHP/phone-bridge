# Phone Bridge — Research Report

**Date:** 2026-03-09  
**Author:** Friday  
**Status:** Complete — ready for architecture decision

---

## 1. Android Bridge Options

### Option A: ADB (Android Debug Bridge)
**How it works:** USB or WiFi connection from Mac → Android phone. ADB can run shell commands on the phone, broadcast intents to trigger calls/SMS.

**Pros:**
- No app install required (debug mode only)
- Full shell access via `adb shell`
- `adb shell am start -a android.intent.action.CALL tel:+1XXXXXXXXXX` — triggers outbound call
- `adb shell service call isms ...` — can send SMS (complex, varies by Android version)
- Works over USB or WiFi (`adb tcpip 5555`)

**Cons:**
- Requires USB debug mode enabled (security risk)
- WiFi ADB can drop on phone sleep
- SMS via ADB is fragile/version-specific
- No audio routing — can't capture/inject call audio via ADB
- **Cannot intercept or route audio streams** — calls go through phone's native audio stack only

**Verdict:** ❌ Not suitable for AI voice calling (no audio access). Usable only for triggering dumb calls or SMS.

---

### Option B: Termux + Python Server
**How it works:** Install Termux on Android, run a Python/Node server on the phone, expose API over local WiFi. Use `termux-telephony-call` and `termux-sms-send` (Termux:API addon).

**Pros:**
- Full API control over calls and SMS
- `termux-telephony-call <number>` — outbound call
- `termux-sms-send -n <number> <message>` — SMS
- `termux-sms-list` — read incoming SMS
- Can run a local HTTP server (Python/Node) exposing REST endpoints
- Works over WiFi — no USB needed once set up
- Can poll for incoming SMS and forward to Mac

**Cons:**
- Requires Termux + Termux:API install on phone
- Still no audio injection for AI voice — calls use phone speaker/mic natively
- Outbound call places call but AI can't speak through it (unless using SIP/VOIP trick)
- Battery drain from persistent server
- Setup required per-device

**Verdict:** ✅ **Best option for SMS send/receive.** ⚠️ Limited for AI voice (can dial but not inject AI audio).

---

### Option C: Dedicated Bridge App (e.g., Android SMS Gateway, SMSGateway.me)
**How it works:** Install a purpose-built SMS gateway app on Android. Exposes HTTP REST API for send/receive SMS. Some support webhooks for inbound.

**Best options:**
- **SMSGateway.me** (open source, self-hostable) — REST API, webhooks, multi-device
- **Android SMS Gateway** (GitHub: capcom6/android-sms-gateway) — Docker control plane + Android app
- **Sms4Me / SMS Gateway Pro** — commercial options

**Pros:**
- Purpose-built — reliable, maintained
- REST API: `POST /message` to send, webhooks for receive
- Some support multiple SIM cards
- No ADB/debug mode needed
- Background service (survives sleep with wake lock)

**Cons:**
- SMS only — no call control
- Requires app install + account/API key setup

**Verdict:** ✅ **Best option for SMS.** Use `android-sms-gateway` (self-hosted, open source).

---

### Option D: SIP/VoIP (Linphone, Zoiper on Android)
**How it works:** Install SIP client on Android (Linphone, Zoiper). Register with a SIP server (Asterisk, FreeSWITCH) running on Mac. AI speaks through SIP audio channel.

**Pros:**
- Full two-way audio control — AI can speak and listen through the call
- SIP is the standard for VoIP — well-documented
- Asterisk/FreeSWITCH can inject TTS audio into calls
- Works for both inbound and outbound calls
- Can bridge SIP ↔ PSTN (real phone calls) via SIP provider (Twilio, VoIP.ms)

**Cons:**
- Complex setup (Asterisk + SIP provider + Android SIP client)
- Latency: SIP audio encoding adds ~20-50ms on top of TTS
- Real phone number requires SIP provider (not free)
- Android SIP client must stay registered

**Verdict:** ✅ **Best option for AI voice calls.** SIP is the correct architecture for AI call injection.

---

## 2. VoiceBox Latency Assessment

**Current state:** VoiceBox running at `localhost:17493`, CPU-only PyTorch, 0.6B model.

**Test result:** TTS generation for a short phrase (8 words) exceeded 60 seconds on CPU. **Not viable for real-time conversation.**

**Why:** The 0.6B model on CPU (no GPU) produces audio at approximately 0.05-0.1x real-time — meaning a 5-second audio clip takes 50-100 seconds to generate.

**Real-time requirement:** For natural AI conversation, TTS latency must be <500ms (ideally <200ms) to maintain conversational flow.

### Options to fix VoiceBox latency:

| Option | Latency | Effort | Cost |
|--------|---------|--------|------|
| GPU acceleration (add GPU to Mac) | ~100-300ms | High | High |
| Switch to cloud TTS (ElevenLabs, Deepgram) | ~200-500ms | Low | $/mo |
| Switch to faster local TTS (Kokoro, Piper) | ~100-400ms | Medium | Free |
| Use VoiceBox via streaming (chunk-based) | ~300-600ms first chunk | Medium | Free |
| Combine: Piper TTS + VoiceBox voices | ~200-400ms | Medium | Free |

**Recommendation:** Replace VoiceBox TTS with **Kokoro** or **Piper TTS** (both run at real-time on CPU M-series Mac). VoiceBox's voice profiles can potentially be exported and converted.

---

## 3. Call Routing Architecture

### Recommended: SIP Stack

```
Phone (SIP client: Linphone)
    ↓ registers with ↓
Asterisk (Mac, localhost)
    ↓ routes to ↓
AI Call Handler (Python/Node)
    ↓ uses ↓
STT (Whisper) + LLM (Claude) + TTS (Piper/Kokoro)
    ↓ injects audio back via ↓
Asterisk AGI/ARI
    ↓ delivers to ↓
Phone speaker (caller hears AI voice)
```

**Inbound calls:** Phone receives → SIP registers → Asterisk picks up → AI handler takes over  
**Outbound calls:** AI system dials via Asterisk → SIP → Phone places real call

**SIP providers for real PSTN calls:**
- **Twilio** — $15/mo, reliable, easy API
- **VoIP.ms** — ~$0.01/min, Canadian, privacy-friendly
- **Telnyx** — competitive rates, good API

---

## 4. SMS Architecture

### Recommended: android-sms-gateway (self-hosted)

**Repo:** `github.com/capcom6/android-sms-gateway`

**Architecture:**
```
Mac (control plane, Docker) ←→ Android App (sms-gateway APK)
         ↓
REST API: POST /api/3rdparty/v1/message
         {
           "message": "Hello",
           "phoneNumbers": ["+1XXXXXXXXXX"]
         }
```

**Inbound SMS:** App polls for new messages, pushes to Mac via webhook or polling endpoint.

**Setup steps:**
1. `docker run -p 3000:3000 capcom6/android-sms-gateway` on Mac
2. Install APK on Android phone
3. Register phone with local server
4. REST API ready at `localhost:3000`

---

## 5. Architecture Decision

### Recommended Stack

| Component | Tool | Status |
|-----------|------|--------|
| SMS send/receive | android-sms-gateway (Docker) + Android APK | To implement |
| AI voice calls | Asterisk + Linphone SIP + Piper TTS + Whisper STT | To implement |
| TTS engine | Replace VoiceBox with Piper TTS | Action needed |
| STT engine | OpenAI Whisper (already available locally) | Available |
| AI brain | Claude via API | Available |
| Orchestration | Friday (OpenClaw) | Available |

### Phase 1 (SMS — simpler, faster):
1. Install android-sms-gateway Docker + Android APK
2. Build SMS handler: inbound webhook → Claude → outbound reply
3. Test end-to-end SMS conversation

### Phase 2 (Voice — more complex):
1. Install Asterisk
2. Set up Linphone on Android as SIP client
3. Install Piper TTS + Whisper STT
4. Build AI call handler with Asterisk AGI
5. Test inbound call → AI answers → conversation

---

## Open Questions for @michael

1. **Real phone calls or VoIP only?** Real calls need SIP provider (Twilio/VoIP.ms). VoIP only = WiFi/data only.
2. **Which Android phone?** Model matters for ADB/Termux compatibility.
3. **Start with SMS or voice?** SMS is simpler — recommend Phase 1 first.
4. **VoiceBox voices:** Should we try to preserve Friday/claude-code voice profiles, or start fresh with Piper?
5. **Budget for SIP provider?** ~$10-15/mo for Twilio or VoIP.ms.

---

*Report compiled by Friday. Ready for architecture decision.*
