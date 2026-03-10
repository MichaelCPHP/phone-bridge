#!/usr/bin/env python3
"""
SMS Monitor via ADB — polls content://sms for new received messages.

No default SMS app change needed. No webhook. No ngrok.
Works entirely via USB + ADB content provider.

Usage:
    python3 sms_adb_monitor.py           # interactive loop
    python3 sms_adb_monitor.py --test    # send one test reply
"""

import logging
import os
import subprocess
import sys
import time
import datetime

log = logging.getLogger("sms-monitor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3"))   # seconds between polls
PHONE_IP      = os.getenv("PHONE_IP",   "192.168.1.40")
PHONE_PORT    = os.getenv("PHONE_PORT", "8080")
SMS_USER      = os.getenv("SMS_USER",   "sms")
SMS_PASS      = os.getenv("SMS_PASS",   "smspass1")
ADB_SERIAL    = os.getenv("ADB_SERIAL", "ZY22K45948")   # USB serial, avoids multi-device ambiguity


# ── ADB ───────────────────────────────────────────────────────────────────────

def adb(*args, timeout: int = 15) -> tuple[int, str]:
    cmd = ["adb", "-s", ADB_SERIAL] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def get_all_sms() -> list[dict]:
    """Read full SMS database via ADB content provider."""
    import re
    rc, out = adb("shell", "content", "query", "--uri", "content://sms", timeout=10)
    if rc != 0:
        log.error(f"ADB content query failed: {out[:100]}")
        return []

    messages = []
    for line in out.split('\n'):
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        # Strip "Row: N " prefix
        _, _, rest = line.partition(' ')
        _, _, rest = rest.partition(' ')
        # Split on ", key=" boundaries (handles commas in body)
        parts = re.split(r',\s*(?=\w+=)', rest)
        m = {}
        for p in parts:
            if '=' in p:
                k, _, v = p.partition('=')
                m[k.strip()] = v.strip()
        if m:
            messages.append(m)

    return messages


def get_received_since(last_id: int) -> list[dict]:
    """Return received SMS (type=1) with _id > last_id, oldest first."""
    all_sms = get_all_sms()
    received = [
        m for m in all_sms
        if m.get('type') == '1' and int(m.get('_id', 0)) > last_id
    ]
    received.sort(key=lambda x: int(x.get('_id', 0)))
    return received


def get_max_id() -> int:
    """Get the current highest SMS _id (watermark)."""
    msgs = get_all_sms()
    if not msgs:
        return 0
    return max(int(m.get('_id', 0)) for m in msgs)


# ── SMS send ──────────────────────────────────────────────────────────────────

def send_sms(number: str, text: str) -> bool:
    """Send SMS via Wi-Fi gateway API."""
    import requests
    try:
        r = requests.post(
            f"http://{PHONE_IP}:{PHONE_PORT}/messages",
            auth=(SMS_USER, SMS_PASS),
            json={"message": text, "phoneNumbers": [number]},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        msg_id = data.get("id")
        # Poll for delivery (up to 15s)
        for _ in range(15):
            time.sleep(1)
            s = requests.get(
                f"http://{PHONE_IP}:{PHONE_PORT}/messages/{msg_id}",
                auth=(SMS_USER, SMS_PASS), timeout=5
            ).json()
            state = s.get("state", "")
            if state in ("Delivered", "Sent"):
                log.info(f"SMS → {number}: {state}")
                return True
            if state == "Failed":
                log.error(f"SMS → {number}: Failed")
                return False
        return True  # assume sent
    except Exception as e:
        log.error(f"send_sms error: {e}")
        return False


# ── AI ────────────────────────────────────────────────────────────────────────

import sys as _sys
_sys.path.insert(0, os.path.dirname(__file__))

_sms_histories: dict = {}

OPENCLAW_URL   = os.getenv("OPENCLAW_URL",   "http://localhost:18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91")
AI_MODEL       = os.getenv("AI_MODEL",       "anthropic/claude-haiku-4-5")

def ai_reply(phone_number: str, text: str) -> str:
    """Get AI reply via OpenClaw gateway (Claude) with per-number conversation history."""
    import requests
    history = _sms_histories.get(phone_number, [])
    try:
        r = requests.post(
            f"{OPENCLAW_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "max_tokens": 150,
                "messages": [
                    {"role": "system", "content": (
                        "You are a helpful AI assistant answering SMS messages. "
                        "Be concise — 1-3 sentences. No markdown."
                    )},
                ] + history + [{"role": "user", "content": text}],
            },
            timeout=30,
        )
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"].strip()
        history = history + [
            {"role": "user",      "content": text},
            {"role": "assistant", "content": reply},
        ]
        _sms_histories[phone_number] = history[-20:]
        return reply
    except Exception as e:
        log.error(f"AI error: {e}")
        return "Sorry, I'm having trouble right now. Please try again."


# ── Shortcode filter ──────────────────────────────────────────────────────────

def is_shortcode(address: str) -> bool:
    """True if address looks like a spam shortcode (≤6 digits)."""
    digits = address.replace("+", "").replace("-", "").replace(" ", "")
    return len(digits) <= 6


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_monitor():
    log.info("SMS ADB monitor starting...")

    # Check ADB
    rc, out = adb("devices")
    devices = [l for l in out.splitlines() if "\tdevice" in l]
    if not devices:
        log.error("No ADB device connected. Plug in USB and run: adb devices")
        sys.exit(1)
    log.info(f"ADB device: {devices[0].split()[0]}")

    # Set watermark to current max _id (ignore historical messages)
    last_id = get_max_id()
    log.info(f"Watermark set to SMS _id={last_id}. Watching for new messages...")
    log.info(f"Send a text to the Android number to test auto-reply.")

    while True:
        try:
            new_msgs = get_received_since(last_id)

            for msg in new_msgs:
                msg_id  = int(msg.get('_id', 0))
                sender  = msg.get('address', 'unknown')
                body    = msg.get('body', '')
                ts      = int(msg.get('date', 0)) // 1000
                dt      = datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '?'

                log.info(f"📨 [{dt}] SMS from {sender}: {body[:60]}")

                # Update watermark
                if msg_id > last_id:
                    last_id = msg_id

                # Skip shortcodes
                if is_shortcode(sender):
                    log.info(f"  ↳ Skipping shortcode {sender}")
                    continue

                # Get AI reply
                log.info(f"  ↳ Getting AI reply...")
                reply = ai_reply(sender, body)
                log.info(f"  ↳ AI: {reply[:60]}")

                # Send reply
                ok = send_sms(sender, reply)
                log.info(f"  ↳ Sent: {'✅' if ok else '❌'}")

        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error(f"Monitor error: {e}")

        time.sleep(POLL_INTERVAL)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--test" in sys.argv:
        # Quick test: check ADB reads, send test SMS
        import requests
        log.info("Running test...")

        msgs = get_all_sms()
        log.info(f"Total SMS in database: {len(msgs)}")
        recv = [m for m in msgs if m.get('type') == '1']
        log.info(f"  Received (type=1): {len(recv)}")
        sent = [m for m in msgs if m.get('type') == '2']
        log.info(f"  Sent (type=2): {len(sent)}")
        if recv:
            m = max(recv, key=lambda x: int(x.get('_id', 0)))
            log.info(f"  Latest received: from={m.get('address')} body={m.get('body','')[:40]}")

        log.info("Sending test SMS to Michael's iPhone...")
        ok = send_sms("+19495772413",
                      "USB bridge test — reply to this and I will respond via AI!")
        log.info(f"Send result: {'✅' if ok else '❌'}")

    else:
        run_monitor()
