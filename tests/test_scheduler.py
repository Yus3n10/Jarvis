from datetime import UTC, datetime, timedelta

import pytest

from jarvis.config import Config
from jarvis.domain import Announcement, Event
from jarvis.scheduler import give_up, is_due

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=START,
        end_utc=START + timedelta(hours=1),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def _ann(**kw):
    defaults = dict(
        event_id="evt1",
        rung_minutes=60,
        due_utc=START - timedelta(minutes=60),
    )
    return Announcement(**{**defaults, **kw})


def test_not_due_before_its_time():
    now = START - timedelta(minutes=61)
    assert is_due(now, _ann(), _event(), None) is False


def test_due_exactly_at_its_time():
    now = START - timedelta(minutes=60)
    assert is_due(now, _ann(), _event(), None) is True


def test_acked_is_never_due():
    now = START - timedelta(minutes=30)
    assert is_due(now, _ann(state="acked"), _event(), None) is False


def test_failed_is_never_due():
    now = START - timedelta(minutes=30)
    assert is_due(now, _ann(state="failed"), _event(), None) is False


def test_snoozed_is_not_due_until_snooze_expires():
    now = START - timedelta(minutes=50)
    ann = _ann(snoozed_until_utc=START - timedelta(minutes=40))
    assert is_due(now, ann, _event(), None) is False


def test_due_again_after_snooze_expires():
    now = START - timedelta(minutes=39)
    ann = _ann(snoozed_until_utc=START - timedelta(minutes=40))
    assert is_due(now, ann, _event(), None) is True


def test_not_due_again_before_repeat_interval_elapses():
    """Spoken 2 min ago at 45 min out; band is 10 min. Too soon."""
    now = START - timedelta(minutes=45)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=2))
    assert is_due(now, ann, _event(), None) is False


def test_due_again_once_repeat_interval_elapses():
    now = START - timedelta(minutes=45)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=10))
    assert is_due(now, ann, _event(), None) is True


def test_escalates_when_imminent():
    """At 5 min out the interval is 90s, so a 2-min-old utterance is stale."""
    now = START - timedelta(minutes=5)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=2))
    assert is_due(now, ann, _event(), None) is True


def test_snooze_across_midnight():
    """Snooze set before midnight, checked after. Plain UTC arithmetic must hold."""
    start = datetime(2026, 7, 21, 0, 30, tzinfo=UTC)
    event = _event(start_utc=start, end_utc=start + timedelta(hours=1))
    ann = _ann(
        due_utc=start - timedelta(minutes=60),
        snoozed_until_utc=datetime(2026, 7, 21, 0, 10, tzinfo=UTC),
    )
    assert is_due(datetime(2026, 7, 20, 23, 55, tzinfo=UTC), ann, event, None) is False
    assert is_due(datetime(2026, 7, 21, 0, 15, tzinfo=UTC), ann, event, None) is True


def test_infinite_attempts_never_gives_up():
    ann = _ann(attempts=9999)
    assert give_up(ann, None) is ann
    assert is_due(START, ann, _event(), None) is True


def test_gives_up_once_attempts_exhausted():
    ann = _ann(attempts=5)
    assert give_up(ann, 5).state == "failed"


def test_does_not_give_up_below_the_bound():
    ann = _ann(attempts=4)
    assert give_up(ann, 5) is ann


def test_exhausted_announcement_is_not_due():
    ann = _ann(attempts=5)
    assert is_due(START, ann, _event(), 5) is False


def test_config_defaults_to_infinite_attempts():
    assert Config().max_attempts is None


def test_config_load_missing_file_returns_defaults(tmp_path):
    """Missing file means all defaults are used."""
    config_path = tmp_path / "nonexistent.toml"
    config = Config.load(config_path)
    assert config.max_attempts is None
    assert config.poll_seconds == 300
    assert config.calendar_id == "primary"


def test_config_load_valid_toml_overrides_all_values(tmp_path):
    """Valid TOML file overrides all values."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("max_attempts = 10\npoll_seconds = 60\ncalendar_id = 'work'")

    config = Config.load(config_path)
    assert config.max_attempts == 10
    assert config.poll_seconds == 60
    assert config.calendar_id == "work"


def test_config_load_partial_toml_mixes_defaults(tmp_path):
    """TOML with only some keys leaves others at defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("max_attempts = 5\n")

    config = Config.load(config_path)
    assert config.max_attempts == 5
    assert config.poll_seconds == 300  # default
    assert config.calendar_id == "primary"  # default


def test_config_load_ignores_unknown_keys(tmp_path):
    """TOML with unknown keys silently ignores them."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("max_attempts = 7\nunknown_key = 'ignored'\nanother_unknown = 42\npoll_seconds = 120")

    config = Config.load(config_path)
    assert config.max_attempts == 7
    assert config.poll_seconds == 120
    assert config.calendar_id == "primary"  # default
    assert not hasattr(config, 'unknown_key')
    assert not hasattr(config, 'another_unknown')


def test_config_load_invalid_toml_raises_naming_the_file(tmp_path):
    """A hand-typed typo must fail loudly, not crash-loop with no clue why."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("max_attempts = [unterminated\n")

    with pytest.raises(ValueError, match=r"config\.toml"):
        Config.load(config_path)


def test_config_load_rejects_string_poll_seconds(tmp_path):
    """poll_seconds as a string would otherwise reach asyncio.sleep() and die
    outside the try in _sync_loop, taking the whole gather() down with it."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('poll_seconds = "300"\n')

    with pytest.raises(ValueError, match="poll_seconds"):
        Config.load(config_path)


def test_config_load_rejects_non_positive_poll_seconds(tmp_path):
    """A zero or negative interval turns the sync loop into a busy loop against
    the Google API, which is a good way to get rate-limited."""
    for value in (0, -30):
        config_path = tmp_path / f"config{value}.toml"
        config_path.write_text(f"poll_seconds = {value}\n")

        with pytest.raises(ValueError, match="positive"):
            Config.load(config_path)


def test_config_load_rejects_string_max_attempts(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('max_attempts = "5"\n')

    with pytest.raises(ValueError, match="max_attempts"):
        Config.load(config_path)


def test_config_load_rejects_boolean_max_attempts(tmp_path):
    """TOML booleans are ints under isinstance() - must be explicitly rejected."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("max_attempts = true\n")

    with pytest.raises(ValueError, match="max_attempts"):
        Config.load(config_path)


def test_config_load_still_allows_none_max_attempts_by_omission(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("poll_seconds = 60\n")

    config = Config.load(config_path)
    assert config.max_attempts is None
    assert config.poll_seconds == 60
