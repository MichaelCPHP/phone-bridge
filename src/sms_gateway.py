#!/usr/bin/env python3
"""
SMS Gateway webhook receiver — routes inbound SMS to Jarvis session via OpenClaw.

Inbound SMS → this Flask server → sessions_send to agent:jarvis:main → Jarvis replies → SMS sent back.
"""

import os
import json
import logging
import requests
import subprocess
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

BASE_URL = f"http://{PHONE_IP}:{PHONE_PORT}"
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
    """Route message to Jarvis agent via `openclaw agent` CLI and return reply."""
    prompt = f"[Inbound {channel} from {sender}]: {message}\n\nReply concisely (1-3 sentences for SMS). Do not include any preamble."
    try:
        result = subprocess.run(
            ["openclaw", "agent", "--agent", "jarvis", "--message", prompt, "--json"],
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "PATH": "/Users/michaeltgcm/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout.strip())
                reply = data.get("reply") or data.get("response") or data.get("content") or data.get("message", "")
                if reply:
                    log.info(f"Jarvis replied via CLI: {reply[:80]}")
                    return reply
            except json.JSONDecodeError:
                # Plain text output
                reply = result.stdout.strip()
                if reply and len(reply) > 5:
                    log.info(f"Jarvis replied (plain): {reply[:80]}")
                    return reply
        if result.stderr:
            log.warning(f"openclaw agent stderr: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log.error("openclaw agent timed out")
    except Exception as e:
        log.error(f"openclaw agent CLI failed: {e}")

    return "Hey! I'm Jarvis, Michael's AI phone assistant. Got your message!"


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
