# Talking to Pace

Pace is the assistant. **The wake word is still "Hey Jarvis"** — the assistant was
renamed but the wake-word model was not retrained, so you summon it by its old name
and it answers to its new one. That is a known quirk, not a bug.

## How a conversation works

```
You:   Hey Jarvis
Pace:  Yes?
You:   Turn on the plug
Pace:  Okay, switching it on.
```

Four things happen in that gap:

1. The wake word is detected on-device. There is a **6 second cooldown** afterwards, so
   saying it twice in a row does nothing the second time.
2. Pace says **"Yes?"** and immediately opens a **4 second recording window**. Start
   talking as soon as you hear it. Anything after 4 seconds is cut off.
3. A short beep means it heard you and is thinking. Wait through it.
4. It replies.

**It cannot hear you while it is speaking.** The microphone is gated shut for the whole
reply, so talking over Pace does nothing. Wait for it to finish, then say "Hey Jarvis"
again.

---

## It talks first

The core feature needs no wake word at all. Pace announces appointments on its own and
keeps repeating until you acknowledge:

```
Pace:  Dentist appointment in 60 minutes.
       ...
Pace:  Dentist appointment in 15 minutes.

You:   Hey Jarvis
Pace:  Yes?
You:   Okay, got it
Pace:  Okay, acknowledged.
```

You can also just tap the card on the touchscreen. Either works.

---

## Your schedule

```
You:   What's next?
Pace:  Next up: Dentist appointment, at 5:00 PM.
```
Also: *what's coming up*, *what's after this*, *anything upcoming*
When empty: **"You have nothing else scheduled."**

```
You:   What's on today?
Pace:  Today you have: Standup at 9:00 AM; Dentist appointment at 5:00 PM.
```
Also: *what's my day look like*, *rest of my day*, *rest of the day*
When empty: **"You have nothing else scheduled today."**

```
You:   Acknowledge
Pace:  Okay, acknowledged.
```
Also: *okay*, *got it*, *I heard you*, *yes*, *yep*, *thanks*, *stop*, *alright*,
*roger*, *copy*, *I'm up*
When nothing is pending: **"There is nothing to acknowledge."**

```
You:   Snooze for 15 minutes
Pace:  Snoozed for 15 minutes.
```
Also: *remind me later*, *give me ten minutes*. Say a number, in digits or words
(*five*, *twenty*, *forty five*). **No number defaults to 10 minutes.**
When nothing is pending: **"There is nothing to snooze."**

---

## Clock and calendar

```
You:   What time is it?
Pace:  It's 7:15 PM.
```
Also: *the time*, *current time*

```
You:   What's the date?
Pace:  It's Tuesday, the 18th of August.
```
Also: *what day is it*, *today's date*

---

## Smart plug

```
You:   Turn on the plug
Pace:  Okay, switching it on.

You:   Turn off the charger
Pace:  Okay, switching it off.
```

A plug command needs **both** a device word and an on/off word, so a bare "off" never
switches anything.

- Device words: *plug*, *charger*, *charge*, *outlet*, *socket*
- On: *on*, *start*, *enable* · Off: *off*, *shut*, *stop*

If the plug is unreachable: **"Sorry, I couldn't reach the plug."**
If no credentials are configured: **"The plug isn't set up."**

---

## Drives and storage

```
You:   How much space is left?
Pace:  Right now, the movies drive has 197 of 298 gigabytes free,
       and reports 784 unreadable sectors.
```
Also: *disk space*, *drive space*, *storage*, *drive health*, *disk health*,
*how full is the nas*, *how are the hard drives*

If no drives are configured: **"Storage isn't set up."**

Pace also raises drive problems **without being asked** — at startup, and whenever a
drive is plugged in or unplugged:

```
Pace:  Warning: the movies drive reports 784 unreadable sectors.
       It is failing. Do not keep anything important on it.

Pace:  The movies drive has disconnected.

Pace:  A drive named SANDISK is connected, 58 gigabytes free.
```

---

## Rest and shutdown

```
You:   Go to sleep
Pace:  Resting. Say wake up whenever you need me.
```
Also: *take a rest*, *take a nap*, *sleep mode*, *go to bed*
The screen goes dark and announcements pause. **It is still listening.** Anything that
falls due while resting is announced right after you wake it.

```
You:   Wake up
Pace:  I'm back.
```
Also: *come back*, *I'm back*, *resume*
If it was never asleep: **"I'm already awake."**

```
You:   Shut down
Pace:  Shutting down is permanent. Say yes to confirm, or anything else to cancel.
You:   Yes
Pace:  Shutting down. Goodbye.
```

Cancelling is the default. Anything that is not a clear yes cancels it and is then
handled as a normal command:

```
You:   Shut down
Pace:  Shutting down is permanent. Say yes to confirm, or anything else to cancel.
You:   Actually, what time is it?
Pace:  It's 7:15 PM.
```

Confirmation words: *yes*, *yeah*, *yep*, *confirm*, *do it*, *sure*, *go ahead*,
*affirmative*, *proceed*. The window is **30 seconds**, after which the request lapses.

---

## Asking who it is

```
You:   What's your name?
Pace:  My name is Pace, it stands for Personal AI Companion Engine. I was
       initially named Jarvis, but my master watched Spider-Man name his own
       personal AI, so he came up with his own.

You:   Who made you?
Pace:  I was created by Yusen, to serve as his personal butler and companion.
```

These are answered from a local lookup. No network, no tokens, instant.

---

## Anything else

Whatever matches no command falls through to an optional conversation layer:

```
You:   Tell me a joke
Pace:  <a reply from Gemini>
```

With no API key configured, this is disabled entirely and you get:
**"Sorry, I didn't catch that."**

---

## Gotchas worth knowing

**Order decides ties.** Commands are matched in a fixed order, so overlapping phrases
resolve predictably:

| You say | You get | Why |
|---|---|---|
| "turn off the plug" | Plug off | Plug words are checked before shutdown |
| "shut down" | Shutdown | No device word, so it is not a plug command |
| "what time is it today" | The time | Clock outranks schedule |
| "yeah, later" | **Snooze**, not acknowledge | "later" is a snooze word and snooze is checked first |

**"Acknowledge" clears everything pending**, not just the most recent announcement.

**Whisper mishears sometimes.** "Tell me a joke" occasionally arrives as something else.
Commands match on whole words only, so a misheard word usually falls through to
conversation rather than triggering the wrong action.

---

## Safety notes

**Shutdown really powers off the Pi.** It is guarded by a spoken confirmation with a
30 second window, and anything other than a clear yes cancels. But a genuine "yes" said
for an unrelated reason inside that window will shut the machine down. The service is
enabled at boot, so recovery is a power cycle. This requires passwordless sudo for
`systemctl poweroff` on the host.

**The plug switches mains power.** Do not put anything on it that must not lose power
without warning. Control runs through the Tuya cloud, so it also depends on the internet.

**The microphone is always on.** Wake-word detection runs continuously and entirely
on-device. Audio is never streamed anywhere, and nothing is recorded until the wake word
fires.

**One thing leaves the Pi.** When an utterance matches no command, the transcribed
**text** (plus the day's schedule and recent conversation turns) is sent to Google's
Gemini API for a reply. Never audio, never anything before the wake word. Set no API key
and the layer is off completely. The read-only Google Calendar sync is the only other
outbound traffic.

**Storage answers never reach the cloud.** Drive and volume names are answered locally
and return before the conversation layer is consulted, by design.

**State changes are never decided by a language model.** Acknowledging, snoozing,
switching the plug, sleeping, and shutting down all run through deterministic local
matching. An LLM hallucination cannot acknowledge a real appointment or cut the power.

---

## When something does not respond

| Symptom | Likely cause |
|---|---|
| No reaction to "Hey Jarvis" | Said within 6s of the last wake, or Pace is still speaking |
| Reply is cut off mid-sentence | You ran past the 4 second window; keep commands short |
| "The plug isn't set up." | Tuya credentials missing from the environment |
| "Storage isn't set up." | `NAS_DRIVES` is unset |
| "Sorry, I didn't catch that." | Not a known command, and no Gemini key configured |
| Silence and no announcements | Check `systemctl status jarvis` and that the speaker is connected |
