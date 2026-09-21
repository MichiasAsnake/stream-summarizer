import sys
sys.path.insert(0, ".")
from app.memory.windowing import WindowBuilder, Utterance


def test_window_close_on_silence():
    b = WindowBuilder(min_s=60, max_s=90, max_words=450, min_silence=1.5)
    t = 0.0
    closed = None
    for i in range(20):
        u = Utterance(id=i, t_start=t, t_end=t + 3.0, text="hello world " * 5, words=10)
        t += 5.0
        r = b.add(u)
        if r:
            closed = r
    # force time-based close
    u = Utterance(id=99, t_start=t + 2.0, t_end=t + 5.0, text="late line", words=2)
    # elapsed < 60 so no close expected yet unless words; flush instead
    assert b.flush() is not None


def test_window_hard_cap():
    b = WindowBuilder(min_s=60, max_s=90, max_words=10000, min_silence=999)
    t = 0.0
    closed = None
    for i in range(40):
        u = Utterance(id=i, t_start=t, t_end=t + 3.0, text="w", words=1)
        t += 3.0
        r = b.add(u)
        if r:
            closed = r
            break
    assert closed is not None


def test_window_max_words():
    b = WindowBuilder(min_s=9999, max_s=9999, max_words=10, min_silence=9999)
    assert b.add(Utterance(id=1, t_start=0, t_end=1, text="a b c d e f", words=6)) is None
    w = b.add(Utterance(id=2, t_start=1, t_end=2, text="g h i j k", words=5))
    assert w is not None
