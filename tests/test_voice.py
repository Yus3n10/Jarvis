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


def _create_temp_piper_standin(exit_code: int = 0, write_output: bool = True) -> Path:
    """Create a temporary script that mimics piper.

    Creates a platform-specific wrapper (batch file on Windows, shell script on Unix)
    that runs Python code to simulate piper behavior.

    Args:
        exit_code: Exit code for the script (0 = success, non-zero = failure).
        write_output: Whether to write fake WAV output to stdout.

    Returns:
        Path to the temporary script.
    """
    import sys
    import tempfile
    import platform
    import os

    output_part = "sys.stdout.buffer.write(b'RIFFfake');" if write_output else ""
    python_code = f"import sys; {output_part} sys.exit({exit_code})"

    if platform.system() == "Windows":
        # On Windows, create a batch file that invokes Python
        batch_script = "@echo off\npython -c \"" + python_code + "\"\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".bat", delete=False, encoding="utf-8"
        ) as f:
            f.write(batch_script)
            return Path(f.name)
    else:
        # On Unix, create a shell script that invokes Python
        shell_script = "#!/bin/bash\npython3 -c '" + python_code + "'\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, encoding="utf-8"
        ) as f:
            f.write(shell_script)
            script_path = Path(f.name)
            os.chmod(script_path, 0o755)  # Make executable on Unix
            return script_path


def test_piper_succeeds_playback_fails_returns_false():
    """Piper succeeds but playback fails -> speak() returns False.

    This is the critical branch: Piper produces audio successfully, but the
    player (e.g., Bluetooth speaker) fails silently. speak() must return False
    to prevent the caller from marking this announcement as delivered.
    """
    import sys

    piper_script = _create_temp_piper_standin(exit_code=0, write_output=True)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=[sys.executable, "-c", "import sys; sys.exit(1)"],
    )

    assert voice.speak("hello") is False


def test_piper_fails_returns_false():
    """Piper fails (non-zero exit) -> speak() returns False.

    Exercises the piper.returncode != 0 branch directly. The existing
    test_missing_piper_binary_reports_failure_not_crash never reaches this
    because FileNotFoundError is caught first.
    """
    import sys

    piper_script = _create_temp_piper_standin(exit_code=1, write_output=False)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )

    assert voice.speak("hello") is False


def test_both_piper_and_playback_succeed_returns_true():
    """Both piper and playback succeed -> speak() returns True.

    Proves the happy path actually returns True and validates that
    the failure tests are failing for the right reasons.
    """
    import sys

    piper_script = _create_temp_piper_standin(exit_code=0, write_output=True)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )

    assert voice.speak("hello") is True


def test_missing_player_binary_returns_false():
    """Missing player binary -> speak() returns False (no exception).

    Verifies that FileNotFoundError or other OSError from a missing player
    are caught and converted to False, not propagated.
    """
    piper_script = _create_temp_piper_standin(exit_code=0, write_output=True)

    voice = Voice(
        piper_bin=piper_script,
        model=Path("dummy"),
        player=["/nonexistent/player"],
    )

    assert voice.speak("hello") is False
