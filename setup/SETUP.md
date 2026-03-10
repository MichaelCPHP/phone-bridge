# android-sms-gateway Setup Guide

## Overview
android-sms-gateway runs a local HTTP server ON the Android phone.
No Docker required — the phone IS the server.

## Step 1: Install APK on Android Phone
1. Transfer `android-sms-gateway.apk` to your Android phone
   - Via USB: `adb push android-sms-gateway.apk /sdcard/`
   - Via WiFi: share file via AirDrop or Google Drive
2. On phone: Settings → Security → Allow unknown sources
3. Open APK to install

## Step 2: Configure the App
1. Open android-sms-gateway on phone
2. Enable "Local Server" mode
3. Set a username and password
4. Note your phone's local IP (shown in app or Settings → WiFi)
5. Default port: 8080

## Step 3: Test Connection from Mac
```bash
export PHONE_IP=192.168.1.XXX  # Your phone's WiFi IP
export SMS_USER=user
export SMS_PASS=password

# Health check
python3 sms-api.py

# Send SMS
python3 sms-api.py send +1XXXXXXXXXX "Hello from phone-bridge"

# List received
python3 sms-api.py list
```

## Step 4: AI Integration (next phase)
- Inbound SMS → webhook fires → Claude Haiku → reply sent
- Outbound: `POST /api/3rdparty/v1/message` from AI handler

## Requirements
- Android phone on same WiFi as Mac
- android-sms-gateway APK (v1.54.0) installed
- SMS permissions granted to app

## Troubleshooting
- Phone not reachable: check WiFi, phone IP, port 8080
- SMS not sending: check SIM card, SMS permissions in Android settings
- Auth error: double-check user/pass set in app settings
