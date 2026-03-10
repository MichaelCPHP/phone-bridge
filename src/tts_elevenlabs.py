#!/usr/bin/env python3
"""
Phone Bridge — ElevenLabs TTS pipeline (Issue #11)
Text-to-speech for AI voice responses in calls.
"""

import os
import logging
import requests
import tempfile
from pathlib import Path

log = logging.getLogger("tts-elevenlabs")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel (default)
ELEVENLABS_MODEL    = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2")  # Lowest latency model

ELEVENLABS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"


def synthesize(text: str, output_path: str | None = None) -> str:
    """
    Convert text to speech via ElevenLabs.
    Returns path to WAV/MP3 file.
    Latency: ~200-400ms for short phrases with turbo model.
    """
    if not ELEVENLABS_API_KEY:
        raise ValueError("ELEVENLABS_API_KEY not set")

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    resp = requests.post(ELEVENLABS_URL, headers=headers, json=body, timeout=15)
    resp.raise_for_status()

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        output_path = tmp.name

    with open(output_path, "wb") as f:
        f.write(resp.content)

    log.info(f"TTS generated: '{text[:60]}' → {output_path} ({len(resp.content)} bytes)")
    return output_path


def synthesize_for_asterisk(text: str, output_path: str) -> str:
    """
    Synthesize speech and convert to Asterisk-compatible format (8kHz μ-law WAV).
    Requires: ffmpeg
    """
    mp3_path = synthesize(text)
    
    import subprocess
    result = subprocess.run([
        "ffmpeg", "-y", "-i", mp3_path,
        "-ar", "8000", "-ac", "1", "-f", "wav",
        "-acodec", "pcm_mulaw", output_path
    ], capture_output=True, timeout=10)
    
    Path(mp3_path).unlink(missing_ok=True)  # cleanup temp mp3
    
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr.decode()}")
    
    log.info(f"Asterisk audio ready: {output_path}")
    return output_path


def list_voices() -> list:
    """List available ElevenLabs voices."""
    resp = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json().get("voices", [])


def test_connection() -> bool:
    """Verify ElevenLabs API key is valid."""
    if not ELEVENLABS_API_KEY:
        log.error("ELEVENLABS_API_KEY not set")
        return False
    try:
        resp = requests.get(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            timeout=5
        )
        ok = resp.status_code == 200
        log.info(f"ElevenLabs connection: {'✅' if ok else '❌'} ({resp.status_code})")
        return ok
    except Exception as e:
        log.error(f"ElevenLabs connection failed: {e}")
        return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if not ELEVENLABS_API_KEY:
        print("❌ Set ELEVENLABS_API_KEY environment variable")
        sys.exit(1)
    
    if not test_connection():
        print("❌ ElevenLabs API key invalid")
        sys.exit(1)
    
    print("✅ ElevenLabs TTS ready")
    
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        path = synthesize(text, "/tmp/test-tts.mp3")
        print(f"Generated: {path}")
        print("Play with: afplay /tmp/test-tts.mp3")
