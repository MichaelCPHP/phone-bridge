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
