import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute

from jarvis.api import create_app, tick
from jarvis.config import Config
from jarvis.domain import Event
from jarvis.store import Store
from jarvis.voice import NullVoice

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_events([
        Event(
            id="evt1",
            title="Job interview",
            start_utc=START,
            end_utc=START + timedelta(hours=1),
            reminder_minutes=(60,),
        )
    ])
    return s


class FailingVoice:
    def speak(self, text: str) -> bool:
        return False


class RaisingVoice:
    def speak(self, text: str) -> bool:
        raise RuntimeError("speaker wedged")


def test_tick_speaks_when_due(store):
    voice = NullVoice()
    said = tick(START - timedelta(minutes=60), store, voice, Config())
    assert said == ["Job interview in 60 minutes."]
    assert voice.spoken == ["Job interview in 60 minutes."]


def test_tick_is_silent_before_due(store):
    assert tick(START - timedelta(minutes=90), store, NullVoice(), Config()) == []


def test_tick_records_attempt_after_speaking(store):
    tick(START - timedelta(minutes=60), store, NullVoice(), Config())
    ann = store.all_announcements()[0]
    assert ann.attempts == 1
    assert ann.last_spoken_utc == START - timedelta(minutes=60)


def test_failed_playback_is_not_recorded_as_spoken(store):
    """The worst bug available: an undelivered announcement looking delivered."""
    said = tick(START - timedelta(minutes=60), store, FailingVoice(), Config())
    assert said == []
    ann = store.all_announcements()[0]
    assert ann.attempts == 0
    assert ann.last_spoken_utc is None


def test_tick_does_not_repeat_within_the_interval(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())
    assert tick(now + timedelta(minutes=1), store, NullVoice(), Config()) == []


def test_tick_repeats_after_the_interval(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())
    assert tick(now + timedelta(minutes=10), store, NullVoice(), Config()) != []


def test_ack_silences_further_ticks(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())
    store.ack("evt1")
    assert tick(now + timedelta(minutes=30), store, NullVoice(), Config()) == []


def test_tick_writes_heartbeat(store):
    now = START - timedelta(minutes=90)
    tick(now, store, NullVoice(), Config())
    assert store.last_heartbeat() == now


def test_tick_does_not_write_heartbeat_when_it_raises(store):
    """A pass that dies partway through must not look like a healthy pass.

    The heartbeat means "I completed a pass", not "I started one" - otherwise
    a Jarvis wedged on the same exception every 10 seconds would refresh its
    heartbeat forever while never announcing anything, and the dashboard would
    never show NOT UPDATING.
    """
    now = START - timedelta(minutes=60)
    with pytest.raises(RuntimeError):
        tick(now, store, RaisingVoice(), Config())
    assert store.last_heartbeat() is None


def test_max_attempts_marks_failed_and_silences(store):
    config = Config(max_attempts=2)
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), config)
    tick(now + timedelta(minutes=10), store, NullVoice(), config)
    assert tick(now + timedelta(minutes=20), store, NullVoice(), config) == []
    assert store.all_announcements()[0].state == "failed"


def test_default_config_never_gives_up(store):
    """max_attempts=None is the chosen default: repeat forever."""
    now = START - timedelta(minutes=60)
    for i in range(20):
        tick(now + timedelta(minutes=10 * i), store, NullVoice(), Config())
    assert store.all_announcements()[0].state == "pending"


# --- create_app / routes / websocket ---------------------------------------


def test_state_returns_seeded_event(store):
    client = TestClient(create_app(store, Config()))
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 1
    evt = data["events"][0]
    assert evt["title"] == "Job interview"
    # Must be an ISO-8601 UTC string, not PHT-shifted -- display layer's job.
    assert evt["start_utc"] == START.isoformat()
    assert evt["start_utc"].endswith("+00:00")


def test_needs_ack_flips_after_speaking(store):
    client = TestClient(create_app(store, Config()))
    now = START - timedelta(minutes=60)

    before = client.get("/api/state").json()["events"][0]
    assert before["needs_ack"] is False

    tick(now, store, NullVoice(), Config())

    after = client.get("/api/state").json()["events"][0]
    assert after["needs_ack"] is True


def test_ack_endpoint_silences_event(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())

    client = TestClient(create_app(store, Config()))
    resp = client.post("/api/ack/evt1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    evt = client.get("/api/state").json()["events"][0]
    assert evt["needs_ack"] is False

    assert tick(now + timedelta(minutes=30), store, NullVoice(), Config()) == []


def test_snooze_endpoint_sets_snoozed_until_and_silences_tick(store):
    client = TestClient(create_app(store, Config()))

    # A huge window, so snoozed_until_utc lands well after the fixture's
    # simulated `now` regardless of the real wall-clock date the suite runs on.
    resp = client.post("/api/snooze/evt1", params={"minutes": 100_000_000})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    anns = store.all_announcements()
    assert any(a.snoozed_until_utc is not None for a in anns)

    now = START - timedelta(minutes=60)
    assert tick(now, store, NullVoice(), Config()) == []


def test_state_stale_reflects_heartbeat(store):
    client = TestClient(create_app(store, Config()))

    data = client.get("/api/state").json()
    assert data["heartbeat_utc"] is None
    assert data["stale"] is True

    tick(datetime.now(UTC), store, NullVoice(), Config())

    data = client.get("/api/state").json()
    assert data["stale"] is False


def test_declined_events_excluded_from_state(store):
    store.upsert_events([
        Event(
            id="evt2",
            title="Declined meeting",
            start_utc=START + timedelta(hours=2),
            end_utc=START + timedelta(hours=3),
            declined=True,
            reminder_minutes=(60,),
        )
    ])
    client = TestClient(create_app(store, Config()))
    data = client.get("/api/state").json()
    ids = [e["id"] for e in data["events"]]
    assert "evt1" in ids
    assert "evt2" not in ids


def test_ws_sends_a_state_payload(store):
    client = TestClient(create_app(store, Config()))
    rest_state = client.get("/api/state").json()

    with client.websocket_connect("/ws") as ws:
        payload = ws.receive_json()

    assert set(payload.keys()) == set(rest_state.keys())
    assert payload["events"][0]["id"] == "evt1"


def test_ws_disconnect_is_handled_cleanly(store):
    """The handler must return cleanly when send_json raises WebSocketDisconnect.

    Driven directly against the endpoint coroutine rather than through
    TestClient: TestClient's WebSocketTestSession.__exit__ closes the
    connection by cancelling the anyio scope wrapping the app task, which
    raises inside the handler's `asyncio.sleep(1)` -- not a
    WebSocketDisconnect, and not what `except WebSocketDisconnect` catches.
    That cancellation is then silently swallowed by TestClient's own
    teardown scope, so a test built on it "passes" without ever exercising
    the handler's except-clause. Calling the registered endpoint directly
    with a fake socket that raises WebSocketDisconnect from send_json is
    the only way to pin the actual contract.
    """
    app = create_app(store, Config())
    ws_route = next(
        route
        for route in app.routes
        if isinstance(route, WebSocketRoute) and route.path == "/ws"
    )

    class FakeSocket:
        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            raise WebSocketDisconnect(code=1006)

    # If the handler doesn't catch WebSocketDisconnect, this raises and the
    # test fails.
    asyncio.run(ws_route.endpoint(FakeSocket()))


def test_state_payload_reports_failed_flag(store):
    store.upsert_events([
        Event(
            id="evt2",
            title="Untouched meeting",
            start_utc=START + timedelta(hours=2),
            end_utc=START + timedelta(hours=3),
            reminder_minutes=(60,),
        )
    ])
    config = Config(max_attempts=2)
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), config)
    tick(now + timedelta(minutes=10), store, NullVoice(), config)
    tick(now + timedelta(minutes=20), store, NullVoice(), config)
    assert store.all_announcements()[0].state == "failed"

    client = TestClient(create_app(store, config))
    data = client.get("/api/state").json()
    by_id = {e["id"]: e for e in data["events"]}

    assert by_id["evt1"]["failed"] is True
    assert by_id["evt2"]["failed"] is False
