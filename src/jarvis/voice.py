"""Text to speech via Piper. Thin shell.

speak() returns False on any failure. The caller must not record an
announcement as spoken when it returns False — an undelivered announcement that
looks delivered is the worst failure this system can have, because it silently
breaks the trust the whole device depends on.
"""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class NullVoice:
    """Development stand-in. No Piper on Windows."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
        self.spoken.append(text)
        log.info("NullVoice would say: %s", text)
        return True


class Voice:
    def __init__(self, piper_bin: Path, model: Path, player: list[str]) -> None:
        self._piper = piper_bin
        self._model = model
        self._player = player

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
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

            played = subprocess.run(
                self._player, input=piper.stdout, capture_output=True, timeout=60
            )
            if played.returncode != 0:
                log.error("playback %s failed: %s", " ".join(self._player), played.stderr.decode("utf-8", "replace"))
                return False
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("speech failed: %s", exc)
            return False
