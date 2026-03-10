# Phone Bridge — AI Caller/SMS System

## Goal
Connect an Android phone as a bridge server to enable:
- Make phone calls (outbound)
- Receive phone calls (inbound)
- Send SMS
- Receive SMS
- AI voice caller with ultra-low latency responses

## Architecture (planned)
- **Android Bridge**: Phone acts as gateway (ADB or dedicated app)
- **Voice Engine**: VoiceBox (Docker, already running)
- **AI Layer**: Claude/LLM for conversation logic
- **SMS Layer**: TBD (Android Messages API, ADB, or bridge app)

## Status
- [ ] Research phase — Android bridge options
- [ ] VoiceBox integration assessment
- [ ] Architecture decision
- [ ] Implementation

## Research Needed
1. Android bridge options (ADB, Termux, dedicated bridge app)
2. VoiceBox Docker config & TTS/STT latency
3. Call routing (SIP? direct ADB? Android auto-answer?)
4. SMS send/receive API
5. Latency targets for real-time AI conversation
