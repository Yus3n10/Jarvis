"""Half-duplex gate: Jarvis must not listen while it speaks.

A single lock, shared by the audio-out path (Voice holds it while playing) and the
audio-in loop (ears skips detection while it is held). Holding it also serializes
speech, so a tick-loop announcement and an ears reply never play over each other on
the one speaker.

The mouth also carries the loudness envelope of whatever is currently playing, so
the dashboard orb can move with the voice instead of merely switching on for the
duration of an utterance. Voice hands over the envelope just before playback
starts; `level` reads off it by elapsed time, offset by the Bluetooth pipeline
delay so the orb peaks when the speaker actually makes the sound rather than when
aplay was handed the bytes.
"""

import threading
import time

# aplay -> PipeWire -> Bluetooth A2DP is roughly a fifth of a second behind the
# bytes. Without this the orb consistently leads the audio and reads as "out of
# sync" even though the envelope itself is correct.
DEFAULT_LATENCY_MS = 200

# What the orb shows when something is playing but we have no envelope for it
# (a non-WAV player, or an envelope that failed to parse). Better a steady glow
# than a dead orb during speech.
_FALLBACK_LEVEL = 0.35


class Mouth:
    def __init__(self, latency_ms: int = DEFAULT_LATENCY_MS) -> None:
        self._lock = threading.Lock()
        self._latency = latency_ms / 1000.0
        # (levels, frame_seconds, started_monotonic). Stored as one tuple so a
        # reader can never see a new envelope paired with an old start time --
        # a single attribute read is atomic under the GIL, two are not.
        self._envelope: tuple[list[float], float, float] | None = None

    def __enter__(self) -> "Mouth":
        self._lock.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self._lock.release()

    @property
    def busy(self) -> bool:
        """True while something is speaking. ears checks this before listening."""
        return self._lock.locked()

    def set_envelope(self, levels: list[float], frame_ms: int) -> None:
        """Attach the loudness curve of the audio about to play. Called by Voice
        immediately before handing bytes to the player, so t=0 is the moment the
        first sample is written."""
        if not levels:
            self._envelope = None
            return
        self._envelope = (levels, frame_ms / 1000.0, time.monotonic())

    def clear_envelope(self) -> None:
        self._envelope = None

    @property
    def level(self) -> float:
        """Loudness 0..1 of what the listener is hearing right now."""
        env = self._envelope  # one atomic read; never re-read below
        if env is None:
            return _FALLBACK_LEVEL if self.busy else 0.0
        levels, frame_s, started = env
        # Negative while the audio is still in flight down the Bluetooth pipe,
        # which is exactly when the room is still silent.
        elapsed = time.monotonic() - started - self._latency
        if elapsed < 0:
            return 0.0
        index = int(elapsed / frame_s)
        if index >= len(levels):
            return 0.0
        return levels[index]
