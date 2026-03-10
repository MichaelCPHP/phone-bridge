# Phone Bridge Changelog

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
