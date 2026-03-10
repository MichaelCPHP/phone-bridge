#!/usr/bin/env python3
"""
Phone Bridge — Kokoro TTS pipeline (Issue #15)
Local text-to-speech using Kokoro 82M model. No API key. No cloud calls.
No 'say' command. Docker and headless compatible.

Model: hexgrad/Kokoro-82M (~330MB, auto-downloads on first run)
Voices: af_heart (default), am_echo, af_sarah, am_adam
"""
import os, logging, tempfile
from pathlib import Path

log = logging.getLogger("tts-kokoro")

KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.0"))
ASTERISK_SAMPLE_RATE = 8000

_pipeline = None

def _get_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    try:
        from kokoro import KPipeline
    except ImportError:
        raise RuntimeError(
            "Kokoro not installed. Run: pip install kokoro soundfile resampy\n"
            "Model auto-downloads (~330MB) on first use."
        )
    log.info(f"Loading Kokoro pipeline (voice={KOKORO_VOICE})...")
    _pipeline = KPipeline(lang_code="a")
    log.info("Kokoro pipeline ready ✅")
    return _pipeline

def synthesize(text: str, output_path: str | None = None) -> str:
    """Synthesize text to a WAV file. Returns path to 24kHz WAV."""
    import soundfile as sf, numpy as np
    if not text or not text.strip():
        raise ValueError("Text must not be empty")
    pipeline = _get_pipeline()
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="kokoro_")
        os.close(fd)
    all_audio = []
    for _gs, _ps, audio in pipeline(text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, split_pattern=r"\n+"):
        if audio is not None and len(audio) > 0:
            all_audio.append(audio)
    if not all_audio:
        raise RuntimeError(f"Kokoro generated no audio for: {text[:60]!r}")
    combined = np.concatenate(all_audio)
    sf.write(output_path, combined, samplerate=24000)
    log.info(f"TTS: {len(combined)/24000:.2f}s → {output_path}")
    return output_path

def synthesize_for_asterisk(text: str, output_path: str | None = None) -> str:
    """Synthesize and resample to 8kHz mono PCM16 for Asterisk AGI."""
    import soundfile as sf, numpy as np
    hq = synthesize(text)
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="ast_")
        os.close(fd)
    data, sr = sf.read(hq)
    if sr != ASTERISK_SAMPLE_RATE:
        try:
            import resampy
            data = resampy.resample(data, sr, ASTERISK_SAMPLE_RATE)
        except ImportError:
            data = data[::sr // ASTERISK_SAMPLE_RATE]
    if data.ndim > 1:
        data = data.mean(axis=1)
    sf.write(output_path, data, samplerate=ASTERISK_SAMPLE_RATE, subtype="PCM_16")
    Path(hq).unlink(missing_ok=True)
    log.info(f"Asterisk WAV (8kHz): {output_path}")
    return output_path

def test_connection() -> bool:
    try:
        _get_pipeline()
        return True
    except Exception as e:
        log.error(f"Kokoro load failed: {e}")
        return False

if __name__ == "__main__":
    import sys, logging as _l
    _l.basicConfig(level=_l.INFO)
    text = " ".join(sys.argv[1:]) or "Hello, this is a test of the Kokoro text to speech system."
    if not test_connection():
        print("❌ Kokoro not available — run: pip install kokoro soundfile resampy")
        sys.exit(1)
    path = synthesize(text)
    print(f"✅ WAV (24kHz): {path}")
    ast_path = synthesize_for_asterisk(text)
    print(f"✅ WAV (8kHz Asterisk): {ast_path}")
