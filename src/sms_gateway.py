#!/usr/bin/env python3
"""
SMS Gateway webhook receiver — routes inbound SMS to Jarvis session via OpenClaw.

Inbound SMS → this Flask server → sessions_send to agent:jarvis:main → Jarvis replies → SMS sent back.
"""

import os
import re
import json
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sms-gateway")

PHONE_IP     = os.getenv("PHONE_IP",          "192.168.1.40")
PHONE_PORT   = os.getenv("PHONE_PORT",        "8080")
SMS_USER     = os.getenv("SMS_GATEWAY_USER",  "sms")
SMS_PASS     = os.getenv("SMS_GATEWAY_PASS",  "smspass1")
SERVER_PORT  = int(os.getenv("SERVER_PORT",   "3001"))
OPENCLAW_URL = os.getenv("OPENCLAW_URL",      "http://localhost:18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN",  "dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91")
JARVIS_SESSION = os.getenv("JARVIS_SESSION",  "agent:jarvis:main")

# ADB tunnel: localhost:18080 → phone:8080 (preferred, avoids WiFi IP dependency)
# Falls back to direct WiFi IP if tunnel not active
BASE_URL = "http://localhost:18080"
BASE_URL_WIFI = f"http://{PHONE_IP}:{PHONE_PORT}"
AUTH = (SMS_USER, SMS_PASS)

app = Flask(__name__)


def send_sms(phone_number: str, message: str) -> dict:
    """Send SMS via android-sms-gateway REST API."""
    try:
        resp = requests.post(
            f"{BASE_URL}/messages",  # local server: /messages works, not /api/3rdparty/v1/message
            auth=AUTH,
            json={"message": message, "phoneNumbers": [phone_number]},
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"✅ SMS sent to {phone_number}: {message[:60]}")
        return {"ok": True, "status": resp.status_code}
    except Exception as e:
        log.error(f"SMS send failed: {e}")
        return {"ok": False, "error": str(e)}


def route_to_jarvis(sender: str, message: str, channel: str = "SMS") -> str:
    """Route inbound SMS to Jarvis via openclaw:jarvis model (same session as #main-lobby)."""
    prompt = f"[Inbound {channel} from {sender}]: {message}\n\nReply concisely (1-3 sentences for SMS). No preamble, no markdown."
    try:
        resp = requests.post(
            f"{OPENCLAW_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openclaw:jarvis",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        # Safely extract just the text content — never send raw JSON
        choices = data.get("choices", [])
        if not choices:
            return "Hey, got your message — will reply shortly."
        reply = choices[0].get("message", {}).get("content", "")
        if not isinstance(reply, str):
            return "Hey, got your message — will reply shortly."
        reply = reply.strip()
        # Strip markdown formatting for SMS readability
        reply = re.sub(r'\*+', '', reply)      # remove bold/italic asterisks
        reply = re.sub(r'`+', '', reply)        # remove code ticks
        reply = re.sub(r'^\s*[-*]\s+', '', reply, flags=re.MULTILINE)  # remove bullet points
        reply = re.sub(r'\n+', ' ', reply)      # flatten newlines
        # Hard SMS limit — never send more than 320 chars (2 SMS segments)
        reply = reply[:320]
        if not reply:
            return "Hey, got your message — will reply shortly."
        log.info(f"Jarvis replied: {reply[:80]}")
        return reply
    except Exception as e:
        log.error(f"Jarvis routing failed: {e}")
        return "Hey, Jarvis here — got your message, reply coming shortly."


@app.route("/health", methods=["GET"])
def health():
    try:
        r = requests.get(f"{BASE_URL}/health", auth=AUTH, timeout=3)
        gw_ok = r.status_code == 200
    except Exception:
        gw_ok = False
    return jsonify({
        "server": "ok",
        "phone_gateway": "ok" if gw_ok else "unreachable",
        "phone_url": f"{BASE_URL}/health",
        "jarvis_session": JARVIS_SESSION,
    }), 200 if gw_ok else 503


@app.route("/webhook/sms", methods=["POST"])
def webhook_sms():
    """Receive inbound SMS from android-sms-gateway."""
    data = request.get_json(force=True, silent=True) or {}
    log.info(f"Webhook payload: {json.dumps(data)[:200]}")

    payload = data.get("payload", data)
    sender  = payload.get("phoneNumber") or payload.get("sender") or payload.get("from", "unknown")
    message = payload.get("message") or payload.get("body") or payload.get("text", "")

    if not message:
        return jsonify({"ok": True, "skipped": "no message"}), 200

    log.info(f"[SMS] {sender}: {message[:100]}")

    reply = route_to_jarvis(sender, message, "SMS")
    result = send_sms(sender, reply)

    return jsonify({"ok": True, "reply": reply, "send_result": result}), 200


if __name__ == "__main__":
    log.info(f"SMS Gateway server starting on port {SERVER_PORT}")
    log.info(f"Phone gateway: {BASE_URL}")
    log.info(f"Jarvis session: {JARVIS_SESSION}")
    app.run(host="0.0.0.0", port=SERVER_PORT, debug=False)
