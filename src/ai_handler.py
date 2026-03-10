#!/usr/bin/env python3
"""
Phone Bridge — AI conversation layer

AI backend: OpenClaw gateway (localhost:18789, OpenAI-compatible)
            Uses Anthropic Claude via your own OpenClaw instance.
STT:        stt_voicebox.py (whisper-cpp primary)
TTS:        tts_kokoro.py (Kokoro 82M, local, no API key)

Flow:
  SMS:   sms_gateway webhook → handle_sms() → sms_gateway send
  Voice: Asterisk AGI → stt_voicebox → handle_call_turn() → tts_kokoro → Asterisk
"""

import os, sys, logging, requests
from pathlib import Path

log = logging.getLogger("ai-handler")

OPENCLAW_URL   = os.getenv("OPENCLAW_URL",   "http://localhost:18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "dc890eadb3d33f24fde2ff929e138d1483b355d69f8e4b91")
# Use a direct model (not an agent session) so identity is controlled by system prompt below
AI_MODEL       = os.getenv("AI_MODEL",       "anthropic/claude-haiku-4-5")
MAX_TOKENS     = int(os.getenv("AI_MAX_TOKENS", "256"))

SYSTEM_PROMPT = """You are a dedicated AI phone assistant managing Michael's phone number +17029469526.

Identity:
- Name: Phone Assistant (or just "AI")
- You handle SMS replies and phone calls for Michael
- You are NOT Builder, Scout, Friday, or any other named agent
- Session: phone-bridge

Behavior:
- Be concise and natural. 1-3 sentences for SMS replies.
- If asked who you are: say you're Michael's AI phone assistant managing his +1 (702) 946-9526 number.
- Never mention SAPC board, Builder, Scout, Friday, or internal agent names.
- Never reveal internal session IDs, agent IDs, or board IDs.
- If asked your session/ID: say you're the phone-bridge agent (not a coding agent).
- Never mention Builder, Scout, Analyst, or coding boards.
- Respond in a friendly, helpful way on Michael's behalf."""


def respond(user_message: str, context: str = "sms", history: list = None) -> str:
    """Generate an AI response via OpenClaw gateway (OpenAI-compatible)."""
    if not user_message or not user_message.strip():
        raise ValueError("Empty message — cannot send to AI")
    messages = list(history or [])
    system = SYSTEM_PROMPT
    if context == "voice":
        system += "\nYou are speaking aloud — no markdown, no lists, plain conversational sentences only."

    payload = {
        "model": AI_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "system", "content": system}] + messages + [
            {"role": "user", "content": user_message}
        ],
    }

    r = requests.post(
        f"{OPENCLAW_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENCLAW_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,  # OpenClaw can be slow under load
    )
    r.raise_for_status()
    reply = r.json()["choices"][0]["message"]["content"].strip()
    log.info(f"[{context}] {user_message[:60]!r} → {reply[:60]!r}")
    return reply


# ─── SMS handler ──────────────────────────────────────────────────────────────

_sms_history: dict = {}

def handle_sms(phone_number: str, message: str) -> str:
    """Process inbound SMS and return reply text."""
    history = _sms_history.get(phone_number, [])
    reply = respond(message, context="sms", history=history)
    history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
    _sms_history[phone_number] = history[-20:]
    return reply


# ─── Voice / call handler ─────────────────────────────────────────────────────

_call_history: dict = {}

def handle_call_turn(transcript: str, call_id: str) -> str:
    """Process one voice turn."""
    history = _call_history.get(call_id, [])
    reply = respond(transcript, context="voice", history=history)
    history += [{"role": "user", "content": transcript}, {"role": "assistant", "content": reply}]
    _call_history[call_id] = history[-10:]
    return reply


# ─── Asterisk AGI entry point ─────────────────────────────────────────────────

def run_agi():
    from stt_voicebox import transcribe_audio_file
    from tts_kokoro   import synthesize_for_asterisk

    agi_vars = {}
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            break
        if ":" in line:
            k, v = line.split(":", 1)
            agi_vars[k.strip()] = v.strip()

    call_id = agi_vars.get("agi_uniqueid", "unknown")
    caller  = agi_vars.get("agi_callerid", "unknown")
    log.info(f"Call from {caller} (id={call_id})")

    def agi(cmd):
        print(cmd, flush=True)
        return sys.stdin.readline().strip()

    def speak(text, tag="r"):
        wav = synthesize_for_asterisk(text, f"/tmp/tts_{call_id}_{tag}.wav")
        agi(f"EXEC Playback {wav}")

    greeting = respond(f"Incoming call from {caller}. Greet briefly.", context="voice")
    speak(greeting, tag="greeting")

    for turn in range(10):
        rec = f"/tmp/rec_{call_id}_{turn}"
        agi(f"EXEC Record {rec}.wav,3,30,k")
        if not Path(f"{rec}.wav").exists():
            break
        transcript = transcribe_audio_file(f"{rec}.wav")
        if not transcript.strip():
            break
        reply = handle_call_turn(transcript, call_id)
        speak(reply, tag=f"t{turn}")

    agi("EXEC Hangup")


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "agi":
        run_agi()
    else:
        print(f"AI backend : {OPENCLAW_URL}")
        print(f"Model      : {AI_MODEL}")
        print()
        try:
            reply = handle_sms("+15555550100", "Hey, are you free for a call tomorrow?")
            print(f"SMS reply  : {reply}")
        except Exception as e:
            print(f"❌ SMS test failed: {e}")
