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
