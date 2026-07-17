"""Speech to text via faster-whisper. Thin I/O shell.

`transcribe` returns recognized text, or "" on any failure -- never raises,
mirroring `voice.py`: a hearing failure must degrade the ears loop, not crash it.

faster-whisper (CTranslate2) is used instead of the spec's whisper.cpp because it
installs prebuilt on aarch64 -- no compiler, no cmake -- and runs `tiny.en` fast on
a Pi 5. Same Whisper model underneath.
"""

import logging

log = logging.getLogger(__name__)


class Transcriber:
    """Loads a Whisper model once and transcribes short command clips.

    The model load is slow (seconds) and downloads on first use, so construct one
    Transcriber and reuse it for the life of the process.
    """

    def __init__(self, model_name: str = "tiny.en") -> None:
        from faster_whisper import WhisperModel

        # int8 keeps it fast and small on the Pi; CPU only (no GPU on a Pi 5).
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio) -> str:
        """Transcribe a clip to text.

        `audio` is anything faster-whisper accepts: a path to a WAV, or a float32
        numpy array of 16kHz mono samples (what the capture path produces). Returns
        the stripped transcript, or "" if nothing was recognized or an error occurred.
        """
        try:
            segments, _info = self._model.transcribe(
                audio, language="en", vad_filter=False, beam_size=5
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as exc:
            log.error("transcription failed: %s", exc)
            return ""
