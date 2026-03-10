#!/usr/bin/env python3
"""
Phone Bridge — SMS API wrapper for android-sms-gateway (local server mode).

The android-sms-gateway app runs a local HTTP server on the Android phone.
Default: http://<phone-ip>:8080 (set in app settings)
Auth: Basic auth (user/pass set in app)
"""

import requests
import os

PHONE_IP = os.getenv("PHONE_IP", "192.168.1.X")  # Set to Android phone's local IP
PHONE_PORT = os.getenv("PHONE_PORT", "8080")
SMS_USER = os.getenv("SMS_USER", "user")           # From app settings
SMS_PASS = os.getenv("SMS_PASS", "password")       # From app settings

BASE_URL = f"http://{PHONE_IP}:{PHONE_PORT}"
AUTH = (SMS_USER, SMS_PASS)


def send_sms(phone_number: str, message: str) -> dict:
    """Send an SMS via android-sms-gateway local server."""
    resp = requests.post(
        f"{BASE_URL}/api/3rdparty/v1/message",
        auth=AUTH,
        json={"message": message, "phoneNumbers": [phone_number]},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def list_messages(state: str = "received") -> list:
    """List received SMS messages."""
    resp = requests.get(
        f"{BASE_URL}/api/3rdparty/v1/message",
        auth=AUTH,
        params={"state": state},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def health_check() -> bool:
    """Check if the phone server is reachable."""
    try:
        resp = requests.get(f"{BASE_URL}/api/3rdparty/v1/health", auth=AUTH, timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    if not health_check():
        print(f"ERROR: Phone server not reachable at {BASE_URL}")
        print("Make sure:")
        print("  1. android-sms-gateway APK is installed on Android phone")
        print("  2. App is running with local server enabled")
        print(f"  3. Phone IP is correct (set PHONE_IP env var, current: {PHONE_IP})")
        sys.exit(1)
    
    print(f"✅ Phone server reachable at {BASE_URL}")
    
    if len(sys.argv) >= 3 and sys.argv[1] == "send":
        number = sys.argv[2]
        msg = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "Test from phone-bridge"
        result = send_sms(number, msg)
        print(f"SMS sent: {result}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "list":
        msgs = list_messages()
        print(f"Received messages ({len(msgs)}):")
        for m in msgs:
            print(f"  From: {m.get('phoneNumber')} | {m.get('message','')[:60]}")
    else:
        print("Usage: python3 sms-api.py send +1XXXXXXXXXX 'your message'")
        print("       python3 sms-api.py list")
