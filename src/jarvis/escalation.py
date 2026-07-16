"""How often an unacknowledged announcement repeats. Pure."""

from datetime import timedelta

_BANDS: tuple[tuple[timedelta, timedelta], ...] = (
    (timedelta(minutes=30), timedelta(minutes=10)),
    (timedelta(minutes=10), timedelta(minutes=5)),
)
_IMMINENT = timedelta(seconds=90)


def repeat_interval(time_to_event: timedelta) -> timedelta:
    """Repeat pressure scales with urgency.

    Being late (negative time_to_event) stays maximally urgent.
    """
    for threshold, interval in _BANDS:
        if time_to_event > threshold:
            return interval
    return _IMMINENT
