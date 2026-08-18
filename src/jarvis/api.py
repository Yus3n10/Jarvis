"""The seam: where the pure core meets the world."""

import asyncio
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
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

# 20Hz level pushes, full state every 20th (once a second). 20Hz comfortably
# resolves syllable rate (4-8Hz); the browser eases between samples.
_WS_TICK_SECONDS = 0.05
_STATE_EVERY_N_TICKS = 20


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


def _state_payload(store: Store, now: datetime, config: Config, mouth=None, storage=None) -> dict:
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
        "type": "state",
        "now_utc": now.isoformat(),
        "heartbeat_utc": heartbeat.isoformat() if heartbeat else None,
        "last_sync_ok_utc": last_sync_ok.isoformat() if last_sync_ok else None,
        "stale": heartbeat is None or (now - heartbeat) > timedelta(minutes=2) or sync_stale,
        # The orb reacts to these: `speaking` is the on/off gate, `level` is the
        # loudness of what the listener is hearing right now. Absent mouth
        # (tests, no audio) reads as silent.
        "speaking": bool(mouth is not None and mouth.busy),
        "level": round(mouth.level, 3) if mouth is not None else 0.0,
        # Cached by the storage loop, never polled here: this payload goes out
        # at 5Hz and running smartctl at 5Hz would hold the disk awake forever.
        "drives": [
            {
                "name": d.name,
                "present": d.present,
                "free_bytes": d.free_bytes,
                "total_bytes": d.total_bytes,
                "free_pct": round(d.free_pct, 1),
                "pending": d.pending,
                "reallocated": d.reallocated,
                "failing": d.failing,
            }
            for d in (storage.last_status() if storage is not None else [])
        ],
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


def create_app(store: Store, config: Config, mouth=None, storage=None) -> FastAPI:
    app = FastAPI(title="Jarvis")

    @app.get("/api/state")
    def state() -> dict:
        return _state_payload(store, datetime.now(UTC), config, mouth, storage)

    @app.post("/api/ack/{event_id}")
    def ack(event_id: str) -> dict:
        store.ack(event_id)
        return {"ok": True}

    @app.post("/api/snooze/{event_id}")
    def snooze(event_id: str, minutes: int = 10) -> dict:
        store.snooze(event_id, datetime.now(UTC) + timedelta(minutes=minutes))
        return {"ok": True}

    @app.post("/api/kiosk/exit")
    def kiosk_exit(request: Request) -> dict:
        """Drop out of the kiosk to the desktop. Localhost only.

        uvicorn binds 0.0.0.0 so the dashboard is reachable from the LAN, but
        only the kiosk browser itself has any business closing the kiosk --
        otherwise anyone on the network could blank the appliance's screen.
        Getting back in is the 'Jarvis Kiosk' icon on the desktop.
        """
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="kiosk control is localhost only")
        # chromium runs as the same user as this service, so no sudo is needed.
        subprocess.run(["pkill", "-f", "chromium"], check=False)
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            ticks = 0
            while True:
                # Two cadences on one socket. The full state costs two SQLite
                # reads, and events do not change 20 times a second, so it goes
                # out once a second. The orb needs `level` far more often than
                # that, and level is a float read off the mouth -- no I/O. This
                # is both smoother than the old 5Hz full-state push and cheaper.
                if ticks % _STATE_EVERY_N_TICKS == 0:
                    await socket.send_json(
                        _state_payload(store, datetime.now(UTC), config, mouth, storage)
                    )
                else:
                    await socket.send_json(
                        {
                            "type": "level",
                            "level": round(mouth.level, 3) if mouth is not None else 0.0,
                            "speaking": bool(mouth is not None and mouth.busy),
                        }
                    )
                ticks += 1
                await asyncio.sleep(_WS_TICK_SECONDS)
        except WebSocketDisconnect:
            pass

    dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app
