#!/usr/bin/env python3
"""
Phone Bridge — Local STT via Voicebox /transcribe (Whisper 0.6B)
Replaces stt_deepgram.py — zero external API, zero cost, zero rate limits.

Voicebox endpoint: http://localhost:17493/transcribe
Fallback: native whisper CLI (openai-whisper installed via Homebrew)
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

log = logging.getLogger("stt-voicebox")

VOICEBOX_URL  = os.getenv("VOICEBOX_URL", "http://localhost:17493")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny.en")   # tiny.en | base | large-v3-turbo
STT_TIMEOUT   = int(os.getenv("STT_TIMEOUT_SEC", "30"))


# ─── Primary: Voicebox /transcribe ───────────────────────────────────────────

async def transcribe_audio_file(audio_path: str) -> str:
    """
    Transcribe a WAV/MP3 file using Voicebox's local Whisper endpoint.
    Falls back to whisper CLI if Voicebox is unavailable.

    Args:
        audio_path: Path to audio file (WAV 16kHz mono preferred for Asterisk)

    Returns:
        Transcribed text string (empty string if nothing detected)
    """
    path = Path(audio_path)
    if not path.exists():
        log.error(f"Audio file not found: {audio_path}")
        return ""

    # Try Voicebox first
    try:
        return await _transcribe_voicebox(path)
    except Exception as e:
        log.warning(f"Voicebox STT failed ({e}), falling back to whisper CLI")
        return await _transcribe_whisper_cli(path)


async def _transcribe_voicebox(path: Path) -> str:
    """POST audio to Voicebox /transcribe endpoint."""
    async with httpx.AsyncClient(timeout=STT_TIMEOUT) as client:
        with open(path, "rb") as f:
            files = {"audio": (path.name, f, _mime_type(path))}
            resp = await client.post(f"{VOICEBOX_URL}/transcribe", files=files)
        resp.raise_for_status()
        data = resp.json()

    # Voicebox returns {"text": "...", "language": "en", ...}
    text = data.get("text", "").strip()
    log.info(f"[voicebox-stt] '{path.name}' → '{text[:80]}'")
    return text


async def _transcribe_whisper_cli(path: Path) -> str:
    """Fallback: run the native whisper CLI (Homebrew openai-whisper)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "whisper",
            str(path),
            "--model", WHISPER_MODEL,
            "--output_format", "txt",
            "--output_dir", tmpdir,
            "--language", "en",
            "--fp16", "False",   # required on CPU/MPS
        ]
        log.info(f"[whisper-cli] running: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=STT_TIMEOUT)

        if proc.returncode != 0:
            log.error(f"whisper CLI failed: {stderr.decode()[:200]}")
            return ""

        # whisper writes <filename>.txt
        out_file = Path(tmpdir) / (path.stem + ".txt")
        if out_file.exists():
            text = out_file.read_text().strip()
            log.info(f"[whisper-cli] '{path.name}' → '{text[:80]}'")
            return text

        log.warning("whisper CLI ran but no output file found")
        return ""


def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4"}.get(ext[1:], "audio/wav")


# ─── Sync wrapper for Asterisk AGI ───────────────────────────────────────────

def transcribe_sync(audio_path: str) -> str:
    """Synchronous wrapper — use in non-async contexts (e.g. Asterisk AGI scripts)."""
    return asyncio.run(transcribe_audio_file(audio_path))


# ─── Health check ─────────────────────────────────────────────────────────────

def voicebox_healthy() -> bool:
    """Returns True if Voicebox STT endpoint is reachable."""
    try:
        import requests
        r = requests.get(f"{VOICEBOX_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ─── Test ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1:
        result = asyncio.run(transcribe_audio_file(sys.argv[1]))
        print(f"Transcript: {result}")
    else:
        print(f"Voicebox healthy: {voicebox_healthy()}")
        print("Usage: python3 stt_voicebox.py <audio_file.wav>")
