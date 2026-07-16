import platform
import sys
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
