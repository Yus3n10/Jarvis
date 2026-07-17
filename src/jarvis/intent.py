"""Understand a spoken command. Pure -- transcript string in, Intent out.

No audio, no I/O, no clock. Every hard bug in command understanding lives here
and is catchable with a plain string and no Pi. Rule-based on purpose: the
command vocabulary is tiny and structured, so a language model would be a hot
Pi solving a problem we do not have.
"""

import re
from dataclasses import dataclass

DEFAULT_SNOOZE_MINUTES = 10


@dataclass(frozen=True)
class Ack:
    pass


@dataclass(frozen=True)
class Snooze:
    minutes: int


@dataclass(frozen=True)
class QueryNext:
    pass


@dataclass(frozen=True)
class QueryToday:
    pass


@dataclass(frozen=True)
class Unknown:
    pass


Intent = Ack | Snooze | QueryNext | QueryToday | Unknown

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "forty five": 45,
    "sixty": 60,
}

_ACK_WORDS = (
    "okay", "ok", "kay", "got it", "heard you", "i heard you", "acknowledge",
    "acknowledged", "yes", "yep", "yeah", "thanks", "thank you", "stop",
    "alright", "all right", "roger", "copy", "i'm up", "im up",
)

_SNOOZE_WORDS = ("snooze", "remind me later", "later", "give me")
_TODAY_WORDS = ("today", "rest of my day", "rest of the day", "my day")
_NEXT_WORDS = ("next", "coming up", "after this", "upcoming")


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_minutes(text: str) -> int:
    digits = re.search(r"\b(\d{1,3})\b", text)
    if digits:
        return int(digits.group(1))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return value
    return DEFAULT_SNOOZE_MINUTES


def _has_any(text: str, needles) -> bool:
    return any(n in text for n in needles)


def parse(transcript: str) -> Intent:
    """Map a transcript to a command. First match wins, in a deliberate order:
    snooze > query-today > query-next > ack > unknown."""
    text = _normalize(transcript)
    if not text:
        return Unknown()

    if _has_any(text, _SNOOZE_WORDS):
        return Snooze(_extract_minutes(text))
    if _has_any(text, _TODAY_WORDS):
        return QueryToday()
    if _has_any(text, _NEXT_WORDS):
        return QueryNext()
    if _has_any(text, _ACK_WORDS):
        return Ack()
    return Unknown()
