from jarvis.plug import Plug


class FakeCloud:
    """Stand-in for tinytuya.Cloud -- records commands, returns a canned response."""

    def __init__(self, response):
        self._response = response
        self.sent = []

    def sendcommand(self, device_id, payload):
        self.sent.append((device_id, payload))
        return self._response


def _plug(response, **kw):
    return Plug(api_key="k", api_secret="s", device_id="dev1",
                cloud=FakeCloud(response), **kw)


def test_disabled_without_credentials():
    p = Plug(api_key=None, api_secret="s", device_id="dev1")
    assert p.enabled is False
    assert p.turn_on() is False  # never touches the cloud


def test_enabled_with_all_credentials():
    assert _plug({"success": True}).enabled is True


def test_turn_on_sends_switch_true():
    cloud = FakeCloud({"success": True})
    p = Plug(api_key="k", api_secret="s", device_id="dev1", cloud=cloud)
    assert p.turn_on() is True
    assert cloud.sent == [("dev1", {"commands": [{"code": "switch", "value": True}]})]


def test_turn_off_sends_switch_false():
    cloud = FakeCloud({"success": True})
    p = Plug(api_key="k", api_secret="s", device_id="dev1", cloud=cloud)
    assert p.turn_off() is True
    assert cloud.sent[0][1] == {"commands": [{"code": "switch", "value": False}]}


def test_rejected_command_returns_false():
    assert _plug({"success": False, "msg": "nope"}).turn_on() is False


def test_cloud_exception_returns_false():
    class Boom:
        def sendcommand(self, *a):
            raise RuntimeError("network down")

    p = Plug(api_key="k", api_secret="s", device_id="dev1", cloud=Boom())
    assert p.turn_on() is False  # never raises; the ears loop is protected


def test_custom_switch_code():
    cloud = FakeCloud({"success": True})
    p = Plug(api_key="k", api_secret="s", device_id="dev1",
             switch_code="switch_1", cloud=cloud)
    p.turn_on()
    assert cloud.sent[0][1]["commands"][0]["code"] == "switch_1"
