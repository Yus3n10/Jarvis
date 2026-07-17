# Jarvis

A proactive calendar butler for the Raspberry Pi. It reads Google Calendar and
speaks upcoming appointments aloud, unprompted, repeating until acknowledged on
a touchscreen.

## Why it talks first

Most assistants are reactive: you ask, they answer. That is useless if your
failure mode is forgetting an appointment exists — you have to remember in order
to ask. Jarvis inverts it. It speaks first, without being asked, and keeps
speaking until you acknowledge. Voice input (planned for v2) is the reply
channel, not the trigger.

## Design

- **A pure scheduling core.** The ladder, escalation, due logic, and collapse are
  I/O-free functions taking `now` as a parameter. The entire brain is tested on a
  laptop with a fake clock — no Pi, no network, no audio.
- **Sync is the only networked component.** Everything else reads SQLite, so a
  WiFi drop degrades Jarvis to "working from cache" rather than "broken".
- **Google Calendar is the config surface.** Per-event lead times come from the
  reminder overrides you already set. No new UI, no new habit.
- **Failures are loud.** A speaker that fails leaves the announcement pending; a
  wedged process shows NOT UPDATING on screen. A butler you cannot trust is worse
  than no butler.

## Privacy

Speech synthesis runs entirely on-device via Piper. Nothing leaves the Pi except
the read-only Google Calendar poll.

## Scope

Jarvis addresses knowing an appointment is imminent. It does not wake heavy
sleepers — it speaks, and sound does not rouse everyone. See
`docs/superpowers/specs/2026-07-17-jarvis-butler-design.md`.

## Hardware

Raspberry Pi 5, 7" HDMI touchscreen, USB webcam or headset (v2), any Bluetooth or
USB speaker. Note the Pi 5 has no 3.5mm jack — all audio is USB, Bluetooth, or HDMI.

## Setup

See `docs/google-setup.md`, then `deploy/`.

## Configuration

`config.toml` in the repo root, all keys optional:

    max_attempts = 5      # omit for infinite repeats (default)
    poll_seconds = 300
    calendar_id = "primary"

## Tests

    python -m pytest
