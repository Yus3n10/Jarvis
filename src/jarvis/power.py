"""Power + rest control: a confirmed full shutdown and a reversible sleep mode.

Shutdown is destructive and irreversible, so it is NEVER done on a single utterance
-- a misheard wake word must not power off the device whose whole job is reliability.
It requires a spoken "yes" within a short window. Sleep is reversible (screen off,
announcer muted, ears still listening) so it acts immediately.

The real I/O (poweroff, screen on/off) is injected, so the confirmation logic is
testable without touching hardware. `sleeping` is a plain bool read by the announcer
loop; bool read/write is atomic under the GIL, so no lock is needed.
"""

import logging
import os
import subprocess
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


def _poweroff() -> None:
    # stand has passwordless sudo (same as the deploy restarts); -n never prompts.
    subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)


def _set_display(on: bool) -> None:
    # The kiosk is X11 (Xorg); the service has no DISPLAY of its own, so supply it.
    # ponytail: xset DPMS blanks the HDMI panel; if a given panel ignores DPMS this
    # is the one knob to swap (vcgencmd display_power / backlight sysfs).
    env = {**os.environ, "DISPLAY": ":0", "XAUTHORITY": os.path.expanduser("~/.Xauthority")}
    try:
        if on:
            subprocess.run(["xset", "dpms", "force", "on"], env=env, check=False)
            subprocess.run(["xset", "s", "reset"], env=env, check=False)
        else:
            subprocess.run(["xset", "+dpms"], env=env, check=False)
            subprocess.run(["xset", "dpms", "force", "off"], env=env, check=False)
    except Exception as exc:  # never let a failed screen toggle escape into the loop
        log.error("display toggle failed: %s", exc)


class Power:
    def __init__(self, poweroff=_poweroff, set_display=_set_display, window_seconds: int = 30) -> None:
        self._poweroff = poweroff
        self._set_display = set_display
        self._window = timedelta(seconds=window_seconds)
        self._pending_until: datetime | None = None
        self.sleeping = False

    # --- shutdown: guarded by a spoken confirmation -------------------------
    def request_shutdown(self, now: datetime) -> str:
        self._pending_until = now + self._window
        return "Shutting down is permanent. Say yes to confirm, or anything else to cancel."

    def awaiting_confirmation(self, now: datetime) -> bool:
        return self._pending_until is not None and now < self._pending_until

    def confirm(self) -> str:
        self._pending_until = None
        self._poweroff()
        return "Shutting down. Goodbye."

    def cancel(self) -> None:
        self._pending_until = None

    # --- sleep / wake: reversible, immediate --------------------------------
    def sleep(self) -> str:
        self.sleeping = True
        self._set_display(False)
        return "Resting. Say wake up whenever you need me."

    def wake(self) -> str:
        was_sleeping = self.sleeping
        self.sleeping = False
        self._set_display(True)
        return "I'm back." if was_sleeping else "I'm already awake."
