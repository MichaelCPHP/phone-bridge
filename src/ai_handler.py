#!/usr/bin/env python3
"""
Phone Bridge — AI conversation handler via local Ollama (llama3.2)
Replaces Claude/Anthropic API — zero external dependency, zero cost, zero rate limits.

Ollama endpoint: http://localhost:11434
Model: llama3.2 (2GB, already pulled)

Flow:
  SMS:   sms_gateway webhook → ai_handler → sms_gateway send
  Voice: Asterisk AGI → stt_voicebox → ai_handler → tts_voicebox → Asterisk
"""

import logging
import os
import sys
from typing import Optional

import requests

log = logging.getLogger("ai-handler")

OLLAMA_URL  = os.getenv("OLLAMA_URL", "http://localhost:11434")
AI_MODEL    = os.getenv("AI_MODEL", "llama3.2")
MAX_TOKENS  = int(os.getenv("AI_MAX_TOKENS", "256"))
AI_TIMEOUT  = int(os.getenv("AI_TIMEOUT_SEC", "30"))

SYSTEM_PROMPT = """You are a helpful AI assistant answering calls and SMS for the owner of this phone.
Be concise and natural — you are speaking or texting on their behalf.
Keep responses brief: 1-3 sentences for SMS, natural conversational length for calls.
If asked who you are: say you're an AI assistant helping manage calls and messages."""


# ─── Core completion ──────────────────────────────────────────────────────────

def respond(user_message: str, context: str = "sms", history: list | None = None) -> str:
    """
    Generate an AI response via local Ollama.

    Args:
        user_message: The incoming text (SMS or voice transcript)
        context:      "sms" or "voice" (affects tone/length)
        history:      Optional conversation history [{"role": ..., "content": ...}]

    Returns: AI response text
    """
    system = SYSTEM_PROMPT
    if context == "voice":
        system += "\nYou are speaking out loud — avoid markdown, bullet points, or lists. Keep it under 2 sentences."

    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "options": {
            "num_predict": MAX_TOKENS,
            "temperature": 0.7,
        },
    }

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=AI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["message"]["content"].strip()
        log.info(f"[{context}] '{user_message[:60]}' → '{reply[:60]}'")
        return reply

    except requests.exceptions.ConnectionError:
        log.error("Ollama not running — start with: ollama serve")
        return "I'm sorry, the AI service is temporarily unavailable."
    except requests.exceptions.Timeout:
        log.error(f"Ollama timeout after {AI_TIMEOUT}s")
        return "I'm sorry, the response took too long. Please try again."
    except Exception as e:
        log.error(f"Ollama error: {e}")
        return "I'm sorry, I encountered an error processing your message."


# ─── SMS handler ──────────────────────────────────────────────────────────────

# Simple in-memory conversation store (keyed by phone number)
_sms_history: dict[str, list] = {}

def handle_sms(phone_number: str, message: str) -> str:
    """Process inbound SMS and return reply text."""
    history = _sms_history.get(phone_number, [])
    reply = respond(message, context="sms", history=history)

    # Update history (keep last 10 turns = 20 entries)
    history.append({"role": "user",      "content": message})
    history.append({"role": "assistant", "content": reply})
    _sms_history[phone_number] = history[-20:]

    return reply


# ─── Voice / Asterisk AGI handler ─────────────────────────────────────────────

# Separate history store for active calls
_call_history: dict[str, list] = {}

def handle_call_turn(transcript: str, call_id: str) -> str:
    """
    Process a single voice turn during a call.
    Called by Asterisk AGI after each user utterance is transcribed.
    """
    history = _call_history.get(call_id, [])
    reply = respond(transcript, context="voice", history=history)

    history.append({"role": "user",      "content": transcript})
    history.append({"role": "assistant", "content": reply})
    _call_history[call_id] = history[-10:]

    return reply


# ─── Asterisk AGI script ──────────────────────────────────────────────────────

def run_agi():
    """
    Entry point when called as Asterisk AGI script.
    Reads audio from Asterisk, transcribes via Voicebox, gets AI reply, speaks it back.

    Usage in extensions.conf:
      exten => _X.,1,AGI(ai_handler.py)
    """
    import asyncio

    # AGI init — read Asterisk variables from stdin
    agi_vars = {}
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            break
        if ":" in line:
            key, val = line.split(":", 1)
            agi_vars[key.strip()] = val.strip()

    call_id = agi_vars.get("agi_uniqueid", "unknown")
    caller  = agi_vars.get("agi_callerid", "unknown")
    log.info(f"Call from {caller} (id: {call_id})")

    # Greeting
    greeting = respond(f"Incoming call from {caller}. Greet them briefly.", context="voice")

    try:
        from tts_voicebox import synthesize_for_asterisk
        greeting_wav = f"/tmp/greeting_{call_id}.wav"
        synthesize_for_asterisk(greeting, greeting_wav)
        print(f"EXEC Playback {greeting_wav}")
        sys.stdout.flush()
        sys.stdin.readline()  # wait for Asterisk result
    except Exception as e:
        log.error(f"TTS failed: {e}")
        print("EXEC SayAlpha hello")
        sys.stdout.flush()
        sys.stdin.readline()

    # Listen + respond loop
    for turn in range(10):  # max 10 turns per call
        # Record caller utterance
        rec_file = f"/tmp/rec_{call_id}_{turn}"
        print(f"EXEC Record {rec_file}.wav,3,30")  # 3s silence timeout, 30s max
        sys.stdout.flush()
        sys.stdin.readline()

        # Transcribe via Voicebox/Whisper
        try:
            from stt_voicebox import transcribe_audio_file
            transcript = asyncio.run(transcribe_audio_file(f"{rec_file}.wav"))
        except Exception as e:
            log.error(f"STT failed: {e}")
            break

        if not transcript.strip():
            log.info("Silence detected — ending call")
            break

        # AI response
        reply = handle_call_turn(transcript, call_id)

        # Speak reply
        try:
            from tts_voicebox import synthesize_for_asterisk
            reply_wav = f"/tmp/reply_{call_id}_{turn}.wav"
            synthesize_for_asterisk(reply, reply_wav)
            print(f"EXEC Playback {reply_wav}")
            sys.stdout.flush()
            sys.stdin.readline()
        except Exception as e:
            log.error(f"TTS failed: {e}")

    print("EXEC Hangup")
    sys.stdout.flush()

    # Cleanup temp files
    import glob
    for f in glob.glob(f"/tmp/*_{call_id}*.wav"):
        try:
            os.unlink(f)
        except Exception:
            pass


# ─── Health check ─────────────────────────────────────────────────────────────

def ollama_healthy() -> bool:
    """Returns True if Ollama is running and the model is available."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code != 200:
            return False
        models = [m["name"] for m in r.json().get("models", [])]
        return any(AI_MODEL in m for m in models)
    except Exception:
        return False


# ─── Test ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "agi":
        run_agi()
    else:
        print(f"Ollama healthy (model={AI_MODEL}): {ollama_healthy()}")

        if not ollama_healthy():
            print("❌ Start Ollama: ollama serve && ollama pull llama3.2")
            sys.exit(1)

        print("\nTesting SMS handler...")
        reply = handle_sms("+15555550100", "Hey, are you available for a meeting tomorrow at 2pm?")
        print(f"Reply: {reply}")

        print("\nTesting voice handler...")
        reply = handle_call_turn("Hi, who am I speaking with?", "test-call-001")
        print(f"Voice reply: {reply}")
