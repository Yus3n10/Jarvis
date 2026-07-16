"""Decides what to say right now. Pure — `now` is always a parameter."""

import dataclasses
from dataclasses import dataclass
from datetime import datetime

from jarvis.domain import Announcement, Event
from jarvis.escalation import repeat_interval


def _exhausted(ann: Announcement, max_attempts: int | None) -> bool:
    return max_attempts is not None and ann.attempts >= max_attempts


def give_up(ann: Announcement, max_attempts: int | None) -> Announcement:
    """Mark an announcement failed once its attempt budget is spent.

    With max_attempts=None (the default) this never fires and the announcement
    repeats forever.
    """
    if ann.state == "pending" and _exhausted(ann, max_attempts):
        return dataclasses.replace(ann, state="failed")
    return ann


def is_due(
    now: datetime,
    ann: Announcement,
    event: Event,
    max_attempts: int | None,
) -> bool:
    """Should this announcement be spoken at `now`?"""
    if ann.state != "pending":
        return False
    if _exhausted(ann, max_attempts):
        return False
    if now < ann.due_utc:
        return False
    if ann.snoozed_until_utc is not None and now < ann.snoozed_until_utc:
        return False
    if ann.last_spoken_utc is None:
        return True
    return now - ann.last_spoken_utc >= repeat_interval(event.start_utc - now)


@dataclass(frozen=True)
class Utterance:
    event_id: str
    text: str
    announcement: Announcement


def _phrase(event: Event, ann: Announcement) -> str:
    if ann.rung_minutes == 1:
        when = "in 1 minute"
    else:
        when = f"in {ann.rung_minutes} minutes"
    return f"{event.title} {when}."


def utterances_due(
    now: datetime,
    events: list[Event],
    announcements: list[Announcement],
    max_attempts: int | None,
) -> list[Utterance]:
    """What Jarvis should say at `now` — at most one utterance per event.

    Several rungs for the same event can come due together. Saying the same
    thing three times in a row is worse than saying it once, so the most urgent
    rung wins.
    """
    by_id = {e.id: e for e in events}
    winners: dict[str, Announcement] = {}

    for ann in announcements:
        event = by_id.get(ann.event_id)
        if event is None:
            continue  # event vanished from the calendar mid-flight
        if not is_due(now, ann, event, max_attempts):
            continue
        current = winners.get(ann.event_id)
        if current is None or ann.rung_minutes < current.rung_minutes:
            winners[ann.event_id] = ann

    return [
        Utterance(event_id=eid, text=_phrase(by_id[eid], ann), announcement=ann)
        for eid, ann in winners.items()
    ]
