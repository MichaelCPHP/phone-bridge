#!/usr/bin/env python3
"""
Phone Bridge — Local STT via whisper-cpp (Homebrew, Metal-accelerated)
Fallback chain: whisper-cpp → whisper CLI → Voicebox

whisper-cpp: ~420ms latency (tiny.en), Apple Silicon optimized
whisper CLI: ~150ms latency (tiny.en), Python-based fallback
Voicebox:    slow (~3s+), but provides transcription if others fail
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
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny.en")
STT_TIMEOUT   = int(os.getenv("STT_TIMEOUT_SEC", "30"))

# whisper-cpp tiny.en model path (downloaded by brew or manually)
WHISPER_CPP_MODEL = os.path.expanduser("~/.cache/whisper-cpp/ggml-tiny.en.bin")


# ─── Attempt 1: whisper-cpp (fastest on Apple Silicon) ──────────────────────

async def transcribe_audio_file(audio_path: str) -> str:
    """
    Transcribe using the fastest available method.
    Priority: whisper-cpp → whisper CLI → Voicebox
    """
    path = Path(audio_path)
    if not path.exists():
        log.error(f"Audio file not found: {audio_path}")
        return ""

    # Try whisper-cpp first (fastest on Apple Silicon, ~420ms)
    try:
        return await _transcribe_whisper_cpp(path)
    except Exception as e:
        log.debug(f"whisper-cpp failed ({e}), trying whisper CLI")

    # Fallback 2: whisper CLI (Python, ~150ms)
    try:
        return await _transcribe_whisper_cli(path)
    except Exception as e:
        log.debug(f"whisper CLI failed ({e}), trying Voicebox")

    # Fallback 3: Voicebox (slowest but most reliable if API available)
    try:
        return await _transcribe_voicebox(path)
    except Exception as e:
        log.error(f"All STT methods failed: {e}")
        return ""


async def _transcribe_whisper_cpp(path: Path) -> str:
    """Run whisper-cpp CLI (Homebrew binary)."""
    if not Path(WHISPER_CPP_MODEL).exists():
        raise FileNotFoundError(
            f"whisper-cpp model not found at {WHISPER_CPP_MODEL}. "
            "Download: curl -L https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin "
            f"-o {WHISPER_CPP_MODEL}"
        )

    cmd = [
        "whisper-cli",
        "-m", WHISPER_CPP_MODEL,
        "-f", str(path),
        "--no-prints",
        "--gpu-devices", "",  # CPU-only to avoid Metal initialization issues
    ]

    log.debug(f"[whisper-cpp] {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=STT_TIMEOUT)

    if proc.returncode != 0:
        err = stderr.decode()[:200]
        raise RuntimeError(f"whisper-cpp failed: {err}")

    # whisper-cpp outputs the transcript to stdout
    text = stdout.decode().strip()
    log.info(f"[whisper-cpp] '{path.name}' → '{text[:80]}'")
    return text


async def _transcribe_whisper_cli(path: Path) -> str:
    """Fallback: native whisper CLI (openai-whisper via Homebrew)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "whisper",
            str(path),
            "--model", WHISPER_MODEL,
            "--output_format", "txt",
            "--output_dir", tmpdir,
            "--language", "en",
            "--fp16", "False",
        ]
        log.debug(f"[whisper-cli] running")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=STT_TIMEOUT)

        if proc.returncode != 0:
            raise RuntimeError(f"whisper CLI failed: {stderr.decode()[:200]}")

        out_file = Path(tmpdir) / (path.stem + ".txt")
        if out_file.exists():
            text = out_file.read_text().strip()
            log.info(f"[whisper-cli] '{path.name}' → '{text[:80]}'")
            return text

        raise RuntimeError("whisper CLI ran but produced no output file")


async def _transcribe_voicebox(path: Path) -> str:
    """Fallback: Voicebox /transcribe endpoint (slow but reliable)."""
    async with httpx.AsyncClient(timeout=STT_TIMEOUT) as client:
        with open(path, "rb") as f:
            files = {"audio": (path.name, f, _mime_type(path))}
            resp = await client.post(f"{VOICEBOX_URL}/transcribe", files=files)
        resp.raise_for_status()
        data = resp.json()

    text = data.get("text", "").strip()
    log.info(f"[voicebox-stt] '{path.name}' → '{text[:80]}'")
    return text


def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4"}.get(ext[1:], "audio/wav")


# ─── Sync wrapper ─────────────────────────────────────────────────────────────

def transcribe_sync(audio_path: str) -> str:
    """Synchronous wrapper for Asterisk AGI scripts."""
    return asyncio.run(transcribe_audio_file(audio_path))


# ─── Health checks ────────────────────────────────────────────────────────────

def whisper_cpp_available() -> bool:
    return subprocess.run(["which", "whisper-cli"], capture_output=True).returncode == 0


def whisper_available() -> bool:
    return subprocess.run(["which", "whisper"], capture_output=True).returncode == 0


def voicebox_healthy() -> bool:
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

    print(f"whisper-cpp available: {whisper_cpp_available()}")
    print(f"whisper CLI available: {whisper_available()}")
    print(f"Voicebox available: {voicebox_healthy()}")

    if len(sys.argv) > 1:
        result = asyncio.run(transcribe_audio_file(sys.argv[1]))
        print(f"Transcript: {result}")
    else:
        print("Usage: python3 stt_voicebox.py <audio_file.wav>")
