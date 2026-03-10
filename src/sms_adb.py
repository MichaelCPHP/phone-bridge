#!/usr/bin/env python3
"""
Phone Bridge — ADB SMS Bridge (no third-party app required)

Sends and receives SMS directly via ADB over USB.
No app installation needed on Android.
No permission restrictions — ADB has full shell access.

Requirements:
    - Android phone connected via USB
    - USB Debugging enabled (Settings → About Phone → tap Build Number 7x → Developer Options → USB Debugging)
    - ADB installed (already at ~/Library/Android/sdk/platform-tools/adb)

Usage:
    python3 src/sms_adb.py                  # start polling loop
    python3 src/sms_adb.py send +19495772413 "Hello"  # send one SMS
    python3 src/sms_adb.py test             # run connectivity test
"""

import os, sys, subprocess, time, logging, json, re
from pathlib import Path

log = logging.getLogger("sms-adb")

# ADB path — Android Studio install or PATH
ADB_PATHS = [
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    "/usr/local/bin/adb",
    "/opt/homebrew/bin/adb",
    "adb",
]
ADB = next((p for p in ADB_PATHS if Path(p).exists() or p == "adb"), "adb")

POLL_SECS     = float(os.getenv("POLL_INTERVAL_SECS", "3"))
SMS_DB        = "/data/data/com.android.providers.telephony/databases/mmssms.db"
LAST_ID_FILE  = Path("/tmp/sms_adb_last_id.txt")


def adb(*args, timeout=10) -> tuple[int, str]:
    """Run an adb command. Returns (returncode, stdout)."""
    cmd = [ADB] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except FileNotFoundError:
        return 1, f"adb not found at {ADB}"


def shell(*args, timeout=10) -> tuple[int, str]:
    """Run adb shell command."""
    return adb("shell", *args, timeout=timeout)


def check_connection() -> bool:
    """Check if a device is connected and authorized."""
    code, out = adb("devices")
    lines = [l for l in out.splitlines() if "\t" in l]
    for line in lines:
        serial, state = line.split("\t", 1)
        if state.strip() == "device":
            log.info(f"✅ ADB device connected: {serial}")
            return True
        elif state.strip() == "unauthorized":
            log.warning("⚠️  Phone connected but not authorized — check phone screen for 'Allow USB debugging?' and tap OK")
            return False
    log.warning("❌ No ADB device found. Connect phone via USB cable.")
    return False


def send_sms(phone_number: str, message: str) -> bool:
    """
    Send SMS via ADB UI automation — works on Android 12-16.

    Method:
        1. am start SENDTO intent → opens Google Messages compose view
        2. UI automator finds "Send message" button → taps it
        3. Verify message appears in /sms/sent

    No app installation needed. No restricted permissions needed.
    """
    import re, time as _time

    # Normalize number
    phone_number = phone_number.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    if not phone_number.startswith("+"):
        phone_number = "+1" + phone_number.lstrip("1")

    # Open Google Messages compose with pre-filled body
    safe_msg = message.replace("'", "").replace('"', "")  # basic sanitize for shell
    code, out = shell(
        f"am start -a android.intent.action.SENDTO "
        f"-d 'smsto:{phone_number}' "
        f"--es 'sms_body' '{safe_msg}'"
    )
    if code != 0 and "Error" in out:
        log.error(f"Failed to open compose: {out}")
        return False

    _time.sleep(2)  # wait for Messages to open

    # Find Send button via uiautomator dump
    shell("uiautomator dump /sdcard/ui.xml")
    _, xml = shell("cat /sdcard/ui.xml")

    m = re.search(
        r'content-desc="Send message"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml
    )
    if not m:
        log.error("❌ Send button not found in UI dump")
        return False

    cx = (int(m.group(1)) + int(m.group(3))) // 2
    cy = (int(m.group(2)) + int(m.group(4))) // 2
    shell(f"input tap {cx} {cy}")
    log.info(f"✅ SMS sent to {phone_number}: {message[:60]!r}")
    return True


def get_last_message_id() -> int:
    """Read the last processed SMS id from disk."""
    try:
        return int(LAST_ID_FILE.read_text().strip())
    except Exception:
        return 0


def save_last_message_id(msg_id: int):
    LAST_ID_FILE.write_text(str(msg_id))


def get_new_incoming_sms(since_id: int) -> list[dict]:
    """
    Query the SMS database for new incoming messages since `since_id`.
    Returns list of {id, address, body, date} dicts.
    """
    # Read SMS inbox via content provider (works without root on most Android)
    query = (
        "content query --uri content://sms/inbox "
        "--projection _id,address,body,date "
        f"--where '_id > {since_id}' "
        "--sort '_id ASC'"
    )
    code, out = shell(query)

    messages = []
    current = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Row:"):
            if current:
                messages.append(current)
            current = {}
        elif "=" in line:
            key, _, val = line.partition("=")
            current[key.strip()] = val.strip()
    if current:
        messages.append(current)

    result = []
    for m in messages:
        try:
            result.append({
                "id":      int(m.get("_id", 0)),
                "address": m.get("address", "unknown"),
                "body":    m.get("body", ""),
                "date":    int(m.get("date", 0)),
            })
        except Exception:
            pass

    return result


def poll_loop():
    """Poll for new SMS every POLL_SECS and reply via AI."""
    from ai_handler import handle_sms

    log.info(f"🚀 ADB SMS server started (polling every {POLL_SECS}s)")

    if not check_connection():
        log.error("No device connected. Connect phone via USB and retry.")
        sys.exit(1)

    # Seed last id so we don't reply to old messages
    last_id = get_last_message_id()
    if last_id == 0:
        msgs = get_new_incoming_sms(0)
        last_id = max((m["id"] for m in msgs), default=0)
        save_last_message_id(last_id)
        log.info(f"Seeded last SMS id: {last_id}")

    log.info(f"Watching for new SMS (after id={last_id})...")

    while True:
        try:
            if not check_connection():
                log.warning("Device disconnected — waiting...")
                time.sleep(5)
                continue

            new_msgs = get_new_incoming_sms(last_id)
            for msg in new_msgs:
                log.info(f"📩 SMS from {msg['address']}: {msg['body']!r}")
                try:
                    reply = handle_sms(msg["address"], msg["body"])
                    log.info(f"🤖 Replying: {reply!r}")
                    send_sms(msg["address"], reply)
                except Exception as e:
                    log.error(f"AI/send error: {e}")
                last_id = max(last_id, msg["id"])
                save_last_message_id(last_id)

        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error(f"Poll error: {e}")

        time.sleep(POLL_SECS)


def test():
    """Quick connectivity and send test."""
    print(f"ADB path: {ADB}")

    if not check_connection():
        print("❌ No device. Connect phone via USB with USB Debugging enabled.")
        return

    # Phone model
    code, model = shell("getprop ro.product.model")
    print(f"✅ Device: {model}")

    # Android version
    code, ver = shell("getprop ro.build.version.release")
    print(f"✅ Android: {ver}")

    # SMS inbox count
    code, out = shell("content query --uri content://sms/inbox --projection _id")
    count = out.count("Row:")
    print(f"✅ SMS inbox: {count} messages")

    # Try send
    test_number = os.getenv("TEST_PHONE", "+19495772413")
    print(f"\nSending test SMS to {test_number}...")
    ok = send_sms(test_number, "Hello from Friday via ADB! 🤖 SMS bridge is working.")
    print("✅ Sent!" if ok else "❌ Send failed — check logs above")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sys.path.insert(0, str(Path(__file__).parent))

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "test":
            test()
        elif cmd == "send" and len(sys.argv) >= 4:
            to, msg = sys.argv[2], " ".join(sys.argv[3:])
            ok = send_sms(to, msg)
            print("✅ Sent" if ok else "❌ Failed")
        elif cmd == "send":
            print("Usage: sms_adb.py send <number> <message>")
    else:
        # Load .env
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent.parent / ".env")
        except ImportError:
            pass
        poll_loop()
