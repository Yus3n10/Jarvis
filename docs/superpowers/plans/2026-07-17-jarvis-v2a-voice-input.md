# Jarvis v2a — Voice Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an always-on "hey jarvis" wake word and three spoken commands (acknowledge, snooze, query) that reuse the existing store and Piper, without touching the scheduler.

**Architecture:** A pure command parser (`intent.py`) and a pure answer-phraser turn transcript text into actions and spoken replies; a thin audio-in shell (`ears.py`) wires the mic → wake word → whisper → intent → dispatch. Voice is additive: if the whole layer fails, announcing and touchscreen ack still work.

**Tech Stack:** Python 3.11, openWakeWord (pretrained "hey jarvis"), whisper.cpp (`tiny.en`), Piper (existing), pytest.

**Spec:** `docs/superpowers/specs/2026-07-17-jarvis-v2a-voice-input-design.md`

## Global Constraints

- **Python 3.11** — no 3.12+ syntax.
- **All internal datetimes are timezone-aware UTC.** PHT (UTC+8) conversion only at the display/speech edge.
- **`intent.py` and the answer-phraser are pure** — no audio, no I/O, no clock read (`now` is a parameter). Same discipline as `scheduler.py`.
- **The scheduler, ladder, escalation, store, gcal, api do NOT change.** Voice reuses `store.ack(event_id)` and `store.snooze(event_id, until)`.
- **Bare "snooze" with no number = 10 minutes**, matching `api.py`'s `snooze(event_id, minutes=10)`.
- **Voice is additive, never load-bearing.** Mic/whisper/wakeword failure disables `ears` and logs; the rest of Jarvis is unaffected.
- **No AI/Claude/Anthropic attribution** in commits, code, comments, or docs. The repo is public.
- Commit after every task.

## Consumed interfaces (from v1, unchanged)

- `store.ack(event_id: str) -> None`
- `store.snooze(event_id: str, until: datetime) -> None`
- `store.all_events() -> list[Event]`, `store.all_announcements() -> list[Announcement]`
- `Event(id, title, start_utc, end_utc, declined, reminder_minutes)` — frozen, UTC datetimes
- `voice.speak(text: str) -> bool` — Piper audio-out (existing)

---

## Task 0 — Mic verification (GATE) — COMPLETE

Done manually over SSH on 2026-07-17. Genius WideCam F100 mic on a USB 2.0 port,
ALSA card 2 "USB Audio". Test recording: 16kHz mono, RMS 1561, peak 8199/32767 — healthy,
not clipping; playback confirmed clear by ear. **The gate passed; build on this mic.** No
code. Recorded here so it is not re-run.

---

# Phase 1 — the pure command core (laptop, no Pi, no audio)

## Task 1: Intent parser

**Files:**
- Create: `src/jarvis/intent.py`
- Test: `tests/test_intent.py`

**Interfaces:**
- Consumes: nothing (stdlib only)
- Produces:
  - Frozen dataclasses `Ack`, `Snooze(minutes: int)`, `QueryNext`, `QueryToday`, `Unknown`
  - `Intent = Ack | Snooze | QueryNext | QueryToday | Unknown`
  - `DEFAULT_SNOOZE_MINUTES: int = 10`
  - `parse(transcript: str) -> Intent`

**Precedence** (first match wins): snooze → query-today → query-next → ack → unknown.
Snooze outranks ack because "okay snooze it" is a snooze. Query-today outranks query-next
so "what's on today" isn't caught by a stray "next".

- [ ] **Step 1: Write the failing test**

Create `tests/test_intent.py`:

```python
import pytest

from jarvis.intent import (
    Ack,
    DEFAULT_SNOOZE_MINUTES,
    QueryNext,
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


def test_snooze_outranks_ack():
    # contains both "okay" and "snooze" -> the action wins
    assert parse("okay snooze ten") == Snooze(10)


def test_today_outranks_next():
    assert parse("what's next today") == QueryToday()


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "banana helicopter",
    "turn on the lights",
])
def test_unknown(text):
    assert parse(text) == Unknown()


def test_punctuation_and_case_ignored():
    assert parse("  OKAY!!  ") == Ack()
    assert parse("Snooze, please.") == Snooze(DEFAULT_SNOOZE_MINUTES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_intent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.intent'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/intent.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_intent.py -v`
Expected: PASS (all parametrized cases green).

- [ ] **Step 5: Run the whole suite (nothing regressed)**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 118 prior + the new intent tests, all passing.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/intent.py tests/test_intent.py
git commit -m "feat: add pure intent parser for spoken commands"
```

## Task 2: Query answer phrasing

**Files:**
- Create: `src/jarvis/phrasing.py`
- Test: `tests/test_phrasing.py`

**Interfaces:**
- Consumes: `Event` from `jarvis.domain`
- Produces:
  - `answer_next(now: datetime, events: list[Event]) -> str`
  - `answer_today(now: datetime, events: list[Event]) -> str`
  - Both pure; `now` is a parameter; times rendered in PHT.

**Why pure and separate:** producing the spoken sentence is logic (which event is
"next", how to say the time, the empty-calendar case), distinct from parsing input.
Isolating it means the whole answer surface is testable with a fake clock — no Pi, no
Piper. `ears.py` will pass the result to `voice.speak()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phrasing.py`:

```python
from datetime import UTC, datetime, timedelta

from jarvis.domain import Event
from jarvis.phrasing import answer_next, answer_today

# 2026-07-17 08:00 PHT == 2026-07-17 00:00 UTC
NOW = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)


def _event(title, start_utc):
    return Event(id=title, title=title, start_utc=start_utc,
                 end_utc=start_utc + timedelta(hours=1))


def test_next_names_event_and_pht_time():
    events = [_event("Job interview", NOW + timedelta(hours=3))]  # 11:00 PHT
    out = answer_next(NOW, events)
    assert "Job interview" in out
    assert "11" in out  # 11 AM PHT


def test_next_picks_the_soonest_future_event():
    events = [
        _event("Later thing", NOW + timedelta(hours=5)),
        _event("Sooner thing", NOW + timedelta(hours=1)),
    ]
    assert "Sooner thing" in answer_next(NOW, events)


def test_next_ignores_past_events():
    events = [_event("Done", NOW - timedelta(hours=2))]
    assert "nothing" in answer_next(NOW, events).lower()


def test_next_empty_calendar():
    assert "nothing" in answer_next(NOW, []).lower()


def test_today_lists_todays_events_only():
    events = [
        _event("Morning", NOW + timedelta(hours=2)),
        _event("Tomorrow", NOW + timedelta(days=1)),
    ]
    out = answer_today(NOW, events)
    assert "Morning" in out
    assert "Tomorrow" not in out


def test_today_empty():
    assert "nothing" in answer_today(NOW, []).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_phrasing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.phrasing'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/phrasing.py`:

```python
"""Turn events into spoken answers. Pure -- `now` is a parameter, PHT only here."""

from datetime import datetime, timedelta, timezone

from jarvis.domain import Event

_PHT = timezone(timedelta(hours=8))  # Philippines has never observed DST


def _clock(dt: datetime) -> str:
    local = dt.astimezone(_PHT)
    # strip a leading zero: "07:00 AM" -> "7:00 AM"
    return local.strftime("%I:%M %p").lstrip("0")


def _future_sorted(now: datetime, events: list[Event]) -> list[Event]:
    return sorted(
        (e for e in events if e.start_utc >= now and not e.declined),
        key=lambda e: e.start_utc,
    )


def answer_next(now: datetime, events: list[Event]) -> str:
    upcoming = _future_sorted(now, events)
    if not upcoming:
        return "You have nothing else scheduled."
    e = upcoming[0]
    return f"Next up: {e.title}, at {_clock(e.start_utc)}."


def answer_today(now: datetime, events: list[Event]) -> str:
    today = now.astimezone(_PHT).date()
    todays = [
        e for e in _future_sorted(now, events)
        if e.start_utc.astimezone(_PHT).date() == today
    ]
    if not todays:
        return "You have nothing else scheduled today."
    parts = [f"{e.title} at {_clock(e.start_utc)}" for e in todays]
    return "Today you have: " + "; ".join(parts) + "."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_phrasing.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/phrasing.py tests/test_phrasing.py
git commit -m "feat: add pure query-answer phrasing"
```

---

# Phase 2 — the audio pipeline (Pi, next hardware session)

**Detailed step-by-step for these tasks is written in the hardware session, not now**,
because the exact openWakeWord and whisper.cpp APIs must be pinned against the versions
actually installed on the Pi — writing integration code against uninstalled libraries
would be fabrication. Each task below is spec-level; each becomes a full TDD task (with
real code and commands) once its library is installed and its API verified on the Pi.

## Task 3 — whisper.cpp transcription shell

- Build/install whisper.cpp on the Pi; fetch the `tiny.en` model.
- `src/jarvis/hearing.py`: `transcribe(wav_path: Path) -> str` — thin subprocess wrapper,
  mirroring `voice.py`'s style (returns `""` on failure, never raises).
- Verify by ear + transcript on the Pi: record the mic-test clip, transcribe, eyeball the text.

## Task 4 — openWakeWord wake detection shell

- Install openWakeWord + the pretrained "hey jarvis" model on the Pi.
- Add a wake-detection loop to `ears.py`: continuous 16kHz mono capture from ALSA card 2,
  fire on "hey jarvis".
- Verify trigger reliability on the Pi (false-positive rate, miss rate at ~1m). If the mic
  proves marginal despite Task 0, this is where it shows.

## Task 5 — half-duplex "mouth busy" gate

- A small shared object (e.g. `Mouth` wrapping a `threading.Event`) that the audio-out path
  holds while `voice.speak()` produces audio and `ears` checks before detecting/recording.
- Modify `voice.py` (or wrap it) to set/clear the gate; `ears` respects it.
- **Testable**: assert `ears` dispatches nothing while the gate is held. This task gets a
  real unit test even though it is Phase 2, because the gate is pure coordination logic.

## Task 6 — `ears.py` dispatch wiring

- On wake → record ~4s → `hearing.transcribe` → `intent.parse` → dispatch:
  - `Ack` → `store.ack(event_id)` for the currently-`needs_ack` event(s)
  - `Snooze(n)` → `store.snooze(event_id, now + n min)`
  - `QueryNext`/`QueryToday` → `voice.speak(phrasing.answer_*(now, store.all_events()))`
  - `Unknown` → brief spoken "sorry?"
- The "which event does Ack target" logic reuses the same currently-announced set the
  touchscreen clears.

## Task 7 — integration + deploy + docs

- Run `ears` as an additional task in `__main__.py` alongside sync/tick/server.
- systemd unit: env for the wake model + whisper binary/model paths, matching the existing
  `PIPER_*` pattern (quoted; `XDG_RUNTIME_DIR` already present for audio).
- README: an always-on-mic **privacy** note in the same spirit as Piper's on-device line —
  openWakeWord and whisper run locally, nothing leaves the Pi.
- Reboot test: wake word survives a reboot; `ears` disabling itself when the mic is absent
  never takes down announcing or touch-ack.

---

## Done (Phase 1)

After Phase 1: the entire command brain — understanding input and phrasing answers — exists
and is fully tested on the laptop, with the scheduler untouched. Phase 2 bolts the audio
onto it in a hardware session, and its detailed tasks are written then against the real
library APIs.
