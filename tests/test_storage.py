from datetime import UTC, datetime

from jarvis.ears import handle
from jarvis.storage import (
    Drive,
    DriveStatus,
    Storage,
    assess,
    describe,
    parse_attributes,
)

NOW = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
GIB = 1024**3

# Real `smartctl -A` output from the Toshiba MK3275GSX in the enclosure, kept
# verbatim so a parser change that only works on invented data fails here.
SMART_REAL = """
SMART Attributes Data Structure revision number: 16
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  1 Raw_Read_Error_Rate     0x000b   100   100   050    Pre-fail  Always       -       0
  5 Reallocated_Sector_Ct   0x0033   100   100   050    Pre-fail  Always       -       8216
  9 Power_On_Hours          0x0032   097   097   000    Old_age   Always       -       1269
193 Load_Cycle_Count        0x0032   095   095   000    Old_age   Always       -       53860
196 Reallocated_Event_Count 0x0032   100   100   000    Old_age   Always       -       499
197 Current_Pending_Sector  0x0032   100   100   000    Old_age   Always       -       784
198 Offline_Uncorrectable   0x0030   100   100   000    Old_age   Offline      -       0
199 UDMA_CRC_Error_Count    0x0032   200   200   000    Old_age   Always       -       0
"""

SMART_HEALTHY = """
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   050    Pre-fail  Always       -       0
197 Current_Pending_Sector  0x0032   100   100   000    Old_age   Always       -       0
199 UDMA_CRC_Error_Count    0x0032   200   200   000    Old_age   Always       -       0
"""


def _status(name="movies", present=True, total=300, free=200, **kw):
    return DriveStatus(
        name=name, present=present, total_bytes=total * GIB, free_bytes=free * GIB, **kw
    )


# --- parsing -----------------------------------------------------------------


def test_parses_real_smartctl_output():
    attrs = parse_attributes(SMART_REAL)
    assert attrs[5] == 8216
    assert attrs[197] == 784
    assert attrs[199] == 0
    assert attrs[9] == 1269


def test_parse_ignores_headers_and_prose():
    # The header row starts with "ID#", not a digit, and must not become an entry.
    assert "ID#" not in str(parse_attributes(SMART_REAL))
    assert parse_attributes("smartctl: command not found") == {}
    assert parse_attributes("") == {}


def test_parse_survives_a_sleeping_disk():
    # -n standby makes smartctl print a notice and no attribute table.
    assert parse_attributes("Device is in STANDBY mode, exit(2)\n") == {}


# --- assessment: the part that decides whether to speak ----------------------


def test_pending_sectors_warn_on_first_sight():
    # Already failing at startup must be announced, not waited out.
    warnings = assess(None, _status(pending=784))
    assert len(warnings) == 1
    assert "784 unreadable sectors" in warnings[0]
    assert "failing" in warnings[0]


def test_old_reallocations_stay_quiet_on_first_sight():
    # History, not news. Repeating it every boot is how warnings get ignored.
    assert assess(None, _status(reallocated=8216, pending=0)) == []


def test_reallocations_warn_when_they_grow():
    prev = _status(reallocated=8216)
    assert assess(prev, _status(reallocated=8300)) != []


def test_unchanged_bad_drive_says_nothing_twice():
    bad = _status(pending=784, reallocated=8216)
    assert assess(bad, bad) == []


def test_growing_pending_sectors_warn_again():
    # 112 -> 784 is exactly what this drive did in one afternoon.
    assert assess(_status(pending=112), _status(pending=784)) != []


def test_healthy_drive_is_silent():
    healthy = _status(pending=0, reallocated=0, crc=0)
    assert assess(None, healthy) == []
    assert assess(healthy, healthy) == []


def test_disconnect_warns_once_then_stays_quiet():
    present = _status()
    absent = _status(present=False, total=0, free=0)
    first = assess(present, absent)
    assert len(first) == 1 and "disconnected" in first[0]
    assert assess(absent, absent) == []


def test_reconnect_is_announced():
    absent = _status(present=False, total=0, free=0)
    assert any("is back" in w for w in assess(absent, _status()))


def test_crc_errors_blame_the_cable_not_the_disk():
    warning = assess(_status(crc=0), _status(crc=5))[0]
    assert "cable" in warning
    assert "not the disk" in warning


def test_low_space_warns_on_the_crossing_only():
    roomy = _status(total=300, free=200)
    tight = _status(total=300, free=10)
    assert assess(roomy, tight) != []
    assert assess(tight, tight) == []  # already below the line, stay quiet


def test_absent_drive_reports_nothing_else():
    # No free-space or SMART noise about a disk that is not even there.
    assert assess(None, _status(present=False, total=0, free=0)) == []


# --- spoken output -----------------------------------------------------------


def test_describe_reports_space_and_damage():
    said = describe([_status(free=197, total=299, pending=784)])
    assert "197" in said and "299" in said
    assert "784 unreadable sectors" in said


def test_describe_handles_absent_and_empty():
    assert "disconnected" in describe([_status(present=False)])
    assert describe([]) == "No drives are being monitored."


# --- the shell ---------------------------------------------------------------


class FakeStat:
    def __init__(self, total_gib, free_gib):
        self.f_frsize = 4096
        self.f_blocks = total_gib * GIB // 4096
        self.f_bavail = free_gib * GIB // 4096


def _storage(total=299, free=197, smart=SMART_REAL, mounted=True):
    return Storage(
        [Drive("movies", "/srv/nas/movies", "/dev/sda")],
        read_smart=lambda dev: smart,
        statvfs=lambda p: FakeStat(total, free),
        ismount=lambda p: mounted,
    )


def test_drive_spec_parsing():
    assert Drive.parse("movies:/srv/nas/movies:/dev/sda") == Drive(
        "movies", "/srv/nas/movies", "/dev/sda"
    )
    assert Drive.parse("usb:/media/stand/usb").device == ""
    # by-id paths contain colons and are the stable ones, so they must survive.
    by_id = "/dev/disk/by-id/usb-JMicron_Tech_DD564198838A1-0:0"
    assert Drive.parse(f"movies:/srv/nas/movies:{by_id}").device == by_id
    # A typo disables one drive; it must never crash the service at boot.
    assert Drive.parse("garbage") is None
    assert Drive.parse("") is None


def test_from_env_disabled_when_unset():
    assert Storage.from_env(None).enabled is False
    assert Storage.from_env("").enabled is False
    assert Storage.from_env("movies:/srv/nas/movies").enabled is True


def test_check_reports_then_falls_silent():
    s = _storage()
    _, first = s.check()
    assert any("784" in w for w in first)
    _, second = s.check()
    assert second == []  # nothing changed, so nothing more to say


def test_unmounted_drive_reads_as_absent():
    s = _storage(mounted=False)
    assert s.poll()[0].present is False


def test_smart_is_not_read_when_the_drive_is_gone():
    calls = []
    s = Storage(
        [Drive("movies", "/srv/nas/movies", "/dev/sda")],
        read_smart=lambda dev: calls.append(dev) or "",
        statvfs=lambda p: FakeStat(299, 197),
        ismount=lambda p: False,
    )
    s.poll()
    assert calls == []


def test_statvfs_failure_is_survivable():
    def boom(_):
        raise OSError("stale file handle")

    s = Storage(
        [Drive("movies", "/srv/nas/movies")],
        read_smart=lambda dev: "",
        statvfs=boom,
        ismount=lambda p: True,
    )
    assert s.poll()[0].present is False  # no exception escapes


def test_last_status_is_cached_and_does_no_io():
    calls = []
    s = Storage(
        [Drive("movies", "/srv/nas/movies", "/dev/sda")],
        read_smart=lambda dev: calls.append(dev) or SMART_HEALTHY,
        statvfs=lambda p: FakeStat(299, 197),
        ismount=lambda p: True,
    )
    s.poll()
    assert len(calls) == 1
    for _ in range(50):  # the 5Hz dashboard must never touch the disk
        s.last_status()
    assert len(calls) == 1


# --- routing through ears.handle --------------------------------------------


class FakeStore:
    def all_events(self):
        return []

    def all_announcements(self):
        return []


class FakeConversation:
    enabled = True

    def __init__(self):
        self.calls = []

    def reply(self, text, now, events):
        self.calls.append(text)
        return "a cloud reply"


def test_storage_question_is_answered_locally():
    conv = FakeConversation()
    reply = handle("how much space is left", NOW, FakeStore(), conv, storage=_storage())
    assert "197" in reply
    # The load-bearing assertion: drive names never reach Gemini.
    assert conv.calls == []


def test_storage_question_without_storage_configured():
    reply = handle("what's the disk space", NOW, FakeStore(), FakeConversation(), storage=None)
    assert reply == "Storage isn't set up."


def test_storage_absent_does_not_break_other_commands():
    reply = handle("what time is it", NOW, FakeStore(), FakeConversation())
    assert "It's" in reply
