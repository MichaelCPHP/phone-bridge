#!/usr/bin/env python3
"""
Phone Bridge — FastAGI Server (voice call handler)

Asterisk calls this via AGI(agi://192.168.1.235:4573/ai_handler)
This runs on the Mac alongside faster-whisper and Kokoro TTS.

NO external API keys needed:
  STT: faster-whisper (local, MPS accelerated)
  TTS: Kokoro 82M (local, ~100-300ms on Apple Silicon)
  AI:  OpenClaw gateway (localhost:18789)

Usage:
    python3 src/agi_server.py          # start FastAGI server on port 4573
"""

import os, sys, socket, threading, logging, tempfile, subprocess
from pathlib import Path

log = logging.getLogger("agi-server")

AGI_HOST = os.getenv("AGI_HOST", "0.0.0.0")
AGI_PORT = int(os.getenv("AGI_PORT", "4573"))

# Add src/ to path for imports
sys.path.insert(0, str(Path(__file__).parent))


class AGISession:
    """Handles a single Asterisk AGI connection."""

    def __init__(self, conn: socket.socket, addr):
        self.conn = conn
        self.addr = addr
        self.f_in  = conn.makefile("r")
        self.f_out = conn.makefile("w")
        self.vars  = {}

    def send(self, cmd: str) -> str:
        """Send AGI command, return response."""
        self.f_out.write(cmd + "\n")
        self.f_out.flush()
        resp = self.f_in.readline().strip()
        log.debug(f"AGI cmd: {cmd!r} → {resp!r}")
        return resp

    def read_agi_vars(self):
        """Read initial AGI variable block."""
        while True:
            line = self.f_in.readline().strip()
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                self.vars[k.strip()] = v.strip()
        log.info(f"Call from {self.vars.get('agi_callerid', 'unknown')} "
                 f"(id={self.vars.get('agi_uniqueid', 'unknown')})")

    def playback(self, wav_path: str):
        """Play a WAV file via Asterisk."""
        # Strip .wav extension — Asterisk adds it
        path = str(wav_path).removesuffix(".wav")
        self.send(f"EXEC Playback {path}")

    def record(self, path: str, silence_secs: int = 3, max_secs: int = 30) -> bool:
        """Record audio from caller. Returns True if file was created."""
        self.send(f"EXEC Record {path}.wav,{silence_secs},{max_secs},k")
        return Path(f"{path}.wav").exists()

    def hangup(self):
        self.send("EXEC Hangup")

    def handle(self):
        """Main call handling loop."""
        try:
            self.read_agi_vars()
            self._run_call()
        except Exception as e:
            log.error(f"AGI session error: {e}", exc_info=True)
        finally:
            try:
                self.conn.close()
            except Exception:
                pass

    def _run_call(self):
        from ai_handler import handle_call_turn, respond
        from tts_kokoro  import synthesize_for_asterisk

        call_id = self.vars.get("agi_uniqueid", "unknown")
        caller  = self.vars.get("agi_callerid", "unknown")

        def speak(text: str, tag: str = "r"):
            wav = f"/tmp/tts_{call_id}_{tag}.wav"
            synthesize_for_asterisk(text, wav)
            self.playback(wav)

        # Greet
        greeting = respond(
            f"Incoming call from {caller}. Give a brief friendly greeting and ask how you can help.",
            context="voice"
        )
        speak(greeting, "greeting")

        # Conversation loop (up to 10 turns)
        for turn in range(10):
            rec_path = f"/tmp/rec_{call_id}_{turn}"
            if not self.record(rec_path, silence_secs=2, max_secs=30):
                log.info("No recording — ending call")
                break

            # Transcribe with faster-whisper (via stt_voicebox fallback chain)
            try:
                from stt_voicebox import transcribe_sync as transcribe_audio_file
                transcript = transcribe_audio_file(f"{rec_path}.wav")
            except Exception as e:
                log.error(f"STT error: {e}")
                speak("Sorry, I didn't catch that. Could you repeat?", f"err{turn}")
                continue

            if not transcript.strip():
                log.info("Empty transcript — ending call")
                speak("It seems you've gone quiet. Take care, goodbye!", f"bye{turn}")
                break

            log.info(f"[turn {turn}] Caller: {transcript!r}")
            reply = handle_call_turn(transcript, call_id)
            log.info(f"[turn {turn}] AI: {reply!r}")
            speak(reply, f"t{turn}")

            # Check for end-of-call phrases
            lower = transcript.lower()
            if any(w in lower for w in ["goodbye", "bye", "hang up", "that's all", "thanks bye"]):
                speak("Goodbye! Have a great day.", f"bye_final")
                break

        self.hangup()


def serve():
    """Start FastAGI server."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((AGI_HOST, AGI_PORT))
    srv.listen(10)
    log.info(f"🎙️  FastAGI server listening on {AGI_HOST}:{AGI_PORT}")

    while True:
        try:
            conn, addr = srv.accept()
            log.info(f"New call from Asterisk {addr}")
            t = threading.Thread(target=AGISession(conn, addr).handle, daemon=True)
            t.start()
        except KeyboardInterrupt:
            log.info("AGI server stopped.")
            break
        except Exception as e:
            log.error(f"Accept error: {e}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    serve()
