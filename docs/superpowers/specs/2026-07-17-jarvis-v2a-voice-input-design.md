# Jarvis v2a — voice input (wake word + spoken commands)

**Date:** 2026-07-17
**Status:** Approved design, pending implementation plan
**Builds on:** `2026-07-17-jarvis-butler-design.md` (v1, deployed and working)

## The idea

v1 speaks first and is acknowledged by tapping the touchscreen. v2a adds the reply
channel the original design always intended: an always-on **"hey jarvis"** wake word, on-
device speech-to-text, and three spoken commands. Voice input is the *reply* channel, not
the trigger — Jarvis still announces unprompted; now you can answer him without reaching
the screen.

This is **v2a**. Creating calendar events by voice (v2b) is explicitly deferred — see
Non-goals.

## Why this fits without touching the brain

v1 was built so this bolts on. The touchscreen "OK, I heard you" button calls
`store.ack(event_id)`; voice calls the *same method*. Therefore:

- **`scheduler.py`, `ladder.py`, `escalation.py`, `store.py`, `gcal.py`, `api.py` do not
  change.** If any of them had to, v1's boundaries were drawn wrong. This is the test.
- Voice input is a new I/O shell (audio-in), mirroring `voice.py` (audio-out).
- Spoken query answers reuse Piper — the same audio-out path already verified on hardware.

## Goals

- An always-listening **"hey jarvis"** wake word, running entirely on-device.
- Transcribe short spoken commands on the Pi, offline.
- Three commands: **acknowledge**, **snooze**, **query the schedule**.
- Change nothing about v1's scheduler, sync, OAuth scope, or privacy posture.

## Non-goals

- **Creating or modifying calendar events by voice (v2b).** Deferred deliberately, and
  possibly for good: it needs a broader OAuth scope than `calendar.readonly` (breaking
  v1's honest read-only privacy claim), and the user's phone already creates events with
  far better speech recognition — and those events auto-sync to Jarvis, because Google
  Calendar is already the config surface. Voice-creating events on the Pi reinvents a
  wheel the phone spins better. If ever built, it is its own spec with its own decision.
- **Voice shutdown / power control.** A misheard wake word must never be able to disable
  the device whose whole job is reliability, and it could not recover without physical
  access. Out of scope.
- **Waking a sleeping user.** Unchanged from v1: Jarvis is an announcer, not an alarm.
  Voice input assumes the user is awake and speaking to it.
- **Conversational AI / general assistant.** The command vocabulary is small and fixed.

## Hardware

- **Mic:** Genius 1080p webcam with built-in microphone, USB.
- **Plug it into a USB 2.0 (black) port, not USB 3.0 (blue).** USB 3.0 on the Pi 5 emits
  2.4GHz RF interference that degrades Bluetooth — and Jarvis announces through a
  Bluetooth JBL. A 1080p webcam does not need USB 3.0 bandwidth, so the black port costs
  nothing and protects the audio link.
- **Speaker:** existing JBL Go Essential 2 (unchanged).

## The hard gate: mic verification comes first

The highest-risk assumption in this design is that the Genius webcam mic captures audio
good enough for reliable wake-word detection. Webcam mics are mediocre, and wake-word
reliability lives or dies on mic quality. **Before building any pipeline**, the first
implementation task verifies the mic on the actual Pi:

- `arecord -l` to find the capture device and its ALSA name
- record a ~5s clip at ~1m, play it back, confirm the voice is clear
- read the noise floor

This yields three things the rest of the build needs: the **device string**, the
**sample rate** (openWakeWord expects 16kHz mono), and an honest verdict on whether this
mic is adequate — or whether to wait for the USB headset instead. A bad mic discovered at
Task 1 is cheap; discovered after the pipeline is built, expensive.

## Architecture

```
mic (Genius, USB 2.0)
   │  16kHz mono PCM
   ▼
openWakeWord  ("hey jarvis", pretrained model, always listening, CPU)
   │  on trigger
   ▼
record ~4s  ──►  whisper.cpp (tiny.en)  ──►  transcript text
                                               │
                                               ▼
                                    intent.parse(text)  [PURE]
                                               │
              ┌────────────────────────────────┼───────────────────────────┐
              ▼                                 ▼                            ▼
        Ack / Snooze(n)                   QueryNext / QueryToday          Unknown
              │                                 │                            │
        store.ack(id) /                   build sentence →              (chime / brief
        store.snooze(id, until)           voice.speak(it)               "sorry?" reply)
```

### New modules

**`src/jarvis/intent.py` — pure.** The command brain.
- `parse(transcript: str) -> Intent` where `Intent` is one of `Ack`, `Snooze(minutes:int)`,
  `QueryNext`, `QueryToday`, `Unknown`.
- **Bare "snooze" with no number defaults to 10 minutes**, matching the existing
  `POST /api/snooze/{id}?minutes=10` default in `api.py`, so voice and touch snooze
  identically.
- No audio, no I/O, no clock. Fully unit-testable with plain strings. Every hard bug in
  command understanding lives here and is catchable without a Pi.
- Rule-based matching (keyword/pattern spotting). No LLM — the vocabulary is tiny and
  structured, matching v1's rule-based scheduler philosophy.

**`src/jarvis/ears.py` — thin I/O shell.** Captures audio, runs openWakeWord, records on
trigger, runs whisper.cpp, calls `intent.parse`, dispatches. Thin enough to verify by
inspection; correctness lives in `intent.py` and in manual hardware verification.

### Dispatch semantics

- **Ack / Snooze** need a target event. The touchscreen knows because it acts on a
  specific row; voice acts on **the event(s) currently pending and already announced**
  (i.e. currently `needs_ack`) — the same set a tap would clear. If none is pending, a
  brief spoken "nothing to acknowledge." If more than one, acknowledge all currently-
  announced (matches the tap behaviour, which clears all pending for an event).
- **Query** needs no event context — it reads the store and speaks: `QueryNext` → the next
  upcoming event and its time; `QueryToday` → today's events. Times spoken in PHT.

### Wiring

`ears` runs as an additional long-running task alongside the existing sync loop, tick
loop, and web server in `__main__.py`. Always-on wake-word listening is continuous;
whisper runs only in bursts on trigger. The Pi 5's four cores handle this; the cooling fan
matters.

## The echo problem — half-duplex

The mic and JBL share a room, so the mic hears Jarvis speak. Failure modes: the wake-word
detector running on Jarvis's own voice (possible self-trigger during a spoken reply), and
a command recording polluted by an announcement still playing.

**Fix: half-duplex — stop listening while speaking.** A single shared "mouth busy" gate,
held by the audio-out path (`voice.speak`, whether from the tick loop or a query reply)
and checked by `ears` before it detects or records. Modelled as one explicit shared object
both hold, not a global flag, so it stays testable. Rude (no barge-in) but reliable.

The proper fix — PipeWire's WebRTC echo-cancellation module — is a later option if half-
duplex proves annoying.

## Software stack (all free, all on-device)

| Job | Tool |
|---|---|
| Wake word | openWakeWord — ships a pretrained "hey jarvis" model |
| Speech → text | whisper.cpp, `tiny.en` (~75MB); `base.en` if accuracy needs it |
| Command brain | rule-based `intent.parse` |
| Speech → audio | Piper (existing, unchanged) |

The always-on mic never leaves the device — openWakeWord and whisper run locally, nothing
is sent anywhere. This preserves v1's privacy posture and must be stated in the README the
same way Piper's on-device synthesis is, because an always-on bedroom mic is a real
privacy surface the reader deserves to understand.

## Failure modes

| Failure | Handling |
|---|---|
| Mic unplugged / not found | `ears` logs and disables itself; the rest of Jarvis (announcing, touchscreen ack) is unaffected. Voice is additive, never load-bearing. |
| Wake word misfires (false trigger) | Records, whisper produces garbage, `intent.parse` returns `Unknown`, brief "sorry?" and back to listening. No state change. |
| Wake word misses the user | User taps the touchscreen — the existing path always works. Voice never replaces the button; it supplements it. |
| Jarvis hears himself | Half-duplex gate suppresses listening during any speech output. |
| whisper too slow / model missing | Logged; `ears` degrades to disabled, touchscreen unaffected. |

**Guiding rule, inherited from v1:** voice is additive. If the entire voice layer fails,
Jarvis still announces and is still acknowledgeable by touch. The touchscreen remains the
reliable floor.

## Testing

- **`intent.py`** carries the suite: many transcript strings → expected `Intent`, including
  messy real speech ("uh snooze it ten minutes" → `Snooze(10)`, "okay okay got it" →
  `Ack`, "what's next" / "what's on today" → the queries, empty/garbage → `Unknown`).
- **The half-duplex gate** is testable: assert `ears` dispatches nothing while the gate is
  held.
- **Audio I/O** (`arecord`, openWakeWord, whisper) is thin shell — verified by the user's
  ears on the Pi, exactly as Piper output was. The wake word and whisper are pretrained
  externals; we verify, we don't unit-test them.

## Staging within v2a

1. **Mic verification** (the gate) — prove capture on the Pi.
2. **`intent.py`** — pure, fully tested, no hardware. Buildable on the laptop.
3. **whisper.cpp + record-on-demand** — transcribe a clip; verify by ear.
4. **openWakeWord** — always-on "hey jarvis"; verify trigger reliability.
5. **Half-duplex gate + dispatch** — wire transcript → intent → store/Piper.
6. **Integration on hardware** — full loop, then the ack/snooze/query commands end to end.

Steps 3–6 need the Pi and the verified mic. Step 2 is pure and can proceed anywhere.

## Development

- Repo: `C:\Users\LENOVO\jarvis`, branch off the current line. Pi at `stand@192.168.1.118`.
- The pure `intent.py` and its tests run on the laptop. The audio pipeline is built and
  verified on the Pi over SSH, as v1's hardware layer was.
