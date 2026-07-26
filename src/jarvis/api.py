"""The seam: where the pure core meets the world."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from jarvis.config import Config
from jarvis.scheduler import give_up, utterances_due
from jarvis.store import Store

log = logging.getLogger(__name__)

# A single missed poll must stay quiet -- poll_seconds defaults to 300s, so a
# threshold of a couple of minutes would false-alarm on every ordinary cycle.
# Threshold is derived from the configured poll interval instead of a fixed
# number of minutes, so it scales with however often sync is actually meant
# to run.
_SYNC_STALE_POLL_MULTIPLIER = 3


def tick(now: datetime, store: Store, voice, config: Config) -> list[str]:
    """One scheduler pass. Returns the texts actually spoken.

    An utterance is only recorded as spoken if voice.speak() confirms playback.
    A failed speaker must leave the announcement pending so it is retried.

    The heartbeat is written last, only once the pass has completed without
    raising. It means "I completed a pass", not "I started one" - if it meant
    the latter, a pass that dies every time on the same exception would keep
    refreshing the heartbeat forever while never actually announcing anything,
    and the dashboard would show a healthy Jarvis that is in fact permanently
    mute.
    """
    events = store.all_events()
    announcements = store.all_announcements()

    for ann in announcements:
        gave_up = give_up(ann, config.max_attempts)
        if gave_up is not ann:
            store.mark_state(ann, "failed")

    spoken: list[str] = []
    for utt in utterances_due(now, events, store.all_announcements(), config.max_attempts):
        if voice.speak(utt.text):
            store.mark_spoken(utt.announcement, now)
            spoken.append(utt.text)
        else:
            log.warning("playback failed, leaving pending: %s", utt.text)
    store.heartbeat(now)
    return spoken


def _state_payload(store: Store, now: datetime, config: Config, mouth=None) -> dict:
    events = sorted(store.all_events(), key=lambda e: e.start_utc)
    anns = store.all_announcements()
    pending = {a.event_id for a in anns if a.state == "pending"}
    failed = {a.event_id for a in anns if a.state == "failed"}
    heartbeat = store.last_heartbeat()
    last_sync_ok = store.last_sync_ok()
    # Never-synced-yet (None) counts as stale: a Jarvis that has never reached
    # Google has never been trustworthy, regardless of how healthy the tick
    # loop looks.
    sync_stale_after = timedelta(seconds=config.poll_seconds * _SYNC_STALE_POLL_MULTIPLIER)
    sync_stale = last_sync_ok is None or (now - last_sync_ok) > sync_stale_after
    return {
        "now_utc": now.isoformat(),
        "heartbeat_utc": heartbeat.isoformat() if heartbeat else None,
        "last_sync_ok_utc": last_sync_ok.isoformat() if last_sync_ok else None,
        "stale": heartbeat is None or (now - heartbeat) > timedelta(minutes=2) or sync_stale,
        # The orb reacts to this: True while Jarvis is speaking (the mouth lock
        # is held). Absent mouth (tests, no audio) reads as not speaking.
        "speaking": bool(mouth is not None and mouth.busy),
        "events": [
            {
                "id": e.id,
                "title": e.title,
                "start_utc": e.start_utc.isoformat(),
                "declined": e.declined,
                "needs_ack": e.id in pending
                and any(
                    a.event_id == e.id and a.last_spoken_utc is not None and a.state == "pending"
                    for a in anns
                ),
                "failed": e.id in failed,
            }
            for e in events
            if not e.declined
        ],
    }


def create_app(store: Store, config: Config, mouth=None) -> FastAPI:
    app = FastAPI(title="Jarvis")

    @app.get("/api/state")
    def state() -> dict:
        return _state_payload(store, datetime.now(UTC), config, mouth)

    @app.post("/api/ack/{event_id}")
    def ack(event_id: str) -> dict:
        store.ack(event_id)
        return {"ok": True}

    @app.post("/api/snooze/{event_id}")
    def snooze(event_id: str, minutes: int = 10) -> dict:
        store.snooze(event_id, datetime.now(UTC) + timedelta(minutes=minutes))
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                await socket.send_json(_state_payload(store, datetime.now(UTC), config, mouth))
                # Fast cadence so the orb reacts to speech promptly (the mouth
                # flips on/off in well under a second). The payload is small and
                # there is only ever the one kiosk client.
                await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            pass

    dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app
