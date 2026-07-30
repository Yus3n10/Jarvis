import pytest

from jarvis.faq import answer


@pytest.mark.parametrize("q", [
    "who is your creator",
    "who made you",
    "what created you",
    "what are you for",
    "why were you made",
    "why did Ptheusen create you",
    "What's your purpose?",
])
def test_creator_and_purpose(q):
    assert "created by Yusen" in answer(q)


@pytest.mark.parametrize("q", [
    "what is your name",
    "what's your name",
    "who are you",
    "what are you",
])
def test_name(q):
    assert answer(q).startswith("My name is Pace")


def test_purpose_beats_name():
    # "what are you for" contains "what are you"; purpose must still win.
    assert "created by Yusen" in answer("what are you for")


@pytest.mark.parametrize("q", ["what's the weather", "tell me a joke", "", "   "])
def test_no_match_returns_none(q):
    assert answer(q) is None
