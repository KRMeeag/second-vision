"""
F4: the camera *beep beep* goes with the spoken camera alert (REQ-CON-07, its vibration half; D41).

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_camera_alert_vibration.py -v
    my_hailo_env/bin/python -m pytest tests/test_camera_alert_vibration.py -v

Requirement: while the camera is missing, each time the device says "Critical Error: Camera cannot
be reached" (at once, then every 10 s) all three motors give the camera pattern (core/haptic_patterns.py
PATTERN_CAMERA, two long pulses, not the reconnect one). It is tied to the sentence, not to a timer of
its own: the alert is raised with an `on_speak` that the scheduler calls when the speech worker starts
the utterance (core/alerts.py).

Three layers:
  * the scheduler's on_speak hook on a virtual clock (tests/test_alerts.py's World and speech model);
  * the REAL SecondVisionApp camera methods (tests/test_camera_alerts.py's unit_app), with a recorder
    in place of the pattern player, so what is felt and when is seen for any sequence of losses and returns;
  * the REAL tts_worker and alert_worker on a stub espeak-ng, so the hook is proven to fire at the start
    of the real utterance, with the speech switch on and off.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

import test_pipeline_modes  # noqa: E402,F401  (installs the hailo/gi stand-ins where the real modules are missing)
from test_alerts import World  # noqa: E402
from test_alerts_speech import Rig  # noqa: E402
from test_camera_alerts import (  # noqa: E402,F401
    BACK, MISSING, NO_CAMERA, OTHER, STOPPED, lose, returns, unit_world,
)
from test_camera_loss import (  # noqa: E402,F401  (the fixtures are used by name)
    CAMERA_STANDIN, MISSING_NODE, UNOPENABLE_CAMERA, UNOPENABLE_CAMERA_WITH_HAILO,
    FakeCameraLookup, HailoStandIn, app_mod, build_app, frames_flowing, lose_camera,
    made_apps, record_shutdowns, state,
)

from second_vision.core.haptic_patterns import PATTERN_CAMERA, PATTERN_RECONNECT, PATTERNS  # noqa: E402


class Plays:
    """Stands in for the pattern player: records (time, name) of each play()."""

    def __init__(self, clock):
        self.clock = clock
        self.log = []
        self._lock = threading.Lock()

    def play(self, name):
        with self._lock:
            self.log.append((self.clock(), name))
        return True

    def times(self, name=PATTERN_CAMERA):
        with self._lock:
            return [t for t, n in self.log if n == name]

    def names(self):
        with self._lock:
            return [n for _, n in self.log]


def near(actual, expected, tol=0.3):
    return len(actual) == len(expected) and all(abs(a - b) <= tol for a, b in zip(actual, expected))


# ============================================================
# The scheduler's on_speak hook, on a virtual clock
# ============================================================
def test_on_speak_fires_when_each_utterance_starts_not_when_the_alert_is_raised():
    w = World(duration=2.5)
    calls = []
    w.at(3.0, lambda: w.sched.raise_alert("a", "x", interval=10.0, on_speak=lambda: calls.append(w.clock.t)))
    w.run(40)
    assert near(calls, [3, 13, 23, 33]), calls
    assert near(calls, w.starts("x")), "the hook and the speech must start together"


def test_on_speak_stops_with_the_alert():
    w = World()
    calls = []
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0, on_speak=lambda: calls.append(w.clock.t)))
    w.at(15.0, lambda: w.sched.clear("a"))
    w.run(60)
    assert near(calls, [0, 10]), calls


def test_an_alert_without_on_speak_calls_nothing_and_does_not_disturb_one_that_has_it():
    w = World()
    calls = []
    w.at(0.0, lambda: w.sched.raise_alert("plain", "no hook", interval=10.0))
    w.at(0.0, lambda: w.sched.raise_alert("hooked", "with hook", interval=10.0,
                                          on_speak=lambda: calls.append(w.clock.t)))
    w.run(35)
    assert len(w.starts("no hook")) >= 3, "the plain alert was not said"
    assert near(calls, w.starts("with hook"), tol=0.1), (calls, w.starts("with hook"))


def test_a_silent_alert_never_calls_on_speak():
    w = World()
    calls = []
    w.sched.raise_alert("quiet", "never said", speak=False, on_speak=lambda: calls.append(1))
    w.run(30)
    assert calls == []


def test_an_error_in_on_speak_never_stops_the_speech_or_the_scheduler():
    w = World()

    def boom():
        raise RuntimeError("pattern player broke")

    w.sched.raise_alert("a", "x", interval=10.0, on_speak=boom)
    w.run(45)
    assert len(w.starts("x")) == 5, w.starts("x")
    assert all(e["outcome"] == "spoken" for e in w.sim.log)


def test_on_speak_runs_outside_the_scheduler_lock_so_it_may_call_the_scheduler():
    """The lock is not re-entrant: a hook called under it that asks for raised() would hang. The run is
    in a daemon thread with a deadline, so a hang is a failure, not a stuck test run (pytest-timeout is
    not installed in the Poetry environment)."""
    w = World()
    seen = []
    w.sched.raise_alert("a", "x", interval=10.0,
                        on_speak=lambda: seen.append((w.sched.raised(), w.sched.is_raised("a"))))
    runner = threading.Thread(target=lambda: w.run(15), daemon=True)
    runner.start()
    runner.join(timeout=10.0)
    assert not runner.is_alive(), "the hook was called with the scheduler's lock held: it deadlocked"
    assert seen and all(r == ["a"] and flag for r, flag in seen), seen


def test_a_sentence_that_is_cut_and_said_again_asks_for_its_vibration_again():
    """A mode announcement cuts the alert; it is said afresh later, and its pattern goes with it. (The
    pattern player keeps at most one of each waiting, so a cut never piles patterns up.)"""
    w = World(duration=2.5)
    calls = []
    w.sched.raise_alert("a", "x", interval=100.0, on_speak=lambda: calls.append(w.clock.t))
    w.at(1.0, lambda: w.mailbox.offer({"announce": "depth mode"}))
    w.run(20)
    assert [e["outcome"] for e in w.sim.log if e["text"] == "x"] == ["cut", "spoken"], w.sim.log
    assert len(calls) == 2, calls


def test_raising_the_same_alert_again_replaces_its_hook():
    w = World()
    first, second = [], []
    w.sched.raise_alert("a", "x", interval=10.0, on_speak=lambda: first.append(w.clock.t))
    w.at(5.0, lambda: w.sched.raise_alert("a", "x", interval=10.0, on_speak=lambda: second.append(w.clock.t)))
    w.run(25)
    assert near(first, [0]) and near(second, [10, 20]), (first, second)


# ============================================================
# The camera code: the real SecondVisionApp methods, a recorder for the pattern player
# ============================================================
def camera_world(app_mod, monkeypatch, with_patterns=True):
    w, app = unit_world(app_mod, monkeypatch)
    plays = Plays(w.clock)
    if with_patterns:
        app.user_data.patterns = plays
    return w, app, plays


def test_the_camera_pattern_is_the_camera_one_and_not_the_reconnect_one():
    assert PATTERN_CAMERA != PATTERN_RECONNECT
    assert PATTERNS[PATTERN_CAMERA] != PATTERNS[PATTERN_RECONNECT]


def test_lost_while_running_gives_the_pattern_with_each_spoken_alert(app_mod, monkeypatch):
    w, app, plays = camera_world(app_mod, monkeypatch)
    lose(w, app, 5.0, hold=3.0)
    w.run(40)
    starts = w.starts(MISSING)
    assert near(starts, [5, 15, 25, 35]), starts
    assert near(plays.times(), starts, tol=0.1), (plays.times(), starts)
    assert set(plays.names()) == {PATTERN_CAMERA}


def test_absent_at_start_up_gives_the_pattern_at_once_and_every_10_seconds(app_mod, monkeypatch):
    w, app, plays = camera_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    w.run(35)
    assert near(plays.times(), [0, 10, 20, 30]), plays.log
    assert near(plays.times(), w.starts(MISSING), tol=0.1)


def test_the_return_stops_the_pattern_and_gives_none_of_its_own(app_mod, monkeypatch):
    w, app, plays = camera_world(app_mod, monkeypatch)
    lose(w, app, 5.0)
    returns(w, app, 30.3)
    w.run(120)
    assert len(w.starts(MISSING)) == 3 and len(w.starts(BACK)) == 1
    assert near(plays.times(), w.starts(MISSING), tol=0.1), "one pattern per missing sentence, none after"
    assert plays.names() == [PATTERN_CAMERA] * 3, "nothing for the reconnect sentence"


@pytest.mark.parametrize("failure", [NO_CAMERA, OTHER, STOPPED])
def test_a_start_that_fails_keeps_one_pattern_per_spoken_alert(app_mod, monkeypatch, failure):
    w, app, plays = camera_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    for t in (7.0, 14.0, 21.0, 28.0):
        returns(w, app, t, (failure, None))
    w.run(55)
    assert near(plays.times(), w.starts(MISSING), tol=0.1), (plays.times(), w.starts(MISSING))


def test_lost_twice_gives_the_pattern_both_times(app_mod, monkeypatch):
    w, app, plays = camera_world(app_mod, monkeypatch)
    lose(w, app, 5.0)
    returns(w, app, 12.0)
    lose(w, app, 40.0)
    w.run(70)
    assert len(w.starts(MISSING)) == len(plays.times()) >= 4, (w.starts(MISSING), plays.log)
    assert any(t >= 40.0 for t in plays.times()), "the second loss gave no pattern"


def test_without_a_pattern_player_the_sentence_is_said_as_before(app_mod, monkeypatch):
    w, app, plays = camera_world(app_mod, monkeypatch, with_patterns=False)
    lose(w, app, 5.0)
    w.run(40)
    assert near(w.starts(MISSING), [5, 15, 25, 35])
    assert plays.log == []


def test_a_pattern_player_that_raises_never_stops_the_alert(app_mod, monkeypatch):
    w, app, plays = camera_world(app_mod, monkeypatch)

    def boom(name):
        raise RuntimeError("player broke")

    app.user_data.patterns = type("Broken", (), {"play": staticmethod(boom)})()
    lose(w, app, 5.0)
    w.run(40)
    assert near(w.starts(MISSING), [5, 15, 25, 35])


# ============================================================
# The real speech worker on a stub espeak-ng
# ============================================================
def _raise_with_hook(rig, calls, interval=0.6):
    rig.ud.alerts.raise_alert("camera-missing", "Critical Error: Camera cannot be reached", interval=interval,
                              on_speak=lambda: calls.append(time.monotonic()))


@pytest.mark.parametrize("speech_switch", [True, False])
def test_the_real_speech_worker_fires_the_hook_at_the_start_of_each_utterance(tmp_path, monkeypatch, speech_switch):
    """With the speech switch off too: a failsafe alert is spoken anyway (D41) and the pattern goes with it."""
    rig = Rig(tmp_path, monkeypatch, default=0.15)
    rig.config.update(tts_enabled=speech_switch)
    calls = []
    rig.start()
    try:
        _raise_with_hook(rig, calls)
        # Three utterances that have run to their end (the stub logs an end), not just three hook calls:
        # stopping the rig the moment the third call comes would cut the third utterance short.
        rig.wait_for(lambda: len(calls) >= 3 and len(rig.utterances()) >= 3, timeout=8.0)
    finally:
        rig.stop()
    spoken = rig.utterances()
    assert len(spoken) >= 3, spoken
    assert len(calls) >= 3
    for hook_at, u in zip(calls, spoken):
        # The hook is called as the worker takes the item; the stub process starts a few ms later.
        assert -0.1 <= u["start"] - hook_at <= 0.5, (hook_at, u["start"])
    assert all(u["text"] == "Critical Error: Camera cannot be reached" for u in spoken)
