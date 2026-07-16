from pathlib import Path

from jarvis.voice import NullVoice, Voice


def test_null_voice_always_succeeds():
    assert NullVoice().speak("Job interview in 60 minutes.") is True


def test_null_voice_records_what_it_said():
    voice = NullVoice()
    voice.speak("first")
    voice.speak("second")
    assert voice.spoken == ["first", "second"]


def test_missing_piper_binary_reports_failure_not_crash():
    """A vanished Bluetooth speaker or a bad path must return False, not raise."""
    voice = Voice(
        piper_bin=Path("/nonexistent/piper"),
        model=Path("/nonexistent/model.onnx"),
        player=["/nonexistent/aplay"],
    )
    assert voice.speak("hello") is False


def test_empty_text_is_not_spoken():
    assert NullVoice().speak("   ") is False
