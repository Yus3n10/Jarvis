# Jarvis v2c — Conversational Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a spoken utterance is not a known command (`Unknown`), send its text plus grounding context to Google Gemini and speak a short conversational reply — while every real action stays local and deterministic.

**Architecture:** A new isolated module `converse.py` is the only thing that talks to Gemini. A pure `build_prompt` assembles persona + name + PHT time + today's schedule + bounded history + the user text; a stateful `Conversation.reply` owns the memory, calls Gemini via an injectable generator, sanitizes and returns the reply, and falls back gracefully on any failure. Reached only on `Unknown`.

**Tech Stack:** Python 3.11, `google-genai` SDK, Gemini `gemini-2.0-flash`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-18-jarvis-v2c-conversation-design.md`

## Global Constraints

- **Python 3.11** — no 3.12+ syntax.
- **Gemini never acts.** `intent.parse` handles all commands locally; Gemini is called only on `Unknown`, only to produce speech. No state change ever routes through the LLM.
- **All internal datetimes timezone-aware UTC.** PHT (UTC+8) only at the speech edge.
- **Conversation is never load-bearing.** No key, no internet, an API error, or rate-limiting must return a fallback string and never raise or block.
- **`converse.py` must import without the `google-genai` SDK installed** (lazy import inside the real generator), so the pure tests run on the laptop with nothing extra.
- **The API key is never committed and never seen in chat.** It lives only in `GEMINI_API_KEY` on the Pi, sourced from a gitignored file.
- **Name pronunciation:** replies must say "Yusen" (phonetic), not "Ptheusen". Enforced in the persona AND by a text substitution before speech.
- **No AI/Claude/Anthropic attribution** in commits, code, comments, or docs. The repo is public.
- Commit after every task.

## Consumed interfaces (unchanged)

- `Event(id, title, start_utc, end_utc, declined, reminder_minutes)` from `jarvis.domain` — frozen, UTC.
- `intent.parse(text) -> Intent` and `Unknown` from `jarvis.intent`.

---

## Task 1: `converse.py` core (pure + stateful, laptop-testable)

**Files:**
- Create: `src/jarvis/converse.py`
- Test: `tests/test_converse.py`

**Interfaces:**
- Produces:
  - `PERSONA: str` — the system persona (includes the Yusen pronunciation instruction).
  - `FALLBACK: str = "Sorry, I can't reach my brain right now."`
  - `build_prompt(now: datetime, events: list[Event], history: list[tuple[str, str]], text: str) -> str` — pure.
  - `Conversation(api_key: str | None, model: str = "gemini-2.0-flash", max_turns: int = 6, generate=None)` with `.enabled: bool` and `.reply(text: str, now: datetime, events: list[Event]) -> str`.
  - The `generate` param is an injectable `Callable[[str], str]` for testing; when `None`, the real Gemini call (Task 2) is used lazily.

- [ ] **Step 1: Write the failing test**

Create `tests/test_converse.py`:

```python
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
    c2 = c.reply("and the weather?", NOW, [])
    # not asserting on c2's text (same fake), but the first exchange must be remembered:
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
    # only the last 2 exchanges are retained
    assert len(c._memory) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_converse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.converse'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/converse.py`:

```python
"""Conversational replies via Gemini. The only module that talks to Gemini.

Reached ONLY when intent.parse returns Unknown -- Gemini never performs an action.
Every failure path (no key, no internet, API error, bad reply) returns FALLBACK and
never raises: conversation is a bonus layer, never load-bearing.

The google-genai SDK is imported lazily inside the real generator so this module (and its
pure tests) load with nothing extra installed.
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
    "You are Jarvis, a calm, dry, concise butler. Reply in at most two short sentences; "
    "your words are spoken aloud, so never use lists, markdown, or emoji. "
    "You cannot perform any actions (you cannot set reminders, alarms, or control "
    "devices) -- you only converse; never claim to have done something. "
    'The user\'s name is Ptheusen, pronounced "Yu-sen". Because your reply is spoken by a '
    'text-to-speech voice, always write his name phonetically as "Yusen" so it is said '
    "correctly."
)


def _clock(dt: datetime) -> str:
    return dt.astimezone(_PHT).strftime("%I:%M %p").lstrip("0")


def build_prompt(
    now: datetime,
    events: list[Event],
    history: list[tuple[str, str]],
    text: str,
) -> str:
    """Assemble the full Gemini prompt. Pure -- all inputs are arguments."""
    local = now.astimezone(_PHT)
    lines = [PERSONA, "", f"Current time: {local.strftime('%A, %B %d, %I:%M %p').replace(' 0', ' ')} (Philippine time)."]

    today = local.date()
    todays = sorted(
        (e for e in events if e.start_utc.astimezone(_PHT).date() == today and not e.declined),
        key=lambda e: e.start_utc,
    )
    if todays:
        lines.append("His schedule today: " + "; ".join(
            f"{e.title} at {_clock(e.start_utc)}" for e in todays
        ))
    else:
        lines.append("His schedule today: nothing scheduled.")

    if history:
        lines.append("")
        lines.append("Recent conversation:")
        for user, jarvis in history:
            lines.append(f"  Ptheusen: {user}")
            lines.append(f"  Jarvis: {jarvis}")

    lines.append("")
    lines.append(f"Ptheusen: {text}")
    lines.append("Jarvis:")
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
        model: str = "gemini-2.0-flash",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_converse.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 166 prior + the new converse tests, all passing.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/converse.py tests/test_converse.py
git commit -m "feat: add Gemini conversation module (local-first, graceful fallback)"
```

---

## Task 2: Install the SDK + live Gemini verification (Pi)

**Files:**
- Modify: `pyproject.toml` (add `google-genai` to the `[voice]` extra)

**This task needs the real API key on the Pi and internet. The key is placed by the user,
never seen in chat.**

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to the voice optional-dependencies group: `"google-genai>=0.3"`.

- [ ] **Step 2: Install on the Pi**

```
.venv/bin/pip install google-genai
```
Expected: installs cleanly (pure-Python + httpx).

- [ ] **Step 3: User places the key (guided; controller never sees it)**

The user runs, on the Pi's own terminal (not via the controller):

```
install -m 600 /dev/null ~/jarvis/.gemini_env
printf 'GEMINI_API_KEY=%s\n' 'PASTE_KEY_HERE' > ~/jarvis/.gemini_env
```

`.gemini_env` must be gitignored (add it). Confirm the file exists and is not empty
without printing its contents.

- [ ] **Step 4: Live verification**

Run on the Pi (the key sourced from the file, not echoed):

```
set -a; . ~/jarvis/.gemini_env; set +a
.venv/bin/python -c "
import os
from datetime import datetime, UTC
from jarvis.converse import Conversation
c = Conversation(os.environ.get('GEMINI_API_KEY'))
print('enabled:', c.enabled)
print(c.reply('Good evening, introduce yourself in one sentence.', datetime.now(UTC), []))
"
```
Expected: `enabled: True` and a one-to-two sentence in-character reply addressing "Yusen".

- [ ] **Step 5: Commit the dependency**

```bash
git add pyproject.toml .gitignore
git commit -m "chore: add google-genai to the voice extra"
```

---

## Task 3: Wire conversation into the dispatch + live demo

**Files:**
- Modify: the standalone demo loop (Pi) now; the real `ears.py` when it is codified (deferred to v2a completion).

**Interfaces:**
- Consumes: `Conversation.enabled`, `Conversation.reply`, `intent.parse`, `Unknown`.

- [ ] **Step 1: Routing rule**

In the dispatch, replace the `Unknown` branch:

```python
if isinstance(intent, Unknown):
    if conversation.enabled:
        return conversation.reply(text, datetime.now(UTC), store.all_events())
    return "Sorry, I didn't catch that."
```
Known commands are unchanged — they never reach Gemini.

- [ ] **Step 2: Live demo on the Pi**

Extend the standalone demo loop to construct `Conversation(os.environ["GEMINI_API_KEY"])`
and use the routing above. Run it; the user says "Hey Jarvis" then a non-command
("how are you", "tell me something about mornings"), and hears an in-character spoken reply.
Confirm a known command ("what's on today") still answers locally with no Gemini call.

- [ ] **Step 3: Verify graceful degradation live**

Temporarily unset the key (`unset GEMINI_API_KEY`) and confirm `Unknown` → "Sorry, I didn't
catch that" and commands still work — i.e. Jarvis is exactly today's appliance without a key.

---

## Task 4: README privacy revision

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Revise the privacy claim honestly**

Replace the "nothing leaves the Pi" line with:

> Commands are handled entirely on-device. The optional conversation layer sends your
> transcribed words, the day's schedule, and recent conversation turns to Google's Gemini
> API, and is disabled when no API key is configured. The always-on microphone never
> streams anywhere — only deliberate, post-wake, non-command speech is sent.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: revise privacy note for the optional Gemini conversation layer"
```

---

## Deferred (depends on unbuilt v2a `ears.py`)

Final integration into the running systemd service — constructing the `Conversation` in
`__main__.py`, sourcing `GEMINI_API_KEY` via the unit, and having the real `ears.py` dispatch
call it — lands when `ears.py` is codified (v2a Tasks 4–5). Until then, Task 3's demo loop
proves conversation end-to-end.

## Done

`converse.py` exists, is fully unit-tested on the laptop, verified live against Gemini on the
Pi, and demonstrated end-to-end through the voice loop — reached only on `Unknown`, grounded
in name/time/schedule with bounded memory, gracefully degrading with no key or no internet,
and the scheduler untouched.
