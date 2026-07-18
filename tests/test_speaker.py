from jarvis.speaker import is_connected


def test_connected_yes():
    assert is_connected("Device 18:90...\n\tConnected: yes\n\tTrusted: yes") is True


def test_connected_no():
    assert is_connected("Device 18:90...\n\tConnected: no\n\tTrusted: yes") is False


def test_missing_field_is_not_connected():
    assert is_connected("Device not available") is False
