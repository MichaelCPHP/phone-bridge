#!/usr/bin/env python3
"""
Phone Bridge — Claude Haiku AI layer (Issue #12)
Connects SMS gateway + voice pipeline into a unified AI conversation handler.

Flow:
  SMS:   sms_gateway webhook → ai_handler → sms_gateway send
  Voice: Asterisk AGI → stt_deepgram → ai_handler → tts_elevenlabs → Asterisk
"""

import os
import sys
import logging
import anthropic
from pathlib import Path

log = logging.getLogger("ai-handler")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL          = os.getenv("AI_MODEL", "claude-haiku-4-5")
MAX_TOKENS        = int(os.getenv("AI_MAX_TOKENS", "256"))  # Short for low latency

SYSTEM_PROMPT = """You are a helpful AI assistant answering calls and SMS for the owner of this phone.
Be concise and natural — you are speaking or texting on their behalf.
Keep responses brief: 1-3 sentences for SMS, natural conversational length for calls.
If asked who you are: say you're an AI assistant helping manage calls and messages."""


def get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def respond(user_message: str, context: str = "sms", history: list | None = None) -> str:
    """
    Generate an AI response via Claude Haiku.
    
    Args:
        user_message: The incoming text (SMS or voice transcript)
        context: "sms" or "voice" (affects tone/length)
        history: Optional conversation history [{"role": ..., "content": ...}]
    
    Returns: AI response text
    """
    client = get_client()
    
    messages = history or []
    messages.append({"role": "user", "content": user_message})
    
    system = SYSTEM_PROMPT
    if context == "voice":
        system += "\nYou are speaking out loud — avoid markdown, bullet points, or lists."
    
    resp = client.messages.create(
        model=AI_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    
    reply = resp.content[0].text.strip()
    log.info(f"[{context}] '{user_message[:60]}' → '{reply[:60]}'")
    return reply


# ─── SMS handler ─────────────────────────────────────────────────────────────

# Simple in-memory conversation store (keyed by phone number)
_sms_history: dict[str, list] = {}

def handle_sms(phone_number: str, message: str) -> str:
    """Process inbound SMS and return reply text."""
    history = _sms_history.get(phone_number, [])
    reply = respond(message, context="sms", history=history)
    
    # Update history (keep last 10 turns)
    history.append({"role": "user",      "content": message})
    history.append({"role": "assistant", "content": reply})
    _sms_history[phone_number] = history[-20:]  # 10 turns = 20 entries
    
    return reply


# ─── Voice / Asterisk AGI handler ────────────────────────────────────────────

def handle_call_turn(transcript: str, call_id: str) -> str:
    """
    Process a single voice turn during a call.
    Called by Asterisk AGI after each user utterance is transcribed.
    """
    history = _sms_history.get(f"call:{call_id}", [])  # reuse store, different key
    reply = respond(transcript, context="voice", history=history)
    
    history.append({"role": "user",      "content": transcript})
    history.append({"role": "assistant", "content": reply})
    _sms_history[f"call:{call_id}"] = history[-10:]
    
    return reply


# ─── Asterisk AGI script ──────────────────────────────────────────────────────

def run_agi():
    """
    Entry point when called as Asterisk AGI script.
    Reads audio from Asterisk, transcribes, gets AI reply, speaks it back.
    
    Usage in extensions.conf:
      exten => _X.,1,AGI(ai_call_handler.py)
    """
    import sys, os, subprocess, tempfile

    # AGI init — read Asterisk variables
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
        from tts_elevenlabs import synthesize_for_asterisk
        greeting_wav = f"/tmp/greeting_{call_id}.wav"
        synthesize_for_asterisk(greeting, greeting_wav)
        print(f"EXEC Playback {greeting_wav}")
        sys.stdout.flush()
        sys.stdin.readline()  # wait for result
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

        # Transcribe
        try:
            from stt_deepgram import transcribe_audio_file
            import asyncio
            transcript = asyncio.run(transcribe_audio_file(f"{rec_file}.wav"))
        except Exception as e:
            log.error(f"STT failed: {e}")
            break

        if not transcript.strip():
            break  # silence — end call

        # AI response
        reply = handle_call_turn(transcript, call_id)

        # Speak reply
        try:
            reply_wav = f"/tmp/reply_{call_id}_{turn}.wav"
            synthesize_for_asterisk(reply, reply_wav)
            print(f"EXEC Playback {reply_wav}")
            sys.stdout.flush()
            sys.stdin.readline()
        except Exception as e:
            log.error(f"TTS failed: {e}")

    print("EXEC Hangup")
    sys.stdout.flush()


# ─── Test ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "agi":
        run_agi()
    else:
        if not ANTHROPIC_API_KEY:
            print("❌ Set ANTHROPIC_API_KEY env var")
            sys.exit(1)
        
        print("Testing SMS handler...")
        reply = handle_sms("+15555550100", "Hey, are you available for a meeting tomorrow at 2pm?")
        print(f"Reply: {reply}")
        
        print("\nTesting voice handler...")
        reply = handle_call_turn("Hi, who am I speaking with?", "test-call-001")
        print(f"Voice reply: {reply}")
