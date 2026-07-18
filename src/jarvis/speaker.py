"""Keep a Bluetooth speaker connected. Optional, gated on a configured MAC.

Bluetooth on Linux does not reliably auto-reconnect a trusted speaker on boot or
after it power-cycles. This watchdog polls and reconnects, so audio-out comes back
with no human. It is additive: if no SPEAKER_MAC is configured, it never runs.
"""

import logging
import subprocess
import threading

log = logging.getLogger(__name__)


def is_connected(info_output: str) -> bool:
    """Parse `bluetoothctl info <mac>` output. Pure -- testable."""
    return "Connected: yes" in info_output


class SpeakerKeeper:
    def __init__(self, mac: str, interval: float = 20.0) -> None:
        self._mac = mac
        self._interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Poll loop. Meant to run in a daemon thread. Never raises out."""
        try:
            self._run()
        except Exception:
            log.exception("speaker keeper crashed; audio-out reconnection disabled")

    def _run(self) -> None:
        log.info("speaker keeper: watching %s", self._mac)
        while not self._stop.is_set():
            if not self._connected():
                log.info("speaker %s disconnected; reconnecting", self._mac)
                self._connect()
            self._stop.wait(self._interval)

    def _connected(self) -> bool:
        try:
            r = subprocess.run(
                ["bluetoothctl", "info", self._mac],
                capture_output=True, text=True, timeout=10,
            )
            return is_connected(r.stdout)
        except subprocess.SubprocessError:
            return False

    def _connect(self) -> None:
        try:
            subprocess.run(
                ["bluetoothctl", "connect", self._mac],
                capture_output=True, timeout=20,
            )
        except subprocess.SubprocessError as exc:
            log.warning("speaker reconnect failed: %s", exc)
