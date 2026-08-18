import threading
import time

from jarvis.mouth import Mouth


def test_busy_only_while_held():
    m = Mouth()
    assert m.busy is False
    with m:
        assert m.busy is True
    assert m.busy is False


def test_serializes_speech_across_threads():
    m = Mouth()
    order = []

    def hold(label, secs):
        with m:
            order.append(f"{label}-start")
            time.sleep(secs)
            order.append(f"{label}-end")

    a = threading.Thread(target=hold, args=("a", 0.15))
    a.start()
    time.sleep(0.02)  # let a acquire first
    b = threading.Thread(target=hold, args=("b", 0.01))
    b.start()
    a.join()
    b.join()
    # b must not start until a has fully finished -- no overlap
    assert order == ["a-start", "a-end", "b-start", "b-end"]


# --- loudness level for the dashboard orb ------------------------------------


def test_level_is_zero_when_idle():
    assert Mouth().level == 0.0


def test_level_falls_back_to_a_glow_when_busy_without_an_envelope():
    # A non-WAV player, or an envelope that failed to parse: better a steady
    # glow than a dead orb while Jarvis is audibly talking.
    m = Mouth()
    with m:
        assert m.level > 0.0


def test_level_reads_the_envelope_by_elapsed_time():
    m = Mouth(latency_ms=0)
    m.set_envelope([0.1, 0.9], frame_ms=1000)
    assert m.level == 0.1  # frame 0
    time.sleep(1.05)
    assert m.level == 0.9  # frame 1


def test_level_is_silent_until_the_bluetooth_delay_has_passed():
    # The orb must not peak before the speaker has made the sound.
    m = Mouth(latency_ms=5000)
    m.set_envelope([1.0], frame_ms=50)
    assert m.level == 0.0


def test_level_returns_to_zero_past_the_end_of_the_envelope():
    m = Mouth(latency_ms=0)
    m.set_envelope([1.0], frame_ms=10)
    time.sleep(0.05)
    assert m.level == 0.0


def test_empty_envelope_is_treated_as_no_envelope():
    m = Mouth(latency_ms=0)
    m.set_envelope([], frame_ms=50)
    assert m.level == 0.0


def test_clear_envelope_silences_the_orb():
    m = Mouth(latency_ms=0)
    m.set_envelope([1.0] * 100, frame_ms=50)
    m.clear_envelope()
    assert m.level == 0.0
