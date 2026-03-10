#!/usr/bin/env python3
"""
Phone Bridge — Local TTS via Voicebox /generate (Jarvis voice profile)
Replaces tts_elevenlabs.py — zero external API, zero cost, zero rate limits.

Voicebox endpoint: http://localhost:17493/generate
Fallback: macOS `say` command (instant, no setup required)

Jarvis profile ID: 78a6efb2-82b3-4c0e-86a0-e3ab28c4b7c1
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
import requests

log = logging.getLogger("tts-voicebox")

VOICEBOX_URL       = os.getenv("VOICEBOX_URL", "http://localhost:17493")
VOICEBOX_PROFILE   = os.getenv("VOICEBOX_PROFILE_ID", "78a6efb2-82b3-4c0e-86a0-e3ab28c4b7c1")
TTS_TIMEOUT        = int(os.getenv("TTS_TIMEOUT_SEC", "30"))
TTS_FALLBACK_VOICE = os.getenv("TTS_FALLBACK_VOICE", "Samantha")  # macOS say voice


# ─── Primary: Voicebox /generate ─────────────────────────────────────────────

def synthesize(text: str, output_path: str) -> bool:
    """
    Synthesize speech from text using Voicebox Jarvis profile.
    Falls back to macOS `say` if Voicebox is unavailable or times out.

    Args:
        text:        Text to speak
        output_path: Path to write output WAV file

    Returns:
        True on success, False on failure
    """
    # Try Voicebox
    try:
        return _synthesize_voicebox(text, output_path)
    except Exception as e:
        log.warning(f"Voicebox TTS failed ({e}), falling back to macOS say")
        return _synthesize_say(text, output_path)


def _synthesize_voicebox(text: str, output_path: str) -> bool:
    """POST to Voicebox /generate and save audio response."""
    payload = {
        "text": text,
        "profile_id": VOICEBOX_PROFILE,
    }

    resp = requests.post(
        f"{VOICEBOX_URL}/generate",
        json=payload,
        timeout=TTS_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")

    if "audio" in content_type:
        # Direct audio bytes in response
        Path(output_path).write_bytes(resp.content)
        log.info(f"[voicebox-tts] '{text[:60]}' → {output_path} ({len(resp.content)} bytes)")
        return True

    # JSON response with generation_id — fetch audio separately
    try:
        data = resp.json()
        generation_id = data.get("id") or data.get("generation_id")
        if not generation_id:
            raise ValueError(f"No generation_id in response: {data}")

        audio_resp = requests.get(
            f"{VOICEBOX_URL}/audio/{generation_id}",
            timeout=TTS_TIMEOUT,
        )
        audio_resp.raise_for_status()
        Path(output_path).write_bytes(audio_resp.content)
        log.info(f"[voicebox-tts] '{text[:60]}' → {output_path} ({len(audio_resp.content)} bytes)")
        return True
    except Exception as e:
        raise RuntimeError(f"Could not fetch Voicebox audio: {e}") from e


def _synthesize_say(text: str, output_path: str) -> bool:
    """
    Fallback: macOS `say` command.
    Writes AIFF → converts to WAV via afconvert for Asterisk compatibility.
    """
    # say can write AIFF directly
    aiff_path = output_path.replace(".wav", ".aiff")
    result = subprocess.run(
        ["say", "-v", TTS_FALLBACK_VOICE, "-o", aiff_path, text],
        capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        log.error(f"say failed: {result.stderr.decode()}")
        return False

    # Convert AIFF → WAV (8kHz mono for Asterisk telephony)
    conv = subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@8000", "-c", "1", aiff_path, output_path],
        capture_output=True, timeout=10,
    )
    # Clean up AIFF
    Path(aiff_path).unlink(missing_ok=True)

    if conv.returncode != 0:
        # afconvert failed — just use the AIFF (Asterisk can sometimes play it)
        log.warning("afconvert failed, keeping raw AIFF as fallback")
        Path(aiff_path).rename(output_path)

    log.info(f"[say-tts] '{text[:60]}' → {output_path}")
    return True


# ─── Asterisk-compatible synthesize ──────────────────────────────────────────

def synthesize_for_asterisk(text: str, output_path: str) -> str:
    """
    Synthesize and ensure output is in a format Asterisk can play.
    Returns the path to the audio file (may differ from output_path if format adjusted).
    Called by ai_handler.py's run_agi().
    """
    # Ensure .wav extension
    if not output_path.endswith(".wav"):
        output_path = output_path + ".wav"

    if synthesize(text, output_path):
        return output_path

    raise RuntimeError(f"All TTS methods failed for: {text[:60]}")


# ─── Async wrapper ────────────────────────────────────────────────────────────

async def synthesize_async(text: str, output_path: str) -> bool:
    """Async wrapper — runs synthesis in thread pool to avoid blocking event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, synthesize, text, output_path)


# ─── Health check ─────────────────────────────────────────────────────────────

def voicebox_healthy() -> bool:
    try:
        r = requests.get(f"{VOICEBOX_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def say_available() -> bool:
    return subprocess.run(["which", "say"], capture_output=True).returncode == 0


# ─── Test ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    text = sys.argv[1] if len(sys.argv) > 1 else "Hello, this is a phone bridge test."
    out  = "/tmp/tts-test.wav"

    print(f"Voicebox healthy: {voicebox_healthy()}")
    print(f"say available:    {say_available()}")
    print(f"Synthesizing: '{text}'")

    ok = synthesize(text, out)
    if ok:
        print(f"✓ Audio written to {out}")
        # Play it back
        subprocess.run(["afplay", out], check=False)
    else:
        print("✗ Synthesis failed")
        sys.exit(1)
