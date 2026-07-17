from datetime import UTC, datetime, timedelta

from jarvis.domain import Event
from jarvis.phrasing import answer_date, answer_next, answer_time, answer_today

# 2026-07-17 08:00 PHT == 2026-07-17 00:00 UTC
NOW = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)


def _event(title, start_utc):
    return Event(id=title, title=title, start_utc=start_utc,
                 end_utc=start_utc + timedelta(hours=1))


def test_next_names_event_and_pht_time():
    events = [_event("Job interview", NOW + timedelta(hours=3))]  # 11:00 PHT
    out = answer_next(NOW, events)
    assert "Job interview" in out
    assert "11" in out  # 11 AM PHT


def test_next_picks_the_soonest_future_event():
    events = [
        _event("Later thing", NOW + timedelta(hours=5)),
        _event("Sooner thing", NOW + timedelta(hours=1)),
    ]
    assert "Sooner thing" in answer_next(NOW, events)


def test_next_ignores_past_events():
    events = [_event("Done", NOW - timedelta(hours=2))]
    assert "nothing" in answer_next(NOW, events).lower()


def test_next_empty_calendar():
    assert "nothing" in answer_next(NOW, []).lower()


def test_today_lists_todays_events_only():
    events = [
        _event("Morning", NOW + timedelta(hours=2)),
        _event("Tomorrow", NOW + timedelta(days=1)),
    ]
    out = answer_today(NOW, events)
    assert "Morning" in out
    assert "Tomorrow" not in out


def test_today_empty():
    assert "nothing" in answer_today(NOW, []).lower()


def test_time_is_pht():
    # NOW is 00:00 UTC == 08:00 PHT
    assert "8:00 AM" in answer_time(NOW)


def test_date_names_weekday_and_month():
    out = answer_date(NOW)  # 2026-07-17 is a Friday
    assert "Friday" in out
    assert "July" in out
    assert "17th" in out
