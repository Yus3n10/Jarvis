from datetime import timedelta

from jarvis.escalation import repeat_interval


def test_far_out_repeats_every_ten_minutes():
    assert repeat_interval(timedelta(minutes=60)) == timedelta(minutes=10)


def test_boundary_at_thirty_minutes_is_still_five():
    """30 minutes exactly falls in the 10-30 band, not the >30 band."""
    assert repeat_interval(timedelta(minutes=30)) == timedelta(minutes=5)


def test_just_over_thirty_is_ten():
    assert repeat_interval(timedelta(minutes=30, seconds=1)) == timedelta(minutes=10)


def test_mid_band_repeats_every_five_minutes():
    assert repeat_interval(timedelta(minutes=15)) == timedelta(minutes=5)


def test_boundary_at_ten_minutes_is_ninety_seconds():
    assert repeat_interval(timedelta(minutes=10)) == timedelta(seconds=90)


def test_imminent_repeats_every_ninety_seconds():
    assert repeat_interval(timedelta(minutes=2)) == timedelta(seconds=90)


def test_event_already_started_still_repeats_urgently():
    """Negative time to event means we are late. Stay urgent."""
    assert repeat_interval(timedelta(minutes=-5)) == timedelta(seconds=90)
