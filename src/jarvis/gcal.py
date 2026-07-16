"""Google Calendar sync. The only module that touches the network.

Everything downstream reads from SQLite, so a failure here degrades Jarvis to
"working from cache" rather than "broken".
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from jarvis.domain import Event

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

log = logging.getLogger(__name__)


def parse_event(raw: dict) -> Event | None:
    """Convert one Google Calendar API event into an Event.

    Returns None for events Jarvis should ignore entirely, or cannot make
    sense of: all-day events (no dateTime, so no lead time means anything),
    cancelled events, events with no usable end time, and events with no id
    (nothing to key them by). This function is pure and must stay that way -
    no network, no clock, no I/O, no logging.
    """
    if raw.get("status") == "cancelled":
        return None

    event_id = raw.get("id")
    if event_id is None:
        return None  # can't track an event we can't key

    start = raw.get("start", {})
    end = raw.get("end", {})
    if "dateTime" not in start:
        return None  # all-day event
    if "dateTime" not in end:
        return None  # malformed/all-day end; nothing usable to parse

    declined = any(
        a.get("self") and a.get("responseStatus") == "declined"
        for a in raw.get("attendees", [])
    )

    overrides = raw.get("reminders", {}).get("overrides", [])
    reminder_minutes = tuple(o["minutes"] for o in overrides if "minutes" in o)

    return Event(
        id=event_id,
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
    events = []
    for raw in result.get("items", []):
        try:
            event = parse_event(raw)
        except Exception:
            log.warning(
                "skipping malformed calendar event id=%s", raw.get("id"), exc_info=True
            )
            continue
        if event is not None:
            events.append(event)
    return events


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
