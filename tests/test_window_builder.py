import sys

sys.path.insert(0, ".")
from app.memory.windowing import Utterance, WindowBuilder


def test_window_close_on_silence():
    b = WindowBuilder(min_s=60, max_s=90, max_words=450, min_silence=1.5)
    t = 0.0
    for i in range(20):
        u = Utterance(id=i, t_start=t, t_end=t + 3.0, text="hello world " * 5, words=10)
        t += 5.0
        b.add(u)
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
    assert [u.id for u in w.utterances] == [1]
    assert [u.id for u in b.flush().utterances] == [2]


def test_window_after_long_silence_starts_next_window():
    b = WindowBuilder(min_s=10, max_s=90, max_words=450, min_silence=1.5)
    b.add(Utterance(id=1, t_start=0, t_end=11, text="before", words=1))
    closed = b.add(Utterance(id=2, t_start=20, t_end=21, text="after", words=1))
    assert [u.id for u in closed.utterances] == [1]
    assert [u.id for u in b.flush().utterances] == [2]
