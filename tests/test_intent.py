import pytest

from jarvis.intent import (
    Ack,
    DEFAULT_SNOOZE_MINUTES,
    QueryDate,
    QueryNext,
    QueryTime,
    QueryToday,
    Snooze,
    Unknown,
    parse,
)


@pytest.mark.parametrize("text", [
    "okay",
    "OK",
    "okay got it",
    "got it",
    "i heard you",
    "yes jarvis",
    "alright thanks",
    "stop",
])
def test_acknowledgements(text):
    assert parse(text) == Ack()


@pytest.mark.parametrize("text,minutes", [
    ("snooze", DEFAULT_SNOOZE_MINUTES),
    ("snooze it", DEFAULT_SNOOZE_MINUTES),
    ("snooze five minutes", 5),
    ("snooze for ten minutes", 10),
    ("snooze fifteen", 15),
    ("snooze 20 minutes", 20),
    ("remind me later", DEFAULT_SNOOZE_MINUTES),
    ("uh snooze it ten minutes", 10),
])
def test_snooze(text, minutes):
    assert parse(text) == Snooze(minutes)


@pytest.mark.parametrize("text", [
    "what's next",
    "whats next",
    "what do i have next",
    "what's coming up",
])
def test_query_next(text):
    assert parse(text) == QueryNext()


@pytest.mark.parametrize("text", [
    "what's on today",
    "what do i have today",
    "today's schedule",
    "what's the rest of my day",
])
def test_query_today(text):
    assert parse(text) == QueryToday()


@pytest.mark.parametrize("text", [
    "what time is it",
    "what's the time",
    "tell me the time",
    "current time",
])
def test_query_time(text):
    assert parse(text) == QueryTime()


@pytest.mark.parametrize("text", [
    "what day is it",
    "what's the date",
    "what date is it today",
    "today's date",
])
def test_query_date(text):
    assert parse(text) == QueryDate()


def test_snooze_outranks_ack():
    # contains both "okay" and "snooze" -> the action wins
    assert parse("okay snooze ten") == Snooze(10)


def test_today_outranks_next():
    assert parse("what's next today") == QueryToday()


def test_time_outranks_today():
    assert parse("what time is it today") == QueryTime()


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "banana helicopter",
    "turn on the lights",
])
def test_unknown(text):
    assert parse(text) == Unknown()


@pytest.mark.parametrize("text", [
    "tell me a joke",       # "joke" contains "ok" -- must NOT match Ack
    "that is no joke",
    "i think it is broken",  # "broken" contains "ok"
])
def test_substrings_do_not_false_match_commands(text):
    # keyword matching must be on whole words, not substrings
    assert parse(text) == Unknown()


def test_punctuation_and_case_ignored():
    assert parse("  OKAY!!  ") == Ack()
    assert parse("Snooze, please.") == Snooze(DEFAULT_SNOOZE_MINUTES)
