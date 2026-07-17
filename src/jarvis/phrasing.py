"""Turn events into spoken answers. Pure -- `now` is a parameter, PHT only here."""

from datetime import datetime, timedelta, timezone

from jarvis.domain import Event

_PHT = timezone(timedelta(hours=8))  # Philippines has never observed DST


def _clock(dt: datetime) -> str:
    local = dt.astimezone(_PHT)
    # strip a leading zero: "07:00 AM" -> "7:00 AM"
    return local.strftime("%I:%M %p").lstrip("0")


def _future_sorted(now: datetime, events: list[Event]) -> list[Event]:
    return sorted(
        (e for e in events if e.start_utc >= now and not e.declined),
        key=lambda e: e.start_utc,
    )


def answer_next(now: datetime, events: list[Event]) -> str:
    upcoming = _future_sorted(now, events)
    if not upcoming:
        return "You have nothing else scheduled."
    e = upcoming[0]
    return f"Next up: {e.title}, at {_clock(e.start_utc)}."


def answer_today(now: datetime, events: list[Event]) -> str:
    today = now.astimezone(_PHT).date()
    todays = [
        e for e in _future_sorted(now, events)
        if e.start_utc.astimezone(_PHT).date() == today
    ]
    if not todays:
        return "You have nothing else scheduled today."
    parts = [f"{e.title} at {_clock(e.start_utc)}" for e in todays]
    return "Today you have: " + "; ".join(parts) + "."
