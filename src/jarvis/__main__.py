"""Entry point. Sync loop + scheduler loop + web server in one process."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from jarvis.api import create_app, tick
from jarvis.config import Config
from jarvis.gcal import build_service, fetch_events
from jarvis.store import Store
from jarvis.voice import NullVoice, Voice

log = logging.getLogger("jarvis")
ROOT = Path(__file__).resolve().parents[2]


def _voice():
    piper = Path(os.environ.get("PIPER_BIN", Path.home() / "piper/piper"))
    model = Path(os.environ.get("PIPER_MODEL", Path.home() / "piper/en_US-amy-medium.onnx"))
    if not piper.exists() or not model.exists():
        log.warning("piper not found, using NullVoice (no audio)")
        return NullVoice()
    player = os.environ.get("AUDIO_PLAYER", "aplay -q -").split()
    return Voice(piper, model, player)


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


async def _tick_loop(store: Store, config: Config) -> None:
    voice = _voice()
    while True:
        try:
            # tick() -> voice.speak() runs blocking subprocess.run calls (up
            # to 30s + 60s). Running it inline on the event loop would freeze
            # _sync_loop and the web server for that whole window - uvicorn
            # couldn't even read an incoming ACK POST, so a tap on the
            # touchscreen would silently do nothing.
            await asyncio.to_thread(tick, datetime.now(UTC), store, voice, config)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(10)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.load(ROOT / "config.toml")
    store = Store(ROOT / "jarvis.db")

    server = uvicorn.Server(
        uvicorn.Config(create_app(store, config), host="0.0.0.0", port=8000, log_level="warning")
    )
    await asyncio.gather(_sync_loop(store, config), _tick_loop(store, config), server.serve())


if __name__ == "__main__":
    asyncio.run(main())
