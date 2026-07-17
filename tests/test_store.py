from datetime import UTC, datetime, timedelta

import pytest

from jarvis.domain import Event
from jarvis.store import Store

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def _event(id="evt1", title="Job interview", start=START, **kw):
    defaults = dict(end_utc=start + timedelta(hours=1), reminder_minutes=(60, 10))
    return Event(id=id, title=title, start_utc=start, **{**defaults, **kw})


def test_roundtrip_event_preserves_utc(store):
    store.upsert_events([_event()])
    got = store.all_events()
    assert len(got) == 1
    assert got[0].start_utc == START
    assert got[0].start_utc.tzinfo is not None
    assert got[0].reminder_minutes == (60, 10)


def test_upsert_plans_announcements(store):
    store.upsert_events([_event()])
    anns = store.all_announcements()
    assert sorted(a.rung_minutes for a in anns) == [10, 60]


def test_declined_event_plans_nothing(store):
    store.upsert_events([_event(declined=True)])
    assert store.all_announcements() == []


def test_resync_does_not_reset_ack_state(store):
    """The bug that would make Jarvis nag about an acked event forever."""
    store.upsert_events([_event()])
    store.ack("evt1")
    store.upsert_events([_event()])  # same event, polled again
    assert {a.state for a in store.all_announcements()} == {"acked"}


def test_moving_an_event_replans_its_announcements(store):
    store.upsert_events([_event()])
    moved = START + timedelta(hours=2)
    store.upsert_events([_event(start=moved)])
    anns = store.all_announcements()
    assert all(a.state == "pending" for a in anns)
    assert {a.due_utc for a in anns} == {
        moved - timedelta(minutes=60),
        moved - timedelta(minutes=10),
    }


def test_mark_spoken_increments_attempts(store):
    store.upsert_events([_event()])
    ann = next(a for a in store.all_announcements() if a.rung_minutes == 60)
    store.mark_spoken(ann, START)
    store.mark_spoken(ann, START + timedelta(minutes=10))
    got = next(a for a in store.all_announcements() if a.rung_minutes == 60)
    assert got.attempts == 2
    assert got.last_spoken_utc == START + timedelta(minutes=10)


def test_snooze_applies_to_all_pending_for_event(store):
    store.upsert_events([_event()])
    until = START - timedelta(minutes=30)
    store.snooze("evt1", until)
    assert all(a.snoozed_until_utc == until for a in store.all_announcements())


def test_heartbeat_roundtrip(store):
    assert store.last_heartbeat() is None
    store.heartbeat(START)
    assert store.last_heartbeat() == START


def test_changing_reminders_replans_announcements(store):
    store.upsert_events([_event(reminder_minutes=(60, 10))])
    store.upsert_events([_event(reminder_minutes=(60, 90))])
    anns = store.all_announcements()
    assert sorted(a.rung_minutes for a in anns) == [60, 90]
    assert all(a.state == "pending" for a in anns)
    assert {a.due_utc for a in anns} == {
        START - timedelta(minutes=60),
        START - timedelta(minutes=90),
    }


def test_changing_reminders_discards_stale_ack_state(store):
    store.upsert_events([_event(reminder_minutes=(60, 10))])
    store.ack("evt1")
    store.upsert_events([_event(reminder_minutes=(60, 90))])
    anns = store.all_announcements()
    assert sorted(a.rung_minutes for a in anns) == [60, 90]
    assert all(a.state == "pending" for a in anns)


def test_mark_state_sets_state(store):
    store.upsert_events([_event()])
    ann = next(a for a in store.all_announcements() if a.rung_minutes == 60)
    store.mark_state(ann, "failed")
    got = next(a for a in store.all_announcements() if a.rung_minutes == 60)
    assert got.state == "failed"


def test_reordered_reminders_do_not_replan(store):
    """Reordered reminders should not trigger replan; ack state must survive."""
    store.upsert_events([_event(reminder_minutes=(60, 10))])
    store.ack("evt1")
    store.upsert_events([_event(reminder_minutes=(10, 60))])
    assert {a.state for a in store.all_announcements()} == {"acked"}


def test_duplicate_reminders_do_not_replan(store):
    """Duplicate reminders should not trigger replan; ack state must survive."""
    store.upsert_events([_event(reminder_minutes=(60, 10))])
    store.ack("evt1")
    store.upsert_events([_event(reminder_minutes=(60, 60, 10))])
    assert {a.state for a in store.all_announcements()} == {"acked"}


def test_prune_absent_deletes_event_and_its_announcements(store):
    store.upsert_events([_event(id="evt1"), _event(id="evt2")])
    store.prune_absent({"evt2"})
    assert [e.id for e in store.all_events()] == ["evt2"]
    assert all(a.event_id != "evt1" for a in store.all_announcements())


def test_prune_absent_keeps_events_in_keep_ids_including_ack_state(store):
    store.upsert_events([_event(id="evt1"), _event(id="evt2")])
    store.ack("evt1")
    store.prune_absent({"evt1", "evt2"})
    got = store.all_events()
    assert {e.id for e in got} == {"evt1", "evt2"}
    assert {a.state for a in store.all_announcements() if a.event_id == "evt1"} == {"acked"}


def test_prune_absent_with_empty_keep_ids_deletes_everything(store):
    store.upsert_events([_event(id="evt1"), _event(id="evt2")])
    store.prune_absent(set())
    assert store.all_events() == []
    assert store.all_announcements() == []


def test_prune_absent_returns_count_deleted(store):
    store.upsert_events([_event(id="evt1"), _event(id="evt2"), _event(id="evt3")])
    deleted = store.prune_absent({"evt3"})
    assert deleted == 2
