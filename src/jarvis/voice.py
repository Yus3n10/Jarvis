"""Text to speech via Piper. Thin shell.

speak() returns False on any failure. The caller must not record an
announcement as spoken when it returns False — an undelivered announcement that
looks delivered is the worst failure this system can have, because it silently
breaks the trust the whole device depends on.

If a Mouth gate is provided, speak() holds it for the whole call: this serializes
speech (a tick-loop announcement and an ears reply never overlap on the one
speaker) and tells the ears loop not to listen while Jarvis is talking.
"""

import io
import logging
import subprocess
import wave
from pathlib import Path

log = logging.getLogger(__name__)

_LEAD_SILENCE_SECONDS = 0.4  # wakes a Bluetooth amp so the first word isn't clipped


def _prepend_silence(wav_bytes: bytes, seconds: float) -> bytes:
    """Return the WAV with `seconds` of leading silence. On non-WAV input, unchanged."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        pad = b"\x00" * (int(params.framerate * seconds) * params.sampwidth * params.nchannels)
        out = io.BytesIO()
        with wave.open(out, "wb") as o:
            o.setparams(params)
            o.writeframes(pad + frames)
        return out.getvalue()
    except Exception:
        return wav_bytes


class NullVoice:
    """Development stand-in. No Piper on Windows."""

    def __init__(self, mouth=None) -> None:
        self.spoken: list[str] = []
        self._mouth = mouth

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
        if self._mouth is not None:
            with self._mouth:
                pass
        self.spoken.append(text)
        log.info("NullVoice would say: %s", text)
        return True

    def play_wav(self, path: str) -> bool:
        return True


class Voice:
    def __init__(self, piper_bin: Path, model: Path, player: list[str], mouth=None) -> None:
        self._piper = piper_bin
        self._model = model
        self._player = player
        self._mouth = mouth

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
        if self._mouth is not None:
            with self._mouth:
                return self._speak(text)
        return self._speak(text)

    def play_wav(self, path: str) -> bool:
        """Play a WAV file through the same player, holding the mouth. Used for the
        short 'thinking' cue while a reply is being prepared."""
        def _go() -> bool:
            try:
                with open(path, "rb") as f:
                    audio = _prepend_silence(f.read(), _LEAD_SILENCE_SECONDS)
                played = subprocess.run(self._player, input=audio, capture_output=True, timeout=15)
                return played.returncode == 0
            except (OSError, subprocess.SubprocessError) as exc:
                log.error("play_wav failed: %s", exc)
                return False

        if self._mouth is not None:
            with self._mouth:
                return _go()
        return _go()

    def _speak(self, text: str) -> bool:
        try:
            piper = subprocess.run(
                [str(self._piper), "--model", str(self._model), "--output_file", "-"],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if piper.returncode != 0:
                log.error("piper %s failed: %s", self._piper, piper.stderr.decode("utf-8", "replace"))
                return False

            audio = _prepend_silence(piper.stdout, _LEAD_SILENCE_SECONDS)
            played = subprocess.run(
                self._player, input=audio, capture_output=True, timeout=60
            )
            if played.returncode != 0:
                log.error("playback %s failed: %s", " ".join(self._player), played.stderr.decode("utf-8", "replace"))
                return False
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("speech failed: %s", exc)
            return False
