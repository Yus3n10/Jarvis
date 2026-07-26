from datetime import UTC, datetime, timedelta

from jarvis.domain import Announcement, Event
from jarvis.ears import handle

NOW = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)  # 08:00 PHT


class FakeStore:
    def __init__(self, events=(), announcements=()):
        self._events = list(events)
        self._anns = list(announcements)
        self.acked = []
        self.snoozed = []

    def all_events(self):
        return self._events

    def all_announcements(self):
        return self._anns

    def ack(self, event_id):
        self.acked.append(event_id)

    def snooze(self, event_id, until):
        self.snoozed.append((event_id, until))


class FakeConversation:
    def __init__(self, enabled=True, text="A dry remark, sir."):
        self.enabled = enabled
        self._text = text
        self.calls = []

    def reply(self, text, now, events):
        self.calls.append(text)
        return self._text


def _spoken_pending(event_id="evt1"):
    return Announcement(
        event_id=event_id,
        rung_minutes=60,
        due_utc=NOW,
        state="pending",
        last_spoken_utc=NOW,  # spoken => needs ack
    )


def test_time_query_is_local():
    out = handle("what time is it", NOW, FakeStore(), FakeConversation())
    assert out.startswith("It's")


def test_today_query_is_local():
    ev = Event(id="e", title="Standup", start_utc=NOW + timedelta(hours=1),
               end_utc=NOW + timedelta(hours=2))
    out = handle("what's on today", NOW, FakeStore(events=[ev]), FakeConversation())
    assert "Standup" in out


def test_unknown_routes_to_conversation():
    conv = FakeConversation(text="I do try, Yusen.")
    out = handle("tell me a joke", NOW, FakeStore(), conv)
    assert out == "I do try, Yusen."
    assert conv.calls == ["tell me a joke"]


def test_unknown_without_conversation_falls_back():
    out = handle("tell me a joke", NOW, FakeStore(), FakeConversation(enabled=False))
    assert out == "Sorry, I didn't catch that."


def test_ack_acknowledges_a_spoken_pending_event():
    store = FakeStore(announcements=[_spoken_pending("evt1")])
    out = handle("okay", NOW, store, FakeConversation())
    assert out == "Okay, acknowledged."
    assert store.acked == ["evt1"]


def test_ack_with_nothing_pending():
    out = handle("okay", NOW, FakeStore(), FakeConversation())
    assert out == "There is nothing to acknowledge."


def test_snooze_snoozes_a_spoken_pending_event():
    store = FakeStore(announcements=[_spoken_pending("evt1")])
    out = handle("snooze ten minutes", NOW, store, FakeConversation())
    assert out == "Snoozed for 10 minutes."
    assert store.snoozed and store.snoozed[0][0] == "evt1"


def test_command_never_reaches_conversation():
    conv = FakeConversation()
    handle("what time is it", NOW, FakeStore(), conv)
    handle("what's on today", NOW, FakeStore(), conv)
    assert conv.calls == []  # commands are local; the LLM is never consulted


class FakePlug:
    def __init__(self, enabled=True, ok=True):
        self.enabled = enabled
        self._ok = ok
        self.calls = []

    def turn_on(self):
        self.calls.append("on")
        return self._ok

    def turn_off(self):
        self.calls.append("off")
        return self._ok


def test_plug_on_switches_and_never_reaches_conversation():
    conv = FakeConversation()
    plug = FakePlug()
    out = handle("turn on the plug", NOW, FakeStore(), conv, plug)
    assert out == "Okay, switching it on."
    assert plug.calls == ["on"]
    assert conv.calls == []  # a device command must never fall through to the LLM


def test_plug_off_switches():
    plug = FakePlug()
    out = handle("turn off the charger", NOW, FakeStore(), FakeConversation(), plug)
    assert out == "Okay, switching it off."
    assert plug.calls == ["off"]


def test_plug_unreachable_is_reported():
    plug = FakePlug(ok=False)
    out = handle("turn on the plug", NOW, FakeStore(), FakeConversation(), plug)
    assert out == "Sorry, I couldn't reach the plug."


def test_plug_command_without_a_plug_is_graceful():
    out = handle("turn on the plug", NOW, FakeStore(), FakeConversation(), None)
    assert out == "The plug isn't set up."
