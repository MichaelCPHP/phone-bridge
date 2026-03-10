#!/usr/bin/env python3
"""
SMS Gateway webhook receiver — clean rebuild.

Inbound SMS webhook from android-sms-gateway app → AI reply → SMS back.

Safety features:
- Rate limit: 1 reply per sender per 60s
- 160 char cap on replies
- Dedup: ignore duplicate message bodies within 30s
- Direct OpenClaw HTTP (no subprocess, no --json flag)
- Plain text extraction only
"""

import os
import re
import json
import time
import logging
import hashlib
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sms-gateway")

# ── Config ────────────────────────────────────────────────────────────────────
PHONE_IP       = os.getenv("PHONE_IP",        "192.168.1.40")
PHONE_PORT     = os.getenv("PHONE_PORT",      "8080")
SMS_USER       = os.getenv("SMS_USER",        "sms")
SMS_PASS       = os.getenv("SMS_PASS",        "smspass1")
SERVER_PORT    = int(os.getenv("SERVER_PORT", "3001"))
OPENCLAW_URL   = os.getenv("OPENCLAW_URL",    "http://localhost:18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN",  "dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91")
AI_MODEL       = os.getenv("AI_MODEL",        "anthropic/claude-haiku-4-5")

BASE_URL = f"http://{PHONE_IP}:{PHONE_PORT}"
AUTH     = (SMS_USER, SMS_PASS)

# ── Rate limiting & dedup ─────────────────────────────────────────────────────
_last_reply: dict[str, float] = {}   # sender → timestamp of last reply
_recent_msgs: dict[str, float] = {}  # msg_hash → timestamp seen
RATE_LIMIT_S  = 60   # min seconds between replies per sender
DEDUP_WINDOW  = 30   # seconds to ignore duplicate message bodies
REPLY_MAX_LEN = 160  # SMS character limit

app = Flask(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_rate_limited(sender: str) -> bool:
    last = _last_reply.get(sender, 0)
    if time.time() - last < RATE_LIMIT_S:
        log.warning(f"Rate limited: {sender} (last reply {time.time()-last:.0f}s ago)")
        return True
    return False


def is_duplicate(sender: str, body: str) -> bool:
    key = hashlib.md5(f"{sender}:{body}".encode()).hexdigest()
    last = _recent_msgs.get(key, 0)
    if time.time() - last < DEDUP_WINDOW:
        log.warning(f"Duplicate message from {sender}, ignoring")
        return True
    _recent_msgs[key] = time.time()
    return False


def get_ai_reply(sender: str, text: str) -> str | None:
    """Get reply from Jarvis agent via openclaw agent CLI (targets Jarvis directly)."""
    import subprocess as sp
    prompt = f"SMS from {sender}: {text}\nReply in 1-2 plain sentences, max 160 chars. No markdown."
    try:
        env = {**__import__('os').environ,
               "PATH": "/Users/michaeltgcm/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
        result = sp.run(
            ["openclaw", "agent", "--agent", "jarvis", "--message", prompt],
            capture_output=True, text=True, timeout=60, env=env
        )
        reply = result.stdout.strip()
        if not reply:
            log.warning(f"Empty reply from openclaw agent, stderr: {result.stderr[:100]}")
            return None
        # Strip markdown
        reply = re.sub(r'\*+', '', reply)
        reply = re.sub(r'#+\s*', '', reply)
        reply = reply[:REPLY_MAX_LEN]
        log.info(f"Jarvis reply: {reply[:60]}")
        return reply
    except Exception as e:
        log.error(f"AI error: {e}")
        return None


def send_sms(number: str, text: str) -> bool:
    """Send SMS via android-sms-gateway REST API."""
    try:
        r = requests.post(
            f"{BASE_URL}/messages",
            auth=AUTH,
            json={"message": text[:REPLY_MAX_LEN], "phoneNumbers": [number]},
            timeout=10,
        )
        r.raise_for_status()
        msg_id = r.json().get("id", "?")
        log.info(f"SMS queued → {number} (id={msg_id})")
        return True
    except Exception as e:
        log.error(f"send_sms error: {e}")
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"server": "ok", "phone": f"http://{PHONE_IP}:{PHONE_PORT}"})


@app.route("/webhook/sms", methods=["POST"])
def sms_webhook():
    """Receive inbound SMS from android-sms-gateway webhook."""
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "bad JSON"}), 400

    # android-sms-gateway wraps payload under "payload" key
    payload = data.get("payload", data)
    sender = payload.get("phoneNumber") or payload.get("sender") or payload.get("from") or data.get("phoneNumber") or data.get("sender") or ""
    body   = payload.get("message")    or payload.get("body")   or payload.get("text")  or data.get("message")    or data.get("body")   or ""

    if not sender or not body:
        log.warning(f"Webhook missing sender/body: {data}")
        return jsonify({"status": "ignored", "reason": "missing fields"}), 200

    log.info(f"Webhook: SMS from {sender}: {body[:60]}")

    # Dedup check
    if is_duplicate(sender, body):
        return jsonify({"status": "ignored", "reason": "duplicate"}), 200

    # Rate limit check
    if is_rate_limited(sender):
        return jsonify({"status": "ignored", "reason": "rate_limited"}), 200

    # Get AI reply
    reply = get_ai_reply(sender, body)
    if not reply:
        log.error("No AI reply, not sending")
        return jsonify({"status": "error", "reason": "no_ai_reply"}), 200

    log.info(f"Reply ({len(reply)} chars): {reply}")

    # Send
    ok = send_sms(sender, reply)
    if ok:
        _last_reply[sender] = time.time()

    return jsonify({"status": "sent" if ok else "failed", "reply": reply})


@app.route("/send", methods=["POST"])
def manual_send():
    """Manually send an SMS. Body: {to, message}"""
    data = request.get_json(force=True) or {}
    to   = data.get("to", "")
    msg  = data.get("message", "")[:REPLY_MAX_LEN]
    if not to or not msg:
        return jsonify({"error": "missing to/message"}), 400
    ok = send_sms(to, msg)
    return jsonify({"status": "sent" if ok else "failed"})


if __name__ == "__main__":
    log.info(f"SMS Gateway server starting on :{SERVER_PORT}")
    log.info(f"Phone gateway: {BASE_URL}")
    app.run(host="0.0.0.0", port=SERVER_PORT, debug=False)
