# Phase 2 — SMS Integration & Testing

**Status:** Ready to begin  
**Blocker:** Waiting for Android phone IP address + APK installation

---

## Prerequisites Checklist

- [ ] Android phone on same WiFi network as Mac
- [ ] android-sms-gateway APK installed on phone (`setup/android-sms-gateway.apk`)
- [ ] Phone IP address noted (Settings → About Phone → Status → IP Address)
- [ ] `.env` file created with:
  ```
  PHONE_IP=192.168.x.x
  PHONE_PORT=8080
  OPENCLAW_GATEWAY_URL=http://localhost:18789
  OPENCLAW_GATEWAY_TOKEN=<from Cursor: openclaw.gatewayToken>
  OPENCLAW_MODEL=openclaw:friday
  KOKORO_VOICE=af_heart
  ```

---

## Phase 2 Tasks

### Task 1: Verify android-sms-gateway API

```bash
# Test SMS gateway on phone
PHONE_IP=192.168.x.x
curl http://$PHONE_IP:8080/api/v1/message/send \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test message from Mac",
    "phoneNumbers": ["+1XXXXXXXXXX"]
  }'
```

Expected: Message sent to phone number.

### Task 2: Wire SMS handler into Flask server

File: `src/sms_gateway.py`

```python
from src.ai_handler import handle_sms

@app.route('/webhook/sms', methods=['POST'])
def sms_webhook():
    """Receive SMS from android-sms-gateway, reply via AI."""
    data = request.json
    phone = data.get('from')  # sender's phone number
    message = data.get('message')
    
    reply = handle_sms(phone, message)
    
    # Send reply back via gateway
    requests.post(
        f"http://{os.getenv('PHONE_IP')}:8080/api/v1/message/send",
        json={"message": reply, "phoneNumbers": [phone]}
    )
    return {"status": "ok"}
```

### Task 3: Start Docker services

```bash
docker-compose up -d
# Starts: Asterisk, optional services, Flask server on port 3001
```

### Task 4: Register webhook with android-sms-gateway app

On the phone, in the android-sms-gateway app:
- Webhook URL: `http://<MAC_IP>:3001/webhook/sms`
- Method: POST
- Enable webhook on new messages

### Task 5: End-to-end test

1. **Send SMS from phone to itself** (loopback test):
   - Type message in any SMS app
   - app forwards to `localhost:3001/webhook/sms`
   - `handle_sms()` generates AI reply via OpenClaw gateway
   - Reply sent back via android-sms-gateway to phone

2. **Verify in logs**:
   ```bash
   tail -f /tmp/phone-bridge.log
   # Should see: [ai-handler] 'incoming message' → 'AI reply'
   ```

---

## Architecture

```
Phone (Android) 
    ↓ SMS incoming
    ↓
android-sms-gateway app
    ↓ POST http://MAC:3001/webhook/sms
    ↓
Flask server (sms_gateway.py)
    ↓
handle_sms(phone, message)
    ├─ stt_voicebox.py (transcribe if voicemail)
    ├─ ai_handler.respond() 
    │  ├─ OpenClaw gateway (Claude Sonnet)
    │  └─ or Ollama (if OPENCLAW_GATEWAY_URL=localhost:11434)
    └─ tts_kokoro.py (optional: speak reply aloud)
    ↓
HTTP POST: android-sms-gateway /send
    ↓
Reply SMS back to phone
```

---

## Testing Sequence

**1. Connectivity test (5 min)**
```bash
curl http://$PHONE_IP:8080/health
# Expected: 200 OK
```

**2. SMS send test (5 min)**
```bash
curl http://$PHONE_IP:8080/api/v1/message/send \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"message":"Test","phoneNumbers":["YOUR_PHONE_NUMBER"]}'
```

**3. AI handler test (5 min)**
```bash
cd /Volumes/T9\ Drive\ 1/projects/phone-bridge
python3 src/ai_handler.py
# Should call OpenClaw gateway and return SMS + voice replies
```

**4. Full loop test (10 min)**
- Start Flask server: `python3 src/sms_gateway.py`
- Set webhook in android-sms-gateway app
- Send SMS from phone
- Verify reply comes back in seconds

---

## Success Criteria

- [ ] SMS received by Flask webhook
- [ ] OpenClaw gateway responds with AI reply
- [ ] Reply SMS sent back to phone
- [ ] End-to-end latency < 5 seconds
- [ ] No Deepgram/ElevenLabs API calls
- [ ] Logs show correct flow: input → AI → output

---

## Fallback Options

If android-sms-gateway doesn't work:
1. Try Termux + SMS API (more complex setup)
2. Use local SMS simulator script for testing
3. Test voice calls first (Asterisk + Linphone) before SMS

---

## Next After SMS Works

**Phase 3: Voice Calls**
- Register Linphone as SIP client on Android
- Asterisk AGI routes calls to ai_handler
- Live STT → AI → TTS pipeline
- Full end-to-end voice test

Estimated time: 1-2 hours

---

**When ready, provide:**
- Phone IP address
- Confirmation APK is installed
- Phone number (for test SMS)

Then we'll start Phase 2.
