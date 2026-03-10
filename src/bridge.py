#!/usr/bin/env python3
"""
Phone Bridge — Main orchestrator
Ties together SMS (inbound/outbound) and calls via ADB.

Inbound SMS:  poll inbox via ADB → AI reply → send SMS back
Inbound calls: detect ringing via ADB → auto-answer → record audio →
               STT → AI → TTS → play audio → hang up
Outbound:     trigger call or SMS from Mac to any number

Run: python3 src/bridge.py
"""

import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

PHONE_IP   = os.getenv("PHONE_IP", "192.168.1.40")
PHONE_PORT = os.getenv("PHONE_PORT", "8080")
SMS_USER   = os.getenv("SMS_USER", "sms")
SMS_PASS   = os.getenv("SMS_PASS", "smspass1")
POLL_SEC   = int(os.getenv("POLL_INTERVAL", "5"))   # SMS poll interval


# ─── ADB helpers ──────────────────────────────────────────────────────────────

def adb(*args, timeout: int = 15) -> tuple[int, str]:
    cmd = ["adb"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def phone_connected() -> bool:
    rc, out = adb("devices")
    return any("\tdevice" in line for line in out.splitlines())


# ─── SMS ──────────────────────────────────────────────────────────────────────

import requests as _req

def _auth():
    return (SMS_USER, SMS_PASS)

def _base():
    return f"http://{PHONE_IP}:{PHONE_PORT}"


def send_sms(number: str, message: str) -> bool:
    """Send SMS via wifi gateway API."""
    try:
        r = _req.post(
            f"{_base()}/messages",
            auth=_auth(),
            json={"message": message, "phoneNumbers": [number]},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        msg_id = data.get("id")
        # Poll for delivery confirmation (up to 10s)
        for _ in range(10):
            time.sleep(1)
            s = _req.get(f"{_base()}/messages/{msg_id}", auth=_auth(), timeout=5).json()
            state = s.get("state")
            if state in ("Delivered", "Sent"):
                log.info(f"SMS → {number}: {state}")
                return True
            if state == "Failed":
                log.error(f"SMS → {number}: Failed")
                return False
        log.warning(f"SMS → {number}: state unknown after 10s")
        return True
    except Exception as e:
        log.error(f"send_sms error: {e}")
        return False


def get_inbox_since(last_date_ms: int) -> list[dict]:
    """Read new SMS messages received after last_date_ms via ADB."""
    rc, out = adb("shell", "content", "query",
                  "--uri", "content://sms/inbox",
                  "--projection", "address,body,date",
                  "--sort", "date DESC")
    if rc != 0:
        return []

    messages = []
    current = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Row:"):
            if current and int(current.get("date_ms", 0)) > last_date_ms:
                messages.append(current)
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k == "address":
                current["from"] = v
            elif k == "body":
                current["text"] = v
            elif k == "date":
                try:
                    current["date_ms"] = int(v)
                except Exception:
                    pass

    if current and int(current.get("date_ms", 0)) > last_date_ms:
        messages.append(current)

    return list(reversed(messages))  # oldest first


# ─── AI ───────────────────────────────────────────────────────────────────────

def ai_reply(message: str, phone_number: str, context: str = "sms") -> str:
    """Get AI response via Ollama."""
    try:
        import requests
        r = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": os.getenv("AI_MODEL", "llama3.2"),
                "messages": [
                    {"role": "system", "content": (
                        "You are a helpful AI assistant answering SMS and calls. "
                        "Be concise — 1-2 sentences for SMS."
                    )},
                    {"role": "user", "content": message},
                ],
                "stream": False,
                "options": {"num_predict": 150},
            },
            timeout=30,
        )
        return r.json()["message"]["content"].strip()
    except Exception as e:
        log.error(f"AI error: {e}")
        return "I received your message but I'm having trouble responding right now."


# ─── Inbound call handler ─────────────────────────────────────────────────────

def handle_inbound_call():
    """
    Detect incoming call via ADB, auto-answer, run voice conversation loop.
    Uses: STT (whisper-cpp) → AI (Ollama) → TTS (Kokoro) → play audio
    """
    log.info("Waiting for incoming call...")

    # Poll call state
    while True:
        rc, state = adb("shell", "dumpsys", "telephony.registry",
                        timeout=5)
        if "mCallState=1" in state or "RINGING" in state.upper():
            log.info("📞 Incoming call detected — answering...")
            _answer_call()
            _run_voice_conversation()
            break
        time.sleep(1)


def _answer_call():
    """Auto-answer incoming call via ADB."""
    # Android 14+ answer via input keyevent
    adb("shell", "input", "keyevent", "KEYCODE_CALL")
    time.sleep(1)
    # Fallback: telecom service
    adb("shell", "telecom", "accept-ringing-call")
    time.sleep(1)
    log.info("Call answered")


def _run_voice_conversation():
    """Record → STT → AI → TTS → speak loop for an active call."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    try:
        from tts_kokoro import synthesize_for_asterisk
    except ImportError:
        log.error("tts_kokoro not available")
        adb("shell", "input", "keyevent", "KEYCODE_ENDCALL")
        return

    # Play greeting from cache (instant)
    greeting_wav = str(Path(__file__).parent.parent / "audio/tts-cache/greeting.wav")
    if Path(greeting_wav).exists():
        _play_audio_on_call(greeting_wav)

    # Conversation loop
    for turn in range(8):
        # Record 5s of caller audio via ADB
        rec_path = f"/tmp/call_rec_{turn}.wav"
        log.info(f"Recording turn {turn}...")
        _record_audio(rec_path, seconds=5)

        if not Path(rec_path).exists():
            break

        # STT
        try:
            import asyncio
            from stt_voicebox import transcribe_audio_file
            transcript = asyncio.run(transcribe_audio_file(rec_path))
        except Exception as e:
            log.error(f"STT error: {e}")
            break

        if not transcript.strip():
            log.info("Silence — playing hold phrase")
            hold_wav = str(Path(__file__).parent.parent / "audio/tts-cache/not_heard.wav")
            _play_audio_on_call(hold_wav)
            continue

        log.info(f"Heard: {transcript}")

        # AI
        reply_text = ai_reply(transcript, context="voice")
        log.info(f"AI reply: {reply_text}")

        # TTS → play
        reply_wav = f"/tmp/call_reply_{turn}.wav"
        try:
            synthesize_for_asterisk(reply_text, reply_wav)
            _play_audio_on_call(reply_wav)
        except Exception as e:
            log.error(f"TTS error: {e}")

        # Check if call still active
        _, state = adb("shell", "dumpsys", "telephony.registry", timeout=5)
        if "mCallState=0" in state or "IDLE" in state.upper():
            log.info("Call ended by remote")
            break

    # Hang up
    adb("shell", "input", "keyevent", "KEYCODE_ENDCALL")
    log.info("Call ended")


def _record_audio(output_path: str, seconds: int = 5):
    """Record audio from phone mic via ADB during active call."""
    remote_path = f"/sdcard/call_rec_tmp.wav"
    adb("shell", f"am broadcast -a android.intent.action.RECORD "
        f"--es output {remote_path} --ei duration {seconds * 1000}",
        timeout=seconds + 5)
    time.sleep(seconds + 1)
    adb("pull", remote_path, output_path)


def _play_audio_on_call(wav_path: str):
    """Push and play a WAV file through the phone speaker during a call."""
    remote_path = "/sdcard/bridge_playback.wav"
    adb("push", wav_path, remote_path)
    adb("shell", f"am broadcast -a android.intent.action.PLAY_AUDIO "
        f"--es path {remote_path}")


# ─── SMS polling loop ─────────────────────────────────────────────────────────

def run_sms_loop():
    """Poll for new inbound SMS and auto-reply via AI."""
    log.info(f"SMS loop started (polling every {POLL_SEC}s)")
    last_ms = int(time.time() * 1000)  # only process messages from now on

    while True:
        try:
            new_msgs = get_inbox_since(last_ms)
            for msg in new_msgs:
                sender = msg.get("from", "unknown")
                text   = msg.get("text", "")
                ts_ms  = msg.get("date_ms", 0)

                log.info(f"📨 SMS from {sender}: {text[:60]}")

                # Update watermark
                if ts_ms > last_ms:
                    last_ms = ts_ms

                # Skip spam/shortcodes (5-6 digit numbers)
                if len(sender.replace("+", "").replace("-", "")) <= 6:
                    log.info(f"Skipping shortcode {sender}")
                    continue

                # Get AI reply
                reply = ai_reply(text, phone_number=sender, context="sms")
                log.info(f"AI → {sender}: {reply[:60]}")

                # Send reply
                send_sms(sender, reply)

        except Exception as e:
            log.error(f"SMS loop error: {e}")

        time.sleep(POLL_SEC)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    log.info("Phone Bridge starting...")

    if not phone_connected():
        log.warning("No ADB device connected — SMS via Wi-Fi only, no call support")
    else:
        rc, out = adb("shell", "getprop", "ro.product.model")
        log.info(f"ADB device: {out.strip()}")

    # Check Ollama
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        log.info(f"Ollama models: {', '.join(models[:3])}")
    except Exception:
        log.warning("Ollama not running — start with: ollama serve")

    mode = sys.argv[1] if len(sys.argv) > 1 else "sms"

    if mode == "sms":
        log.info("Mode: SMS auto-reply loop")
        run_sms_loop()

    elif mode == "call":
        log.info("Mode: inbound call handler (one shot)")
        handle_inbound_call()

    elif mode == "send":
        # Quick send: python3 bridge.py send +1XXX "message"
        if len(sys.argv) >= 4:
            ok = send_sms(sys.argv[2], " ".join(sys.argv[3:]))
            print("✓ Sent" if ok else "✗ Failed")
        else:
            print("Usage: python3 bridge.py send +1XXXXXXXXXX 'message'")

    elif mode == "test":
        log.info("Running connectivity test...")
        print(f"ADB connected: {phone_connected()}")
        print(f"SMS gateway: ", end="")
        try:
            r = _req.get(f"{_base()}/", auth=_auth(), timeout=5)
            print(r.json())
        except Exception as e:
            print(f"ERROR: {e}")

    else:
        print("Usage: python3 bridge.py [sms|call|send|test]")
        print("  sms:  auto-reply loop (default)")
        print("  call: answer next incoming call with AI")
        print("  send: send a test SMS")
        print("  test: connectivity check")
