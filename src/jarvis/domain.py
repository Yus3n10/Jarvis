"""Pure data structures. No logic, no I/O."""

from dataclasses import dataclass, field
from datetime import datetime

VALID_STATES = ("pending", "acked", "failed")


def _require_aware(value: datetime | None, name: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware UTC, got naive datetime")


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start_utc: datetime
    end_utc: datetime
    declined: bool = False
    reminder_minutes: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_aware(self.start_utc, "start_utc")
        _require_aware(self.end_utc, "end_utc")


@dataclass(frozen=True)
class Announcement:
    event_id: str
    rung_minutes: int
    due_utc: datetime
    state: str = "pending"
    attempts: int = 0
    last_spoken_utc: datetime | None = None
    snoozed_until_utc: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware(self.due_utc, "due_utc")
        _require_aware(self.last_spoken_utc, "last_spoken_utc")
        _require_aware(self.snoozed_until_utc, "snoozed_until_utc")
        if self.state not in VALID_STATES:
            raise ValueError(f"state must be one of {VALID_STATES}, got {self.state!r}")
