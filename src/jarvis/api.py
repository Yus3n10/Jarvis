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


def tick(now: datetime, store: Store, voice, config: Config) -> list[str]:
    """One scheduler pass. Returns the texts actually spoken.

    An utterance is only recorded as spoken if voice.speak() confirms playback.
    A failed speaker must leave the announcement pending so it is retried.
    """
    store.heartbeat(now)
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
    return spoken


def _state_payload(store: Store, now: datetime) -> dict:
    events = sorted(store.all_events(), key=lambda e: e.start_utc)
    anns = store.all_announcements()
    pending = {a.event_id for a in anns if a.state == "pending"}
    failed = {a.event_id for a in anns if a.state == "failed"}
    heartbeat = store.last_heartbeat()
    return {
        "now_utc": now.isoformat(),
        "heartbeat_utc": heartbeat.isoformat() if heartbeat else None,
        "stale": heartbeat is None or (now - heartbeat) > timedelta(minutes=2),
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


def create_app(store: Store, config: Config) -> FastAPI:
    app = FastAPI(title="Jarvis")

    @app.get("/api/state")
    def state() -> dict:
        return _state_payload(store, datetime.now(UTC))

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
                await socket.send_json(_state_payload(store, datetime.now(UTC)))
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass

    dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app
