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

Jarvis addresses knowing an appointment is imminent. It is an announcer, not an
alarm: it assumes the listener is awake and within earshot, and makes no attempt
to rouse a sleeping user. See
`docs/superpowers/specs/2026-07-17-jarvis-butler-design.md`.

## Hardware

Raspberry Pi 5, 7" HDMI touchscreen, USB webcam or headset (v2), any Bluetooth or
USB speaker. Note the Pi 5 has no 3.5mm jack — all audio is USB, Bluetooth, or HDMI.

## Setup / Deploy

A competent stranger with a fresh clone, a Raspberry Pi, and a laptop should
be able to follow this end to end.

1. **Google Calendar access, on the laptop.** Follow `docs/google-setup.md`
   in full — it produces `credentials.json` and `token.json`. Do this step on
   the laptop, not the Pi: the Pi is headless and cannot run the browser
   OAuth flow. Note its warning that Testing-status refresh tokens expire
   after 7 days — see "Sync health" below for what that looks like once it
   happens.

2. **Clone and install on the Pi, editable.**

       git clone <your-repo-url> ~/jarvis && cd ~/jarvis
       python3 -m venv .venv
       .venv/bin/pip install -e .

   The `-e` is required, not a nicety. `ROOT` in `src/jarvis/__main__.py` is
   `Path(__file__).resolve().parents[2]`, which only resolves to the repo
   root under an editable install. A plain `pip install .` makes
   `credentials.json`, `token.json`, `config.toml`, `jarvis.db`, and
   `ui/dist` all resolve to nothing — Jarvis starts cleanly, logs "running
   from cache", and serves nothing useful. Silently.

3. **Copy the Google credentials from the laptop to the Pi.**

       scp you@laptop:path/to/jarvis/token.json ~/jarvis/
       scp you@laptop:path/to/jarvis/credentials.json ~/jarvis/

4. **Build the dashboard.** `ui/dist/` is gitignored, and `create_app` only
   mounts it `if dist.exists()`. Skip this on a fresh clone and the kiosk
   shows a Chromium error page instead of the touchscreen UI.

       cd ui && npm ci && npm run build

5. **Install the systemd service and kiosk autostart.**

       sudo cp deploy/jarvis.service /etc/systemd/system/
       sudo systemctl enable --now jarvis
       mkdir -p ~/.config/autostart && cp deploy/kiosk.desktop ~/.config/autostart/

6. **Verify.**

       systemctl status jarvis          # expect: active (running)
       journalctl -u jarvis -f          # expect: sync succeeding, no tracebacks

   Then `sudo reboot` and confirm both the service and the kiosk come back on
   their own. An appliance that needs a human to start it is not an
   appliance.

### Sync health

The dashboard's `NOT UPDATING` badge reflects two independent signals: the
scheduler tick loop, and whether the calendar has actually synced recently.
A tick loop can stay perfectly healthy while sync is permanently dead — the
7-day Testing-token expiry `docs/google-setup.md` warns about is exactly
this case — so a stalled or never-succeeded sync now shows `NOT UPDATING`
even if everything else looks fine, instead of quietly repeating a stale
cache forever.

## Configuration

`config.toml` in the repo root, all keys optional:

    max_attempts = 5      # omit for infinite repeats (default)
    poll_seconds = 300
    calendar_id = "primary"

## Tests

    python -m pytest
