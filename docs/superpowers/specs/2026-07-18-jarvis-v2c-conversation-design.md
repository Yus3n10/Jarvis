# Jarvis v2c — conversational layer (Gemini)

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Builds on:** v1 (deployed), v2a voice input (intent/hearing/phrasing committed; ears.py/service integration still pending)

## The idea

v2a gives Jarvis a fixed command vocabulary (ack, snooze, query, time, date) via local
rule-based parsing. v2c adds *conversation*: when what you said is not a known command,
the transcribed text is sent to Google's Gemini for a spoken reply. Jarvis gains a bit of
personality and can talk about your day — while every real action stays local and
deterministic.

## The governing rule: Gemini never acts

`intent.parse` (local, rule-based) runs first. If it returns a known command
(`Ack`, `Snooze`, `QueryNext`, `QueryToday`, `QueryTime`, `QueryDate`), Jarvis executes it
locally — instant, free, offline, deterministic. **Gemini is reached only when
`intent.parse` returns `Unknown`, and only to produce conversational speech.** An LLM can
never acknowledge, snooze, or otherwise change scheduler state. A hallucinated ack is a
missed appointment, which is the one failure this whole project exists to prevent.

## Goals

- On `Unknown`, produce a short, grounded, spoken conversational reply via Gemini.
- Ground replies in the user's name, the current PHT time/date, today's schedule, and the
  last few conversation turns (short-term memory).
- Degrade gracefully: no key, no internet, an API error, or rate-limiting must never crash
  or block Jarvis. Conversation is a bonus layer, never load-bearing.

## Non-goals

- **Gemini performing actions.** It only speaks. All state changes stay in `intent.py` +
  `store`. (Supersedes v2a's blanket "no conversational AI" non-goal for chit-chat only.)
- **Long-term / persisted memory.** Conversation memory is an in-process rolling buffer
  that resets on restart. A durable memory store is a separate future feature.
- **Replacing local STT/TTS/wake word.** whisper, Piper, and openWakeWord stay 100% local.
  Only post-`Unknown` transcript text (plus grounding context) is sent to Gemini.

## Architecture

```
transcript --> intent.parse  (local, instant, free, offline)
                   |
        +----------+-----------+
     a command              Unknown
   (Ack/Snooze/Query*)         |
        |                      v
   execute locally      converse.reply(text, now, events)
   via store/phrasing         |  builds prompt (persona + name + time
   (no Gemini)                |  + today's events + recent turns + text)
                              v
                        Gemini (gemini-2.0-flash) --> short reply
                              |  (on ANY failure -> graceful fallback string)
                              v
                        Piper speaks it
```

### New module: `src/jarvis/converse.py`

The only module that talks to Gemini (mirroring `gcal.py` as the only one that talks to
Google Calendar). Holds: the Gemini client, the system prompt, a bounded conversation
memory, and:

```python
class Conversation:
    def __init__(self, api_key: str | None, model: str = "gemini-2.0-flash",
                 max_turns: int = 6) -> None: ...
    def reply(self, text: str, now: datetime, events: list[Event]) -> str: ...
    @property
    def enabled(self) -> bool: ...   # False when api_key is None/empty
```

- `reply` builds the prompt, calls Gemini, appends the exchange to memory, returns the
  reply text. On **any** exception or when `not enabled`, returns a fallback string and
  never raises.
- Memory: a `collections.deque(maxlen=max_turns)` of `(user, jarvis)` pairs, in-process,
  reset on restart. Not persisted.

### The persona (system prompt)

Establishes: Jarvis is a concise, dry butler; replies are **1–2 sentences** (they are
spoken aloud — a paragraph through a speaker is insufferable); it **cannot perform
actions** (only converse), so it must not promise to set reminders or control anything.

Name handling, with the Piper-pronunciation fix baked in:

> The user's name is Ptheusen, pronounced "Yu-sen". Your replies are spoken aloud by a
> text-to-speech voice, so write his name phonetically as "Yusen" so it is pronounced
> correctly.

A **safety net** in the pipeline also substitutes any stray `Ptheusen` → `Yusen` in the
reply text before Piper, so pronunciation is right even if Gemini ignores the instruction.

### Grounding assembled per call

Persona + current PHT time/date + today's events (title + PHT time) + the last
`max_turns` conversation pairs + the new user text.

## Prompt assembly is pure and testable

Split the stateful shell from the pure logic:

- **Pure:** `build_prompt(now, events, history, text) -> str | list` assembles the full
  request from its arguments — persona, name/pronunciation instruction, PHT time/date,
  today's events, the (already-bounded) history, and the new text. No network, no clock
  read. `history` is passed in explicitly so the builder is fully testable.
- **Stateful shell:** `Conversation.reply` owns the `deque` memory, calls `build_prompt`
  with a snapshot of it, makes the Gemini call, appends the new exchange, and returns the
  reply (or fallback).

Tests assert the schedule, PHT time, name+pronunciation instruction, and bounded history
all appear in `build_prompt`'s output. This is where correctness lives; the network call
is a thin wrapper.

## Model and SDK

- Model: `gemini-2.0-flash` (current free-tier flash model; fast, ~1–3s per reply).
- SDK: Google `google-genai` (the current unified Gen AI SDK), added to the project's
  `[voice]` optional dependency group.
- Free tier from Google AI Studio is ample for a single user.

## Configuration and the API key

- The key is provided via the environment variable `GEMINI_API_KEY` (systemd
  `Environment=`, sourced from a gitignored file — never committed, exactly like
  `credentials.json`/`token.json`).
- If `GEMINI_API_KEY` is unset/empty, `Conversation.enabled` is `False` and Jarvis behaves
  exactly as it does today (`Unknown` → "Sorry, I didn't catch that").
- The key is the user's to create at aistudio.google.com/apikey. Jarvis never sees it in
  chat; it lives only on the Pi.

## Privacy — honest revision required

This breaks the README's current "nothing leaves the Pi" claim, which must be revised
truthfully:

> Commands are handled entirely on-device. The optional conversation layer sends your
> transcribed words, the day's schedule, and recent conversation turns to Google's Gemini
> API. It is disabled when no API key is configured.

The always-on mic still never streams anywhere — only deliberate, post-wake, non-command
(`Unknown`) utterances are sent. Softening context for this user specifically: his calendar
is already Google's.

## Failure modes (all graceful; conversation is never load-bearing)

| Failure | Behavior |
|---|---|
| No API key | `enabled` is False; `Unknown` → "Sorry, I didn't catch that." Core untouched. |
| No internet | Gemini call raises → caught → spoken fallback. Announcer still runs from cache. |
| Rate-limited / API error | Logged; spoken fallback. |
| Over-long reply | System prompt caps to 1–2 sentences; reply is also truncated defensively so Piper never reads an essay. |

## Testing

- **Prompt assembly** (pure): asserts name+pronunciation instruction, PHT time, today's
  events, and bounded history all appear. Laptop, no key.
- **Routing** (known intent → local; `Unknown` → converse): tested with a fake conversation
  client. Laptop, no key.
- **Graceful degradation**: a fake client that raises → `reply` returns the fallback, never
  propagates. Laptop, no key.
- **Live call**: verified on the Pi with the real key — a real chit-chat turn produces a
  sensible spoken reply. Ears required.

## Staging

Given the full-context choice (schedule + memory), build in order so there is a working
milestone before the memory complexity:

1. `converse.py` core: `enabled`, pure prompt assembly (persona + name + time + schedule),
   a single-turn live call, graceful degradation. No memory yet.
2. Add the bounded conversation memory (recent turns in the prompt).
3. Wire into the dispatch: `Unknown` → `converse.reply` → speak. (Depends on the real
   `ears.py` from v2a, still to be built; until then, proven via the standalone demo loop.)
4. Deps in `[voice]`, `GEMINI_API_KEY` in the unit, README privacy revision, live verify.

## Dependencies on unfinished v2a work

The real `ears.py` dispatch module and service integration from v2a are not yet built (the
loop currently exists only as a `/tmp` demo). v2c's `converse.py` is independent and can be
built and unit-tested now; the final wiring lands when `ears.py` is codified. The standalone
demo loop can exercise conversation end-to-end in the meantime.
