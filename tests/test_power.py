from datetime import UTC, datetime, timedelta

from jarvis.power import Power

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _power():
    calls = {"poweroff": 0, "display": []}
    p = Power(
        poweroff=lambda: calls.__setitem__("poweroff", calls["poweroff"] + 1),
        set_display=lambda on: calls["display"].append(on),
        window_seconds=30,
    )
    return p, calls


def test_shutdown_needs_confirmation_before_powering_off():
    p, calls = _power()
    msg = p.request_shutdown(NOW)
    assert "yes" in msg.lower()
    assert calls["poweroff"] == 0  # asking is not doing
    assert p.awaiting_confirmation(NOW)
    assert p.confirm() == "Shutting down. Goodbye."
    assert calls["poweroff"] == 1


def test_confirmation_window_expires():
    p, _ = _power()
    p.request_shutdown(NOW)
    assert p.awaiting_confirmation(NOW + timedelta(seconds=29))
    assert not p.awaiting_confirmation(NOW + timedelta(seconds=31))


def test_cancel_clears_the_pending_shutdown():
    p, calls = _power()
    p.request_shutdown(NOW)
    p.cancel()
    assert not p.awaiting_confirmation(NOW)
    assert calls["poweroff"] == 0


def test_sleep_blanks_the_screen_and_wake_restores_it():
    p, calls = _power()
    assert p.sleeping is False
    assert p.sleep() == "Resting. Say wake up whenever you need me."
    assert p.sleeping is True
    assert calls["display"][-1] is False
    assert p.wake() == "I'm back."
    assert p.sleeping is False
    assert calls["display"][-1] is True


def test_wake_when_already_awake_is_a_no_op_message():
    p, _ = _power()
    assert p.wake() == "I'm already awake."
