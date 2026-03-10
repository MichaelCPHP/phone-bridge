#!/usr/bin/env python3
"""
Phone Bridge — Deepgram STT pipeline (Issue #10)
Streaming speech-to-text for real-time AI call handling.
"""

import os
import asyncio
import logging
import httpx
from typing import AsyncIterator

log = logging.getLogger("stt-deepgram")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL   = os.getenv("DEEPGRAM_MODEL", "nova-2")
DEEPGRAM_LANG    = os.getenv("DEEPGRAM_LANG", "en-US")

# Deepgram streaming endpoint
DEEPGRAM_STREAM_URL = (
    f"wss://api.deepgram.com/v1/listen"
    f"?model={DEEPGRAM_MODEL}"
    f"&language={DEEPGRAM_LANG}"
    f"&encoding=linear16"
    f"&sample_rate=8000"   # Asterisk default
    f"&channels=1"
    f"&interim_results=false"
    f"&endpointing=300"    # 300ms silence = end of utterance
)


async def transcribe_audio_file(audio_path: str) -> str:
    """
    Transcribe a WAV/PCM audio file via Deepgram batch API.
    For pre-recorded audio (testing, voicemail).
    """
    if not DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY not set")

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.deepgram.com/v1/listen?model={DEEPGRAM_MODEL}&language={DEEPGRAM_LANG}",
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "audio/wav",
            },
            content=audio_data,
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
        transcript = (
            result.get("results", {})
                  .get("channels", [{}])[0]
                  .get("alternatives", [{}])[0]
                  .get("transcript", "")
        )
        log.info(f"Transcribed: '{transcript[:80]}'")
        return transcript


async def transcribe_stream(audio_chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """
    Stream audio chunks to Deepgram, yield transcript segments.
    Used for live call audio from Asterisk.
    Requires: pip install websockets
    """
    try:
        import websockets
    except ImportError:
        raise ImportError("pip install websockets required for streaming STT")

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    async with websockets.connect(DEEPGRAM_STREAM_URL, extra_headers=headers) as ws:
        async def sender():
            async for chunk in audio_chunks:
                await ws.send(chunk)
            await ws.send(b"")  # EOF signal

        async def receiver():
            async for message in ws:
                import json
                data = json.loads(message)
                if data.get("type") == "Results":
                    transcript = (
                        data.get("channel", {})
                            .get("alternatives", [{}])[0]
                            .get("transcript", "")
                    )
                    if transcript.strip():
                        yield transcript

        sender_task = asyncio.create_task(sender())
        async for transcript in receiver():
            yield transcript
        await sender_task


def test_connection() -> bool:
    """Verify Deepgram API key is valid."""
    import requests
    if not DEEPGRAM_API_KEY:
        log.error("DEEPGRAM_API_KEY not set")
        return False
    try:
        resp = requests.get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
            timeout=5
        )
        ok = resp.status_code == 200
        log.info(f"Deepgram connection: {'✅' if ok else '❌'} ({resp.status_code})")
        return ok
    except Exception as e:
        log.error(f"Deepgram connection failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not DEEPGRAM_API_KEY:
        print("❌ Set DEEPGRAM_API_KEY environment variable")
    else:
        ok = test_connection()
        print(f"Deepgram STT: {'✅ Ready' if ok else '❌ Check API key'}")
