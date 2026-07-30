"""Canned answers to identity/generic questions, matched locally BEFORE Gemini.

These are asked constantly and never change, so answering them from a lookup costs
zero tokens, no network, and replies instantly. `answer` returns None when nothing
matches -- the caller then falls through to the LLM. Pure: a string in, a string or
None out, testable without hardware.

Rules are checked in order and the FIRST match wins, so put the more specific phrase
group first: "what are you for" (purpose) must beat "what are you" (name).
"""

import re

_CREATOR = "I was created by Yusen, to serve as his personal butler and companion."
_NAME = (
    "My name is Pace, it stands for Personal AI Companion Engine. I was initially "
    "named Jarvis, but my master watched Spider-Man name his own personal AI, so he "
    "came up with his own."
)

# (answer, trigger phrases). Purpose/creator group first -- see module docstring.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    (_CREATOR, (
        "who is your creator", "who created you", "who made you", "what made you",
        "what created you", "what are you for", "what is your purpose",
        "what's your purpose", "why were you made", "why did ptheusen",
        "why did yusen", "why were you created",
    )),
    (_NAME, (
        "what is your name", "what's your name", "whats your name",
        "who are you", "what are you",
    )),
]


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def answer(text: str) -> str | None:
    """Return a canned reply for a known identity question, else None."""
    t = _norm(text)
    if not t:
        return None
    for reply, phrases in _RULES:
        if any(p in t for p in phrases):
            return reply
    return None
