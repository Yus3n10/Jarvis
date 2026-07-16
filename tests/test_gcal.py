from datetime import UTC, datetime

from jarvis.gcal import parse_event


def _raw(**kw):
    defaults = {
        "id": "evt1",
        "summary": "Job interview",
        "start": {"dateTime": "2026-07-20T08:00:00+08:00"},
        "end": {"dateTime": "2026-07-20T09:00:00+08:00"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]},
    }
    return {**defaults, **kw}


def test_pht_start_converts_to_utc():
    """8am Manila is midnight UTC. Getting this wrong loses the interview."""
    event = parse_event(_raw())
    assert event.start_utc == datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    assert event.start_utc.tzinfo is not None


def test_reminder_overrides_are_extracted():
    assert parse_event(_raw()).reminder_minutes == (60,)


def test_multiple_overrides_extracted():
    raw = _raw(reminders={"useDefault": False, "overrides": [
        {"method": "popup", "minutes": 60},
        {"method": "email", "minutes": 1440},
    ]})
    assert parse_event(raw).reminder_minutes == (60, 1440)


def test_use_default_reminders_yields_empty_so_ladder_applies():
    raw = _raw(reminders={"useDefault": True})
    assert parse_event(raw).reminder_minutes == ()


def test_missing_reminders_key_yields_empty():
    raw = _raw()
    del raw["reminders"]
    assert parse_event(raw).reminder_minutes == ()


def test_all_day_event_is_skipped():
    raw = _raw(start={"date": "2026-07-20"}, end={"date": "2026-07-21"})
    assert parse_event(raw) is None


def test_declined_event_is_marked_declined():
    raw = _raw(attendees=[{"self": True, "responseStatus": "declined"}])
    assert parse_event(raw).declined is True


def test_accepted_event_is_not_declined():
    raw = _raw(attendees=[{"self": True, "responseStatus": "accepted"}])
    assert parse_event(raw).declined is False


def test_other_attendee_declining_does_not_decline_us():
    raw = _raw(attendees=[{"self": False, "responseStatus": "declined"}])
    assert parse_event(raw).declined is False


def test_missing_summary_gets_placeholder():
    raw = _raw()
    del raw["summary"]
    assert parse_event(raw).title == "Untitled event"


def test_cancelled_event_is_skipped():
    assert parse_event(_raw(status="cancelled")) is None
