from datetime import UTC, datetime, timedelta

from jarvis.converse import FALLBACK, Conversation, build_prompt
from jarvis.domain import Event

NOW = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)  # 08:00 PHT


def _event(title, start):
    return Event(id=title, title=title, start_utc=start, end_utc=start + timedelta(hours=1))


def test_prompt_includes_name_and_pronunciation():
    p = build_prompt(NOW, [], [], "hello")
    assert "Ptheusen" in p and "Yusen" in p


def test_prompt_includes_pht_time_and_todays_events():
    p = build_prompt(NOW, [_event("Interview", NOW + timedelta(hours=3))], [], "what's up")
    assert "Interview" in p
    assert "11:00 AM" in p  # 08:00 PHT + 3h
    assert "hello" not in p
    assert "what's up" in p


def test_prompt_includes_a_future_day_event():
    # regression: an event on a later day must appear, not just today's
    sat = NOW + timedelta(days=2, hours=3)
    p = build_prompt(NOW, [_event("Meeting Call", sat)], [], "is saturday clear")
    assert "Meeting Call" in p


def test_prompt_includes_bounded_history():
    history = [("hi", "Good morning."), ("how are you", "Quite well.")]
    p = build_prompt(NOW, [], history, "and now?")
    assert "Good morning." in p
    assert "Quite well." in p
    assert "and now?" in p


def test_disabled_without_key():
    c = Conversation(api_key=None)
    assert c.enabled is False
    assert c.reply("hello", NOW, []) == FALLBACK


def test_reply_uses_injected_generator_and_remembers():
    seen = {}

    def fake_gen(prompt):
        seen["prompt"] = prompt
        return "Good morning, Yusen."

    c = Conversation(api_key="x", generate=fake_gen)
    out = c.reply("good morning", NOW, [])
    assert out == "Good morning, Yusen."
    assert "good morning" in seen["prompt"]
    # the exchange is now in memory and appears in the next prompt
    c.reply("and the weather?", NOW, [])
    assert "Good morning, Yusen." in seen["prompt"]


def test_reply_falls_back_when_generator_raises():
    def boom(prompt):
        raise RuntimeError("network down")

    c = Conversation(api_key="x", generate=boom)
    assert c.reply("hello", NOW, []) == FALLBACK


def test_reply_substitutes_name_for_tts():
    c = Conversation(api_key="x", generate=lambda p: "Of course, Ptheusen.")
    assert c.reply("hi", NOW, []) == "Of course, Yusen."


def test_memory_is_bounded():
    c = Conversation(api_key="x", max_turns=2, generate=lambda p: "ok")
    for i in range(5):
        c.reply(f"msg{i}", NOW, [])
    assert len(c._memory) == 2
