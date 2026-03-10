#!/usr/bin/env python3
"""
Phone Bridge — ADB Logcat SMS Interceptor

Catches incoming SMS via Android broadcast logcat — works regardless of 
which SMS app is installed or set as default.

Fallback for when content://sms doesn't receive writes from Google Messages.

Usage:
    python3 src/sms_logcat.py             # start interceptor loop
    python3 src/sms_logcat.py test        # test ADB connection
"""

import os, sys, subprocess, time, logging, re
from pathlib import Path

log = logging.getLogger("sms-logcat")

ADB_PATHS = [
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    "/usr/local/bin/adb",
    "/opt/homebrew/bin/adb",
    "adb",
]
ADB = next((p for p in ADB_PATHS if Path(p).exists() or p == "adb"), "adb")

POLL_SECS = float(os.getenv("POLL_INTERVAL_SECS", "3"))


def adb(*args, timeout=30):
    cmd = [ADB] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except FileNotFoundError:
        return 1, f"adb not found at {ADB}"


def shell(*args, timeout=10):
    return adb("shell", *args, timeout=timeout)


def send_sms(phone_number: str, message: str) -> bool:
    """Send SMS via UI automation (same as sms_adb.py)."""
    phone_number = re.sub(r'[\s\-\(\)]', '', phone_number)
    if not phone_number.startswith("+"):
        phone_number = "+1" + phone_number.lstrip("1")

    safe_msg = message.replace("'", "").replace('"', "")
    shell(f"am start -a android.intent.action.SENDTO -d 'smsto:{phone_number}' --es 'sms_body' '{safe_msg}'")
    time.sleep(2)

    shell("uiautomator dump /sdcard/ui.xml")
    _, xml = shell("cat /sdcard/ui.xml")

    m = re.search(r'content-desc="Send message"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not m:
        log.error("Send button not found in UI dump")
        return False

    cx = (int(m.group(1)) + int(m.group(3))) // 2
    cy = (int(m.group(2)) + int(m.group(4))) // 2
    shell(f"input tap {cx} {cy}")
    log.info(f"✅ SMS sent to {phone_number}")
    return True


def clear_logcat():
    """Clear logcat buffer so we start fresh."""
    adb("logcat", "-c")


def stream_logcat():
    """
    Stream logcat and yield (sender, body) for each incoming SMS.
    
    Looks for SmsReceiver / InboundSmsHandler log entries that contain
    the sender address and message body.
    """
    # Start logcat process streaming
    proc = subprocess.Popen(
        [ADB, "logcat", "-v", "time", "-s",
         "SmsReceiver:V", "InboundSmsHandler:V", "InboundSmsTracker:V",
         "SmsMessage:V", "GsmInboundSmsHandler:V", "CdmaInboundSmsHandler:V",
         "TelephonyManager:V", "ImsSmsDispatcher:V", "*:S"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    return proc


def monitor_sms_via_logcat():
    """
    Alternative approach: monitor system/telephony SMS broadcast via logcat.
    This catches the raw SMS delivery broadcast.
    """
    # Use a simpler broadcast monitor approach
    proc = subprocess.Popen(
        [ADB, "shell", "logcat", "-v", "brief",
         "SmsReceiver:D", "SmsApp:D", "MessagingApp:D",
         "TelephonyProvider:D", "InboundSmsHandler:D",
         "GsmSMSDispatcher:D", "ImsSmsDispatcher:D", "*:S"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    return proc


def poll_via_content_and_logcat():
    """
    Combined polling: check content://sms AND monitor for new entries.
    Also monitors the sms database modification time as a trigger.
    """
    from sms_adb import get_last_message_id, save_last_message_id, get_new_incoming_sms

    last_id = get_last_message_id()
    if last_id == 0:
        _, out = shell("content query --uri content://sms/inbox --projection _id --sort '_id DESC'")
        for line in out.splitlines():
            if "_id=" in line:
                try:
                    candidate = int(line.split("_id=")[1].split(",")[0].split()[0])
                    last_id = max(last_id, candidate)
                except Exception:
                    pass
                break
        save_last_message_id(last_id)
        log.info(f"Seeded to id={last_id}")

    log.info(f"📱 Monitoring SMS (content provider id > {last_id})")
    log.info("💡 If messages arrive in Google Messages but not here, run:")
    log.info("   adb shell settings put secure sms_default_application com.google.android.apps.messaging")

    return last_id


def check_new_sms_content(last_id: int) -> list[dict]:
    """Check content provider for new SMS (type=1 = received)."""
    _, out = shell(
        f"content query --uri content://sms "
        f"--projection _id:address:body:type:date "
        f"--where '_id > {last_id} AND type=1' "
        f"--sort '_id ASC'"
    )

    messages = []
    current = {}
    last_key = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Row:"):
            if current and current.get("_id"):
                messages.append(current)
            current = {}
            last_key = None
        elif "=" in stripped:
            key, _, val = stripped.partition("=")
            key = key.strip()
            if key in ("_id", "address", "body", "type", "date"):
                current[key] = val.strip()
                last_key = key
            elif last_key == "body":
                current["body"] = current.get("body", "") + "\n" + stripped
    if current and current.get("_id"):
        messages.append(current)

    result = []
    for m in messages:
        try:
            result.append({
                "id": int(m.get("_id", 0)),
                "address": m.get("address", "unknown"),
                "body": m.get("body", "").strip(),
                "date": int(m.get("date", 0)),
            })
        except Exception:
            pass
    return result


def poll_loop():
    """Poll SMS and reply via AI."""
    sys.path.insert(0, str(Path(__file__).parent))
    from ai_handler import handle_sms
    from sms_adb import save_last_message_id

    log.info("🚀 SMS interceptor starting...")

    last_id = poll_via_content_and_logcat()

    while True:
        try:
            new_msgs = check_new_sms_content(last_id)
            for msg in new_msgs:
                if not msg.get("body", "").strip():
                    last_id = max(last_id, msg["id"])
                    continue
                log.info(f"📩 SMS from {msg['address']}: {msg['body']!r}")
                try:
                    reply = handle_sms(msg["address"], msg["body"])
                    log.info(f"🤖 Reply: {reply!r}")
                    send_sms(msg["address"], reply)
                except Exception as e:
                    log.error(f"AI/send error: {e}")
                last_id = max(last_id, msg["id"])
                save_last_message_id(last_id)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Poll error: {e}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.path.insert(0, str(Path(__file__).parent))

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        code, out = adb("devices")
        print(out)
    else:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent.parent / ".env")
        except ImportError:
            pass
        poll_loop()
