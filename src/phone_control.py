#!/usr/bin/env python3
"""
phone_control.py — Programmatic control of the Android phone via ADB.

Gives the Mac full control over the Android device:
- Set default apps
- Grant/revoke permissions
- Change any system setting
- Install APKs
- Start/stop apps and services
- Configure SMS gateway
- Port forwarding for tunnels

Usage:
  python3 phone_control.py setup      # Full phone setup for phone-bridge
  python3 phone_control.py status     # Current configuration status
  python3 phone_control.py grant-sms  # Grant SMS perms to gateway app
  python3 phone_control.py tunnel     # Set up ADB port tunnels
"""

import subprocess
import sys
import os
import json
import time

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
SERIAL = os.getenv("ADB_SERIAL", "ZY22K45948")
SMS_GATEWAY_PKG = "me.capcom.smsgateway"
SMS_GATEWAY_PORT = 8080
MAC_GATEWAY_PORT = 18080
MAC_WEBHOOK_PORT = 3001


def adb(*args, check=False) -> str:
    """Run an ADB command and return output."""
    cmd = [ADB, "-s", SERIAL] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if check and result.returncode != 0:
            raise RuntimeError(f"ADB failed: {result.stderr.strip()}")
        return (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def shell(*args, **kwargs) -> str:
    """Run a shell command on the device."""
    return adb("shell", *args, **kwargs)


def get_setting(namespace: str, key: str) -> str:
    return shell(f"settings get {namespace} {key}")


def put_setting(namespace: str, key: str, value: str) -> str:
    return shell(f"settings put {namespace} {key} {value}")


def grant_permission(package: str, permission: str) -> bool:
    result = adb("shell", "pm", "grant", package, permission)
    return "Exception" not in result and "Error" not in result


def get_role_holder(role: str) -> str:
    return shell(f"cmd role get-role-holders {role}").strip()


def set_default_sms(package: str) -> bool:
    """Set the default SMS app. Requires user confirmation on Android 10+."""
    # Try direct role assignment (works on some Android versions)
    result = shell(f"cmd role set-role-holder android.app.role.SMS {package} 0")
    if "true" in result.lower():
        return True
    # Fallback: open settings for user to confirm
    shell(f"am start -a android.app.role.action.REQUEST_ROLE "
          f"-n {package}/.DefaultSmsActivity 2>/dev/null || "
          f"am start -a android.settings.MANAGE_DEFAULT_APPS_SETTINGS")
    return False


def setup_port_tunnels():
    """Set up ADB port forwarding and reverse tunnels."""
    # Forward phone:8080 → Mac:18080 (access SMS gateway API from Mac)
    result1 = adb("forward", f"tcp:{MAC_GATEWAY_PORT}", f"tcp:{SMS_GATEWAY_PORT}")
    # Reverse Mac:3001 → phone:3001 (webhook callback from phone to Mac)
    result2 = adb("reverse", f"tcp:{MAC_WEBHOOK_PORT}", f"tcp:{MAC_WEBHOOK_PORT}")
    print(f"  Forward phone:{SMS_GATEWAY_PORT} → Mac:{MAC_GATEWAY_PORT}: {result1}")
    print(f"  Reverse Mac:{MAC_WEBHOOK_PORT} → phone:{MAC_WEBHOOK_PORT}: {result2}")
    return "18080" in result1 or result1 == "" , "3001" in result2 or result2 == ""


def setup_sms_gateway_permissions():
    """Grant all required permissions to the SMS gateway app."""
    perms = [
        "android.permission.SEND_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.READ_SMS",
        "android.permission.READ_CONTACTS",
        "android.permission.READ_PHONE_STATE",
    ]
    results = {}
    for perm in perms:
        ok = grant_permission(SMS_GATEWAY_PKG, perm)
        results[perm.split(".")[-1]] = "✅" if ok else "❌"
        print(f"  {results[perm.split('.')[-1]]} {perm.split('.')[-1]}")
    return results


def register_webhook(url: str = f"http://127.0.0.1:{MAC_WEBHOOK_PORT}/webhook/sms"):
    """Register webhook with the SMS gateway app via its local API."""
    import requests
    try:
        # Check existing webhooks
        resp = requests.get(f"http://localhost:{MAC_GATEWAY_PORT}/webhooks",
                           auth=("sms", "smspass1"), timeout=5)
        existing = resp.json() if resp.status_code == 200 else []

        # Check if webhook already registered
        for wh in existing:
            if wh.get("url") == url:
                print(f"  ✅ Webhook already registered: {url}")
                return True

        # Register new webhook
        resp = requests.post(f"http://localhost:{MAC_GATEWAY_PORT}/webhooks",
                            auth=("sms", "smspass1"),
                            json={"url": url, "event": "sms:received"},
                            timeout=5)
        if resp.status_code in (200, 201):
            print(f"  ✅ Webhook registered: {url}")
            return True
        else:
            print(f"  ❌ Webhook registration failed: {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ Webhook registration error: {e}")
        return False


def get_status():
    """Get current phone configuration status."""
    print("\n📱 Phone Control Status")
    print("=" * 50)

    # ADB connectivity
    result = shell("echo OK")
    print(f"ADB Connection: {'✅ Connected' if 'OK' in result else '❌ Not connected'}")

    # Phone info
    model = shell("getprop ro.product.model")
    android_ver = shell("getprop ro.build.version.release")
    sdk = shell("getprop ro.build.version.sdk")
    print(f"Device: {model} (Android {android_ver} / SDK {sdk})")

    # Default apps
    default_sms = get_role_holder("android.app.role.SMS")
    default_dialer = get_role_holder("android.app.role.DIALER")
    print(f"Default SMS app: {default_sms}")
    print(f"Default Dialer: {default_dialer}")

    # SMS gateway permissions
    perms_out = shell(f"dumpsys package {SMS_GATEWAY_PKG} 2>/dev/null | grep -E 'SEND_SMS|RECEIVE_SMS|READ_SMS' | grep granted=true")
    sms_perms_ok = "SEND_SMS" in perms_out and "RECEIVE_SMS" in perms_out
    print(f"SMS Gateway permissions: {'✅ Granted' if sms_perms_ok else '❌ Missing'}")

    # Port tunnels
    forwards = adb("forward", "--list")
    reverses = adb("reverse", "--list")
    port_forward_ok = str(MAC_GATEWAY_PORT) in forwards
    port_reverse_ok = str(MAC_WEBHOOK_PORT) in reverses
    print(f"Port forward (phone:{SMS_GATEWAY_PORT}→Mac:{MAC_GATEWAY_PORT}): {'✅' if port_forward_ok else '❌'}")
    print(f"Port reverse (Mac:{MAC_WEBHOOK_PORT}→phone:{MAC_WEBHOOK_PORT}): {'✅' if port_reverse_ok else '❌'}")

    # Gateway app reachability
    try:
        import requests
        resp = requests.get(f"http://localhost:{MAC_GATEWAY_PORT}/health",
                           auth=("sms", "smspass1"), timeout=3)
        gw_ok = resp.status_code == 200
        print(f"SMS Gateway API: {'✅ Reachable' if gw_ok else '❌ Unreachable'}")
    except Exception:
        print(f"SMS Gateway API: ❌ Unreachable (tunnel may be down)")

    # Webhook
    try:
        import requests
        resp = requests.get(f"http://localhost:{MAC_GATEWAY_PORT}/webhooks",
                           auth=("sms", "smspass1"), timeout=3)
        if resp.status_code == 200:
            webhooks = resp.json()
            wh_url = f"http://127.0.0.1:{MAC_WEBHOOK_PORT}/webhook/sms"
            wh_ok = any(w.get("url") == wh_url for w in webhooks)
            print(f"Webhook registered: {'✅' if wh_ok else '❌'} ({len(webhooks)} total)")
        else:
            print(f"Webhooks: ❌ API error {resp.status_code}")
    except Exception:
        print(f"Webhooks: ❌ Could not check")

    print()


def full_setup():
    """Run full phone setup for phone-bridge."""
    print("\n🚀 Phone Bridge — Full Setup")
    print("=" * 50)

    print("\n1. Checking ADB connection...")
    result = shell("echo OK")
    if "OK" not in result:
        print("❌ Phone not connected via ADB. Connect USB cable.")
        sys.exit(1)
    print(f"   ✅ {shell('getprop ro.product.model')}")

    print("\n2. Granting SMS permissions to gateway app...")
    setup_sms_gateway_permissions()

    print("\n3. Setting up port tunnels...")
    setup_port_tunnels()

    print("\n4. Registering inbound webhook...")
    time.sleep(1)
    register_webhook()

    print("\n5. Verifying SMS gateway API...")
    try:
        import requests
        resp = requests.get(f"http://localhost:{MAC_GATEWAY_PORT}/health",
                           auth=("sms", "smspass1"), timeout=5)
        data = resp.json()
        print(f"   ✅ Gateway healthy: v{data.get('version')} | Battery: {data.get('checks',{}).get('battery:level',{}).get('observedValue','?')}%")
    except Exception as e:
        print(f"   ❌ Gateway unreachable: {e}")
        print(f"   Make sure the SMS Gateway app is open and the Local Server is ON")

    print("\n✅ Setup complete!")
    print(f"   - Send SMS to Android via: POST http://localhost:{MAC_GATEWAY_PORT}/messages")
    print(f"   - Inbound SMS → webhook → Mac port {MAC_WEBHOOK_PORT}/webhook/sms")
    print()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "setup":
        full_setup()
    elif cmd == "status":
        get_status()
    elif cmd == "grant-sms":
        print("Granting SMS permissions...")
        setup_sms_gateway_permissions()
    elif cmd == "tunnel":
        print("Setting up port tunnels...")
        setup_port_tunnels()
    elif cmd == "webhook":
        print("Registering webhook...")
        register_webhook()
    elif cmd == "setting":
        # python3 phone_control.py setting get secure sms_default_application
        # python3 phone_control.py setting put global airplane_mode_on 1
        if len(sys.argv) >= 5:
            action, ns, key = sys.argv[2], sys.argv[3], sys.argv[4]
            if action == "get":
                print(get_setting(ns, key))
            elif action == "put" and len(sys.argv) >= 6:
                print(put_setting(ns, key, sys.argv[5]))
    elif cmd == "shell":
        # Run arbitrary shell command: python3 phone_control.py shell "ls /sdcard"
        print(shell(" ".join(sys.argv[2:])))
    else:
        print(f"Unknown command: {cmd}")
        print("Commands: setup, status, grant-sms, tunnel, webhook, setting, shell")
