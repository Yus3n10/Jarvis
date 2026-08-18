"""The ears: wake word -> record -> transcribe -> handle -> speak. Thin I/O shell.

The audio pipeline (openWakeWord, sounddevice, pw-record) loads lazily inside run(),
so this module -- and the testable handle() routing -- import with nothing extra.

Voice input is additive: if this loop crashes or the mic is absent, it disables itself
and the announcer + touchscreen ack are unaffected.
"""

import logging
import math
import struct
import subprocess
import threading
import time
import wave
from datetime import UTC, datetime, timedelta

from jarvis import faq, phrasing
from jarvis.intent import (
    Ack,
    PlugOff,
    PlugOn,
    QueryDate,
    QueryNext,
    QueryStorage,
    QueryTime,
    QueryToday,
    Shutdown,
    Sleep,
    Snooze,
    Wake,
    is_affirmation,
    parse,
)

log = logging.getLogger(__name__)

_CMD_WAV = "/tmp/jarvis_cmd.wav"
_PING_WAV = "/tmp/jarvis_ping.wav"


def _make_ping(path: str) -> None:
    """Write a soft ~0.18s tone -- the 'I'm thinking, please wait' cue."""
    rate, dur, freq = 16000, 0.18, 880.0
    n = int(rate * dur)
    frames = bytearray()
    for i in range(n):
        env = math.sin(math.pi * i / n)  # fade in and out
        val = int(0.25 * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", val)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _needs_ack_ids(store) -> set[str]:
    return {
        a.event_id
        for a in store.all_announcements()
        if a.state == "pending" and a.last_spoken_utc is not None
    }


def handle(
    text: str, now: datetime, store, conversation, plug=None, power=None, storage=None
) -> str:
    """Route a transcript to a spoken reply.

    Commands act locally and deterministically; only Unknown reaches conversation.
    Pure with respect to its injected store/conversation/plug/power/storage --
    testable without hardware. Any of them may be None (capability not configured).

    Note the storage branch returns before conversation is ever consulted. That is
    deliberate and load-bearing: disk answers must never round-trip through Gemini,
    because drive and volume names are the user's own and have no business leaving
    the Pi.
    """
    # A pending shutdown intercepts the NEXT utterance: only "yes" powers off;
    # anything else cancels and is then handled normally.
    if power is not None and power.awaiting_confirmation(now):
        if is_affirmation(text):
            return power.confirm()
        power.cancel()

    intent = parse(text)
    if isinstance(intent, Shutdown):
        return power.request_shutdown(now) if power else "I can't shut down right now."
    if isinstance(intent, Sleep):
        return power.sleep() if power else "I can't rest right now."
    if isinstance(intent, Wake):
        return power.wake() if power else "I'm here."
    if isinstance(intent, PlugOn):
        if plug is None or not plug.enabled:
            return "The plug isn't set up."
        return "Okay, switching it on." if plug.turn_on() else "Sorry, I couldn't reach the plug."
    if isinstance(intent, PlugOff):
        if plug is None or not plug.enabled:
            return "The plug isn't set up."
        return "Okay, switching it off." if plug.turn_off() else "Sorry, I couldn't reach the plug."
    if isinstance(intent, QueryStorage):
        if storage is None or not storage.enabled:
            return "Storage isn't set up."
        return storage.describe()
    if isinstance(intent, QueryTime):
        return phrasing.answer_time(now)
    if isinstance(intent, QueryDate):
        return phrasing.answer_date(now)
    if isinstance(intent, QueryToday):
        return phrasing.answer_today(now, store.all_events())
    if isinstance(intent, QueryNext):
        return phrasing.answer_next(now, store.all_events())
    if isinstance(intent, Ack):
        ids = _needs_ack_ids(store)
        if not ids:
            return "There is nothing to acknowledge."
        for i in ids:
            store.ack(i)
        return "Okay, acknowledged."
    if isinstance(intent, Snooze):
        ids = _needs_ack_ids(store)
        if not ids:
            return "There is nothing to snooze."
        for i in ids:
            store.snooze(i, now + timedelta(minutes=intent.minutes))
        return f"Snoozed for {intent.minutes} minutes."
    # Unknown -- a canned local answer first (zero tokens), then the LLM.
    canned = faq.answer(text)
    if canned is not None:
        return canned
    if conversation.enabled:
        return conversation.reply(text, now, store.all_events())
    return "Sorry, I didn't catch that."


class Ears:
    def __init__(
        self,
        store,
        voice,
        transcriber,
        conversation,
        mouth,
        plug=None,
        power=None,
        storage=None,
        wake_name: str = "hey_jarvis",
        device_name: str = "pulse",
        threshold: float = 0.5,
        refractory: float = 6.0,
        record_seconds: int = 4,
    ) -> None:
        self._store = store
        self._voice = voice
        self._trans = transcriber
        self._conv = conversation
        self._mouth = mouth
        self._plug = plug
        self._power = power
        self._storage = storage
        self._wake_name = wake_name
        self._device = device_name
        self._threshold = threshold
        self._refractory = refractory
        self._rec = record_seconds
        self._stop = threading.Event()
        try:
            _make_ping(_PING_WAV)
        except Exception:
            log.warning("could not create the thinking cue; continuing without it")

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Blocking listen loop. Meant to run in its own thread. Never lets an
        exception escape -- voice input is additive, never load-bearing."""
        try:
            self._run()
        except Exception:
            log.exception("ears loop crashed; voice input disabled (announcer unaffected)")

    def _run(self) -> None:
        import sounddevice as sd
        from openwakeword.model import Model

        wake = Model(wakeword_models=[self._wake_name], inference_framework="onnx")
        dev = next(
            (
                i
                for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and d["name"] == self._device
            ),
            None,
        )
        chunk = 1280  # 80ms at 16k
        last = 0.0
        log.info("ears: listening for 'hey %s'", self._wake_name.split("_")[-1])
        with sd.InputStream(
            samplerate=16000, channels=1, dtype="int16", blocksize=chunk, device=dev
        ) as stream:
            while not self._stop.is_set():
                data, _ = stream.read(chunk)
                if self._mouth.busy:  # half-duplex: don't listen while speaking
                    continue
                score = float(wake.predict(data[:, 0])[self._wake_name])
                if score > self._threshold and time.time() - last > self._refractory:
                    last = time.time()
                    self._voice.speak("Yes?")
                    self._record()
                    # the "thinking" cue: tells the user we heard them and are
                    # working, so they wait through the transcribe + reply gap
                    # instead of talking over the silence.
                    self._voice.play_wav(_PING_WAV)
                    text = self._trans.transcribe(_CMD_WAV)
                    reply = handle(
                        text,
                        datetime.now(UTC),
                        self._store,
                        self._conv,
                        self._plug,
                        self._power,
                        self._storage,
                    )
                    log.info("ears: heard %r -> %r", text, reply)
                    self._voice.speak(reply)
                    self._drain(stream, chunk)
                    last = time.time()

    def _record(self) -> None:
        try:
            subprocess.run(
                ["timeout", str(self._rec), "pw-record", "--rate", "48000",
                 "--channels", "1", "--format", "s16", _CMD_WAV],
                capture_output=True, timeout=self._rec + 3,
            )
        except subprocess.SubprocessError as exc:
            log.error("command capture failed: %s", exc)

    def _drain(self, stream, chunk: int) -> None:
        try:
            while stream.read_available > chunk:
                stream.read(chunk)
        except Exception:
            pass
