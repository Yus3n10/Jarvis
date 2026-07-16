# Jarvis — a proactive calendar butler

**Date:** 2026-07-17
**Status:** Approved design, pending implementation plan

## The problem

Calendar reminders assume you will look at them. If a commitment is not in your head at
the moment it matters, a notification you must go and check does not help — you have to
already remember it in order to go look.

A missed commitment can come from two distinct places:

1. **You were never told it was imminent.** Nothing surfaced it in a way you would
   notice at the time it mattered.
2. **You did not act on the signal that reached you.** Whatever the reason — asleep,
   absorbed in something else, out of earshot — the announcement arrived and nothing
   happened.

**This project addresses failure (1) only.** Failure (2) — making sure a delivered
signal is actually acted on — is a separate problem and is explicitly out of scope —
see Non-goals.

## Goals

- Surface upcoming commitments **without requiring you to ask, remember, or check**.
- Keep working when the internet does not.
- Cost nothing. No purchases, no subscriptions, no API fees.
- Run entirely on hardware already owned.

## Non-goals

- **Waking a sleeping user.** Jarvis is an announcer, not an alarm. It assumes the
  listener is awake and within earshot; it makes no attempt to rouse a sleeping user,
  and should not be relied on to. Any announcement made while the listener is asleep
  will be missed. This is a known, accepted boundary of the design, not an oversight.
  It is recorded here so that a working Jarvis is never mistaken for an alarm clock.
- General-purpose voice assistance (weather, music, web search). Jarvis knows about the
  calendar. That is all.
- Using the GPS module, relay, HC-SR04, or ESP32. These are owned but serve no purpose
  here. The design will not be bent to justify them.

## Known limitation, stated plainly

Jarvis assumes **the listener is awake and within earshot**. Announcements fire
regardless, but their effect on someone who is asleep is nil. The value delivered is
against failure (1): knowing the appointment exists and is imminent, during waking
hours.

The escalation ladder incidentally produces a timestamped log of every unacknowledged
announcement — useful for diagnosing the scheduler, not for anything beyond that.

## Architecture

One Python service under systemd, serving a React dashboard to a fullscreen Chromium
kiosk on the 7" touchscreen.

```
  Google Calendar
        │  (poll, every few minutes)
        ▼
  ┌───────────┐     ┌─────────────┐     ┌──────────────┐
  │   Sync    │────▶│ Event store │◀───▶│  Scheduler   │
  └───────────┘     │  (SQLite)   │     │  (pure core) │
   only network     └─────────────┘     └──────┬───────┘
   component                                   │
                            ┌──────────────────┴──────────┐
                            ▼                             ▼
                     ┌─────────────┐              ┌──────────────┐
                     │Voice output │              │  Dashboard   │
                     │  (Piper)    │              │(FastAPI + WS)│
                     └──────┬──────┘              └──────┬───────┘
                            ▼                            ▼
                     Bluetooth speaker            Chromium kiosk (7")
```

### Components

**1. Sync** — polls the Google Calendar API, writes to the event store. The *only*
component that touches the network. This isolation is deliberate: when WiFi drops,
everything else keeps working from cache.

**2. Event store** — SQLite. Holds events plus the state Google does not have:
announced, acknowledged, snoozed-until, attempt count.

**3. Scheduler** — the brain. Pure function:

```python
def announcements_due(now, events, ack_state) -> list[Announcement]
```

No I/O, no network, no audio, no clock of its own. `now` is a parameter. The entire
brain is testable with a fake clock in milliseconds, without a Pi.

**4. Voice output** — Piper renders text to audio; audio goes to the Bluetooth speaker.
Must **verify playback occurred** and fall back to the display if it did not. A failed
announcement must never look like a delivered one.

**5. Display** — React/Vite dashboard served by FastAPI, live-updated over WebSocket,
shown fullscreen in Chromium kiosk mode. Chosen over a native GUI because the stack is
already familiar, it screenshots well for portfolio use, and the HTTP/WS boundary keeps
the brain honest.

**6. Voice input (v2)** — openWakeWord → whisper.cpp, feeding acknowledgements to the
scheduler. Nothing else changes when it arrives. That is the test of whether these
boundaries are drawn correctly.

## Scheduler design

### Lead times

Read `reminders.overrides` from each Google Calendar event and use those as the
announcement schedule. Configuration therefore happens in Google Calendar, using the
reminder settings you already set — no new UI, no new config file, no new habit.

Events without overrides fall back to a default ladder: **60 / 20 / 5 minutes** before
start.

### Acknowledgement

Announcements **repeat until acknowledged**. Fire-and-forget is a talking clock, not a
butler: it narrates to an empty room and you learn to trust something that silently failed.

- **v1:** a large "OK, I heard you" button on the touchscreen.
- **v2:** the spoken word "okay".

Same state machine underneath.

### Escalation

Two distinct mechanisms, easily confused:

- **The ladder** (reminder overrides, or the 60/20/5 default) decides **when an
  announcement is first created**.
- **Escalation** decides **how often an already-created announcement repeats** while it
  sits unacknowledged.

An 8am event with default settings creates an announcement at 7:00. If you acknowledge
it, nothing repeats and the next ladder rung fires at 7:40. If you do not, that same
announcement repeats on the schedule below until acknowledged — and the 7:40 and 7:55
rungs still create their own announcements on top.

Repeat pressure scales with urgency:

| Time to event | Repeat interval |
|---|---|
| > 30 min | every 10 min |
| 10–30 min | every 5 min |
| < 10 min | every 90 s |

Concurrent unacknowledged announcements for the same event collapse into one spoken
utterance — the most urgent one. Jarvis says the thing once, not three times over.

### Give-up rule

`max_attempts` is a **config value, default `null` (infinite)**. Announcements repeat
indefinitely until acknowledged.

This is a deliberate choice with a known cost: an unacknowledged announcement escalates
indefinitely and is audible to others nearby, regardless of whether the intended
listener ever hears it. The bounded path exists in the code — setting `max_attempts` to
an integer marks the announcement `failed` and surfaces it on the display — so reversing
this is a one-line config change rather than a rewrite.

### Quiet hours

None. Jarvis speaks whenever the ladder says so, including overnight.

### Event filtering

Announce only events that have a real start time and have not been declined. All-day
events, birthdays, and declined invitations go to the display silently.

## Data model

```
events(id, google_id, title, start_utc, end_utc, declined, updated_at)
announcements(id, event_id, due_utc, spoken_at, acked_at, snoozed_until, attempts, state)
```

`state` ∈ {pending, speaking, acked, failed}.

## Failure modes

| Failure | Handling |
|---|---|
| WiFi drops | By design — everything runs from the SQLite cache. Sync failure is logged, not fatal. |
| Bluetooth speaker disconnects | Audio path verifies playback; on failure, degrade to display and mark the announcement undelivered. |
| Pi crashes | systemd `Restart=always`. |
| Process wedged but alive | Scheduler writes a heartbeat; dashboard shows staleness. A broken Jarvis must *say* it is broken rather than serenely showing yesterday's plan. |
| Timezones | All internal times are timezone-aware UTC. PHT (UTC+8) exists only at the display edge. Naive datetimes are banned. This is the most likely source of a silent miss. |

## Testing

The pure scheduler carries the test suite: fake clock, synthetic calendars, assert what
gets said and when. Cases that matter:

- snooze crossing midnight
- an event that moves after being announced
- the infinite-repeat loop terminating on acknowledgement
- UTC/PHT boundary arithmetic
- an event whose reminder override reaches into the previous day

I/O shells (Google, SQLite, Piper, audio) stay thin enough to verify by inspection. That
is the trade being bought by pushing logic into a pure core.

## Hardware

All owned. Total cost: ₱0.

| Role | Device | Notes |
|---|---|---|
| Compute | Raspberry Pi 5 + cooling fan | Fan matters — wake-word listening is always-on in v2. |
| Display | 7" HDMI touchscreen | Chromium kiosk. |
| Mic (dev) | USB gaming headset | Lab bench: mic and speaker in one USB device, no reverb, no Bluetooth. |
| Mic (deployed) | USB webcam | A butler you have to wear is not a butler. |
| Speaker | Existing Bluetooth speaker | Pi 5 has built-in Bluetooth. |

**Pi 5 has no 3.5mm jack and no analog mic input.** All audio is USB, Bluetooth, or HDMI.
This single fact drives every hardware choice above.

**Build order for hardware:** get the pipeline working on the headset first, then swap in
the webcam mic (learn what reverb costs), then swap in the Bluetooth speaker (fight
pairing). One variable at a time. Debugging all three at once is how projects die.

**Acoustic echo (v2):** once mic and speaker are separate objects in a room, the mic hears
Jarvis and can trigger his own wake word. Fix is **half-duplex** — stop listening while
speaking. PipeWire's WebRTC echo-cancellation module is the proper fix if half-duplex
proves annoying.

## Software stack

Entirely free, entirely offline except calendar sync. No API keys, no subscriptions.

| Job | Tool |
|---|---|
| Wake word (v2) | openWakeWord — ships a pretrained "hey jarvis" model |
| Speech → text (v2) | whisper.cpp, `tiny.en` or `base.en` |
| Text → speech | Piper |
| Brain | Rule-based matching. An LLM here is a solution looking for a problem. |
| Backend | Python + FastAPI |
| Frontend | React + Vite + TypeScript |
| Store | SQLite |

The always-on mic never leaves the device. Worth stating explicitly in the README.

## Staging

**v1 — the proactive announcer.** Calendar sync, event store, scheduler, Piper output,
ambient dashboard, touchscreen acknowledgement. No microphone, no wake word, no speech
recognition. Useful on its own.

**v2 — the voice layer.** openWakeWord + whisper.cpp feeding acknowledgements, snoozes,
and queries to the existing scheduler. Bolts on; rearchitects nothing.

v1 first is not caution, it is sequencing: the voice stack is where weeks disappear, and
it should land on a foundation that already works. If interest runs out at any point, v1
alone is still a working device.

## Development

- Repo: a new git repo, public (portfolio artifact).
- Develop on Windows in VS Code. The pure scheduler needs no Pi — tests run on the laptop.
- Push/pull to the Pi for hardware work.
