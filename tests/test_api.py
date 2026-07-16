from datetime import UTC, datetime, timedelta

import pytest

from jarvis.api import tick
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
