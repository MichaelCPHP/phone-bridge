#!/usr/bin/env python3
"""
Phone Bridge — SMS Gateway integration layer.
Wraps android-sms-gateway local server for bidirectional SMS.
Built on foundation from issue #7 (APK + sms-api.py).
"""

import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sms-gateway")

# Config from environment
PHONE_IP   = os.getenv("PHONE_IP", "192.168.1.X")
PHONE_PORT = os.getenv("PHONE_PORT", "8080")
SMS_USER   = os.getenv("SMS_USER", "user")
SMS_PASS   = os.getenv("SMS_PASS", "password")
SERVER_PORT = int(os.getenv("SERVER_PORT", "3001"))

BASE_URL = f"http://{PHONE_IP}:{PHONE_PORT}"
AUTH = (SMS_USER, SMS_PASS)

app = Flask(__name__)


# ─── Outbound SMS ────────────────────────────────────────────────────────────

def send_sms(phone_number: str, message: str) -> dict:
    """Send an SMS via android-sms-gateway."""
    resp = requests.post(
        f"{BASE_URL}/messages",
        auth=AUTH,
        json={"message": message, "phoneNumbers": [phone_number]},
        timeout=10
    )
    resp.raise_for_status()
    result = resp.json()
    log.info(f"SMS sent to {phone_number}: {result}")
    return result


# ─── Inbound SMS webhook ─────────────────────────────────────────────────────

@app.route("/webhook/sms", methods=["POST"])
def sms_webhook():
    """
    Receive inbound SMS from android-sms-gateway webhook.
    Configure webhook in app settings → Webhooks → URL: http://<mac-ip>:3001/webhook/sms
    """
    data = request.json or {}
    phone_number = data.get("phoneNumber", "unknown")
    message      = data.get("message", "")
    received_at  = data.get("receivedAt", datetime.utcnow().isoformat())

    log.info(f"Inbound SMS from {phone_number}: {message[:80]}")

    # Pass to AI handler (pluggable — swap in Claude Haiku when ready)
    reply = handle_inbound_sms(phone_number, message)

    if reply:
        try:
            send_sms(phone_number, reply)
            log.info(f"Auto-replied to {phone_number}")
        except Exception as e:
            log.error(f"Failed to send reply: {e}")

    return jsonify({"status": "ok"})


def handle_inbound_sms(phone_number: str, message: str) -> str | None:
    """AI handler for inbound SMS — uses Claude Haiku via ai_handler (#12)"""
    try:
        from ai_handler import handle_sms
        return handle_sms(phone_number, message)
    except Exception as e:
        log.error(f"AI handler failed: {e}")
        return None


# ─── Health + status ─────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    try:
        resp = requests.get(f"{BASE_URL}/health", auth=AUTH, timeout=5)
        phone_ok = resp.status_code == 200
    except Exception:
        phone_ok = False

    return jsonify({
        "server": "ok",
        "phone_gateway": "ok" if phone_ok else "unreachable",
        "phone_url": BASE_URL
    }), 200 if phone_ok else 503


@app.route("/send", methods=["POST"])
def send_endpoint():
    """Manual send endpoint: POST {"to": "+1...", "message": "..."}"""
    body = request.json or {}
    to  = body.get("to")
    msg = body.get("message")
    if not to or not msg:
        return jsonify({"error": "to and message required"}), 400
    result = send_sms(to, msg)
    return jsonify(result)


if __name__ == "__main__":
    log.info(f"SMS Gateway server starting on port {SERVER_PORT}")
    log.info(f"Phone gateway: {BASE_URL}")
    app.run(host="0.0.0.0", port=SERVER_PORT)
