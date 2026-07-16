from datetime import UTC, datetime

import pytest

from jarvis.domain import Announcement, Event


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
        end_utc=datetime(2026, 7, 20, 1, 0, tzinfo=UTC),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def test_event_is_frozen():
    event = _event()
    with pytest.raises(AttributeError):
        event.title = "changed"


def test_event_rejects_naive_start():
    with pytest.raises(ValueError, match="timezone-aware"):
        _event(start_utc=datetime(2026, 7, 20, 0, 0))


def test_announcement_defaults_to_pending():
    ann = Announcement(
        event_id="evt1",
        rung_minutes=60,
        due_utc=datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
    )
    assert ann.state == "pending"
    assert ann.attempts == 0
    assert ann.last_spoken_utc is None
    assert ann.snoozed_until_utc is None


def test_announcement_rejects_unknown_state():
    with pytest.raises(ValueError, match="state"):
        Announcement(
            event_id="evt1",
            rung_minutes=60,
            due_utc=datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
            state="bogus",
        )
