"""
F2: the glasses board lost and back (REQ-CON-02 to 04): core/connections.py and the serial worker.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_glasses_link_alerts.py -v
    my_hailo_env/bin/python -m pytest tests/test_glasses_link_alerts.py -v

No ESP32. Two layers:
  * the rules, on a VIRTUAL clock: the real AlertScheduler and PriorityMailbox, and a speech
    worker reduced to take() / finish() with a fixed utterance length. Cadence (30 s while depth
    is on, once while off, a switch-off mid-run), the D-17 sentence, lost and back twice;
  * the REAL serial_worker loop in a thread against a fake board that acknowledges every packet
    while `answering`: the board stops answering, answers again, is absent at start-up, or its
    port cannot be opened; the reconnect pattern on the motors, the switch off, an error in the
    scheduler.

"Depth on" is pipeline_mode "depth" or "both": the panel reports its DEPTH rocker only through
the M: line (firmware/control-panel/PROTOCOL.md), never as depth_enabled.
"""

import ast
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.workers.serial_worker as sw
from second_vision.core.alerts import SPOKEN, AlertScheduler
from second_vision.core.config import SystemConfig
from second_vision.core.connections import (
    CAMERA_MISSING,
    EVERYTHING_CONNECTED_TEXT,
    GLASSES_BACK,
    GLASSES_BACK_TEXT,
    GLASSES_MISSING,
    GLASSES_MISSING_SECONDS,
    GLASSES_MISSING_TEXT,
    depth_on,
    glasses_back,
    glasses_lost,
    reconnect_text,
)
from second_vision.core.haptic_patterns import PATTERN_RECONNECT, PATTERNS, STRONG_DUTY, PatternPlayer
from second_vision.core.priority import PriorityMailbox

APP_PY = Path(__file__).resolve().parent.parent / "src" / "second_vision" / "pipeline" / "app.py"
TICK = 0.05
UTTERANCE = 2.0           # seconds a sentence takes in the model


# ============================================================
# The rules, on a virtual clock
# ============================================================
class VClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class World:
    """Scheduler + mailbox + a minimal speech worker (one utterance at a time) on a virtual clock."""

    def __init__(self, mode="both"):
        self.clock = VClock()
        self.mailbox = PriorityMailbox()
        self.alerts = AlertScheduler(self.mailbox, clock=self.clock)
        self.config = SystemConfig()
        self.config.update(pipeline_mode=mode)
        self.patterns = PatternPlayer()
        self.spoken = []          # (start time, text)
        self._current = None
        self._ends = 0.0
        self.events = []          # (time, callable), run in time order

    def at(self, t, fn):
        self.events.append((t, fn))
        self.events.sort(key=lambda e: e[0])

    def run(self, until):
        while self.clock.t < until - 1e-9:
            while self.events and self.events[0][0] <= self.clock.t + 1e-9:
                self.events.pop(0)[1]()
            self.alerts.poll()
            now = self.clock.t
            if self._current is not None and now >= self._ends - 1e-9:
                self.alerts.finish(self._current, SPOKEN)
                self._current = None
            if self._current is None:
                item = self.alerts.take()
                if item is not None:
                    self.spoken.append((now, item["announce"]))
                    self._current, self._ends = item, now + UTTERANCE
            self.clock.t = round(self.clock.t + TICK, 6)

    def starts(self, text):
        return [t for t, said in self.spoken if said == text]


def near(actual, expected, tol=0.3):
    return len(actual) == len(expected) and all(abs(a - b) <= tol for a, b in zip(actual, expected))


@pytest.mark.parametrize("mode,on", [("both", True), ("depth", True), ("detection", False), ("none", False)])
def test_depth_on_is_read_from_the_pipeline_mode(mode, on):
    config = SystemConfig()
    config.update(pipeline_mode=mode)
    assert depth_on(config) is on


def test_depth_on_ignores_depth_enabled_which_the_panel_never_sends():
    config = SystemConfig()
    config.update(pipeline_mode="detection", depth_enabled=True)
    assert not depth_on(config)


@pytest.mark.parametrize("mode", ["both", "depth"])
def test_lost_with_depth_on_is_said_at_once_then_every_30_seconds(mode):
    w = World(mode)
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.run(4 * GLASSES_MISSING_SECONDS + 5)
    assert GLASSES_MISSING_TEXT == "Vibration is not working"
    assert near(w.starts(GLASSES_MISSING_TEXT), [0, 30, 60, 90, 120]), w.spoken


@pytest.mark.parametrize("mode", ["detection", "none"])
def test_lost_with_depth_off_is_said_once(mode):
    w = World(mode)
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.run(200)
    assert near(w.starts(GLASSES_MISSING_TEXT), [0]), w.spoken
    assert w.alerts.is_raised(GLASSES_MISSING), "a once-said alert stays raised while the board is gone"


def test_depth_switched_off_mid_run_stops_the_repeats():
    """TC-022-006 (W-11): once after depth goes off, not every 30 s."""
    w = World("both")
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(45.0, lambda: w.config.update(pipeline_mode="detection"))
    w.run(200)
    assert near(w.starts(GLASSES_MISSING_TEXT), [0, 30]), w.spoken


def test_back_with_nothing_else_missing_says_everything_is_connected_once_and_plays_reconnect():
    w = World("both")
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(40.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(200)
    assert near(w.starts(GLASSES_MISSING_TEXT), [0, 30]), w.spoken
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [40]), w.spoken
    assert not w.alerts.is_raised(GLASSES_MISSING)
    tick = w.patterns.poll(0.0)
    assert tick is not None and tick.name == PATTERN_RECONNECT


def test_back_stops_the_missing_alert_but_does_not_cut_it():
    """Back at 31 s, while the 30 s repeat is being said: it finishes, then the return is said."""
    w = World("both")
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(31.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(200)
    assert near(w.starts(GLASSES_MISSING_TEXT), [0, 30]), w.spoken
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [32]), w.spoken


def test_back_while_the_camera_is_missing_names_the_part_that_returned():
    """D-17: "Everything is connected" must be true."""
    w = World("both")
    w.at(0.0, lambda: w.alerts.raise_alert(CAMERA_MISSING, "Critical Error: Camera cannot be reached",
                                           interval=10.0))
    w.at(1.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(40.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(60)
    assert GLASSES_BACK_TEXT == "Vibration is working again"
    assert len(w.starts(GLASSES_BACK_TEXT)) == 1, w.spoken
    assert w.starts(EVERYTHING_CONNECTED_TEXT) == [], w.spoken


def test_a_spent_camera_reconnected_sentence_does_not_count_as_missing():
    """The scheduler keeps a spoken once-alert raised; "Camera has been reconnected" is not a missing part."""
    w = World("both")
    w.at(0.0, lambda: w.alerts.raise_alert("camera-reconnected", "Camera has been reconnected"))
    w.at(1.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(40.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(60)
    assert "camera-reconnected" in w.alerts.raised()
    assert len(w.starts(EVERYTHING_CONNECTED_TEXT)) == 1, w.spoken


def test_lost_and_back_twice_says_the_return_both_times():
    """The spent "back" sentence is cleared at each loss, or the second return (same text) would be silent."""
    w = World("both")
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(10.0, lambda: glasses_back(w.alerts, w.patterns))
    w.at(20.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(30.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(60)
    assert near(w.starts(GLASSES_MISSING_TEXT), [0, 20]), w.spoken
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [10, 30]), w.spoken


def test_reconnect_text_is_computed_after_the_own_alert_is_cleared():
    w = World("both")
    glasses_lost(w.alerts, w.config)
    assert reconnect_text(w.alerts, GLASSES_BACK_TEXT) == GLASSES_BACK_TEXT   # still raised: not yet back
    glasses_back(w.alerts, None)                                             # no pattern player: speech only
    w.run(1)
    assert w.starts(EVERYTHING_CONNECTED_TEXT) == [0.0]


def test_no_setting_is_changed():
    w = World("both")
    w.config.update(vibration_enabled=False, tts_enabled=False, motor_strength=0.1, tts_volume=0.1)
    before = w.config.snapshot()
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.at(40.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(60)
    assert w.config.snapshot() == before


def test_the_camera_missing_name_is_the_one_the_camera_code_raises():
    """core/connections.py cannot import pipeline/app.py (GStreamer); the two strings must agree."""
    tree = ast.parse(APP_PY.read_text())
    values = [node.value.value for node in ast.walk(tree)
              if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "CAMERA_ALERT_MISSING"
                                                      for t in node.targets)]
    assert values == [CAMERA_MISSING]


# ============================================================
# The real serial worker, against a fake board
# ============================================================
ACK_TIMEOUT = 0.6         # LinkHealth's, shortened; heartbeats are made frequent so an idle link stays up


class FakeBoard:
    """An open serial port with an ESP32 behind it: acknowledges each packet while `answering`."""

    def __init__(self, answering=True):
        self.lock = threading.Lock()
        self.writes = []
        self.inbox = bytearray()
        self.answering = answering

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

    def motor_duties(self, since):
        with self.lock:
            return [p[2] for t, p in self.writes if p[1] == 0x01 and t >= since]


class Rig:
    """serial_worker in a thread with a scheduler and a pattern player on its user_data."""

    def __init__(self, monkeypatch, board=None, alerts=True, **config):
        real = sw.LinkHealth
        monkeypatch.setattr(sw, "LinkHealth", lambda: real(timeout=ACK_TIMEOUT))
        monkeypatch.setattr(sw, "HEARTBEAT_INTERVAL_SECONDS", 0.1)
        self.board = board
        monkeypatch.setattr(sw, "_open_serial_port", lambda cfg: self.board)
        self.config = SystemConfig()
        self.config.update(serial_port="/dev/fake-glasses", pipeline_mode="both")
        self.config.update(**config)
        self.mailbox = PriorityMailbox()
        self.ud = SimpleNamespace(serial_queue=queue.Queue(maxsize=10), shutdown_event=threading.Event(),
                                  tts_queue=self.mailbox, patterns=PatternPlayer())
        if alerts is True:
            self.ud.alerts = AlertScheduler(self.mailbox)
        elif alerts is not None:
            self.ud.alerts = alerts
        self._worker = threading.Thread(target=sw.serial_worker, args=(self.ud, self.config), daemon=True)

    def start(self):
        self._worker.start()
        time.sleep(0.3)
        return self

    def wait_for(self, check, timeout, what):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if check():
                return time.monotonic()
            time.sleep(0.01)
        pytest.fail(f"never happened: {what}")

    def offered(self):
        """What the scheduler puts in the speech channel next (nothing takes it here)."""
        self.ud.alerts.poll()
        item = self.mailbox.peek()
        return None if item is None else item.get("announce")

    def stop(self):
        self.ud.shutdown_event.set()
        self._worker.join(2.0)
        assert not self._worker.is_alive()


def test_board_stops_answering_raises_the_alert_and_stops_the_motors(monkeypatch):
    r = Rig(monkeypatch, board=FakeBoard()).start()
    assert r.ud.alerts.raised() == [], "a board that answers is not missing"
    t_lost = time.monotonic()
    r.board.answering = False
    t_raised = r.wait_for(lambda: r.ud.alerts.is_raised(GLASSES_MISSING), ACK_TIMEOUT + 1.0,
                          "the missing alert")
    # The last ACK can be read up to one queue wait (0.25 s) before t_lost.
    assert t_raised - t_lost >= ACK_TIMEOUT - sw.QUEUE_POLL_SECONDS - 0.05, "raised before the link timeout"
    assert r.offered() == GLASSES_MISSING_TEXT
    r.stop()
    assert r.board.motor_duties(since=t_lost)[-1] == 0


def test_board_answers_again_clears_the_alert_plays_reconnect_and_says_everything_is_connected(monkeypatch):
    r = Rig(monkeypatch, board=FakeBoard()).start()
    r.board.answering = False
    r.wait_for(lambda: r.ud.alerts.is_raised(GLASSES_MISSING), ACK_TIMEOUT + 1.0, "the missing alert")
    assert r.offered() == GLASSES_MISSING_TEXT          # in the speech channel, not yet taken
    t_back = time.monotonic()
    r.board.answering = True
    r.wait_for(lambda: r.ud.alerts.is_raised(GLASSES_BACK), 1.0, "the reconnect sentence")
    assert not r.ud.alerts.is_raised(GLASSES_MISSING)
    assert r.offered() == EVERYTHING_CONNECTED_TEXT      # the queued missing item was withdrawn
    r.wait_for(lambda: not r.ud.patterns.busy(), 2.0, "the reconnect pattern to finish")
    r.stop()
    expected = [d for _, d in PATTERNS[PATTERN_RECONNECT]]
    duties = r.board.motor_duties(since=t_back)
    assert duties[:len(expected)] == expected, duties


def test_vibration_switch_off_still_plays_the_reconnect_pattern(monkeypatch):
    """D41: a failsafe pattern ignores the switch; strength max(knob, 0.30), here the knob's 1.0."""
    r = Rig(monkeypatch, board=FakeBoard(), vibration_enabled=False).start()
    r.board.answering = False
    r.wait_for(lambda: r.ud.alerts.is_raised(GLASSES_MISSING), ACK_TIMEOUT + 1.0, "the missing alert")
    t_back = time.monotonic()
    r.board.answering = True
    r.wait_for(lambda: r.ud.alerts.is_raised(GLASSES_BACK), 1.0, "the reconnect sentence")
    r.wait_for(lambda: not r.ud.patterns.busy(), 2.0, "the reconnect pattern to finish")
    r.stop()
    assert STRONG_DUTY in r.board.motor_duties(since=t_back)
    assert r.config.get("vibration_enabled") is False


def test_board_absent_at_start_up_counts_as_lost(monkeypatch):
    r = Rig(monkeypatch, board=FakeBoard(answering=False)).start()
    r.wait_for(lambda: r.ud.alerts.is_raised(GLASSES_MISSING), ACK_TIMEOUT + 1.0, "the missing alert")
    assert r.offered() == GLASSES_MISSING_TEXT
    r.stop()


def test_a_requested_port_that_cannot_be_opened_raises_the_alert_at_once(monkeypatch):
    r = Rig(monkeypatch, board=None).start()
    assert r.ud.alerts.is_raised(GLASSES_MISSING)
    assert r.offered() == GLASSES_MISSING_TEXT
    time.sleep(ACK_TIMEOUT + 0.3)
    assert r.ud.alerts.raised() == [GLASSES_MISSING], "no phantom return from a port that never opened"
    r.stop()


def test_no_port_requested_says_nothing(monkeypatch):
    r = Rig(monkeypatch, board=None, serial_port=None).start()
    time.sleep(ACK_TIMEOUT + 0.3)
    assert r.ud.alerts.raised() == []
    r.stop()


def test_without_a_scheduler_the_worker_runs_as_before(monkeypatch):
    r = Rig(monkeypatch, board=FakeBoard(), alerts=None).start()
    t_lost = time.monotonic()
    r.board.answering = False
    time.sleep(ACK_TIMEOUT + 0.4)
    r.board.answering = True
    time.sleep(0.4)
    r.stop()
    assert 0 in r.board.motor_duties(since=t_lost)


class BrokenAlerts:
    """A scheduler whose every call fails."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError("scheduler broken")
        return fail


def test_an_error_in_the_scheduler_never_stops_the_motor_link(monkeypatch):
    r = Rig(monkeypatch, board=FakeBoard(), alerts=BrokenAlerts()).start()
    r.board.answering = False
    time.sleep(ACK_TIMEOUT + 0.4)
    r.board.answering = True
    time.sleep(0.4)
    t = time.monotonic()
    time.sleep(0.4)
    assert r._worker.is_alive()
    with r.board.lock:
        assert any(ts >= t for ts, _ in r.board.writes), "the worker stopped writing"
    r.stop()
