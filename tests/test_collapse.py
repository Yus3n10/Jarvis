from datetime import UTC, datetime, timedelta

from jarvis.domain import Announcement, Event
from jarvis.scheduler import utterances_due

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _event(id="evt1", title="Job interview", start=START):
    return Event(id=id, title=title, start_utc=start, end_utc=start + timedelta(hours=1))


def _ann(rung, event_id="evt1", **kw):
    return Announcement(
        event_id=event_id,
        rung_minutes=rung,
        due_utc=START - timedelta(minutes=rung),
        **kw,
    )


def test_single_due_announcement_produces_one_utterance():
    now = START - timedelta(minutes=60)
    out = utterances_due(now, [_event()], [_ann(60)], None)
    assert len(out) == 1
    assert out[0].event_id == "evt1"


def test_multiple_due_rungs_collapse_to_most_urgent():
    now = START - timedelta(minutes=4)
    anns = [_ann(60), _ann(20), _ann(5)]
    out = utterances_due(now, [_event()], anns, None)
    assert len(out) == 1
    assert out[0].announcement.rung_minutes == 5


def test_separate_events_each_get_an_utterance():
    other = _event(id="evt2", title="Dentist", start=START)
    now = START - timedelta(minutes=60)
    out = utterances_due(now, [_event(), other], [_ann(60), _ann(60, event_id="evt2")], None)
    assert {u.event_id for u in out} == {"evt1", "evt2"}


def test_nothing_due_produces_nothing():
    now = START - timedelta(minutes=90)
    assert utterances_due(now, [_event()], [_ann(60)], None) == []


def test_announcement_with_no_matching_event_is_ignored():
    """Event deleted from the calendar mid-flight. Must not crash."""
    now = START - timedelta(minutes=60)
    assert utterances_due(now, [], [_ann(60)], None) == []


def test_text_names_the_event_and_the_lead_time():
    now = START - timedelta(minutes=60)
    out = utterances_due(now, [_event()], [_ann(60)], None)
    assert "Job interview" in out[0].text
    assert "60 minutes" in out[0].text
