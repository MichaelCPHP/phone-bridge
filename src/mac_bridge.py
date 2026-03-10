#!/usr/bin/env python3
"""
mac_bridge.py — Unified Mac-side phone bridge.

Handles ALL inbound messages from one place:
  - iMessage/SMS via imsg CLI (watches Mac Messages.app chat.db)
  - Android SMS via ADB content://sms poll (fallback/non-Apple senders)

Replies via:
  - imsg send (iMessage/SMS for Apple contacts)
  - SMS Gateway API (Android gateway for non-Apple)

Architecture: Phone = modem. Mac = everything.

Usage:
    python3 src/mac_bridge.py
    python3 src/mac_bridge.py --test-imsg   # test iMessage reply to a number
    python3 src/mac_bridge.py --status       # show current state
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("mac-bridge")

# ── Config ────────────────────────────────────────────────────────────────────
PHONE_IP      = os.getenv("PHONE_IP",       "192.168.1.40")
PHONE_PORT    = os.getenv("PHONE_PORT",     "8080")
SMS_USER      = os.getenv("SMS_USER",       "sms")
SMS_PASS      = os.getenv("SMS_PASS",       "smspass1")
ADB_SERIAL    = os.getenv("ADB_SERIAL",     "ZY22K45948")
OPENCLAW_URL  = os.getenv("OPENCLAW_URL",   "http://localhost:18789")
OPENCLAW_TOKEN= os.getenv("OPENCLAW_TOKEN", "dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91")
AI_MODEL      = os.getenv("AI_MODEL",       "anthropic/claude-haiku-4-5")
IMSG_BIN      = os.getenv("IMSG_BIN",       "/opt/homebrew/bin/imsg")
MY_ANDROID    = "+17029469526"   # The Android phone number
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3"))

# Per-sender conversation history for AI context
_histories: dict[str, list[dict]] = {}
_lock = threading.Lock()


# ── AI ────────────────────────────────────────────────────────────────────────

JARVIS_SESSION = os.getenv("JARVIS_SESSION", "agent:jarvis:main")


def get_ai_reply(sender: str, text: str) -> Optional[str]:
    """Get reply via Jarvis session exclusively.
    
    Uses openclaw sessions_send to route to agent:jarvis:main only.
    No other agents will respond.
    """
    import requests, re

    prompt = (
        f"[SMS from {sender}]: {text}\n\n"
        f"Reply via SMS — plain text only, 1-2 sentences, under 160 characters. "
        f"No markdown. No preamble. Just the reply text."
    )

    try:
        # Route to Jarvis session specifically via OpenClaw sessions API
        r = requests.post(
            f"{OPENCLAW_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENCLAW_TOKEN}",
                     "Content-Type": "application/json"},
            json={
                "model": "anthropic/claude-haiku-4-5",
                "max_tokens": 150,
                "messages": [
                    {"role": "system", "content": (
                        "You are a helpful AI phone assistant. "
                        "Reply in plain text only, 1-2 sentences, under 160 characters. "
                        "No markdown. No bullet points. Just the reply text."
                    )},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=35,
        )
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"].strip()
        # Strip markdown
        reply = re.sub(r'\*+', '', reply)
        reply = re.sub(r'#+\s*', '', reply)
        return reply[:160]
    except Exception as e:
        log.error(f"AI error: {e}")
        return None


# ── iMessage send ─────────────────────────────────────────────────────────────

def send_imessage(to: str, text: str, service: str = "auto") -> bool:
    """Send iMessage or SMS via imsg CLI."""
    try:
        result = subprocess.run(
            [IMSG_BIN, "send", "--to", to, "--text", text, "--service", service],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            log.info(f"imsg → {to}: sent ({service})")
            return True
        log.error(f"imsg send failed: {result.stderr.strip()}")
        return False
    except Exception as e:
        log.error(f"imsg send error: {e}")
        return False


# ── SMS Gateway send ──────────────────────────────────────────────────────────

def send_sms_gateway(number: str, text: str) -> bool:
    """Send SMS via Android gateway Wi-Fi API."""
    import requests
    try:
        r = requests.post(
            f"http://{PHONE_IP}:{PHONE_PORT}/messages",
            auth=(SMS_USER, SMS_PASS),
            json={"message": text, "phoneNumbers": [number]},
            timeout=10,
        )
        r.raise_for_status()
        msg_id = r.json().get("id")
        for _ in range(15):
            time.sleep(1)
            s = requests.get(
                f"http://{PHONE_IP}:{PHONE_PORT}/messages/{msg_id}",
                auth=(SMS_USER, SMS_PASS), timeout=5
            ).json()
            state = s.get("state", "")
            if state in ("Delivered", "Sent"):
                log.info(f"gateway → {number}: {state}")
                return True
            if state == "Failed":
                log.error(f"gateway → {number}: Failed")
                return False
        return True
    except Exception as e:
        log.error(f"send_sms_gateway error: {e}")
        return False


def send_reply(sender: str, text: str, service: str = "auto") -> bool:
    """Send reply via best available path."""
    # iMessage path handles both iMessage and SMS for Apple contacts
    if send_imessage(sender, text, service=service):
        return True
    # Fallback: Android gateway
    log.warning(f"imsg failed, trying gateway for {sender}")
    return send_sms_gateway(sender, text)


# ── iMessage watcher ──────────────────────────────────────────────────────────

def handle_imsg_message(msg: dict) -> None:
    """Process one message from imsg watch."""
    # Skip outbound messages (is_from_me=true)
    if msg.get("is_from_me") or msg.get("isFromMe"):
        return

    sender = msg.get("sender") or msg.get("handle") or msg.get("address") or ""
    text   = msg.get("text") or msg.get("body") or ""
    service = msg.get("service", "auto")

    if not sender or not text:
        return

    log.info(f"📨 iMsg [{service}] from {sender}: {text[:60]}")

    reply = get_ai_reply(sender, text)
    if not reply:
        log.warning("No AI reply")
        return

    log.info(f"  ↳ AI: {reply[:80]}")
    send_reply(sender, reply, service=service)


def watch_imessages() -> None:
    """Stream incoming iMessages/SMS from Mac Messages.app via imsg watch."""
    log.info("Starting imsg watcher...")
    while True:
        try:
            proc = subprocess.Popen(
                [IMSG_BIN, "watch", "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            log.info("✅ imsg watch: streaming")
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    handle_imsg_message(msg)
                except json.JSONDecodeError:
                    pass  # non-JSON line, skip
            proc.wait()
            log.warning("imsg watch exited, restarting in 5s...")
        except Exception as e:
            log.error(f"imsg watch error: {e}")
        time.sleep(5)


# ── ADB SMS poller ────────────────────────────────────────────────────────────

def adb(*args, timeout: int = 15) -> tuple[int, str]:
    cmd = ["adb", "-s", ADB_SERIAL] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def get_sms_since(last_id: int) -> list[dict]:
    """Read new received SMS (type=1) from content://sms via ADB."""
    rc, out = adb("shell", "content", "query", "--uri", "content://sms", timeout=10)
    if rc != 0:
        return []
    msgs = []
    for line in out.split('\n'):
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        _, _, rest = line.partition(' ')
        _, _, rest = rest.partition(' ')
        parts = re.split(r',\s*(?=\w+=)', rest)
        m = {}
        for p in parts:
            if '=' in p:
                k, _, v = p.partition('=')
                m[k.strip()] = v.strip()
        if m.get('type') == '1' and int(m.get('_id', 0)) > last_id:
            msgs.append(m)
    msgs.sort(key=lambda x: int(x.get('_id', 0)))
    return msgs


def get_max_sms_id() -> int:
    rc, out = adb("shell", "content", "query", "--uri", "content://sms", timeout=10)
    if rc != 0:
        return 0
    max_id = 0
    for line in out.split('\n'):
        if not line.strip().startswith("Row:"):
            continue
        m = re.search(r'\b_id=(\d+)', line)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return max_id


def get_max_mms_id() -> int:
    rc, out = adb("shell", "content", "query", "--uri", "content://mms", timeout=10)
    if rc != 0:
        return 0
    max_id = 0
    for line in out.split('\n'):
        if not line.strip().startswith("Row:"):
            continue
        m = re.search(r'\b_id=(\d+)', line)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return max_id


def get_mms_text(mms_id: int) -> str:
    """Get text body from MMS message parts."""
    rc, out = adb("shell", "content", "query", "--uri", f"content://mms/{mms_id}/part", timeout=10)
    if rc != 0:
        return ""
    for line in out.split('\n'):
        if 'text=' in line and 'ct=text/plain' in line:
            m = re.search(r'text=(.+?)(?:,\s*\w+=|$)', line)
            if m:
                return m.group(1).strip()
    return ""


def get_mms_sender(mms_id: int) -> str:
    """Get sender address from MMS message (type=137 = FROM)."""
    rc, out = adb("shell", "content", "query", "--uri", f"content://mms/{mms_id}/addr", timeout=10)
    if rc != 0:
        return ""
    for line in out.split('\n'):
        if 'type=137' in line:  # 137 = FROM address
            m = re.search(r'address=([^,]+)', line)
            if m:
                addr = m.group(1).strip()
                if addr and addr != 'insert-address-token':
                    return addr
    return ""


def get_mms_since(last_id: int) -> list[dict]:
    """Read new inbound MMS/group messages since last_id."""
    rc, out = adb("shell", "content", "query", "--uri", "content://mms", timeout=10)
    if rc != 0:
        return []
    msgs = []
    for line in out.split('\n'):
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        _, _, rest = line.partition(' ')
        _, _, rest = rest.partition(' ')
        parts = re.split(r',\s*(?=\w+=)', rest)
        m = {}
        for p in parts:
            if '=' in p:
                k, _, v = p.partition('=')
                m[k.strip()] = v.strip()
        mid = int(m.get('_id', 0))
        msg_box = m.get('msg_box', '0')
        if mid > last_id and msg_box == '1':  # msg_box=1 = inbound
            msgs.append(m)
    msgs.sort(key=lambda x: int(x.get('_id', 0)))
    return msgs


def poll_adb_sms() -> None:
    """Poll content://sms and content://mms for new inbound messages."""
    log.info("Starting ADB SMS+MMS poller...")
    last_sms_id = get_max_sms_id()
    last_mms_id = get_max_mms_id()
    log.info(f"✅ ADB poll: SMS watermark={last_sms_id}, MMS watermark={last_mms_id}")

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            # Poll SMS
            new_msgs = get_sms_since(last_sms_id)
            for m in new_msgs:
                mid    = int(m.get('_id', 0))
                sender = m.get('address', '')
                text   = m.get('body', '')
                ts_ms  = int(m.get('date', 0))
                ts     = datetime.fromtimestamp(ts_ms / 1000).strftime('%H:%M:%S')

                last_sms_id = max(last_sms_id, mid)

                if not text:
                    continue

                log.info(f"📨 ADB [{ts}] SMS from {sender}: {text[:60]}")
                reply = get_ai_reply(sender, text)
                if not reply:
                    log.warning("No AI reply")
                    continue
                log.info(f"  ↳ AI: {reply[:80]}")
                send_sms_gateway(sender, reply)

            # Poll MMS (group texts)
            new_mms = get_mms_since(last_mms_id)
            for m in new_mms:
                mid  = int(m.get('_id', 0))
                last_mms_id = max(last_mms_id, mid)

                text   = get_mms_text(mid)
                sender = get_mms_sender(mid)
                ts_ms  = int(m.get('date', 0))
                ts     = datetime.fromtimestamp(ts_ms / 1000).strftime('%H:%M:%S')

                if not text or not sender:
                    continue

                log.info(f"📨 ADB [{ts}] MMS/Group from {sender}: {text[:60]}")
                reply = get_ai_reply(sender, text)
                if not reply:
                    log.warning("No AI reply for MMS")
                    continue
                log.info(f"  ↳ AI: {reply[:80]}")
                send_sms_gateway(sender, reply)

        except Exception as e:
            log.error(f"ADB poll error: {e}")


# ── Status ────────────────────────────────────────────────────────────────────

def show_status() -> None:
    import requests
    print("\n=== Mac Bridge Status ===")

    # imsg
    try:
        r = subprocess.run([IMSG_BIN, "chats", "--limit", "1", "--json"],
                           capture_output=True, text=True, timeout=5)
        print(f"  imsg CLI:      {'✅ OK' if r.returncode == 0 else '❌ FAIL'}")
    except Exception as e:
        print(f"  imsg CLI:      ❌ {e}")

    # OpenClaw
    try:
        r = requests.get(f"{OPENCLAW_URL}/v1/models",
                         headers={"Authorization": f"Bearer {OPENCLAW_TOKEN}"},
                         timeout=3)
        print(f"  OpenClaw:      {'✅ OK' if r.ok else '❌ ' + str(r.status_code)}")
    except Exception as e:
        print(f"  OpenClaw:      ❌ {e}")

    # Gateway
    try:
        r = requests.get(f"http://{PHONE_IP}:{PHONE_PORT}/",
                         auth=(SMS_USER, SMS_PASS), timeout=3)
        data = r.json()
        print(f"  SMS Gateway:   ✅ {data.get('name', 'ok')} ({data.get('model', '?')})")
    except Exception as e:
        print(f"  SMS Gateway:   ❌ {e}")

    # ADB
    rc, out = adb("shell", "echo ok", timeout=5)
    print(f"  ADB:           {'✅ ' + ADB_SERIAL if rc == 0 else '❌ not connected'}")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Mac Phone Bridge")
    parser.add_argument("--status", action="store_true", help="Show status and exit")
    parser.add_argument("--test-imsg", metavar="NUMBER",
                        help="Send test iMessage to NUMBER and exit")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.test_imsg:
        ok = send_imessage(args.test_imsg, "Mac bridge test — AI assistant online.")
        print("Sent ✅" if ok else "Failed ❌")
        return

    show_status()

    print("╔══════════════════════════════════════════╗")
    print("║      MAC PHONE BRIDGE LIVE               ║")
    print("║                                          ║")
    print("║  iMessage/SMS : imsg watch (all Apple)   ║")
    print("║  Android SMS  : ADB poll every 3s        ║")
    print("║  AI           : Claude via OpenClaw      ║")
    print("║  Ctrl+C       : stop                     ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # Start imsg watcher in background thread
    t_imsg = threading.Thread(target=watch_imessages, daemon=True)
    t_imsg.start()

    # ADB poll runs in main thread
    poll_adb_sms()


if __name__ == "__main__":
    main()
