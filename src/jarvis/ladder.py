"""Decides which announcements should exist for an event. Pure."""

from datetime import timedelta

from jarvis.domain import Announcement, Event

DEFAULT_LADDER: tuple[int, ...] = (60, 20, 5)


def rungs_for(event: Event) -> tuple[int, ...]:
    """Lead times in minutes, most distant first.

    Google Calendar's own reminder overrides are the configuration surface. An
    event without overrides falls back to the default ladder.
    """
    rungs = event.reminder_minutes or DEFAULT_LADDER
    return tuple(sorted(set(rungs), reverse=True))


def plan_announcements(event: Event) -> list[Announcement]:
    """One pending announcement per ladder rung. Declined events get none."""
    if event.declined:
        return []
    return [
        Announcement(
            event_id=event.id,
            rung_minutes=rung,
            due_utc=event.start_utc - timedelta(minutes=rung),
        )
        for rung in rungs_for(event)
    ]
