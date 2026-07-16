from datetime import UTC, datetime, timedelta

from jarvis.config import Config
from jarvis.domain import Announcement, Event
from jarvis.scheduler import give_up, is_due

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=START,
        end_utc=START + timedelta(hours=1),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def _ann(**kw):
    defaults = dict(
        event_id="evt1",
        rung_minutes=60,
        due_utc=START - timedelta(minutes=60),
    )
    return Announcement(**{**defaults, **kw})


def test_not_due_before_its_time():
    now = START - timedelta(minutes=61)
    assert is_due(now, _ann(), _event(), None) is False


def test_due_exactly_at_its_time():
    now = START - timedelta(minutes=60)
    assert is_due(now, _ann(), _event(), None) is True


def test_acked_is_never_due():
    now = START - timedelta(minutes=30)
    assert is_due(now, _ann(state="acked"), _event(), None) is False


def test_failed_is_never_due():
    now = START - timedelta(minutes=30)
    assert is_due(now, _ann(state="failed"), _event(), None) is False


def test_snoozed_is_not_due_until_snooze_expires():
    now = START - timedelta(minutes=50)
    ann = _ann(snoozed_until_utc=START - timedelta(minutes=40))
    assert is_due(now, ann, _event(), None) is False


def test_due_again_after_snooze_expires():
    now = START - timedelta(minutes=39)
    ann = _ann(snoozed_until_utc=START - timedelta(minutes=40))
    assert is_due(now, ann, _event(), None) is True


def test_not_due_again_before_repeat_interval_elapses():
    """Spoken 2 min ago at 45 min out; band is 10 min. Too soon."""
    now = START - timedelta(minutes=45)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=2))
    assert is_due(now, ann, _event(), None) is False


def test_due_again_once_repeat_interval_elapses():
    now = START - timedelta(minutes=45)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=10))
    assert is_due(now, ann, _event(), None) is True


def test_escalates_when_imminent():
    """At 5 min out the interval is 90s, so a 2-min-old utterance is stale."""
    now = START - timedelta(minutes=5)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=2))
    assert is_due(now, ann, _event(), None) is True


def test_snooze_across_midnight():
    """Snooze set before midnight, checked after. Plain UTC arithmetic must hold."""
    start = datetime(2026, 7, 21, 0, 30, tzinfo=UTC)
    event = _event(start_utc=start, end_utc=start + timedelta(hours=1))
    ann = _ann(
        due_utc=start - timedelta(minutes=60),
        snoozed_until_utc=datetime(2026, 7, 21, 0, 10, tzinfo=UTC),
    )
    assert is_due(datetime(2026, 7, 20, 23, 55, tzinfo=UTC), ann, event, None) is False
    assert is_due(datetime(2026, 7, 21, 0, 15, tzinfo=UTC), ann, event, None) is True


def test_infinite_attempts_never_gives_up():
    ann = _ann(attempts=9999)
    assert give_up(ann, None) is ann
    assert is_due(START, ann, _event(), None) is True


def test_gives_up_once_attempts_exhausted():
    ann = _ann(attempts=5)
    assert give_up(ann, 5).state == "failed"


def test_does_not_give_up_below_the_bound():
    ann = _ann(attempts=4)
    assert give_up(ann, 5) is ann


def test_exhausted_announcement_is_not_due():
    ann = _ann(attempts=5)
    assert is_due(START, ann, _event(), 5) is False


def test_config_defaults_to_infinite_attempts():
    assert Config().max_attempts is None
