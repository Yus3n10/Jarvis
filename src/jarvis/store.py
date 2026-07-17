"""SQLite persistence. Thin shell around the pure core.

SQLite has no native datetime type. Everything is stored as an ISO-8601 UTC
string and parsed back to an aware datetime on read. Naive datetimes never
enter or leave this module.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jarvis.domain import Announcement, Event
from jarvis.ladder import plan_announcements, rungs_for

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


def _parse_reminders(raw: str) -> tuple[int, ...]:
    return tuple(int(m) for m in raw.split(",") if m)


class Store:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_events(self, events: list[Event]) -> None:
        """Store events and plan announcements for any that are new or changed.

        An unchanged event keeps its existing announcements untouched, so
        acknowledgement state survives every poll. An event whose start time,
        declined status, or reminder set has changed is replanned from scratch,
        because a 7am warning for an 8am meeting is wrong once the meeting
        becomes 10am, and a reminder the user turned off should stop firing.
        """
        for event in events:
            row = self._conn.execute(
                "SELECT start_utc, end_utc, declined, reminder_minutes FROM events WHERE id = ?",
                (event.id,),
            ).fetchone()
            moved = row is not None and _parse(row["start_utc"]) != event.start_utc
            declined_changed = row is not None and bool(row["declined"]) != event.declined
            if row is not None:
                stored_event = Event(
                    id=event.id,
                    title=event.title,
                    start_utc=_parse(row["start_utc"]),
                    end_utc=_parse(row["end_utc"]),
                    declined=bool(row["declined"]),
                    reminder_minutes=_parse_reminders(row["reminder_minutes"]),
                )
                reminders_changed = rungs_for(stored_event) != rungs_for(event)
            else:
                reminders_changed = False

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

            if row is None or moved or declined_changed or reminders_changed:
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
                reminder_minutes=_parse_reminders(r["reminder_minutes"]),
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

    def prune_absent(self, keep_ids: set[str]) -> int:
        """Delete every event not in keep_ids, and its announcements with it.

        Called only after a successful sync, so an empty result from the
        calendar (or a network failure) never wipes the cache - a missing
        keep_ids entry means the calendar authoritatively no longer has that
        event, not that we failed to hear about it.
        """
        rows = self._conn.execute("SELECT id FROM events").fetchall()
        stale_ids = [r["id"] for r in rows if r["id"] not in keep_ids]
        for event_id in stale_ids:
            self._conn.execute("DELETE FROM announcements WHERE event_id = ?", (event_id,))
            self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self._conn.commit()
        return len(stale_ids)
