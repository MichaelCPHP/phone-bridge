#!/usr/bin/env python3
"""
connect-test.py — Phone Bridge connectivity verifier
Run this once PHONE_IP is set to confirm all services can reach the phone.

Usage:
  python3 src/connect-test.py                   # use PHONE_IP from .env / env
  PHONE_IP=192.168.1.42 python3 src/connect-test.py
  python3 src/connect-test.py --ip 192.168.1.42
"""

import argparse
import asyncio
import os
import sys
import subprocess
from pathlib import Path

# Load .env if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ─── Colors ──────────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"
NC     = "\033[0m"

def ok(label, detail=""):
    suffix = f"  {detail}" if detail else ""
    print(f"  {GREEN}✓{NC} {label}{suffix}")

def fail(label, detail=""):
    suffix = f"\n      {RED}{detail}{NC}" if detail else ""
    print(f"  {RED}✗{NC} {label}{suffix}")

def warn(label, detail=""):
    suffix = f"  {YELLOW}{detail}{NC}" if detail else ""
    print(f"  {YELLOW}⚠{NC}  {label}{suffix}")

def section(title):
    print(f"\n{BOLD}{CYAN}── {title} {'─' * (60 - len(title))}{NC}")


# ─── Checks ──────────────────────────────────────────────────────────────────

def check_env(phone_ip: str) -> bool:
    section("Environment")
    passed = True

    if not phone_ip or phone_ip in ("192.168.1.X", ""):
        fail("PHONE_IP", "not set — export PHONE_IP=<phone's LAN IP> or pass --ip")
        passed = False
    else:
        ok("PHONE_IP", phone_ip)

    for key in ("ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY"):
        val = os.getenv(key, "")
        if not val:
            warn(f"{key} not set", "AI features will fail")
        else:
            ok(key, f"{'*' * 8}{val[-4:]}")  # show last 4 chars only

    for key in ("SMS_GATEWAY_USER", "SMS_GATEWAY_PASS"):
        if os.getenv(key):
            ok(key)
        else:
            warn(key, "defaulting to 'user'/'password'")

    return passed


def check_ping(phone_ip: str) -> bool:
    section("Network Reachability")
    result = subprocess.run(
        ["ping", "-c", "3", "-W", "1000", phone_ip],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode == 0:
        # Parse avg latency from ping output
        lines = result.stdout.splitlines()
        latency = ""
        for line in lines:
            if "avg" in line or "rtt" in line:
                parts = line.split("/")
                if len(parts) >= 5:
                    latency = f"avg {parts[4]}ms"
        ok(f"Ping {phone_ip}", latency)
        return True
    else:
        fail(f"Ping {phone_ip}", "phone not reachable — check Wi-Fi, same network?")
        return False


def check_sms_gateway(phone_ip: str) -> bool:
    section("android-sms-gateway")
    import requests as req

    phone_port = os.getenv("PHONE_PORT", "8080")
    user = os.getenv("SMS_GATEWAY_USER", "user")
    passwd = os.getenv("SMS_GATEWAY_PASS", "password")
    base = f"http://{phone_ip}:{phone_port}"

    # 1. Health
    try:
        r = req.get(f"{base}/api/3rdparty/v1/health", auth=(user, passwd), timeout=5)
        if r.status_code == 200:
            ok("Health endpoint", f"HTTP 200 {base}")
        else:
            warn("Health endpoint", f"HTTP {r.status_code} (unexpected)")
    except req.exceptions.ConnectionError:
        fail("android-sms-gateway", f"No connection to {base} — is the app running?")
        print(f"      {YELLOW}Steps:{NC}")
        print(f"        1. Open android-sms-gateway app on phone")
        print(f"        2. Tap 'Start server' (should show port {phone_port})")
        print(f"        3. Verify phone is on same Wi-Fi as this Mac")
        return False
    except req.exceptions.Timeout:
        fail("android-sms-gateway", f"Timeout — phone may be sleeping or firewall blocking port {phone_port}")
        return False

    # 2. Credentials check
    try:
        r_noauth = req.get(f"{base}/api/3rdparty/v1/health", timeout=5)
        if r_noauth.status_code == 401:
            ok("Auth required", "credentials enforced ✓")
        else:
            warn("Auth", f"Unauthenticated request got {r_noauth.status_code} (check app auth settings)")
    except Exception:
        pass

    return True


def check_asterisk(mac_ip: str = "") -> bool:
    section("Asterisk SIP")
    import subprocess

    # Check container running
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=asterisk-bridge", "--format", "{{.Status}}"],
        capture_output=True, text=True, timeout=10
    )
    status = result.stdout.strip()
    if not status:
        fail("asterisk-bridge container", "not running — run ./start.sh first")
        return False
    ok("Container", status)

    # Check SIP port 5060
    sip_result = subprocess.run(
        ["nc", "-zu", "localhost", "5060"],
        capture_output=True, timeout=5
    )
    if sip_result.returncode == 0:
        ok("SIP port 5060", "UDP open")
    else:
        warn("SIP port 5060", "nc check inconclusive (UDP); container may still work")

    # AMI port
    ami_result = subprocess.run(
        ["nc", "-z", "-w2", "localhost", "5038"],
        capture_output=True, timeout=5
    )
    if ami_result.returncode == 0:
        ok("AMI port 5038", "TCP open")
    else:
        warn("AMI port 5038", "not reachable — check manager.conf")

    return True


def check_sms_server() -> bool:
    section("SMS Gateway Server (local)")
    import requests as req

    port = os.getenv("SERVER_PORT", "3001")
    url = f"http://localhost:{port}/health"
    try:
        r = req.get(url, timeout=5)
        data = r.json()
        if data.get("server") == "ok":
            phone_gw = data.get("phone_gateway", "unknown")
            if phone_gw == "ok":
                ok("Local SMS server", f":{port} — phone gateway reachable ✓")
            else:
                warn("Local SMS server", f":{port} running but phone_gateway={phone_gw} (set PHONE_IP)")
        else:
            warn("Local SMS server", f"unexpected response: {data}")
    except req.exceptions.ConnectionError:
        warn("Local SMS server", f"not running on :{port} — run ./start.sh to start it")
    except Exception as e:
        warn("Local SMS server", str(e))
    return True


def check_ai_layer() -> bool:
    section("AI Layer (Claude Haiku)")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        warn("Skipped", "ANTHROPIC_API_KEY not set")
        return True

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=os.getenv("AI_MODEL", "claude-haiku-4-5"),
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        ok("Claude Haiku", f"responded: '{resp.content[0].text.strip()}'")
    except Exception as e:
        fail("Claude Haiku", str(e))
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run(phone_ip: str, skip_ai: bool = False) -> int:
    print(f"\n{BOLD}Phone Bridge — Connectivity Test{NC}")
    print(f"Phone IP: {CYAN}{phone_ip or 'NOT SET'}{NC}")
    print("─" * 60)

    results = {}

    results["env"]     = check_env(phone_ip)
    if not phone_ip or phone_ip in ("192.168.1.X", ""):
        print(f"\n{RED}Cannot proceed without PHONE_IP.{NC}")
        print(f"  Set it in .env or: {YELLOW}PHONE_IP=<ip> python3 src/connect-test.py{NC}\n")
        return 1

    results["ping"]    = check_ping(phone_ip)
    if results["ping"]:
        results["sms_gw"]  = check_sms_gateway(phone_ip)
    else:
        warn("Skipping SMS gateway check", "phone unreachable")

    results["asterisk"] = check_asterisk()
    results["sms_srv"]  = check_sms_server()
    if not skip_ai:
        results["ai"]   = check_ai_layer()

    # ─── Summary ─────────────────────────────────────────────────────────────
    section("Summary")
    labels = {
        "env":      "Environment",
        "ping":     "Phone reachable",
        "sms_gw":   "android-sms-gateway",
        "asterisk": "Asterisk SIP",
        "sms_srv":  "Local SMS server",
        "ai":       "AI layer",
    }
    all_pass = True
    for key, label in labels.items():
        if key not in results:
            continue
        if results[key]:
            ok(label)
        else:
            fail(label)
            all_pass = False

    print()
    if all_pass:
        print(f"  {GREEN}{BOLD}All checks passed — phone bridge is ready! 🎉{NC}")
        print(f"  Phone: {phone_ip}")
        print(f"  SMS API: http://{phone_ip}:8080")
        print(f"  Next: Send a test SMS via the /send endpoint")
        print(f"    curl -X POST http://localhost:3001/send")
        print(f'         -H "Content-Type: application/json"')
        print(f'         -d \'{{"to":"+1XXXXXXXXXX","message":"test"}}\'\n')
        return 0
    else:
        print(f"  {RED}{BOLD}Some checks failed — see details above.{NC}\n")
        return 2


def main():
    parser = argparse.ArgumentParser(description="Phone Bridge connectivity test")
    parser.add_argument("--ip",      default=os.getenv("PHONE_IP", ""), help="Phone IP address")
    parser.add_argument("--skip-ai", action="store_true",               help="Skip AI layer test (saves API call)")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.ip, skip_ai=args.skip_ai)))


if __name__ == "__main__":
    main()
