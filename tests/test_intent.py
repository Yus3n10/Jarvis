import pytest

from jarvis.intent import (
    Ack,
    DEFAULT_SNOOZE_MINUTES,
    PlugOff,
    PlugOn,
    QueryDate,
    QueryNext,
    QueryTime,
    QueryToday,
    Shutdown,
    Sleep,
    Snooze,
    Wake,
    is_affirmation,
    parse,
)


@pytest.mark.parametrize("text", [
    "shut down", "shutdown", "power off", "power down", "turn off the pi",
])
def test_shutdown(text):
    assert isinstance(parse(text), Shutdown)


@pytest.mark.parametrize("text", [
    "go to sleep", "take a rest", "take a nap", "sleep mode", "go to bed",
])
def test_sleep(text):
    assert isinstance(parse(text), Sleep)


@pytest.mark.parametrize("text", ["wake up", "come back", "resume", "im back"])
def test_wake(text):
    assert isinstance(parse(text), Wake)


@pytest.mark.parametrize("text", ["yes", "yeah", "confirm", "do it", "sure", "go ahead"])
def test_affirmation_true(text):
    assert is_affirmation(text)


@pytest.mark.parametrize("text", ["no", "nope", "what time is it", ""])
def test_affirmation_false(text):
    assert not is_affirmation(text)


def test_turn_off_the_plug_still_beats_shutdown():
    # "turn off" appears in both, but the plug needs a plug noun and is matched
    # first, so a plug command never triggers a shutdown.
    assert isinstance(parse("turn off the plug"), PlugOff)


@pytest.mark.parametrize("text", [
    "turn on the plug",
    "turn the plug on",
    "switch on the charger",
    "plug on",
    "power on the outlet",
    "start the charger",
])
def test_plug_on(text):
    assert isinstance(parse(text), PlugOn)


@pytest.mark.parametrize("text", [
    "turn off the plug",
    "turn the charger off",
    "switch off the outlet",
    "plug off",
    "shut off the charger",
    "stop the charger",
])
def test_plug_off(text):
    assert isinstance(parse(text), PlugOff)


def test_off_beats_on_when_both_present():
    # deliberate: "off" is checked first, so a mixed utterance stays safe-off
    assert isinstance(parse("turn the plug off not on"), PlugOff)


@pytest.mark.parametrize("text", [
    "on",              # no device noun -> not a plug command
    "turn it off",     # no device noun
    "what's the charger",  # device noun but no on/off signal
])
def test_bare_direction_is_not_a_plug_command(text):
    assert not isinstance(parse(text), (PlugOn, PlugOff))


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
