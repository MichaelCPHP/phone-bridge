#!/usr/bin/env python3
"""
Mac Bridge — unified iMessage + SMS AI responder.
Phone = modem only. All logic on Mac.

Channels:
  iMessage/SMS from iPhones: imsg CLI (Mac Messages.app)
  SMS from Android/others:   android-sms-gateway REST API
"""

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mac-bridge")

# ── Config ─────────────────────────────────────────────────────────────────
OPENCLAW_URL   = os.getenv("OPENCLAW_URL", "http://localhost:18789/v1/chat/completions")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91")
GATEWAY_URL    = os.getenv("GATEWAY_URL", "http://192.168.1.40:8080")
GATEWAY_USER   = os.getenv("SMS_GATEWAY_USER", "sms")
GATEWAY_PASS   = os.getenv("SMS_GATEWAY_PASS", "smspass1")
POLL_SEC       = int(os.getenv("POLL_SEC", "5"))

# Track processed message IDs to avoid double-replies
_seen_ids: set = set()
_seen_lock = threading.Lock()


def get_ai_reply(sender: str, message: str, channel: str = "SMS") -> str:
    """Call OpenClaw AI for a reply."""
    try:
        resp = httpx.post(
            OPENCLAW_URL,
            headers={"Authorization": f"Bearer {OPENCLAW_TOKEN}"},
            json={
                "model": "openclaw:main",
                "messages": [
                    {"role": "system", "content": (
                        f"You are Jarvis, a helpful AI assistant for Michael. "
                        f"Reply naturally via {channel}. Be concise, friendly, max 160 chars for SMS."
                    )},
                    {"role": "user", "content": message},
                ],
                "max_tokens": 150,
            },
            timeout=30,
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"AI error: {e}")
        return "Sorry, I'm having trouble responding right now."


# ── iMessage thread via imsg ────────────────────────────────────────────────

def imsg_send(to: str, text: str) -> bool:
    """Send via imsg CLI."""
    try:
        result = subprocess.run(
            ["imsg", "send", "--to", to, "--text", text],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception as e:
        log.error(f"imsg send failed: {e}")
        return False


def imsg_watcher():
    """Watch Mac Messages.app for new iMessages/SMS via imsg."""
    log.info("📱 iMessage watcher starting...")
    try:
        # Get recent chats to initialize
        result = subprocess.run(
            ["imsg", "chats", "--limit", "20", "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            log.warning(f"imsg chats failed: {result.stderr[:200]}")
            log.warning("iMessage watcher disabled — grant Full Disk Access to Terminal in System Settings")
            return

        chats = [json.loads(line) for line in (result.stdout or "").strip().splitlines() if line.strip()]
        # Track last message ID per chat
        last_ids: dict = {}
        for chat in chats:
            last_ids[chat.get("chatId")] = chat.get("lastMessageId", 0)

        log.info(f"✅ Watching {len(chats)} iMessage chats")

        while True:
            time.sleep(POLL_SEC)
            for chat in chats:
                chat_id = chat.get("chatId")
                try:
                    hist = subprocess.run(
                        ["imsg", "history", "--chat-id", str(chat_id), "--limit", "3", "--json"],
                        capture_output=True, text=True, timeout=10
                    )
                    if hist.returncode != 0:
                        continue
                    messages = [json.loads(line) for line in (hist.stdout or "").strip().splitlines() if line.strip()]
                    for msg in messages:
                        msg_id = msg.get("messageId") or msg.get("id")
                        is_from_me = msg.get("isFromMe", False)
                        text = msg.get("text") or msg.get("body") or ""
                        sender = msg.get("sender") or msg.get("handle") or chat.get("displayName", "unknown")

                        if is_from_me or not text or not msg_id:
                            continue

                        with _seen_lock:
                            if msg_id in _seen_ids:
                                continue
                            _seen_ids.add(msg_id)

                        if last_ids.get(chat_id, 0) and msg_id <= last_ids.get(chat_id, 0):
                            continue

                        log.info(f"[iMessage] {sender}: {text[:80]}")
                        reply = get_ai_reply(sender, text, "iMessage")
                        log.info(f"[iMessage] → {reply[:80]}")
                        imsg_send(sender, reply)
                        last_ids[chat_id] = msg_id

                except Exception as e:
                    log.debug(f"Chat {chat_id} error: {e}")

    except Exception as e:
        log.error(f"iMessage watcher error: {e}")


# ── SMS gateway poller ──────────────────────────────────────────────────────

def gateway_sms_poller():
    """Poll android-sms-gateway for new inbound SMS."""
    log.info("📲 SMS gateway poller starting...")
    last_check = datetime.now(timezone.utc)

    while True:
        time.sleep(POLL_SEC)
        try:
            # Get recent messages
            resp = httpx.get(
                f"{GATEWAY_URL}/api/v1/messages",
                auth=(GATEWAY_USER, GATEWAY_PASS),
                timeout=10,
            )
            if resp.status_code != 200:
                log.debug(f"Gateway poll: HTTP {resp.status_code}")
                continue

            messages = resp.json() if isinstance(resp.json(), list) else resp.json().get("results", [])
            for msg in messages:
                msg_id = msg.get("id") or msg.get("messageId")
                sender = msg.get("phoneNumber") or msg.get("sender") or msg.get("from")
                text   = msg.get("message") or msg.get("body") or ""
                state  = msg.get("state", "")

                if not msg_id or not text or not sender:
                    continue
                if state not in ("", "received", "pending"):
                    continue

                with _seen_lock:
                    if msg_id in _seen_ids:
                        continue
                    _seen_ids.add(msg_id)

                log.info(f"[SMS] {sender}: {text[:80]}")
                reply = get_ai_reply(sender, text, "SMS")
                log.info(f"[SMS] → {reply[:80]}")

                # Send reply via gateway
                send_resp = httpx.post(
                    f"{GATEWAY_URL}/api/3rdparty/v1/message",
                    auth=(GATEWAY_USER, GATEWAY_PASS),
                    json={"message": reply, "phoneNumbers": [sender]},
                    timeout=15,
                )
                if send_resp.status_code in (200, 201):
                    log.info(f"[SMS] ✅ Reply sent to {sender}")
                else:
                    log.warning(f"[SMS] Send failed: HTTP {send_resp.status_code}")

        except Exception as e:
            log.debug(f"Gateway poller error: {e}")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("🚀 Mac Bridge starting — phone is just a modem")
    log.info(f"OpenClaw: {OPENCLAW_URL}")
    log.info(f"SMS Gateway: {GATEWAY_URL}")

    threads = [
        threading.Thread(target=imsg_watcher, daemon=True, name="iMessage"),
        threading.Thread(target=gateway_sms_poller, daemon=True, name="SMS-Gateway"),
    ]
    for t in threads:
        t.start()
        log.info(f"✅ Started {t.name} thread")

    log.info("Bridge running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
