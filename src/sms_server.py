#!/usr/bin/env python3
"""
Phone Bridge — SMS AI Server (Phase 2)

Polls android-sms-gateway for inbound messages every 2s.
When a new SMS arrives, passes it to the AI handler and replies automatically.

Usage:
    python3 src/sms_server.py

Environment (.env):
    PHONE_IP=192.168.1.40
    PHONE_PORT=8080
    SMS_GATEWAY_USER=sms
    SMS_GATEWAY_PASS=smspass1
    OPENCLAW_GATEWAY_URL=http://localhost:18789
    OPENCLAW_GATEWAY_TOKEN=<your token>
    OPENCLAW_MODEL=openclaw:friday
"""

import os, time, logging, json, base64, urllib.request, urllib.error
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("sms-server")

PHONE_IP   = os.getenv("PHONE_IP",          "192.168.1.40")
PHONE_PORT = os.getenv("PHONE_PORT",         "8080")
SMS_USER   = os.getenv("SMS_GATEWAY_USER",   "sms")
SMS_PASS   = os.getenv("SMS_GATEWAY_PASS",   "smspass1")
POLL_SECS  = float(os.getenv("POLL_INTERVAL_SECS", "2"))

PHONE_BASE = f"http://{PHONE_IP}:{PHONE_PORT}"
AUTH_HEADER = "Basic " + base64.b64encode(f"{SMS_USER}:{SMS_PASS}".encode()).decode()

# Track processed message IDs so we don't reply twice
_seen_ids: set = set()


def api(method: str, path: str, body=None):
    """Make a request to the android-sms-gateway API."""
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{PHONE_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", AUTH_HEADER)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def send_sms(to: str, message: str) -> bool:
    """Send an SMS via the Android gateway."""
    code, resp = api("POST", "/message", {
        "message": message,
        "phoneNumbers": [to]
    })
    if code == 202:
        log.info(f"✅ Sent to {to}: {message[:60]!r}")
        return True
    else:
        log.error(f"❌ Send failed ({code}): {resp}")
        return False


def get_inbox() -> list:
    """Fetch received messages from the gateway."""
    code, resp = api("GET", "/message")
    if code == 200 and isinstance(resp, list):
        return resp
    return []


def handle_new_message(msg: dict):
    """Process one inbound SMS and send AI reply."""
    from ai_handler import handle_sms

    msg_id   = msg.get("id", "")
    sender   = msg.get("phoneNumber") or msg.get("from", "unknown")
    text     = msg.get("message") or msg.get("text", "")

    if not text.strip():
        return

    log.info(f"📩 New SMS from {sender}: {text!r}")

    try:
        reply = handle_sms(sender, text)
        log.info(f"🤖 AI reply: {reply!r}")
        send_sms(sender, reply)
    except Exception as e:
        log.error(f"AI handler error: {e}")
        send_sms(sender, "Sorry, I couldn't process that right now. Please try again.")


def poll_loop():
    """Main polling loop — checks for new messages every POLL_SECS."""
    log.info(f"🚀 SMS server started — polling {PHONE_BASE} every {POLL_SECS}s")

    # Seed seen IDs with existing messages on startup (don't reply to old ones)
    for msg in get_inbox():
        _seen_ids.add(msg.get("id"))
    log.info(f"Seeded {len(_seen_ids)} existing message IDs (will not reply to these)")

    while True:
        try:
            messages = get_inbox()
            for msg in messages:
                msg_id = msg.get("id")
                if msg_id and msg_id not in _seen_ids:
                    _seen_ids.add(msg_id)
                    handle_new_message(msg)
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error(f"Poll error: {e}")

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    # Quick connectivity check
    code, resp = api("GET", "/")
    if code != 200:
        log.error(f"Cannot reach gateway at {PHONE_BASE} — check PHONE_IP and the app is running")
        sys.exit(1)

    model = resp.get("model", "unknown") if isinstance(resp, dict) else "unknown"
    log.info(f"✅ Connected to gateway: {model}")

    poll_loop()
