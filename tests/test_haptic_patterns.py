"""
F1: failsafe vibration patterns (core/haptic_patterns.py) and the serial worker that plays them.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_haptic_patterns.py -v
    my_hailo_env/bin/python -m pytest tests/test_haptic_patterns.py -v

No ESP32. Two layers:
  * the PatternPlayer on a virtual clock: steps, order, queueing, abort;
  * the REAL serial_worker loop in a thread, against a fake board that acknowledges every packet
    and records it with its time, fed 30 depth frames a second the way callbacks.py feeds them
    (drop-oldest). That checks packet order and timing, the hand-back to depth, the vibration
    switch off, the strength knob low, a lost board, a hazard during a pattern, and shutdown.

Decision D41 (DISAGREEMENTS.md D-15): a failsafe pattern plays even with the vibration switch off,
at max(motor_strength, 0.30) by plain multiply (the user's choice, 2026-10-18), and never changes a
setting.
"""

import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.workers.serial_worker as sw
from second_vision.core.alerts import FAILSAFE_MIN_LEVEL, failsafe_level
from second_vision.core.config import SystemConfig
from second_vision.core.haptic_patterns import (
    PATTERN_CAMERA,
    PATTERN_EARPHONES,
    PATTERN_RECONNECT,
    PATTERNS,
    STRONG_DUTY,
    WEAK_DUTY,
    PatternPlayer,
)

MOTOR = 0x01
HAZARD = 0x04
TIMING_SLACK = 0.06       # seconds; a step is 0.15 s or more, the loop wakes within a few ms


def duties(name):
    return [duty for _, duty in PATTERNS[name]]


def seconds(name):
    return [s for s, _ in PATTERNS[name]]


# ============================================================
# The patterns themselves
# ============================================================
def test_every_pattern_ends_with_the_motors_stopped():
    for name, steps in PATTERNS.items():
        assert steps[-1][1] == 0, name


def test_reconnect_is_two_short_pulses():
    assert duties(PATTERN_RECONNECT) == [STRONG_DUTY, 0, STRONG_DUTY, 0]


def test_camera_is_two_pulses_longer_than_the_reconnect_ones():
    assert duties(PATTERN_CAMERA) == [STRONG_DUTY, 0, STRONG_DUTY, 0]
    cam, rec = seconds(PATTERN_CAMERA), seconds(PATTERN_RECONNECT)
    assert min(cam[0], cam[2]) >= 2 * max(rec[0], rec[2]), "the two beep beeps must differ by feel"


def test_earphones_is_strong_weak_three_times():
    assert duties(PATTERN_EARPHONES) == [STRONG_DUTY, WEAK_DUTY, 0] * 3
    assert 0 < WEAK_DUTY < STRONG_DUTY


def test_no_pattern_holds_the_depth_stream_long():
    for name in PATTERNS:
        assert sum(seconds(name)) <= 2.5, name


# ============================================================
# failsafe_level: the knob, never under 30 %
# ============================================================
@pytest.mark.parametrize("knob, level", [
    (1.0, 1.0), (0.6, 0.6), (0.30, 0.30), (0.29, 0.30), (0.1, 0.30), (0.0, 0.30), (-1.0, 0.30),
    (1.7, 1.0), (None, 1.0), ("loud", 1.0), (float("nan"), 1.0), (float("inf"), 1.0),
])
def test_failsafe_level(knob, level):
    assert failsafe_level(knob) == pytest.approx(level)
    assert FAILSAFE_MIN_LEVEL == 0.30


# ============================================================
# PatternPlayer on a virtual clock
# ============================================================
def test_idle_player_gives_nothing():
    p = PatternPlayer()
    assert p.poll(0.0) is None and not p.busy()


def test_each_step_is_handed_out_once_at_its_time():
    p = PatternPlayer()
    assert p.play(PATTERN_RECONNECT)
    t, out = 10.0, []
    for s, duty in PATTERNS[PATTERN_RECONNECT]:
        tick = p.poll(t)
        assert tick.duty == duty and tick.wake_at == pytest.approx(t + s)
        out.append(tick.duty)
        mid = p.poll(t + s / 2)
        assert mid.duty is None and mid.wake_at == pytest.approx(t + s), "a step is sent once"
        t += s
    assert out == duties(PATTERN_RECONNECT)
    assert p.poll(t) is None and not p.busy()


def test_a_late_poll_never_skips_a_step():
    p = PatternPlayer()
    p.play(PATTERN_RECONNECT)
    p.poll(0.0)
    tick = p.poll(5.0)                    # far past the whole pattern
    assert tick.duty == 0 and tick.wake_at == pytest.approx(5.15), "step 2 starts when handed out"
    assert p.poll(5.15).duty == STRONG_DUTY


def test_patterns_play_back_to_back_in_the_order_asked():
    p = PatternPlayer()
    p.play(PATTERN_RECONNECT)
    p.play(PATTERN_CAMERA)
    t, names, sent = 0.0, [], []
    while (tick := p.poll(t)) is not None:
        if tick.duty is not None:
            names.append(tick.name)
            sent.append(tick.duty)
        t = tick.wake_at
    assert sent == duties(PATTERN_RECONNECT) + duties(PATTERN_CAMERA)
    assert names == [PATTERN_RECONNECT] * 4 + [PATTERN_CAMERA] * 4


def test_a_pattern_already_waiting_is_not_queued_twice():
    p = PatternPlayer()
    assert p.play(PATTERN_CAMERA)
    assert not p.play(PATTERN_CAMERA)
    p.poll(0.0)                           # now playing, no longer waiting
    assert p.play(PATTERN_CAMERA), "the next one may be asked for while one plays"


def test_at_most_one_of_each_pattern_waits():
    p = PatternPlayer()
    for _ in range(3):
        for name in PATTERNS:
            p.play(name)
    assert sorted(p._waiting) == sorted(PATTERNS)


def test_abort_drops_the_current_and_the_waiting_patterns():
    p = PatternPlayer()
    p.play(PATTERN_EARPHONES)
    p.play(PATTERN_CAMERA)
    p.poll(0.0)
    p.abort("test")
    assert p.poll(0.1) is None and not p.busy()


def test_an_unknown_pattern_is_a_caller_bug():
    with pytest.raises(ValueError):
        PatternPlayer().play("siren")


def test_play_from_many_threads_is_safe():
    p = PatternPlayer()
    errors = []

    def hammer():
        try:
            for _ in range(500):
                p.play(PATTERN_RECONNECT)
                p.busy()
        except Exception as e:            # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for th in threads:
        th.start()
    t = 0.0
    while any(th.is_alive() for th in threads) or p.busy():
        tick = p.poll(t)
        t = tick.wake_at if tick else t + 0.01
    for th in threads:
        th.join()
    assert errors == []


# ============================================================
# The real serial worker loop, against a fake board
# ============================================================
class FakeBoard:
    """An open serial port with an ESP32 behind it: acknowledges each packet, records it with its time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.writes = []
        self.inbox = bytearray()
        self.answering = True

    def write(self, data):
        with self.lock:
            self.writes.append((time.monotonic(), bytes(data)))
            if self.answering:
                self.inbox += bytes([0xAA, 0xFF, data[1]])
        return len(data)

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.inbox)

    def read(self, n):
        with self.lock:
            out = bytes(self.inbox[:n])
            del self.inbox[:n]
            return out

    def close(self):
        pass

    def packets(self, kind, since=0.0):
        with self.lock:
            return [(t, p) for t, p in self.writes if p[1] == kind and t >= since]

    def motors(self, since=0.0):
        """(time, duty) of each motor packet; all three motors must agree for the duty to be one number."""
        out = []
        for t, p in self.packets(MOTOR, since):
            assert p[2] == p[3] == p[4], f"motors differ: {tuple(p[2:5])}"
            out.append((t, p[2]))
        return out


class Rig:
    """serial_worker in a thread, a fake board on its port, and a 30 Hz depth producer."""

    def __init__(self, monkeypatch, with_patterns=True, **config):
        self.board = FakeBoard()
        monkeypatch.setattr(sw, "_open_serial_port", lambda cfg: self.board)
        self.config = SystemConfig()
        self.config.update(**config)
        self.ud = SimpleNamespace(serial_queue=queue.Queue(maxsize=10), shutdown_event=threading.Event())
        if with_patterns:
            self.ud.patterns = PatternPlayer()
        self.depth = 100                  # the duty every depth frame carries, on all three zones
        self.hazard = 0                   # severity; 0 = no hazard
        self.numbered = False             # True: each frame's duty is its sequence number (1-254)
        self.made_at = {}                 # sequence number -> time the frame was produced
        self._stop = threading.Event()
        self._worker = threading.Thread(target=sw.serial_worker, args=(self.ud, self.config), daemon=True)
        self._producer = threading.Thread(target=self._produce, daemon=True)

    def _produce(self):
        seq = 0
        while not self._stop.is_set():
            if self.numbered:
                seq = min(seq + 1, 254)
                self.depth = seq
                self.made_at[seq] = time.monotonic()
            frame = {"left": self.depth, "center": self.depth, "right": self.depth,
                     "hazard": self.hazard > 0, "hazard_severity": self.hazard}
            try:                                              # drop-oldest, as callbacks.py does
                self.ud.serial_queue.put_nowait(frame)
            except queue.Full:
                try:
                    self.ud.serial_queue.get_nowait()
                    self.ud.serial_queue.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass
            time.sleep(1 / 30)

    def start(self, produce=True):
        self._worker.start()
        if produce:
            self._producer.start()
        time.sleep(0.3)
        return self

    def play(self, name):
        t = time.monotonic()
        assert self.ud.patterns.play(name)
        return t

    def wait_idle(self, timeout=4.0):
        end = time.monotonic() + timeout
        while self.ud.patterns.busy() and time.monotonic() < end:
            time.sleep(0.01)
        assert not self.ud.patterns.busy(), "the pattern never finished"

    def stop(self):
        self._stop.set()
        self.ud.shutdown_event.set()
        self._worker.join(2.0)
        assert not self._worker.is_alive()


def first_index(seq, pred):
    return next(i for i, x in enumerate(seq) if pred(x))


@pytest.mark.parametrize("name", [PATTERN_RECONNECT, PATTERN_CAMERA, PATTERN_EARPHONES])
def test_pattern_packets_in_order_on_time_then_depth_resumes_on_a_fresh_frame(monkeypatch, name):
    r = Rig(monkeypatch, motor_strength=1.0).start()
    t_play = r.play(name)
    r.depth = 120                         # frames from now on are "fresh"; 100 is the old stream
    r.wait_idle()
    time.sleep(0.3)
    r.stop()

    after = r.board.motors(since=t_play)
    start = first_index(after, lambda x: x[1] == STRONG_DUTY)
    assert all(d in (100, 120) for _, d in after[:start]), "only depth before the pattern"
    assert after[start][0] - t_play < 0.1, "the pattern starts at once while depth frames flow"
    steps = PATTERNS[name]
    played = after[start:start + len(steps)]
    assert [d for _, d in played] == duties(name), "each step sent once, in order, nothing in between"
    for i in range(1, len(steps)):        # each step starts when the previous one has lasted its time
        gap = played[i][0] - played[i - 1][0]
        assert abs(gap - steps[i - 1][0]) < TIMING_SLACK, f"step {i}: {gap:.3f} s"
    rest = after[start + len(steps):]
    assert rest, "the depth stream never resumed"
    assert rest[0][1] == 120, "resumed on a stale frame from before the pattern"
    assert abs((rest[0][0] - played[-1][0]) - steps[-1][0]) < TIMING_SLACK + 1 / 30
    assert all(d == 120 for _, d in rest[:-1]) and rest[-1][1] == 0, "depth, then the shutdown stop"
    assert r.board.packets(HAZARD) == []


def test_frames_queued_during_a_pattern_are_discarded_not_replayed(monkeypatch):
    """Without the drain, ~10 frames from the pattern's last step would go out in a burst."""
    r = Rig(monkeypatch)
    r.numbered = True
    r.start()
    t_play = r.play(PATTERN_RECONNECT)
    r.wait_idle()
    time.sleep(0.3)
    r.stop()
    after = r.board.motors(since=t_play)
    start = first_index(after, lambda x: x[1] == STRONG_DUTY)
    pattern_end = after[start + 3][0] + PATTERNS[PATTERN_RECONNECT][-1][0]
    rest = after[start + 4:-1]            # depth after the pattern, without the shutdown stop
    assert len(rest) >= 5
    assert r.made_at[rest[0][1]] >= pattern_end - 0.02, "resumed on a frame queued during the pattern"
    assert [d for _, d in rest] == list(range(rest[0][1], rest[0][1] + len(rest))), "frames skipped or replayed"


def test_vibration_switch_off_still_plays_the_pattern_and_nothing_else(monkeypatch):
    r = Rig(monkeypatch, motor_strength=1.0, vibration_enabled=False).start()
    before = r.config.snapshot()
    t_play = r.play(PATTERN_RECONNECT)
    r.wait_idle()
    time.sleep(0.5)
    r.stop()
    assert r.config.snapshot() == before, "a setting was changed"
    pre = r.board.motors()
    assert [d for t, d in pre if t < t_play] == [0], "switch off: one stop packet, no depth duty"
    assert [d for _, d in r.board.motors(since=t_play)] == duties(PATTERN_RECONNECT) + [0], \
        "pattern at full strength, then the shutdown stop; no depth duty after it"


@pytest.mark.parametrize("knob", [0.0, 0.1, 0.29, 0.6, 1.0])
def test_strength_knob_scales_the_pattern_but_never_under_30_percent(monkeypatch, knob):
    r = Rig(monkeypatch, motor_strength=knob).start()
    before = r.config.snapshot()
    t_play = r.play(PATTERN_EARPHONES)
    r.wait_idle()
    time.sleep(0.2)
    r.stop()
    assert r.config.snapshot() == before, "a setting was changed"
    level = max(knob, 0.30)
    want = [min(255, int(d * level)) for d in duties(PATTERN_EARPHONES)]
    depth_duty = min(255, int(100 * knob))
    got = [d for _, d in r.board.motors(since=t_play)]
    start = first_index(got, lambda d: d == want[0] and d != depth_duty)
    assert got[start:start + len(want)] == want
    assert all(d == depth_duty for d in got[:start]), "depth before the pattern keeps the plain knob"
    tail = got[start + len(want):]
    assert tail and all(d in (depth_duty, 0) for d in tail), "depth after the pattern keeps the plain knob"


def test_a_pattern_plays_with_no_depth_frames_arriving(monkeypatch):
    """Depth off: the worker parks on the queue 0.25 s at a time; the pattern must still keep its timing."""
    r = Rig(monkeypatch).start(produce=False)
    t_play = r.play(PATTERN_RECONNECT)
    r.wait_idle()
    r.stop()
    played = r.board.motors(since=t_play)[:4]
    assert [d for _, d in played] == duties(PATTERN_RECONNECT)
    assert played[0][0] - t_play <= sw.QUEUE_POLL_SECONDS + TIMING_SLACK
    for i in range(1, 4):
        assert abs(played[i][0] - played[i - 1][0] - seconds(PATTERN_RECONNECT)[i - 1]) < TIMING_SLACK


def test_a_lost_board_stops_the_pattern_and_drops_the_rest(monkeypatch):
    real = sw.LinkHealth
    monkeypatch.setattr(sw, "LinkHealth", lambda: real(timeout=0.3))
    r = Rig(monkeypatch).start()
    r.play(PATTERN_EARPHONES)
    r.play(PATTERN_CAMERA)
    time.sleep(0.3)
    t_lost = time.monotonic()
    r.board.answering = False
    end = time.monotonic() + 2.0
    while r.ud.patterns.busy() and time.monotonic() < end:
        time.sleep(0.01)
    assert not r.ud.patterns.busy(), "patterns kept playing to a board that does not answer"
    t_dropped = time.monotonic()
    # Both dropped, not finished: played out, they would last until about 3.2 s after t_lost.
    assert t_dropped - t_lost < 0.3 + 0.5
    r.play(PATTERN_RECONNECT)             # asked for while the board is gone
    time.sleep(0.4)
    assert not r.ud.patterns.busy()
    r.stop()
    motors = r.board.motors(since=t_lost)
    assert motors[-1][1] == 0
    last_nonzero = max((t for t, d in motors if d), default=t_lost)
    assert last_nonzero - t_lost < 0.3 + 0.3, "a pattern step was sent after the board was found lost"
    assert all(d == 0 for _, d in r.board.motors(since=t_dropped)), "a pattern played to a lost board"


def test_a_hazard_during_a_pattern_is_still_sent(monkeypatch):
    r = Rig(monkeypatch).start()
    t_play = r.play(PATTERN_EARPHONES)
    time.sleep(0.4)
    r.hazard = 200
    r.wait_idle()
    r.stop()
    after = r.board.motors(since=t_play)
    start = first_index(after, lambda x: x[1] == STRONG_DUTY)
    n = len(PATTERNS[PATTERN_EARPHONES])
    played = after[start:start + n]
    assert [d for _, d in played] == duties(PATTERN_EARPHONES), "the pattern's steps were all still sent"
    t_end = played[-1][0] + PATTERNS[PATTERN_EARPHONES][-1][0]
    hazards = [p for t, p in r.board.packets(HAZARD, since=played[0][0]) if t < t_end]
    assert hazards and all(p[2] == 200 for p in hazards), "a ground hazard was hidden by the pattern"


def test_no_hazard_is_sent_during_a_pattern_with_vibration_off(monkeypatch):
    r = Rig(monkeypatch, vibration_enabled=False).start()
    r.hazard = 200
    r.play(PATTERN_RECONNECT)
    r.wait_idle()
    r.stop()
    assert r.board.packets(HAZARD) == []


def test_shutdown_in_the_middle_of_a_pattern_leaves_the_motors_off(monkeypatch):
    r = Rig(monkeypatch).start()
    r.play(PATTERN_CAMERA)
    time.sleep(0.2)                       # inside the first 0.4 s pulse
    r.stop()
    assert r.board.motors()[-1][1] == 0


def test_without_a_pattern_player_the_worker_runs_as_before(monkeypatch):
    r = Rig(monkeypatch, with_patterns=False, motor_strength=0.5).start()
    time.sleep(0.3)
    r.stop()
    got = [d for _, d in r.board.motors()]
    assert got[:-1] and all(d == 50 for d in got[:-1]) and got[-1] == 0
