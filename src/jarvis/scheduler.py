"""Decides what to say right now. Pure — `now` is always a parameter."""

import dataclasses
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
