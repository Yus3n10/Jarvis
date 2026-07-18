"""Half-duplex gate: Jarvis must not listen while it speaks.

A single lock, shared by the audio-out path (Voice holds it while playing) and the
audio-in loop (ears skips detection while it is held). Holding it also serializes
speech, so a tick-loop announcement and an ears reply never play over each other on
the one speaker.
"""

import threading


class Mouth:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self) -> "Mouth":
        self._lock.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self._lock.release()

    @property
    def busy(self) -> bool:
        """True while something is speaking. ears checks this before listening."""
        return self._lock.locked()
