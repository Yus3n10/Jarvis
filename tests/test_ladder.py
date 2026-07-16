from datetime import UTC, datetime

from jarvis.domain import Event
from jarvis.ladder import DEFAULT_LADDER, plan_announcements, rungs_for

START = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=START,
        end_utc=datetime(2026, 7, 20, 1, 0, tzinfo=UTC),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def test_no_overrides_uses_default_ladder():
    assert rungs_for(_event()) == DEFAULT_LADDER


def test_overrides_win_over_default():
    assert rungs_for(_event(reminder_minutes=(90,))) == (90,)


def test_rungs_sorted_descending():
    assert rungs_for(_event(reminder_minutes=(5, 90, 30))) == (90, 30, 5)


def test_duplicate_overrides_collapse():
    assert rungs_for(_event(reminder_minutes=(30, 30, 10))) == (30, 10)


def test_plan_creates_one_announcement_per_rung():
    anns = plan_announcements(_event(reminder_minutes=(60, 10)))
    assert [a.rung_minutes for a in anns] == [60, 10]
    assert [a.due_utc for a in anns] == [
        datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
        datetime(2026, 7, 19, 23, 50, tzinfo=UTC),
    ]
    assert all(a.event_id == "evt1" for a in anns)
    assert all(a.state == "pending" for a in anns)


def test_plan_uses_default_ladder_when_no_overrides():
    anns = plan_announcements(_event())
    assert [a.rung_minutes for a in anns] == [60, 20, 5]


def test_declined_events_produce_nothing():
    assert plan_announcements(_event(declined=True)) == []


def test_reminder_reaching_into_previous_day():
    """A 26-hour reminder must land on the previous day, not wrap."""
    anns = plan_announcements(_event(reminder_minutes=(26 * 60,)))
    assert anns[0].due_utc == datetime(2026, 7, 18, 22, 0, tzinfo=UTC)
