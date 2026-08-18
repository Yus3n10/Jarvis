"""Entry point. Sync loop + scheduler loop + web server in one process."""

import asyncio
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from jarvis.api import create_app, tick
from jarvis.config import Config
from jarvis.gcal import build_service, fetch_events
from jarvis.mouth import Mouth
from jarvis.power import Power
from jarvis.storage import Storage
from jarvis.store import Store
from jarvis.voice import NullVoice, Voice

log = logging.getLogger("jarvis")
ROOT = Path(__file__).resolve().parents[2]


def _make_voice(mouth: Mouth):
    """Return (voice, is_real). is_real is False when Piper is absent (NullVoice)."""
    piper = Path(os.environ.get("PIPER_BIN", Path.home() / "piper/piper"))
    model = Path(os.environ.get("PIPER_MODEL", Path.home() / "piper/en_US-amy-medium.onnx"))
    if not piper.exists() or not model.exists():
        log.warning("piper not found, using NullVoice (no audio)")
        return NullVoice(mouth), False
    player = os.environ.get("AUDIO_PLAYER", "aplay -q -").split()
    return Voice(piper, model, player, mouth), True


def _start_ears(store: Store, voice, mouth: Mouth, power: Power, storage: Storage) -> None:
    """Start the voice-input loop in a daemon thread. Additive: any failure here
    (missing voice deps, no mic, whisper load error) just disables voice input;
    the announcer and touchscreen ack are unaffected."""
    try:
        from jarvis.converse import Conversation
        from jarvis.ears import Ears
        from jarvis.hearing import Transcriber
        from jarvis.plug import Plug

        transcriber = Transcriber(os.environ.get("WHISPER_MODEL", "tiny.en"))
        conversation = Conversation(os.environ.get("GEMINI_API_KEY") or None)
        plug = Plug(
            api_key=os.environ.get("TUYA_API_KEY"),
            api_secret=os.environ.get("TUYA_API_SECRET"),
            device_id=os.environ.get("TUYA_DEVICE_ID"),
            region=os.environ.get("TUYA_API_REGION", "sg"),
        )
        ears = Ears(store, voice, transcriber, conversation, mouth, plug, power, storage)
        threading.Thread(target=ears.run, name="ears", daemon=True).start()
        log.info(
            "voice input enabled (conversation %s, plug %s, storage %s)",
            "on" if conversation.enabled else "off",
            "on" if plug.enabled else "off",
            "on" if storage.enabled else "off",
        )
    except Exception:
        log.exception("could not start voice input; continuing without it")


def _start_speaker_keeper() -> None:
    """Keep a configured Bluetooth speaker connected. Optional and additive: with
    no SPEAKER_MAC set it does nothing."""
    mac = os.environ.get("SPEAKER_MAC", "").strip()
    if not mac:
        return
    try:
        from jarvis.speaker import SpeakerKeeper

        keeper = SpeakerKeeper(mac)
        threading.Thread(target=keeper.run, name="speaker-keeper", daemon=True).start()
    except Exception:
        log.exception("could not start the speaker keeper; continuing without it")


async def _sync_loop(store: Store, config: Config) -> None:
    service = None
    while True:
        try:
            if service is None:
                service = await asyncio.to_thread(
                    build_service, ROOT / "credentials.json", ROOT / "token.json"
                )
            now = datetime.now(UTC)
            events = await asyncio.to_thread(
                fetch_events, service, config.calendar_id, now
            )
            store.upsert_events(events)
            # Only reachable on success - a network blip must never prune the
            # cache, or a WiFi drop would look like the calendar going empty.
            deleted = store.prune_absent({e.id for e in events})
            if deleted > 0:
                log.info("pruned %d event(s) no longer on the calendar", deleted)
            # Also only reachable on success - this is the signal the
            # dashboard uses to detect a sync that has silently died, so it
            # must never be written on a failed or partial pass.
            store.sync_ok(now)
        except Exception as exc:
            # Network down is expected and survivable — the cache carries us.
            log.warning("calendar sync failed, running from cache: %s", exc)
            service = None
        await asyncio.sleep(config.poll_seconds)


async def _tick_loop(store: Store, config: Config, voice, power: Power) -> None:
    while True:
        try:
            if power.sleeping:
                # Resting: stay silent. Events that fall due now stay pending and
                # get announced right after wake -- "here's what you missed".
                await asyncio.sleep(10)
                continue
            # tick() -> voice.speak() runs blocking subprocess.run calls (up
            # to 30s + 60s). Running it inline on the event loop would freeze
            # _sync_loop and the web server for that whole window - uvicorn
            # couldn't even read an incoming ACK POST, so a tap on the
            # touchscreen would silently do nothing.
            await asyncio.to_thread(tick, datetime.now(UTC), store, voice, config)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(10)


async def _storage_loop(storage: Storage, voice, power: Power, interval_seconds: int = 900) -> None:
    """Watch the NAS drives and speak anything that got worse.

    Warnings go out through the same voice as appointment announcements, because
    a drive shedding sectors is the same class of failure as a sync that quietly
    died: invisible until it costs you something.

    Slow on purpose. Reading SMART wakes a sleeping disk, so a fast poll would
    keep the drives spinning 24/7 and shorten their lives to improve their
    monitoring, which is a poor trade.

    While resting, the check is skipped entirely rather than run-and-muted, so
    nothing gets marked as already-warned. Whatever is wrong gets announced on
    the next pass after wake.
    """
    if not storage.enabled:
        log.info("storage monitoring off (NAS_DRIVES unset)")
        return
    while True:
        try:
            if not power.sleeping:
                _, warnings = await asyncio.to_thread(storage.check)
                for text in warnings:
                    log.warning("storage: %s", text)
                    voice.speak(text)
        except Exception:
            # A disk that cannot be read must never take down the announcer.
            log.exception("storage check failed")
        await asyncio.sleep(interval_seconds)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.load(ROOT / "config.toml")
    store = Store(ROOT / "jarvis.db")
    mouth = Mouth()
    power = Power()
    storage = Storage.from_env(os.environ.get("NAS_DRIVES"))
    voice, is_real = _make_voice(mouth)
    if is_real:
        _start_speaker_keeper()
        _start_ears(store, voice, mouth, power, storage)

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(store, config, mouth, storage),
            host="0.0.0.0",
            port=8000,
            log_level="warning",
        )
    )
    await asyncio.gather(
        _sync_loop(store, config),
        _tick_loop(store, config, voice, power),
        _storage_loop(storage, voice, power),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
