"""Smart-plug on/off via the Tuya cloud. Additive and never load-bearing.

The Lasco plug's firmware ignores local (LAN) control -- it only actuates
through Tuya's cloud -- so this is the one Jarvis feature that reaches out to
Tuya. Local monitoring would be possible, but on/off is not, hence the cloud.

Every failure path (no credentials, no internet, API error, rejected command)
returns False and never raises: a plug that won't switch must never take down
the ears loop. The tinytuya SDK is imported lazily so this module and its pure
tests load with nothing extra installed.
"""

import logging

log = logging.getLogger(__name__)


class Plug:
    def __init__(
        self,
        api_key: str | None,
        api_secret: str | None,
        device_id: str | None,
        region: str = "sg",
        switch_code: str = "switch",
        cloud=None,
    ) -> None:
        self._key = api_key or None
        self._secret = api_secret or None
        self._id = device_id or None
        self._region = region
        self._switch = switch_code
        self._cloud = cloud  # injectable for tests; None -> real Tuya Cloud

    @property
    def enabled(self) -> bool:
        return all((self._key, self._secret, self._id))

    def turn_on(self) -> bool:
        return self._set(True)

    def turn_off(self) -> bool:
        return self._set(False)

    def _client(self):
        if self._cloud is None:
            import tinytuya

            self._cloud = tinytuya.Cloud(
                apiRegion=self._region,
                apiKey=self._key,
                apiSecret=self._secret,
                apiDeviceID=self._id,
            )
        return self._cloud

    def _set(self, on: bool) -> bool:
        if not self.enabled:
            return False
        payload = {"commands": [{"code": self._switch, "value": on}]}
        try:
            resp = self._client().sendcommand(self._id, payload)
        except Exception as exc:
            log.error("plug cloud command failed: %s", exc)
            return False
        # Tuya replies {"success": true, "result": true, ...}; accept either flag.
        if isinstance(resp, dict) and (resp.get("success") or resp.get("result") is True):
            return True
        log.error("plug command rejected: %s", resp)
        return False
