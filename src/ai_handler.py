#!/usr/bin/env python3
"""
Phone Bridge — AI conversation layer

AI backend: Ollama (local, no API key) at localhost:11434
STT:        stt_voicebox.py (whisper-cpp primary)
TTS:        tts_kokoro.py (Kokoro 82M, local, no API key)

Flow:
  SMS:   sms_gateway webhook → handle_sms() → sms_gateway send
  Voice: Asterisk AGI → stt_voicebox → handle_call_turn() → tts_kokoro → Asterisk
"""

import os, sys, logging, requests
from pathlib import Path

log = logging.getLogger("ai-handler")

OLLAMA_URL  = os.getenv("OLLAMA_URL",  "http://localhost:11434")
AI_MODEL    = os.getenv("AI_MODEL",    "llama3.2")
MAX_TOKENS  = int(os.getenv("AI_MAX_TOKENS", "256"))

SYSTEM_PROMPT = """You are a helpful AI assistant answering calls and SMS on behalf of the phone owner.
Be concise and natural. 1-3 sentences for SMS, conversational length for calls.
If asked who you are: say you're an AI assistant managing calls and messages."""


def respond(user_message: str, context: str = "sms", history: list = None) -> str:
    """Generate an AI response via Ollama."""
    messages = list(history or [])
    system = SYSTEM_PROMPT
    if context == "voice":
        system += "\nYou are speaking aloud — no markdown, no lists, plain conversational sentences only."

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system}] + messages + [
            {"role": "user", "content": user_message}
        ],
        "stream": False,
        "options": {"num_predict": MAX_TOKENS},
    }

    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=45)
    r.raise_for_status()
    reply = r.json()["message"]["content"].strip()
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


# ─── Voice / Asterisk AGI handler ────────────────────────────────────────────

_call_history: dict = {}

def handle_call_turn(transcript: str, call_id: str) -> str:
    """Process one voice turn. Called after each utterance."""
    history = _call_history.get(call_id, [])
    reply = respond(transcript, context="voice", history=history)
    history += [{"role": "user", "content": transcript}, {"role": "assistant", "content": reply}]
    _call_history[call_id] = history[-10:]
    return reply


# ─── Asterisk AGI entry point ─────────────────────────────────────────────────

def run_agi():
    """
    Called as Asterisk AGI script. Reads audio, transcribes, responds, speaks.

    extensions.conf:
      exten => _X.,1,AGI(ai_handler.py,agi)
    """
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
        print(f"AI backend : {OLLAMA_URL}")
        print(f"Model      : {AI_MODEL}")
        print()
        try:
            reply = handle_sms("+15555550100", "Hey, are you free for a call tomorrow?")
            print(f"SMS reply  : {reply}")
        except Exception as e:
            print(f"❌ SMS test failed: {e}")
        try:
            reply = handle_call_turn("Hi, who am I speaking with?", "test-001")
            print(f"Voice reply: {reply}")
        except Exception as e:
            print(f"❌ Voice test failed: {e}")
