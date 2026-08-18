"""Disk capacity and drive health. Additive and never load-bearing.

The rule this project runs on is that failures must be loud. A drive quietly
shedding sectors is the same class of silent failure as a sync that has died:
everything looks fine right up until the day it very much is not. This module
reads free space (statvfs) and SMART attributes (smartctl) and turns anything
that got worse into a spoken warning through the same voice the announcer uses.

Three things shape the design:

- **Warnings fire on CHANGE, not on state.** A drive sitting on 784 bad sectors
  must be announced once, not every fifteen minutes until the end of time.
- **Reading SMART wakes a sleeping disk**, so the poll is slow and passes
  `-n standby`: a spun-down drive reports nothing and stays spun down.
- **The dashboard reads a cache.** The websocket pushes state at 5Hz; running
  smartctl at 5Hz would keep the disk awake forever and peg a core. The
  background loop polls, everything else reads `last_status()`.

Everything that decides anything is pure. `parse_attributes` and `assess` take
strings and dataclasses and do no I/O, so the whole judgement layer is testable
on a laptop with no disks attached. The class is the thin shell around statvfs
and subprocess, and every failure path in it returns empty rather than raising:
a drive that cannot be read must never take down the announcer.
"""

import glob
import logging
import os
import re
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The three SMART attributes worth speaking about. Deliberately not a longer
# list: most attributes are vendor-specific noise, and a warning nobody can act
# on is just a way to teach yourself to ignore warnings.
REALLOCATED = 5  # sectors already remapped -- history, matters when it GROWS
PENDING = 197  # sectors that failed a read and await remap -- actively bad NOW
CRC = 199  # interface errors -- the cable or bridge, NOT the platters

_LOW_SPACE_PCT = 10.0
_GIB = 1024**3


@dataclass(frozen=True)
class Drive:
    """One monitored drive. `device` may be empty: capacity still works, SMART
    just goes unread (a USB stick has no SMART, and that is not an error)."""

    name: str
    mountpoint: str
    device: str = ""

    @classmethod
    def parse(cls, spec: str) -> "Drive | None":
        """`name:/mount/point[:/dev/sdX]` -> Drive. None if malformed.

        Split at most twice, because a by-id device path legitimately contains
        colons (`/dev/disk/by-id/usb-JMicron_Tech_DD564198838A1-0:0`) and those
        paths are the stable ones worth using.

        A typo in the env var must disable one drive, not crash the service on
        boot, so this never raises.
        """
        parts = [p.strip() for p in spec.strip().split(":", 2)]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return None
        return cls(name=parts[0], mountpoint=parts[1], device=parts[2] if len(parts) > 2 else "")


@dataclass(frozen=True)
class DriveStatus:
    name: str
    present: bool
    total_bytes: int = 0
    free_bytes: int = 0
    reallocated: int | None = None
    pending: int | None = None
    crc: int | None = None

    @property
    def free_pct(self) -> float:
        return 100.0 * self.free_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def failing(self) -> bool:
        """True if the platters have known damage. Pending sectors are the
        urgent signal; reallocated ones mean it has happened before."""
        return bool(self.pending or self.reallocated)


def _gib(n: int) -> int:
    # 1024-based, so spoken numbers match what df -h and Windows both show.
    return round(n / _GIB)


def parse_attributes(text: str) -> dict[int, int]:
    """`smartctl -A` output -> {attribute id: raw value}. Pure.

    Attribute rows look like:
        197 Current_Pending_Sector  0x0032  100  100  000  Old_age  Always  -  784
    id ---^                                                       raw value ---^

    Anything that is not a well-formed attribute row is skipped, so an error
    message, an empty string from a sleeping disk, or a bridge with no SMART
    passthrough all yield {} rather than an exception.
    """
    found: dict[int, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or not fields[0].isdigit():
            continue
        raw = re.match(r"\d+", fields[9])
        if raw:
            found[int(fields[0])] = int(raw.group())
    return found


def assess(
    prev: DriveStatus | None, cur: DriveStatus, low_space_pct: float = _LOW_SPACE_PCT
) -> list[str]:
    """Spoken warnings for whatever got worse since `prev`. Pure.

    `prev` of None is the first observation after a restart. A drive that is
    *already* failing gets announced then; one that merely has old reallocations
    does not, because that is history and repeating it at every boot is how a
    warning becomes background noise.
    """
    if prev is not None and prev.present and not cur.present:
        return [f"The {cur.name} drive has disconnected."]
    if not cur.present:
        return []  # still absent; already said so when it went

    out: list[str] = []
    if prev is not None and not prev.present:
        out.append(f"The {cur.name} drive is back.")

    def worse(attr: str, warn_on_first: bool) -> bool:
        new = getattr(cur, attr)
        if not new:  # None or 0
            return False
        old = getattr(prev, attr) if prev is not None else None
        return new > old if old is not None else warn_on_first

    # Pending sectors are unreadable RIGHT NOW, so say it even on first sight.
    if worse("pending", warn_on_first=True):
        out.append(
            f"Warning: the {cur.name} drive reports {cur.pending} unreadable sectors. "
            "It is failing. Do not keep anything important on it."
        )
    # These two only matter as trends, so they stay quiet until they move.
    if worse("reallocated", warn_on_first=False):
        out.append(
            f"The {cur.name} drive has remapped more bad sectors, {cur.reallocated} in total."
        )
    if worse("crc", warn_on_first=False):
        out.append(
            f"The {cur.name} drive is logging connection errors. That is the cable or the "
            "enclosure, not the disk."
        )

    # Only on the crossing, not every poll while it sits below the line.
    was_ok = prev is None or not prev.present or prev.free_pct > low_space_pct
    if was_ok and cur.free_pct <= low_space_pct:
        out.append(
            f"The {cur.name} drive is nearly full, {_gib(cur.free_bytes)} gigabytes left."
        )
    return out


def describe(statuses: list[DriveStatus]) -> str:
    """The spoken answer to "how are the drives". Pure."""
    if not statuses:
        return "No drives are being monitored."
    parts = []
    for s in statuses:
        if not s.present:
            parts.append(f"the {s.name} drive is disconnected")
            continue
        part = f"the {s.name} drive has {_gib(s.free_bytes)} of {_gib(s.total_bytes)} gigabytes free"
        if s.pending:
            part += f", and reports {s.pending} unreadable sectors"
        elif s.reallocated:
            part += f", with {s.reallocated} remapped sectors"
        parts.append(part)
    return "Right now, " + "; ".join(parts) + "."


def _device_for(mountpoint: str) -> str:
    """The block device mounted at this path, per /proc/mounts. "" if unknown.

    Needed for wildcard mountpoints, where the device is not known until the
    drive is actually plugged in and mounted by the desktop automounter.
    """
    try:
        with open("/proc/mounts", encoding="utf-8") as mounts:
            for line in mounts:
                parts = line.split()
                # /proc/mounts escapes spaces in paths as \040.
                if len(parts) >= 2 and parts[1].replace("\\040", " ") == mountpoint:
                    return parts[0]
    except OSError:
        pass
    return ""


def _default_statvfs(path: str):
    """os.statvfs is POSIX-only. Looked up at call time rather than bound at
    construction, so this module still imports and constructs on the Windows
    laptop where the pure tests run. It is only ever *called* on the Pi.
    """
    return os.statvfs(path)


def _read_smart(device: str) -> str:
    """`smartctl -A` for one device, or "" on any failure.

    A missing smartctl, a bridge with no SMART passthrough, a sleeping disk and
    a permissions problem all mean "no attributes available", never an
    exception. `-n standby` is what keeps a spun-down drive spun down.

    The return code is deliberately ignored: smartctl uses it as a bitfield and
    sets bits for conditions (a failing attribute, for one) where stdout is
    still perfectly good.
    """
    try:
        done = subprocess.run(
            ["sudo", "-n", "smartctl", "-A", "-n", "standby", "-d", "sat", device],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return done.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("smartctl on %s failed: %s", device, exc)
        return ""


class Storage:
    def __init__(
        self,
        drives,
        read_smart=_read_smart,
        statvfs=None,
        ismount=None,
        expand=None,
        device_for=_device_for,
        low_space_pct: float = _LOW_SPACE_PCT,
    ) -> None:
        self._drives = list(drives)
        self._read_smart = read_smart
        self._statvfs = statvfs or _default_statvfs  # injectable for tests
        self._ismount = ismount or os.path.ismount
        self._expand = expand or glob.glob
        self._device_for = device_for
        self._low = low_space_pct
        self._prev: dict[str, DriveStatus] = {}
        self._last: list[DriveStatus] = []
        # SMART values are re-read only on a full check, and remembered in
        # between so the dashboard keeps showing them without waking the disk.
        self._smart: dict[str, dict[int, int]] = {}
        self._seen_once = False

    @classmethod
    def from_env(cls, spec: str | None, **kw) -> "Storage":
        """Build from NAS_DRIVES, e.g. "movies:/srv/nas/movies:/dev/sda".
        Comma-separated for more than one. Unset or malformed -> disabled."""
        drives = [d for d in (Drive.parse(p) for p in (spec or "").split(",")) if d]
        return cls(drives, **kw)

    @property
    def enabled(self) -> bool:
        return bool(self._drives)

    def last_status(self) -> list[DriveStatus]:
        """Cached, no I/O. This is what the 20Hz dashboard payload reads."""
        return self._last

    def presence(self) -> frozenset[str]:
        """Names currently mounted. Deliberately cheap: no statvfs, no SMART, so
        this can be called often without touching (or waking) a disk. It is the
        trigger for a full check."""
        return frozenset(d.name for d in self._resolve() if self._ismount(d.mountpoint))

    def refresh(self) -> list[DriveStatus]:
        """Update capacity for the dashboard without re-reading SMART. Silent."""
        return self.poll(read_smart=False)

    def poll(self, read_smart: bool = True) -> list[DriveStatus]:
        self._last = [self._status(d, read_smart) for d in self._resolve()]
        return self._last

    def check(self) -> tuple[list[DriveStatus], list[str]]:
        """Full poll including SMART, diffed against the last one.

        Called only when something actually changed (first run after boot, or a
        drive appearing or vanishing), never on a timer. A failing disk grows its
        pending-sector count continuously, so a periodic check would re-announce
        the same bad news every cycle until it became noise.
        """
        statuses = self.poll(read_smart=True)
        warnings: list[str] = []
        for status in statuses:
            previous = self._prev.get(status.name)
            # A drive that was never in the config until now, discovered after
            # startup, means someone just plugged something in.
            if previous is None and self._seen_once and status.present:
                warnings.append(
                    f"A drive named {status.name} is connected, "
                    f"{_gib(status.free_bytes)} gigabytes free."
                )
            warnings.extend(assess(previous, status, self._low))
            self._prev[status.name] = status

        # A globbed mountpoint does not go present=False when it is unplugged,
        # it stops existing altogether, so that departure is caught here.
        live = {s.name for s in statuses}
        for name in [n for n in self._prev if n not in live]:
            warnings.append(f"The {name} drive has disconnected.")
            del self._prev[name]
            self._smart.pop(name, None)

        self._seen_once = True
        return statuses, warnings

    def describe(self) -> str:
        return describe(self._last or self.poll())

    def _resolve(self) -> list[Drive]:
        """Expand any wildcard mountpoints into the drives actually mounted now.

        `usb:/media/stand/*` picks up whatever the desktop automounter has
        mounted, so a freshly plugged disk is monitored without editing config.
        Each match is named after its own directory; the configured name is only
        a placeholder for the pattern.
        """
        out: list[Drive] = []
        for drive in self._drives:
            if "*" not in drive.mountpoint:
                out.append(drive)
                continue
            for path in sorted(self._expand(drive.mountpoint)):
                out.append(
                    Drive(
                        name=os.path.basename(path.rstrip("/")) or drive.name,
                        mountpoint=path,
                        device=self._device_for(path),
                    )
                )
        return out

    def _status(self, drive: Drive, read_smart: bool) -> DriveStatus:
        present, total, free = False, 0, 0
        try:
            if self._ismount(drive.mountpoint):
                stat = self._statvfs(drive.mountpoint)
                total = stat.f_blocks * stat.f_frsize
                free = stat.f_bavail * stat.f_frsize
                present = total > 0
        except OSError as exc:
            # An unplugged USB drive can leave the mountpoint erroring rather
            # than simply absent, which reads as "not present" and is correct.
            log.warning("could not stat %s: %s", drive.mountpoint, exc)

        if not present:
            self._smart.pop(drive.name, None)  # a re-plugged drive gets re-read
        elif drive.device and (read_smart or drive.name not in self._smart):
            self._smart[drive.name] = parse_attributes(self._read_smart(drive.device))

        attrs = self._smart.get(drive.name, {})
        return DriveStatus(
            name=drive.name,
            present=present,
            total_bytes=total,
            free_bytes=free,
            reallocated=attrs.get(REALLOCATED),
            pending=attrs.get(PENDING),
            crc=attrs.get(CRC),
        )
