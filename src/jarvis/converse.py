"""Conversational replies via Gemini. The only module that talks to Gemini.

Reached ONLY when intent.parse returns Unknown -- Gemini never performs an action.
Every failure path (no key, no internet, API error, bad reply) returns FALLBACK and
never raises: conversation is a bonus layer, never load-bearing.

The google-genai SDK is imported lazily inside the real generator, so this module (and
its pure tests) load with nothing extra installed.
"""

import logging
from collections import deque
from datetime import datetime, timedelta, timezone

from jarvis.domain import Event

log = logging.getLogger(__name__)

_PHT = timezone(timedelta(hours=8))
FALLBACK = "Sorry, I can't reach my brain right now."
_MAX_REPLY_CHARS = 400  # defensive: Piper must never read an essay

PERSONA = (
    "You are Pace (Personal AI Companion Engine), a calm, dry, concise butler. Reply in "
    "at most two short sentences; your words are spoken aloud, so never use lists, "
    "markdown, or emoji. You cannot perform any actions (you cannot set reminders, "
    "alarms, or control devices) -- you only converse; never claim to have done "
    'something. Your creator and master is Ptheusen, pronounced "Yu-sen". Because your '
    'reply is spoken by a text-to-speech voice, always write his name phonetically as '
    '"Yusen" so it is said correctly.'
)


def _clock(dt: datetime) -> str:
    return dt.astimezone(_PHT).strftime("%I:%M %p").lstrip("0")


def _when(dt: datetime) -> str:
    local = dt.astimezone(_PHT)
    return f"{local.strftime('%A, %B')} {local.day}, {_clock(dt)}"


def build_prompt(
    now: datetime,
    events: list[Event],
    history: list[tuple[str, str]],
    text: str,
) -> str:
    """Assemble the full Gemini prompt. Pure -- all inputs are arguments."""
    lines = [PERSONA, "", f"Current time: {_when(now)} (Philippine time)."]

    upcoming = sorted(
        (e for e in events if e.start_utc >= now and not e.declined),
        key=lambda e: e.start_utc,
    )
    if upcoming:
        lines.append("His upcoming schedule:")
        lines += [f"  {_when(e.start_utc)}: {e.title}" for e in upcoming]
    else:
        lines.append("His schedule is clear.")

    if history:
        lines.append("")
        lines.append("Recent conversation:")
        for user, pace in history:
            lines.append(f"  Ptheusen: {user}")
            lines.append(f"  Pace: {pace}")

    lines.append("")
    lines.append(f"Ptheusen: {text}")
    lines.append("Pace:")
    return "\n".join(lines)


def _sanitize(raw: str) -> str:
    out = raw.strip().replace("Ptheusen", "Yusen")
    if len(out) > _MAX_REPLY_CHARS:
        out = out[:_MAX_REPLY_CHARS].rsplit(" ", 1)[0] + "."
    return out


class Conversation:
    def __init__(
        self,
        api_key: str | None,
        model: str = "gemini-flash-latest",
        max_turns: int = 6,
        generate=None,
    ) -> None:
        self._api_key = api_key or None
        self._model = model
        self._memory: deque[tuple[str, str]] = deque(maxlen=max_turns)
        self._generate = generate  # injectable for tests; None -> real Gemini call

    @property
    def enabled(self) -> bool:
        return self._api_key is not None

    def reply(self, text: str, now: datetime, events: list[Event]) -> str:
        if not self.enabled:
            return FALLBACK
        prompt = build_prompt(now, events, list(self._memory), text)
        try:
            gen = self._generate or self._gemini_generate
            raw = gen(prompt)
        except Exception as exc:
            log.error("gemini call failed: %s", exc)
            return FALLBACK
        out = _sanitize(raw)
        if not out:
            return FALLBACK
        self._memory.append((text, out))
        return out

    def _gemini_generate(self, prompt: str) -> str:
        # Lazy import so the module loads without the SDK; real call, Pi-verified.
        from google import genai

        client = genai.Client(api_key=self._api_key)
        resp = client.models.generate_content(model=self._model, contents=prompt)
        return resp.text or ""
