from datetime import UTC, datetime

import pytest

from jarvis.gcal import fetch_events, parse_event


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


def test_end_date_only_is_skipped():
    """start has dateTime but end is date-only (all-day-ish end): unusable, skip."""
    raw = _raw(end={"date": "2026-07-21"})
    assert parse_event(raw) is None


def test_missing_end_key_is_skipped():
    """Some third-party/imported events have no end block at all."""
    raw = _raw()
    del raw["end"]
    assert parse_event(raw) is None


def test_override_missing_minutes_is_ignored():
    raw = _raw(reminders={"useDefault": False, "overrides": [
        {"method": "popup"},
        {"method": "popup", "minutes": 60},
    ]})
    assert parse_event(raw).reminder_minutes == (60,)


def test_event_with_no_id_is_skipped():
    raw = _raw()
    del raw["id"]
    assert parse_event(raw) is None


class _FakeEventsResource:
    def __init__(self, response):
        self._response = response

    def list(self, **kwargs):
        return self

    def execute(self):
        return self._response


class _FakeService:
    def __init__(self, response):
        self._resource = _FakeEventsResource(response)

    def events(self):
        return self._resource


def test_fetch_events_skips_malformed_item_keeps_good_one():
    """One event that blows up inside parse_event must not sink the batch.

    An unparseable dateTime string passes the "dateTime" presence checks but
    raises inside datetime.fromisoformat, so this exercises fetch_events'
    per-item try/except rather than parse_event's None-returning guards.
    """
    good = _raw(id="good-evt")
    malformed = _raw(id="bad-evt", start={"dateTime": "not-a-real-datetime"})
    service = _FakeService({"items": [good, malformed]})

    events = fetch_events(service, "primary", datetime(2026, 7, 20, tzinfo=UTC))

    assert [e.id for e in events] == ["good-evt"]


def test_fetch_events_raises_when_every_item_fails_to_parse():
    """A batch that is 100% parse failures must not look like a clean empty sync.

    Returning [] here would be indistinguishable from a genuinely empty
    calendar to _sync_loop, which would then prune_absent() the entire cache
    on what is actually a parsing bug, not a real state.
    """
    malformed = [
        _raw(id="bad1", start={"dateTime": "not-a-real-datetime"}),
        _raw(id="bad2", start={"dateTime": "also-not-real"}),
    ]
    service = _FakeService({"items": malformed})

    with pytest.raises(ValueError):
        fetch_events(service, "primary", datetime(2026, 7, 20, tzinfo=UTC))


def test_fetch_events_with_genuinely_empty_items_returns_empty():
    """A real empty calendar (items: []) is a real state, not a failure."""
    service = _FakeService({"items": []})

    events = fetch_events(service, "primary", datetime(2026, 7, 20, tzinfo=UTC))

    assert events == []
