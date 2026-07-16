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
