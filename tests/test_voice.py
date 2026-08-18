import platform
import sys
from pathlib import Path

from jarvis.mouth import Mouth
from jarvis.voice import NullVoice, Voice, envelope


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


def _write_piper_stub(tmp_path: Path, exit_code: int, write_output: bool) -> Path:
    """Write the fake-piper logic as real Python source, no shell quoting involved."""
    stub = tmp_path / "piper_stub.py"
    stub.write_text(
        "import sys\n"
        f"if {write_output}:\n"
        "    sys.stdout.buffer.write(b'RIFFfake')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return stub


def _create_temp_piper_standin(tmp_path: Path, exit_code: int = 0, write_output: bool = True) -> Path:
    """Build a piper stand-in: a launcher that runs the stub .py via sys.executable, unknown args tolerated."""
    stub = _write_piper_stub(tmp_path, exit_code, write_output)

    if platform.system() == "Windows":
        launcher = tmp_path / "piper.bat"
        launcher.write_text(f'@echo off\n"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
    else:
        launcher = tmp_path / "piper.sh"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def test_piper_succeeds_playback_fails_returns_false(tmp_path):
    """Piper succeeds but playback fails -> speak() returns False (the critical branch)."""
    piper_script = _create_temp_piper_standin(tmp_path, exit_code=0, write_output=True)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=[sys.executable, "-c", "import sys; sys.exit(1)"],
    )

    assert voice.speak("hello") is False


def test_piper_fails_returns_false(tmp_path):
    """Piper fails (non-zero exit) -> speak() returns False."""
    piper_script = _create_temp_piper_standin(tmp_path, exit_code=1, write_output=False)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )

    assert voice.speak("hello") is False


def test_both_piper_and_playback_succeed_returns_true(tmp_path):
    """Both piper and playback succeed -> speak() returns True."""
    piper_script = _create_temp_piper_standin(tmp_path, exit_code=0, write_output=True)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )

    assert voice.speak("hello") is True


def test_missing_player_binary_returns_false(tmp_path):
    """Missing player binary -> speak() returns False (no exception)."""
    piper_script = _create_temp_piper_standin(tmp_path, exit_code=0, write_output=True)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=["/nonexistent/player"],
    )

    assert voice.speak("hello") is False


# --- loudness envelope (drives the dashboard orb) ---------------------------


def _wav(samples, rate=22050, channels=1):
    """Build a 16-bit PCM WAV in memory."""
    import io as _io
    import wave as _wave
    from array import array as _array

    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(_array("h", samples).tobytes())
    return buf.getvalue()


def _piper_emitting(tmp_path: Path, wav_bytes: bytes) -> Path:
    """A piper stand-in that emits a real WAV, so the envelope has something to chew."""
    data = tmp_path / "out.wav"
    data.write_bytes(wav_bytes)
    stub = tmp_path / "emit.py"
    stub.write_text(
        "import sys\nsys.stdout.buffer.write(open(sys.argv[-1], 'rb').read())\n",
        encoding="utf-8",
    )
    if platform.system() == "Windows":
        launcher = tmp_path / "piper_wav.bat"
        launcher.write_text(f'@echo off\n"{sys.executable}" "{stub}" "{data}"\n', encoding="utf-8")
    else:
        launcher = tmp_path / "piper_wav.sh"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "{data}"\n', encoding="utf-8"
        )
        launcher.chmod(0o755)
    return launcher


class RecordingMouth(Mouth):
    def __init__(self):
        super().__init__()
        self.published = []

    def set_envelope(self, levels, frame_ms):
        self.published.append((len(levels), frame_ms))
        super().set_envelope(levels, frame_ms)


_SINK = [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"]


def test_envelope_is_empty_for_non_wav():
    # The orb must never be able to stop Jarvis speaking, so junk in -> [] out.
    assert envelope(b"not a wav at all") == []
    assert envelope(b"") == []


def test_envelope_is_empty_for_pure_silence():
    assert envelope(_wav([0] * 22050)) == []


def test_envelope_tracks_loudness_over_time():
    rate = 22050
    quiet = [1000] * rate  # one second quiet
    loud = [20000] * rate  # one second loud
    env = envelope(_wav(quiet + loud, rate=rate), frame_ms=50)
    # Two seconds at 50ms, give or take the partial frame at the tail.
    assert 40 <= len(env) <= 41
    assert env[5] < env[-5]  # the loud half really is louder
    assert max(env) == 1.0  # normalised to the peak frame
    assert all(0.0 <= v <= 1.0 for v in env)


def test_envelope_handles_stereo():
    rate = 8000
    interleaved = [8000, 8000] * rate  # 1s stereo
    env = envelope(_wav(interleaved, rate=rate, channels=2), frame_ms=100)
    assert len(env) == 10  # one second, not two: channels are not counted as time


def test_speaking_publishes_then_clears_the_envelope(tmp_path):
    mouth = RecordingMouth()
    piper = _piper_emitting(tmp_path, _wav([9000] * 22050))
    assert Voice(piper, Path("dummy"), _SINK, mouth).speak("hello") is True
    assert mouth.published and mouth.published[0][0] > 0  # a real curve went out
    # Cleared after playback: a finished utterance must not leave the orb lit.
    assert mouth.level == 0.0


def test_failed_playback_still_clears_the_envelope(tmp_path):
    mouth = RecordingMouth()
    piper = _piper_emitting(tmp_path, _wav([9000] * 22050))
    dying = [sys.executable, "-c", "import sys; sys.exit(1)"]
    assert Voice(piper, Path("dummy"), dying, mouth).speak("hello") is False
    assert mouth.level == 0.0  # the finally, not the happy path
