# Jarvis v1 — Proactive Announcer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Raspberry Pi appliance that reads Google Calendar and proactively speaks upcoming appointments aloud, repeating until acknowledged on a touchscreen.

**Architecture:** A pure, I/O-free scheduling core (ladder → escalation → due → collapse) wrapped in thin I/O shells (SQLite store, Google Calendar sync, Piper TTS, FastAPI). A React dashboard runs fullscreen in Chromium kiosk on the 7" touchscreen and talks to the backend over WebSocket. The entire brain is testable on a Windows laptop with a fake clock; no Raspberry Pi required until Task 8.

**Tech Stack:** Python 3.11, pytest, FastAPI, SQLite, Piper TTS, React + Vite + TypeScript, systemd, Chromium kiosk.

## Global Constraints

- **Python 3.11** (matches Raspberry Pi OS Bookworm; do not use 3.12+ syntax).
- **All internal datetimes are timezone-aware UTC.** Naive `datetime` objects are banned. Use `datetime.now(UTC)`, never `datetime.now()`. PHT (UTC+8) conversion happens only in the display layer.
- **`now` is always a parameter**, never read from the clock inside pure functions in `domain.py`, `ladder.py`, `escalation.py`, or `scheduler.py`.
- **Default lead ladder:** 60 / 20 / 5 minutes before event start.
- **`max_attempts` default is `None`** = repeat forever. The bounded path must exist in code regardless.
- **No quiet hours.** Announcements fire at any hour.
- **Cost constraint:** no paid services, no API fees. Google Calendar API personal use only.
- **No AI/Claude/Anthropic attribution** in commits, code, comments, or docs.
- Commit after every task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/jarvis/domain.py` | Frozen dataclasses: `Event`, `Announcement`. No logic. |
| `src/jarvis/ladder.py` | Which announcements should exist for an event. |
| `src/jarvis/escalation.py` | How often an unacked announcement repeats. |
| `src/jarvis/scheduler.py` | What to say right now. Combines ladder + escalation. |
| `src/jarvis/config.py` | Config loading (`max_attempts`, poll interval, calendar id). |
| `src/jarvis/store.py` | SQLite persistence. |
| `src/jarvis/gcal.py` | Google Calendar API sync. Only networked module. |
| `src/jarvis/voice.py` | Piper TTS + playback verification. |
| `src/jarvis/api.py` | FastAPI app, WebSocket, ack endpoint, run loop. |
| `ui/` | React + Vite dashboard. |
| `deploy/` | systemd unit + kiosk launcher. |

Tasks 1–5 are pure and need no Pi, no network, and no audio. Tasks 6–11 are I/O shells.

---

### Task 1: Project scaffolding and domain models

**Files:**
- Create: `pyproject.toml`
- Create: `src/jarvis/__init__.py`
- Create: `src/jarvis/domain.py`
- Create: `.gitignore`
- Test: `tests/test_domain.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `Event(id: str, title: str, start_utc: datetime, end_utc: datetime, declined: bool, reminder_minutes: tuple[int, ...])` and `Announcement(event_id: str, rung_minutes: int, due_utc: datetime, state: str, attempts: int, last_spoken_utc: datetime | None, snoozed_until_utc: datetime | None)`. Both frozen dataclasses. `state` is one of `"pending"`, `"acked"`, `"failed"`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "jarvis"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "google-api-python-client>=2.140",
    "google-auth-oauthlib>=1.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.6"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
jarvis.db
credentials.json
token.json
config.toml
ui/node_modules/
ui/dist/
```

- [ ] **Step 3: Create the virtualenv and install**

Run in the repo root:

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Expected: `Successfully installed jarvis-0.1.0 ...`

- [ ] **Step 4: Write the failing test**

Create `tests/test_domain.py`:

```python
from datetime import UTC, datetime

import pytest

from jarvis.domain import Announcement, Event


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
        end_utc=datetime(2026, 7, 20, 1, 0, tzinfo=UTC),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def test_event_is_frozen():
    event = _event()
    with pytest.raises(AttributeError):
        event.title = "changed"


def test_event_rejects_naive_start():
    with pytest.raises(ValueError, match="timezone-aware"):
        _event(start_utc=datetime(2026, 7, 20, 0, 0))


def test_announcement_defaults_to_pending():
    ann = Announcement(
        event_id="evt1",
        rung_minutes=60,
        due_utc=datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
    )
    assert ann.state == "pending"
    assert ann.attempts == 0
    assert ann.last_spoken_utc is None
    assert ann.snoozed_until_utc is None


def test_announcement_rejects_unknown_state():
    with pytest.raises(ValueError, match="state"):
        Announcement(
            event_id="evt1",
            rung_minutes=60,
            due_utc=datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
            state="bogus",
        )
```

- [ ] **Step 5: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_domain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.domain'`

- [ ] **Step 6: Write the implementation**

Create `src/jarvis/__init__.py` (empty file).

Create `src/jarvis/domain.py`:

```python
"""Pure data structures. No logic, no I/O."""

from dataclasses import dataclass, field
from datetime import datetime

VALID_STATES = ("pending", "acked", "failed")


def _require_aware(value: datetime | None, name: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware UTC, got naive datetime")


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start_utc: datetime
    end_utc: datetime
    declined: bool = False
    reminder_minutes: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_aware(self.start_utc, "start_utc")
        _require_aware(self.end_utc, "end_utc")


@dataclass(frozen=True)
class Announcement:
    event_id: str
    rung_minutes: int
    due_utc: datetime
    state: str = "pending"
    attempts: int = 0
    last_spoken_utc: datetime | None = None
    snoozed_until_utc: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware(self.due_utc, "due_utc")
        _require_aware(self.last_spoken_utc, "last_spoken_utc")
        _require_aware(self.snoozed_until_utc, "snoozed_until_utc")
        if self.state not in VALID_STATES:
            raise ValueError(f"state must be one of {VALID_STATES}, got {self.state!r}")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_domain.py -v`
Expected: PASS, 4 passed

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore src/jarvis/__init__.py src/jarvis/domain.py tests/test_domain.py
git commit -m "feat: add project scaffolding and domain models"
```

---

### Task 2: The ladder — which announcements should exist

**Files:**
- Create: `src/jarvis/ladder.py`
- Test: `tests/test_ladder.py`

**Interfaces:**
- Consumes: `Event`, `Announcement` from `jarvis.domain`
- Produces:
  - `DEFAULT_LADDER: tuple[int, ...] = (60, 20, 5)`
  - `rungs_for(event: Event) -> tuple[int, ...]` — reminder overrides if present, else `DEFAULT_LADDER`, sorted descending
  - `plan_announcements(event: Event) -> list[Announcement]` — one pending `Announcement` per rung; returns `[]` for declined events

- [ ] **Step 1: Write the failing test**

Create `tests/test_ladder.py`:

```python
from datetime import UTC, datetime

from jarvis.domain import Event
from jarvis.ladder import DEFAULT_LADDER, plan_announcements, rungs_for

START = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=START,
        end_utc=datetime(2026, 7, 20, 1, 0, tzinfo=UTC),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def test_no_overrides_uses_default_ladder():
    assert rungs_for(_event()) == DEFAULT_LADDER


def test_overrides_win_over_default():
    assert rungs_for(_event(reminder_minutes=(90,))) == (90,)


def test_rungs_sorted_descending():
    assert rungs_for(_event(reminder_minutes=(5, 90, 30))) == (90, 30, 5)


def test_duplicate_overrides_collapse():
    assert rungs_for(_event(reminder_minutes=(30, 30, 10))) == (30, 10)


def test_plan_creates_one_announcement_per_rung():
    anns = plan_announcements(_event(reminder_minutes=(60, 10)))
    assert [a.rung_minutes for a in anns] == [60, 10]
    assert [a.due_utc for a in anns] == [
        datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
        datetime(2026, 7, 19, 23, 50, tzinfo=UTC),
    ]
    assert all(a.event_id == "evt1" for a in anns)
    assert all(a.state == "pending" for a in anns)


def test_plan_uses_default_ladder_when_no_overrides():
    anns = plan_announcements(_event())
    assert [a.rung_minutes for a in anns] == [60, 20, 5]


def test_declined_events_produce_nothing():
    assert plan_announcements(_event(declined=True)) == []


def test_reminder_reaching_into_previous_day():
    """A 26-hour reminder must land on the previous day, not wrap."""
    anns = plan_announcements(_event(reminder_minutes=(26 * 60,)))
    assert anns[0].due_utc == datetime(2026, 7, 18, 22, 0, tzinfo=UTC)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_ladder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.ladder'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/ladder.py`:

```python
"""Decides which announcements should exist for an event. Pure."""

from datetime import timedelta

from jarvis.domain import Announcement, Event

DEFAULT_LADDER: tuple[int, ...] = (60, 20, 5)


def rungs_for(event: Event) -> tuple[int, ...]:
    """Lead times in minutes, most distant first.

    Google Calendar's own reminder overrides are the configuration surface. An
    event without overrides falls back to the default ladder.
    """
    rungs = event.reminder_minutes or DEFAULT_LADDER
    return tuple(sorted(set(rungs), reverse=True))


def plan_announcements(event: Event) -> list[Announcement]:
    """One pending announcement per ladder rung. Declined events get none."""
    if event.declined:
        return []
    return [
        Announcement(
            event_id=event.id,
            rung_minutes=rung,
            due_utc=event.start_utc - timedelta(minutes=rung),
        )
        for rung in rungs_for(event)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_ladder.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/ladder.py tests/test_ladder.py
git commit -m "feat: add announcement ladder from calendar reminder overrides"
```

---

### Task 3: Escalation — how often an unacked announcement repeats

**Files:**
- Create: `src/jarvis/escalation.py`
- Test: `tests/test_escalation.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (stdlib only)
- Produces: `repeat_interval(time_to_event: timedelta) -> timedelta`

Escalation is a *separate mechanism* from the ladder. The ladder decides when an
announcement is **created**; escalation decides how often an already-created,
unacknowledged announcement **repeats**.

| Time to event | Repeat interval |
|---|---|
| > 30 min | 10 min |
| 10–30 min | 5 min |
| < 10 min | 90 s |

- [ ] **Step 1: Write the failing test**

Create `tests/test_escalation.py`:

```python
from datetime import timedelta

from jarvis.escalation import repeat_interval


def test_far_out_repeats_every_ten_minutes():
    assert repeat_interval(timedelta(minutes=60)) == timedelta(minutes=10)


def test_boundary_at_thirty_minutes_is_still_five():
    """30 minutes exactly falls in the 10-30 band, not the >30 band."""
    assert repeat_interval(timedelta(minutes=30)) == timedelta(minutes=5)


def test_just_over_thirty_is_ten():
    assert repeat_interval(timedelta(minutes=30, seconds=1)) == timedelta(minutes=10)


def test_mid_band_repeats_every_five_minutes():
    assert repeat_interval(timedelta(minutes=15)) == timedelta(minutes=5)


def test_boundary_at_ten_minutes_is_ninety_seconds():
    assert repeat_interval(timedelta(minutes=10)) == timedelta(seconds=90)


def test_imminent_repeats_every_ninety_seconds():
    assert repeat_interval(timedelta(minutes=2)) == timedelta(seconds=90)


def test_event_already_started_still_repeats_urgently():
    """Negative time to event means we are late. Stay urgent."""
    assert repeat_interval(timedelta(minutes=-5)) == timedelta(seconds=90)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_escalation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.escalation'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/escalation.py`:

```python
"""How often an unacknowledged announcement repeats. Pure."""

from datetime import timedelta

_BANDS: tuple[tuple[timedelta, timedelta], ...] = (
    (timedelta(minutes=30), timedelta(minutes=10)),
    (timedelta(minutes=10), timedelta(minutes=5)),
)
_IMMINENT = timedelta(seconds=90)


def repeat_interval(time_to_event: timedelta) -> timedelta:
    """Repeat pressure scales with urgency.

    Being late (negative time_to_event) stays maximally urgent.
    """
    for threshold, interval in _BANDS:
        if time_to_event > threshold:
            return interval
    return _IMMINENT
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_escalation.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/escalation.py tests/test_escalation.py
git commit -m "feat: add escalation intervals for unacknowledged announcements"
```

---

### Task 4: Due logic and the give-up rule

**Files:**
- Create: `src/jarvis/config.py`
- Create: `src/jarvis/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `Event`, `Announcement` from `jarvis.domain`; `repeat_interval` from `jarvis.escalation`
- Produces:
  - `Config(max_attempts: int | None = None, poll_seconds: int = 300, calendar_id: str = "primary")` in `jarvis.config`
  - `is_due(now: datetime, ann: Announcement, event: Event, max_attempts: int | None) -> bool` in `jarvis.scheduler`
  - `give_up(ann: Announcement, max_attempts: int | None) -> Announcement` in `jarvis.scheduler` — returns a `failed` copy when attempts are exhausted, else the original unchanged

**Design note:** `max_attempts` defaults to `None`, meaning repeat forever. This is the
chosen behaviour. The bounded path exists so that setting an integer is a config change,
not a rewrite.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler.py`:

```python
from datetime import UTC, datetime, timedelta

from jarvis.config import Config
from jarvis.domain import Announcement, Event
from jarvis.scheduler import give_up, is_due

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _event(**kw):
    defaults = dict(
        id="evt1",
        title="Job interview",
        start_utc=START,
        end_utc=START + timedelta(hours=1),
        declined=False,
        reminder_minutes=(),
    )
    return Event(**{**defaults, **kw})


def _ann(**kw):
    defaults = dict(
        event_id="evt1",
        rung_minutes=60,
        due_utc=START - timedelta(minutes=60),
    )
    return Announcement(**{**defaults, **kw})


def test_not_due_before_its_time():
    now = START - timedelta(minutes=61)
    assert is_due(now, _ann(), _event(), None) is False


def test_due_exactly_at_its_time():
    now = START - timedelta(minutes=60)
    assert is_due(now, _ann(), _event(), None) is True


def test_acked_is_never_due():
    now = START - timedelta(minutes=30)
    assert is_due(now, _ann(state="acked"), _event(), None) is False


def test_failed_is_never_due():
    now = START - timedelta(minutes=30)
    assert is_due(now, _ann(state="failed"), _event(), None) is False


def test_snoozed_is_not_due_until_snooze_expires():
    now = START - timedelta(minutes=50)
    ann = _ann(snoozed_until_utc=START - timedelta(minutes=40))
    assert is_due(now, ann, _event(), None) is False


def test_due_again_after_snooze_expires():
    now = START - timedelta(minutes=39)
    ann = _ann(snoozed_until_utc=START - timedelta(minutes=40))
    assert is_due(now, ann, _event(), None) is True


def test_not_due_again_before_repeat_interval_elapses():
    """Spoken 2 min ago at 45 min out; band is 10 min. Too soon."""
    now = START - timedelta(minutes=45)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=2))
    assert is_due(now, ann, _event(), None) is False


def test_due_again_once_repeat_interval_elapses():
    now = START - timedelta(minutes=45)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=10))
    assert is_due(now, ann, _event(), None) is True


def test_escalates_when_imminent():
    """At 5 min out the interval is 90s, so a 2-min-old utterance is stale."""
    now = START - timedelta(minutes=5)
    ann = _ann(last_spoken_utc=now - timedelta(minutes=2))
    assert is_due(now, ann, _event(), None) is True


def test_snooze_across_midnight():
    """Snooze set before midnight, checked after. Plain UTC arithmetic must hold."""
    start = datetime(2026, 7, 21, 0, 30, tzinfo=UTC)
    event = _event(start_utc=start, end_utc=start + timedelta(hours=1))
    ann = _ann(
        due_utc=start - timedelta(minutes=60),
        snoozed_until_utc=datetime(2026, 7, 21, 0, 10, tzinfo=UTC),
    )
    assert is_due(datetime(2026, 7, 20, 23, 55, tzinfo=UTC), ann, event, None) is False
    assert is_due(datetime(2026, 7, 21, 0, 15, tzinfo=UTC), ann, event, None) is True


def test_infinite_attempts_never_gives_up():
    ann = _ann(attempts=9999)
    assert give_up(ann, None) is ann
    assert is_due(START, ann, _event(), None) is True


def test_gives_up_once_attempts_exhausted():
    ann = _ann(attempts=5)
    assert give_up(ann, 5).state == "failed"


def test_does_not_give_up_below_the_bound():
    ann = _ann(attempts=4)
    assert give_up(ann, 5) is ann


def test_exhausted_announcement_is_not_due():
    ann = _ann(attempts=5)
    assert is_due(START, ann, _event(), 5) is False


def test_config_defaults_to_infinite_attempts():
    assert Config().max_attempts is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.config'`

- [ ] **Step 3: Write `src/jarvis/config.py`**

```python
"""Runtime configuration."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    max_attempts: int | None = None
    poll_seconds: int = 300
    calendar_id: str = "primary"

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load from TOML. A missing file means all defaults."""
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
```

- [ ] **Step 4: Write `src/jarvis/scheduler.py`**

```python
"""Decides what to say right now. Pure — `now` is always a parameter."""

import dataclasses
from datetime import datetime

from jarvis.domain import Announcement, Event
from jarvis.escalation import repeat_interval


def _exhausted(ann: Announcement, max_attempts: int | None) -> bool:
    return max_attempts is not None and ann.attempts >= max_attempts


def give_up(ann: Announcement, max_attempts: int | None) -> Announcement:
    """Mark an announcement failed once its attempt budget is spent.

    With max_attempts=None (the default) this never fires and the announcement
    repeats forever.
    """
    if ann.state == "pending" and _exhausted(ann, max_attempts):
        return dataclasses.replace(ann, state="failed")
    return ann


def is_due(
    now: datetime,
    ann: Announcement,
    event: Event,
    max_attempts: int | None,
) -> bool:
    """Should this announcement be spoken at `now`?"""
    if ann.state != "pending":
        return False
    if _exhausted(ann, max_attempts):
        return False
    if now < ann.due_utc:
        return False
    if ann.snoozed_until_utc is not None and now < ann.snoozed_until_utc:
        return False
    if ann.last_spoken_utc is None:
        return True
    return now - ann.last_spoken_utc >= repeat_interval(event.start_utc - now)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_scheduler.py -v`
Expected: PASS, 15 passed

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/config.py src/jarvis/scheduler.py tests/test_scheduler.py
git commit -m "feat: add due logic, snooze handling, and configurable give-up rule"
```

---

### Task 5: Collapse — one utterance per event

**Files:**
- Modify: `src/jarvis/scheduler.py`
- Test: `tests/test_collapse.py`

**Interfaces:**
- Consumes: `is_due` from Task 4
- Produces:
  - `Utterance(event_id: str, text: str, announcement: Announcement)` frozen dataclass in `jarvis.scheduler`
  - `utterances_due(now: datetime, events: list[Event], announcements: list[Announcement], max_attempts: int | None) -> list[Utterance]`

**Why this exists:** an 8am event whose 60/20/5 rungs all go unacknowledged would
otherwise have three due announcements at 7:56 and Jarvis would say the same thing three
times in a row. Collapse to the most urgent rung — the smallest `rung_minutes`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collapse.py`:

```python
from datetime import UTC, datetime, timedelta

from jarvis.domain import Announcement, Event
from jarvis.scheduler import utterances_due

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _event(id="evt1", title="Job interview", start=START):
    return Event(id=id, title=title, start_utc=start, end_utc=start + timedelta(hours=1))


def _ann(rung, event_id="evt1", **kw):
    return Announcement(
        event_id=event_id,
        rung_minutes=rung,
        due_utc=START - timedelta(minutes=rung),
        **kw,
    )


def test_single_due_announcement_produces_one_utterance():
    now = START - timedelta(minutes=60)
    out = utterances_due(now, [_event()], [_ann(60)], None)
    assert len(out) == 1
    assert out[0].event_id == "evt1"


def test_multiple_due_rungs_collapse_to_most_urgent():
    now = START - timedelta(minutes=4)
    anns = [_ann(60), _ann(20), _ann(5)]
    out = utterances_due(now, [_event()], anns, None)
    assert len(out) == 1
    assert out[0].announcement.rung_minutes == 5


def test_separate_events_each_get_an_utterance():
    other = _event(id="evt2", title="Dentist", start=START)
    now = START - timedelta(minutes=60)
    out = utterances_due(now, [_event(), other], [_ann(60), _ann(60, event_id="evt2")], None)
    assert {u.event_id for u in out} == {"evt1", "evt2"}


def test_nothing_due_produces_nothing():
    now = START - timedelta(minutes=90)
    assert utterances_due(now, [_event()], [_ann(60)], None) == []


def test_announcement_with_no_matching_event_is_ignored():
    """Event deleted from the calendar mid-flight. Must not crash."""
    now = START - timedelta(minutes=60)
    assert utterances_due(now, [], [_ann(60)], None) == []


def test_text_names_the_event_and_the_lead_time():
    now = START - timedelta(minutes=60)
    out = utterances_due(now, [_event()], [_ann(60)], None)
    assert "Job interview" in out[0].text
    assert "60 minutes" in out[0].text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_collapse.py -v`
Expected: FAIL with `ImportError: cannot import name 'utterances_due'`

- [ ] **Step 3: Append to `src/jarvis/scheduler.py`**

Add these imports at the top of the existing import block:

```python
from dataclasses import dataclass
```

Append to the end of the file:

```python
@dataclass(frozen=True)
class Utterance:
    event_id: str
    text: str
    announcement: Announcement


def _phrase(event: Event, ann: Announcement) -> str:
    if ann.rung_minutes == 1:
        when = "in 1 minute"
    else:
        when = f"in {ann.rung_minutes} minutes"
    return f"{event.title} {when}."


def utterances_due(
    now: datetime,
    events: list[Event],
    announcements: list[Announcement],
    max_attempts: int | None,
) -> list[Utterance]:
    """What Jarvis should say at `now` — at most one utterance per event.

    Several rungs for the same event can come due together. Saying the same
    thing three times in a row is worse than saying it once, so the most urgent
    rung wins.
    """
    by_id = {e.id: e for e in events}
    winners: dict[str, Announcement] = {}

    for ann in announcements:
        event = by_id.get(ann.event_id)
        if event is None:
            continue  # event vanished from the calendar mid-flight
        if not is_due(now, ann, event, max_attempts):
            continue
        current = winners.get(ann.event_id)
        if current is None or ann.rung_minutes < current.rung_minutes:
            winners[ann.event_id] = ann

    return [
        Utterance(event_id=eid, text=_phrase(by_id[eid], ann), announcement=ann)
        for eid, ann in winners.items()
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_collapse.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Run the whole pure core**

Run: `.venv\Scripts\python -m pytest -v`
Expected: PASS, 40 passed. **The entire brain is now done and tested without a Pi.**

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/scheduler.py tests/test_collapse.py
git commit -m "feat: collapse concurrent announcements to one utterance per event"
```

---

### Task 6: SQLite store

**Files:**
- Create: `src/jarvis/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Event`, `Announcement` from `jarvis.domain`; `plan_announcements` from `jarvis.ladder`
- Produces: `Store` class with:
  - `Store(path: Path)` — creates schema on init
  - `upsert_events(events: list[Event]) -> None` — also plans announcements for new events
  - `all_events() -> list[Event]`
  - `all_announcements() -> list[Announcement]`
  - `mark_spoken(ann: Announcement, at: datetime) -> None` — sets `last_spoken_utc`, increments `attempts`
  - `mark_state(ann: Announcement, state: str) -> None`
  - `snooze(event_id: str, until: datetime) -> None` — snoozes all pending announcements for an event
  - `ack(event_id: str) -> None` — marks all pending announcements for an event `acked`
  - `heartbeat(at: datetime) -> None` / `last_heartbeat() -> datetime | None`

**Key correctness rule:** re-syncing an unchanged event must **not** reset acknowledgement
state. Announcements are keyed `(event_id, rung_minutes)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.domain import Event
from jarvis.store import Store

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def _event(id="evt1", title="Job interview", start=START, **kw):
    defaults = dict(end_utc=start + timedelta(hours=1), reminder_minutes=(60, 10))
    return Event(id=id, title=title, start_utc=start, **{**defaults, **kw})


def test_roundtrip_event_preserves_utc(store):
    store.upsert_events([_event()])
    got = store.all_events()
    assert len(got) == 1
    assert got[0].start_utc == START
    assert got[0].start_utc.tzinfo is not None
    assert got[0].reminder_minutes == (60, 10)


def test_upsert_plans_announcements(store):
    store.upsert_events([_event()])
    anns = store.all_announcements()
    assert sorted(a.rung_minutes for a in anns) == [10, 60]


def test_declined_event_plans_nothing(store):
    store.upsert_events([_event(declined=True)])
    assert store.all_announcements() == []


def test_resync_does_not_reset_ack_state(store):
    """The bug that would make Jarvis nag about an acked event forever."""
    store.upsert_events([_event()])
    store.ack("evt1")
    store.upsert_events([_event()])  # same event, polled again
    assert {a.state for a in store.all_announcements()} == {"acked"}


def test_moving_an_event_replans_its_announcements(store):
    store.upsert_events([_event()])
    moved = START + timedelta(hours=2)
    store.upsert_events([_event(start=moved)])
    anns = store.all_announcements()
    assert all(a.state == "pending" for a in anns)
    assert {a.due_utc for a in anns} == {
        moved - timedelta(minutes=60),
        moved - timedelta(minutes=10),
    }


def test_mark_spoken_increments_attempts(store):
    store.upsert_events([_event()])
    ann = next(a for a in store.all_announcements() if a.rung_minutes == 60)
    store.mark_spoken(ann, START)
    store.mark_spoken(ann, START + timedelta(minutes=10))
    got = next(a for a in store.all_announcements() if a.rung_minutes == 60)
    assert got.attempts == 2
    assert got.last_spoken_utc == START + timedelta(minutes=10)


def test_snooze_applies_to_all_pending_for_event(store):
    store.upsert_events([_event()])
    until = START - timedelta(minutes=30)
    store.snooze("evt1", until)
    assert all(a.snoozed_until_utc == until for a in store.all_announcements())


def test_heartbeat_roundtrip(store):
    assert store.last_heartbeat() is None
    store.heartbeat(START)
    assert store.last_heartbeat() == START
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.store'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/store.py`:

```python
"""SQLite persistence. Thin shell around the pure core.

SQLite has no native datetime type. Everything is stored as an ISO-8601 UTC
string and parsed back to an aware datetime on read. Naive datetimes never
enter or leave this module.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jarvis.domain import Announcement, Event
from jarvis.ladder import plan_announcements

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    declined INTEGER NOT NULL DEFAULT 0,
    reminder_minutes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS announcements (
    event_id TEXT NOT NULL,
    rung_minutes INTEGER NOT NULL,
    due_utc TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_spoken_utc TEXT,
    snoozed_until_utc TEXT,
    PRIMARY KEY (event_id, rung_minutes)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).isoformat()


def _parse(raw: str | None) -> datetime | None:
    return None if raw is None else datetime.fromisoformat(raw).astimezone(UTC)


class Store:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_events(self, events: list[Event]) -> None:
        """Store events and plan announcements for any that are new or moved.

        An unchanged event keeps its existing announcements untouched, so
        acknowledgement state survives every poll. A moved event is replanned
        from scratch, because a 7am warning for an 8am meeting is wrong once the
        meeting becomes 10am.
        """
        for event in events:
            row = self._conn.execute(
                "SELECT start_utc, declined FROM events WHERE id = ?", (event.id,)
            ).fetchone()
            moved = row is not None and _parse(row["start_utc"]) != event.start_utc
            declined_changed = row is not None and bool(row["declined"]) != event.declined

            self._conn.execute(
                """INSERT INTO events (id, title, start_utc, end_utc, declined, reminder_minutes)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       title=excluded.title, start_utc=excluded.start_utc,
                       end_utc=excluded.end_utc, declined=excluded.declined,
                       reminder_minutes=excluded.reminder_minutes""",
                (
                    event.id,
                    event.title,
                    _iso(event.start_utc),
                    _iso(event.end_utc),
                    int(event.declined),
                    ",".join(str(m) for m in event.reminder_minutes),
                ),
            )

            if row is None or moved or declined_changed:
                self._conn.execute(
                    "DELETE FROM announcements WHERE event_id = ?", (event.id,)
                )
                for ann in plan_announcements(event):
                    self._conn.execute(
                        """INSERT INTO announcements (event_id, rung_minutes, due_utc)
                           VALUES (?, ?, ?)""",
                        (ann.event_id, ann.rung_minutes, _iso(ann.due_utc)),
                    )
        self._conn.commit()

    def all_events(self) -> list[Event]:
        rows = self._conn.execute("SELECT * FROM events").fetchall()
        return [
            Event(
                id=r["id"],
                title=r["title"],
                start_utc=_parse(r["start_utc"]),
                end_utc=_parse(r["end_utc"]),
                declined=bool(r["declined"]),
                reminder_minutes=tuple(
                    int(m) for m in r["reminder_minutes"].split(",") if m
                ),
            )
            for r in rows
        ]

    def all_announcements(self) -> list[Announcement]:
        rows = self._conn.execute("SELECT * FROM announcements").fetchall()
        return [
            Announcement(
                event_id=r["event_id"],
                rung_minutes=r["rung_minutes"],
                due_utc=_parse(r["due_utc"]),
                state=r["state"],
                attempts=r["attempts"],
                last_spoken_utc=_parse(r["last_spoken_utc"]),
                snoozed_until_utc=_parse(r["snoozed_until_utc"]),
            )
            for r in rows
        ]

    def mark_spoken(self, ann: Announcement, at: datetime) -> None:
        self._conn.execute(
            """UPDATE announcements SET last_spoken_utc = ?, attempts = attempts + 1
               WHERE event_id = ? AND rung_minutes = ?""",
            (_iso(at), ann.event_id, ann.rung_minutes),
        )
        self._conn.commit()

    def mark_state(self, ann: Announcement, state: str) -> None:
        self._conn.execute(
            "UPDATE announcements SET state = ? WHERE event_id = ? AND rung_minutes = ?",
            (state, ann.event_id, ann.rung_minutes),
        )
        self._conn.commit()

    def ack(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE announcements SET state = 'acked' WHERE event_id = ? AND state = 'pending'",
            (event_id,),
        )
        self._conn.commit()

    def snooze(self, event_id: str, until: datetime) -> None:
        self._conn.execute(
            """UPDATE announcements SET snoozed_until_utc = ?
               WHERE event_id = ? AND state = 'pending'""",
            (_iso(until), event_id),
        )
        self._conn.commit()

    def heartbeat(self, at: datetime) -> None:
        self._conn.execute(
            """INSERT INTO meta (key, value) VALUES ('heartbeat', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (_iso(at),),
        )
        self._conn.commit()

    def last_heartbeat(self) -> datetime | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'heartbeat'"
        ).fetchone()
        return None if row is None else _parse(row["value"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_store.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/store.py tests/test_store.py
git commit -m "feat: add SQLite store preserving ack state across syncs"
```

---

### Task 7: Google Calendar sync

**Files:**
- Create: `src/jarvis/gcal.py`
- Create: `docs/google-setup.md`
- Test: `tests/test_gcal.py`

**Interfaces:**
- Consumes: `Event` from `jarvis.domain`
- Produces:
  - `parse_event(raw: dict) -> Event | None` — pure; returns `None` for all-day events
  - `fetch_events(service, calendar_id: str, now: datetime, horizon_hours: int = 48) -> list[Event]`
  - `build_service(credentials_path: Path, token_path: Path)` — OAuth flow

**Testing approach:** `parse_event` is pure and carries the tests — it is where every real
bug lives (timezones, all-day events, declined status, missing reminders). `fetch_events`
and `build_service` are thin enough to verify by inspection. **Do not mock the Google
client library**; testing a mock teaches you nothing about Google's actual JSON.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gcal.py`:

```python
from datetime import UTC, datetime

from jarvis.gcal import parse_event


def _raw(**kw):
    defaults = {
        "id": "evt1",
        "summary": "Job interview",
        "start": {"dateTime": "2026-07-20T08:00:00+08:00"},
        "end": {"dateTime": "2026-07-20T09:00:00+08:00"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]},
    }
    return {**defaults, **kw}


def test_pht_start_converts_to_utc():
    """8am Manila is midnight UTC. Getting this wrong loses the interview."""
    event = parse_event(_raw())
    assert event.start_utc == datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    assert event.start_utc.tzinfo is not None


def test_reminder_overrides_are_extracted():
    assert parse_event(_raw()).reminder_minutes == (60,)


def test_multiple_overrides_extracted():
    raw = _raw(reminders={"useDefault": False, "overrides": [
        {"method": "popup", "minutes": 60},
        {"method": "email", "minutes": 1440},
    ]})
    assert parse_event(raw).reminder_minutes == (60, 1440)


def test_use_default_reminders_yields_empty_so_ladder_applies():
    raw = _raw(reminders={"useDefault": True})
    assert parse_event(raw).reminder_minutes == ()


def test_missing_reminders_key_yields_empty():
    raw = _raw()
    del raw["reminders"]
    assert parse_event(raw).reminder_minutes == ()


def test_all_day_event_is_skipped():
    raw = _raw(start={"date": "2026-07-20"}, end={"date": "2026-07-21"})
    assert parse_event(raw) is None


def test_declined_event_is_marked_declined():
    raw = _raw(attendees=[{"self": True, "responseStatus": "declined"}])
    assert parse_event(raw).declined is True


def test_accepted_event_is_not_declined():
    raw = _raw(attendees=[{"self": True, "responseStatus": "accepted"}])
    assert parse_event(raw).declined is False


def test_other_attendee_declining_does_not_decline_us():
    raw = _raw(attendees=[{"self": False, "responseStatus": "declined"}])
    assert parse_event(raw).declined is False


def test_missing_summary_gets_placeholder():
    raw = _raw()
    del raw["summary"]
    assert parse_event(raw).title == "Untitled event"


def test_cancelled_event_is_skipped():
    assert parse_event(_raw(status="cancelled")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_gcal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.gcal'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/gcal.py`:

```python
"""Google Calendar sync. The only module that touches the network.

Everything downstream reads from SQLite, so a failure here degrades Jarvis to
"working from cache" rather than "broken".
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from jarvis.domain import Event

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def parse_event(raw: dict) -> Event | None:
    """Convert one Google Calendar API event into an Event.

    Returns None for events Jarvis should ignore entirely: all-day events (no
    dateTime, so no lead time means anything) and cancelled events.
    """
    if raw.get("status") == "cancelled":
        return None

    start = raw.get("start", {})
    end = raw.get("end", {})
    if "dateTime" not in start:
        return None  # all-day event

    declined = any(
        a.get("self") and a.get("responseStatus") == "declined"
        for a in raw.get("attendees", [])
    )

    overrides = raw.get("reminders", {}).get("overrides", [])
    reminder_minutes = tuple(o["minutes"] for o in overrides)

    return Event(
        id=raw["id"],
        title=raw.get("summary") or "Untitled event",
        start_utc=datetime.fromisoformat(start["dateTime"]).astimezone(UTC),
        end_utc=datetime.fromisoformat(end["dateTime"]).astimezone(UTC),
        declined=declined,
        reminder_minutes=reminder_minutes,
    )


def fetch_events(
    service,
    calendar_id: str,
    now: datetime,
    horizon_hours: int = 48,
) -> list[Event]:
    """Pull upcoming events. Raises on network failure; the caller decides."""
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=now.astimezone(UTC).isoformat(),
            timeMax=(now + timedelta(hours=horizon_hours)).astimezone(UTC).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        .execute()
    )
    parsed = (parse_event(raw) for raw in result.get("items", []))
    return [e for e in parsed if e is not None]


def build_service(credentials_path: Path, token_path: Path):
    """Authorise and return a Calendar API service.

    First run opens a browser. After that the refresh token in token.json is
    reused, which matters because the Pi is headless.
    """
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_gcal.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Write `docs/google-setup.md`**

```markdown
# Google Calendar setup

One-time, free, no billing account required.

1. Go to https://console.cloud.google.com/ and create a project (any name).
2. APIs & Services → Library → search "Google Calendar API" → Enable.
3. APIs & Services → OAuth consent screen:
   - User type: **External**
   - Fill in app name and your email. Skip scopes.
   - Under **Test users**, add your own Gmail address.
   - Leave it in **Testing** status. Publishing is not needed for personal use.
4. APIs & Services → Credentials → Create Credentials → **OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON, save it as `credentials.json` in the repo root.
5. Run the auth flow once on your **laptop** (it opens a browser):

       .venv\Scripts\python -c "from pathlib import Path; from jarvis.gcal import build_service; build_service(Path('credentials.json'), Path('token.json'))"

6. Copy the generated `token.json` to the Pi. The Pi is headless and cannot run
   the browser flow itself.

`credentials.json` and `token.json` are gitignored. Never commit them.

**Note:** apps in Testing status get refresh tokens that expire after 7 days.
If Jarvis stops syncing after a week, either re-run step 5, or set the consent
screen to **In production** (still free, and no verification is required for
personal use with sensitive scopes you own).
```

- [ ] **Step 6: Authorise against your real calendar**

Follow `docs/google-setup.md` steps 1–5, then verify against real data:

```
.venv\Scripts\python -c "from datetime import datetime,UTC; from pathlib import Path; from jarvis.gcal import build_service, fetch_events; s=build_service(Path('credentials.json'),Path('token.json')); [print(e.start_utc, e.reminder_minutes, e.title) for e in fetch_events(s,'primary',datetime.now(UTC))]"
```

Expected: your real upcoming events, times in UTC. **Check one against your phone** —
if an event shows 8am in Google Calendar it must print `00:00` here, because PHT is
UTC+8. If it prints `08:00`, the timezone handling is wrong and everything downstream
will fire eight hours late.

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/gcal.py tests/test_gcal.py docs/google-setup.md
git commit -m "feat: add Google Calendar sync with reminder override parsing"
```

---

### Task 8: Voice output with playback verification

**Files:**
- Create: `src/jarvis/voice.py`
- Test: `tests/test_voice.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `Voice(piper_bin: Path, model: Path, player: list[str])` with `speak(text: str) -> bool`
  - `NullVoice()` with `speak(text: str) -> bool` — always returns True, logs only. Used on the laptop where there is no Piper.

**The rule from the spec:** a failed announcement must never look like a delivered one.
`speak` returns `False` when playback fails, and the caller must not mark the
announcement spoken.

**Setup on the Pi only** (skip on Windows — `NullVoice` covers development):

```bash
sudo apt install -y piper-alsa-utils || sudo apt install -y alsa-utils
mkdir -p ~/piper && cd ~/piper
# Piper release binary + a voice model:
wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_aarch64.tar.gz
tar xzf piper_linux_aarch64.tar.gz
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice.py`:

```python
from pathlib import Path

from jarvis.voice import NullVoice, Voice


def test_null_voice_always_succeeds():
    assert NullVoice().speak("Job interview in 60 minutes.") is True


def test_null_voice_records_what_it_said():
    voice = NullVoice()
    voice.speak("first")
    voice.speak("second")
    assert voice.spoken == ["first", "second"]


def test_missing_piper_binary_reports_failure_not_crash():
    """A vanished Bluetooth speaker or a bad path must return False, not raise."""
    voice = Voice(
        piper_bin=Path("/nonexistent/piper"),
        model=Path("/nonexistent/model.onnx"),
        player=["/nonexistent/aplay"],
    )
    assert voice.speak("hello") is False


def test_empty_text_is_not_spoken():
    assert NullVoice().speak("   ") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_voice.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.voice'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/voice.py`:

```python
"""Text to speech via Piper. Thin shell.

speak() returns False on any failure. The caller must not record an
announcement as spoken when it returns False — an undelivered announcement that
looks delivered is the worst failure this system can have, because it silently
breaks the trust the whole device depends on.
"""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class NullVoice:
    """Development stand-in. No Piper on Windows."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
        self.spoken.append(text)
        log.info("NullVoice would say: %s", text)
        return True


class Voice:
    def __init__(self, piper_bin: Path, model: Path, player: list[str]) -> None:
        self._piper = piper_bin
        self._model = model
        self._player = player

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
        try:
            piper = subprocess.run(
                [str(self._piper), "--model", str(self._model), "--output_file", "-"],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if piper.returncode != 0:
                log.error("piper failed: %s", piper.stderr.decode("utf-8", "replace"))
                return False

            played = subprocess.run(
                self._player, input=piper.stdout, capture_output=True, timeout=60
            )
            if played.returncode != 0:
                log.error("playback failed: %s", played.stderr.decode("utf-8", "replace"))
                return False
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("speech failed: %s", exc)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_voice.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Verify real speech on the Pi**

**This step requires the Pi and the headset — not the Bluetooth speaker yet.** Per the
spec's build order: headset first (one USB device, no reverb, no Bluetooth), then swap
hardware one variable at a time.

Plug in the USB headset, then:

```bash
python3 -c "
from pathlib import Path
from jarvis.voice import Voice
v = Voice(Path.home()/'piper/piper', Path.home()/'piper/en_US-amy-medium.onnx', ['aplay','-q','-'])
print(v.speak('Job interview in sixty minutes.'))
"
```

Expected: you hear the sentence, and it prints `True`.

If it prints `True` but you hear nothing, `aplay` is writing to the wrong device — run
`aplay -l` and set the right card with `['aplay','-q','-D','plughw:1,0','-']`.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/voice.py tests/test_voice.py
git commit -m "feat: add Piper speech output with playback verification"
```

---

### Task 9: The run loop and API

**Files:**
- Create: `src/jarvis/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Store`, `utterances_due`, `give_up`, `Config`, `Voice`/`NullVoice`, `fetch_events`
- Produces:
  - `tick(now, store, voice, config) -> list[str]` — one scheduler pass; returns texts actually spoken
  - `create_app(store, config) -> FastAPI` — serves `/api/state`, `POST /api/ack/{event_id}`, `POST /api/snooze/{event_id}`, `WS /ws`

**`tick` is the seam.** It is the only place where the pure core meets the world, and it
takes `now`, `store`, and `voice` as parameters — so it is fully testable with a
`NullVoice` and a temp database. This is where the "spoken but not delivered" bug would
live, so it gets tests.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.api import tick
from jarvis.config import Config
from jarvis.domain import Event
from jarvis.store import Store
from jarvis.voice import NullVoice

START = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_events([
        Event(
            id="evt1",
            title="Job interview",
            start_utc=START,
            end_utc=START + timedelta(hours=1),
            reminder_minutes=(60,),
        )
    ])
    return s


class FailingVoice:
    def speak(self, text: str) -> bool:
        return False


def test_tick_speaks_when_due(store):
    voice = NullVoice()
    said = tick(START - timedelta(minutes=60), store, voice, Config())
    assert said == ["Job interview in 60 minutes."]
    assert voice.spoken == ["Job interview in 60 minutes."]


def test_tick_is_silent_before_due(store):
    assert tick(START - timedelta(minutes=90), store, NullVoice(), Config()) == []


def test_tick_records_attempt_after_speaking(store):
    tick(START - timedelta(minutes=60), store, NullVoice(), Config())
    ann = store.all_announcements()[0]
    assert ann.attempts == 1
    assert ann.last_spoken_utc == START - timedelta(minutes=60)


def test_failed_playback_is_not_recorded_as_spoken(store):
    """The worst bug available: an undelivered announcement looking delivered."""
    said = tick(START - timedelta(minutes=60), store, FailingVoice(), Config())
    assert said == []
    ann = store.all_announcements()[0]
    assert ann.attempts == 0
    assert ann.last_spoken_utc is None


def test_tick_does_not_repeat_within_the_interval(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())
    assert tick(now + timedelta(minutes=1), store, NullVoice(), Config()) == []


def test_tick_repeats_after_the_interval(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())
    assert tick(now + timedelta(minutes=10), store, NullVoice(), Config()) != []


def test_ack_silences_further_ticks(store):
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), Config())
    store.ack("evt1")
    assert tick(now + timedelta(minutes=30), store, NullVoice(), Config()) == []


def test_tick_writes_heartbeat(store):
    now = START - timedelta(minutes=90)
    tick(now, store, NullVoice(), Config())
    assert store.last_heartbeat() == now


def test_max_attempts_marks_failed_and_silences(store):
    config = Config(max_attempts=2)
    now = START - timedelta(minutes=60)
    tick(now, store, NullVoice(), config)
    tick(now + timedelta(minutes=10), store, NullVoice(), config)
    assert tick(now + timedelta(minutes=20), store, NullVoice(), config) == []
    assert store.all_announcements()[0].state == "failed"


def test_default_config_never_gives_up(store):
    """max_attempts=None is the chosen default: repeat forever."""
    now = START - timedelta(minutes=60)
    for i in range(20):
        tick(now + timedelta(minutes=10 * i), store, NullVoice(), Config())
    assert store.all_announcements()[0].state == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.api'`

- [ ] **Step 3: Write the implementation**

Create `src/jarvis/api.py`:

```python
"""The seam: where the pure core meets the world."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from jarvis.config import Config
from jarvis.scheduler import give_up, utterances_due
from jarvis.store import Store

log = logging.getLogger(__name__)


def tick(now: datetime, store: Store, voice, config: Config) -> list[str]:
    """One scheduler pass. Returns the texts actually spoken.

    An utterance is only recorded as spoken if voice.speak() confirms playback.
    A failed speaker must leave the announcement pending so it is retried.
    """
    store.heartbeat(now)
    events = store.all_events()
    announcements = store.all_announcements()

    for ann in announcements:
        gave_up = give_up(ann, config.max_attempts)
        if gave_up is not ann:
            store.mark_state(ann, "failed")

    spoken: list[str] = []
    for utt in utterances_due(now, events, store.all_announcements(), config.max_attempts):
        if voice.speak(utt.text):
            store.mark_spoken(utt.announcement, now)
            spoken.append(utt.text)
        else:
            log.warning("playback failed, leaving pending: %s", utt.text)
    return spoken


def _state_payload(store: Store, now: datetime) -> dict:
    events = sorted(store.all_events(), key=lambda e: e.start_utc)
    anns = store.all_announcements()
    pending = {a.event_id for a in anns if a.state == "pending"}
    failed = {a.event_id for a in anns if a.state == "failed"}
    heartbeat = store.last_heartbeat()
    return {
        "now_utc": now.isoformat(),
        "heartbeat_utc": heartbeat.isoformat() if heartbeat else None,
        "stale": heartbeat is None or (now - heartbeat) > timedelta(minutes=2),
        "events": [
            {
                "id": e.id,
                "title": e.title,
                "start_utc": e.start_utc.isoformat(),
                "declined": e.declined,
                "needs_ack": e.id in pending
                and any(
                    a.event_id == e.id and a.last_spoken_utc is not None and a.state == "pending"
                    for a in anns
                ),
                "failed": e.id in failed,
            }
            for e in events
            if not e.declined
        ],
    }


def create_app(store: Store, config: Config) -> FastAPI:
    app = FastAPI(title="Jarvis")

    @app.get("/api/state")
    def state() -> dict:
        return _state_payload(store, datetime.now(UTC))

    @app.post("/api/ack/{event_id}")
    def ack(event_id: str) -> dict:
        store.ack(event_id)
        return {"ok": True}

    @app.post("/api/snooze/{event_id}")
    def snooze(event_id: str, minutes: int = 10) -> dict:
        store.snooze(event_id, datetime.now(UTC) + timedelta(minutes=minutes))
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                await socket.send_json(_state_payload(store, datetime.now(UTC)))
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass

    dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: PASS, 73 passed

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/api.py tests/test_api.py
git commit -m "feat: add scheduler tick, state API, and ack/snooze endpoints"
```

---

### Task 10: The ambient dashboard

**Files:**
- Create: `ui/` (Vite scaffold)
- Create: `ui/src/App.tsx`
- Create: `ui/src/App.css`
- Modify: `ui/vite.config.ts`

**Interfaces:**
- Consumes: `GET /api/state`, `POST /api/ack/{event_id}`, `WS /ws` from Task 9
- Produces: static build in `ui/dist`, served by FastAPI

**Design constraints:** this is a **7" screen viewed from across a room**, not a desktop
app. Big type, high contrast, dark background (it sits in a bedroom at night). No
scrolling, no navigation, no chrome. It must be readable at a glance from bed — that is
the entire point of an ambient display. All times shown in **PHT**; UTC never reaches the
screen.

- [ ] **Step 1: Scaffold the UI**

Run in the repo root:

```
npm create vite@latest ui -- --template react-ts
cd ui
npm install
```

- [ ] **Step 2: Configure the dev proxy**

Replace `ui/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
```

- [ ] **Step 3: Write `ui/src/App.tsx`**

```tsx
import { useEffect, useState } from "react";
import "./App.css";

type EventRow = {
  id: string;
  title: string;
  start_utc: string;
  needs_ack: boolean;
  failed: boolean;
};

type State = {
  now_utc: string;
  stale: boolean;
  events: EventRow[];
};

const PHT = "Asia/Manila";

function clockTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-PH", {
    timeZone: PHT,
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function countdown(startIso: string, nowIso: string) {
  const mins = Math.round(
    (new Date(startIso).getTime() - new Date(nowIso).getTime()) / 60000,
  );
  if (mins < 0) return "now";
  if (mins < 60) return `in ${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `in ${hours}h ${mins % 60}m`;
  return `in ${Math.floor(hours / 24)}d`;
}

export default function App() {
  const [state, setState] = useState<State | null>(null);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${location.host}/ws`);
    socket.onmessage = (e) => setState(JSON.parse(e.data));
    return () => socket.close();
  }, []);

  if (!state) return <div className="screen loading">Connecting…</div>;

  const ack = (id: string) =>
    fetch(`/api/ack/${encodeURIComponent(id)}`, { method: "POST" });

  const today = new Date(state.now_utc).toLocaleDateString("en-PH", {
    timeZone: PHT,
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  return (
    <div className="screen">
      <header>
        <div className="clock">{clockTime(state.now_utc)}</div>
        <div className="date">{today}</div>
        {state.stale && <div className="stale">NOT UPDATING</div>}
      </header>

      {state.events.length === 0 && <div className="empty">Nothing scheduled</div>}

      <ul className="events">
        {state.events.map((e) => (
          <li key={e.id} className={e.needs_ack ? "event urgent" : "event"}>
            <div className="when">
              <span className="at">{clockTime(e.start_utc)}</span>
              <span className="rel">{countdown(e.start_utc, state.now_utc)}</span>
            </div>
            <div className="title">{e.title}</div>
            {e.failed && <div className="failed">missed</div>}
            {e.needs_ack && (
              <button className="ack" onClick={() => ack(e.id)}>
                OK, I heard you
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Write `ui/src/App.css`**

```css
:root {
  --bg: #0b0d12;
  --fg: #e8ecf4;
  --dim: #7c869c;
  --urgent: #ff6b3d;
  --ok: #3ddc84;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, sans-serif;
  overflow: hidden;
  cursor: none;
}

.screen { height: 100vh; padding: 20px 28px; display: flex; flex-direction: column; }
.loading { align-items: center; justify-content: center; color: var(--dim); }

header { display: flex; align-items: baseline; gap: 18px; margin-bottom: 18px; }
.clock { font-size: 58px; font-weight: 700; letter-spacing: -2px; }
.date { font-size: 20px; color: var(--dim); }
.stale {
  margin-left: auto; font-size: 15px; font-weight: 700;
  color: var(--urgent); border: 2px solid var(--urgent);
  padding: 4px 10px; border-radius: 6px;
}

.empty { color: var(--dim); font-size: 26px; margin-top: 40px; }

.events { list-style: none; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.event {
  display: grid; grid-template-columns: 130px 1fr auto;
  align-items: center; gap: 18px;
  background: #151922; border-radius: 12px; padding: 14px 18px;
  border-left: 5px solid transparent;
}
.event.urgent { border-left-color: var(--urgent); background: #23161233; }

.when { display: flex; flex-direction: column; }
.at { font-size: 26px; font-weight: 700; }
.rel { font-size: 15px; color: var(--dim); }
.title { font-size: 28px; font-weight: 600; }
.failed { color: var(--urgent); font-size: 15px; font-weight: 700; }

.ack {
  font-size: 20px; font-weight: 700; padding: 16px 24px;
  border: none; border-radius: 10px;
  background: var(--ok); color: #06210f;
  min-height: 64px; min-width: 190px;
}
.ack:active { transform: scale(0.97); }
```

- [ ] **Step 5: Verify against the real backend**

Terminal 1 — seed a database and serve it:

```
.venv\Scripts\python -c "
from datetime import UTC, datetime, timedelta
from pathlib import Path
import uvicorn
from jarvis.api import create_app
from jarvis.config import Config
from jarvis.domain import Event
from jarvis.store import Store
s = Store(Path('dev.db'))
now = datetime.now(UTC)
s.upsert_events([Event(id='e1', title='Job interview', start_utc=now+timedelta(minutes=45), end_utc=now+timedelta(minutes=105), reminder_minutes=(60,20,5))])
uvicorn.run(create_app(s, Config()), port=8000)
"
```

Terminal 2:

```
cd ui
npm run dev
```

Open the printed URL. Expected: the clock ticks in PHT, "Job interview" shows with a
countdown, and the times match your system clock. **Check the event time is PHT, not
UTC** — if it shows 8 hours off, the display layer is wrong.

- [ ] **Step 6: Build the static bundle**

```
cd ui
npm run build
```

Expected: `ui/dist/index.html` exists. FastAPI mounts this automatically.

- [ ] **Step 7: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/vite.config.ts ui/tsconfig*.json ui/index.html ui/src
git commit -m "feat: add ambient dashboard for the 7in touchscreen"
```

---

### Task 11: Deployment — systemd and kiosk

**Files:**
- Create: `src/jarvis/__main__.py`
- Create: `deploy/jarvis.service`
- Create: `deploy/kiosk.desktop`
- Create: `README.md`

**Interfaces:**
- Consumes: everything
- Produces: `python -m jarvis` runs the whole appliance

- [ ] **Step 1: Write `src/jarvis/__main__.py`**

```python
"""Entry point. Sync loop + scheduler loop + web server in one process."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from jarvis.api import create_app, tick
from jarvis.config import Config
from jarvis.gcal import build_service, fetch_events
from jarvis.store import Store
from jarvis.voice import NullVoice, Voice

log = logging.getLogger("jarvis")
ROOT = Path(__file__).resolve().parents[2]


def _voice():
    piper = Path(os.environ.get("PIPER_BIN", Path.home() / "piper/piper"))
    model = Path(os.environ.get("PIPER_MODEL", Path.home() / "piper/en_US-amy-medium.onnx"))
    if not piper.exists() or not model.exists():
        log.warning("piper not found, using NullVoice (no audio)")
        return NullVoice()
    player = os.environ.get("AUDIO_PLAYER", "aplay -q -").split()
    return Voice(piper, model, player)


async def _sync_loop(store: Store, config: Config) -> None:
    service = None
    while True:
        try:
            if service is None:
                service = build_service(ROOT / "credentials.json", ROOT / "token.json")
            store.upsert_events(fetch_events(service, config.calendar_id, datetime.now(UTC)))
        except Exception as exc:
            # Network down is expected and survivable — the cache carries us.
            log.warning("calendar sync failed, running from cache: %s", exc)
            service = None
        await asyncio.sleep(config.poll_seconds)


async def _tick_loop(store: Store, config: Config) -> None:
    voice = _voice()
    while True:
        try:
            tick(datetime.now(UTC), store, voice, config)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(10)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.load(ROOT / "config.toml")
    store = Store(ROOT / "jarvis.db")

    server = uvicorn.Server(
        uvicorn.Config(create_app(store, config), host="0.0.0.0", port=8000, log_level="warning")
    )
    await asyncio.gather(_sync_loop(store, config), _tick_loop(store, config), server.serve())


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify it starts on the laptop**

```
.venv\Scripts\python -m jarvis
```

Expected: logs show sync succeeding against your real calendar, and
`http://localhost:8000` serves the dashboard with your real events. Audio logs
`piper not found, using NullVoice` — correct on Windows.

Stop with Ctrl+C.

- [ ] **Step 3: Write `deploy/jarvis.service`**

```ini
[Unit]
Description=Jarvis calendar butler
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/jarvis
ExecStart=/home/pi/jarvis/.venv/bin/python -m jarvis
Restart=always
RestartSec=5
Environment=PIPER_BIN=/home/pi/piper/piper
Environment=PIPER_MODEL=/home/pi/piper/en_US-amy-medium.onnx
Environment=AUDIO_PLAYER=aplay -q -

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Write `deploy/kiosk.desktop`**

```ini
[Desktop Entry]
Type=Application
Name=Jarvis Kiosk
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito --check-for-update-interval=31536000 http://localhost:8000
X-GNOME-Autostart-enabled=true
```

- [ ] **Step 5: Deploy to the Pi**

On the Pi:

```bash
git clone <your-repo-url> ~/jarvis && cd ~/jarvis
python3 -m venv .venv
.venv/bin/pip install -e .
# copy token.json from the laptop (the Pi is headless and cannot run the browser flow)
scp you@laptop:path/to/jarvis/token.json ~/jarvis/
scp you@laptop:path/to/jarvis/credentials.json ~/jarvis/
sudo cp deploy/jarvis.service /etc/systemd/system/
sudo systemctl enable --now jarvis
mkdir -p ~/.config/autostart && cp deploy/kiosk.desktop ~/.config/autostart/
```

Note: `ui/dist` is gitignored, so build the UI on the Pi (`cd ui && npm ci && npm run build`)
or copy the `dist` folder across.

- [ ] **Step 6: Verify the whole appliance**

```bash
systemctl status jarvis          # expect: active (running)
journalctl -u jarvis -f          # expect: sync succeeding, no tracebacks
```

Then the real test — **put an event in Google Calendar on your phone, 6 minutes out,
with a 5-minute popup reminder.** Expect:

1. Within `poll_seconds`, it appears on the touchscreen.
2. At T-5, Jarvis speaks it aloud.
3. The "OK, I heard you" button appears.
4. Ignore it — Jarvis repeats every 90 seconds. This is the never-give-up rule working.
5. Tap the button — Jarvis goes quiet.

Then `sudo reboot` and confirm both the service and the kiosk come back by themselves.
**An appliance that needs a human to start it is not an appliance.**

- [ ] **Step 7: Write `README.md`**

```markdown
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
```

- [ ] **Step 8: Commit**

```bash
git add src/jarvis/__main__.py deploy/ README.md
git commit -m "feat: add entry point, systemd unit, kiosk autostart, and docs"
```

---

## Done

v1 is complete: Jarvis syncs your calendar, speaks upcoming events unprompted, escalates
until acknowledged, survives WiFi drops and reboots, and shows an ambient dashboard.

**v2 (separate plan):** openWakeWord + whisper.cpp feeding `store.ack()` and
`store.snooze()` — the same endpoints the touchscreen button already calls. Nothing in
this plan changes. Build order per the spec: headset first, then webcam mic, then
Bluetooth speaker. One variable at a time.
