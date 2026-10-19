"""
T-03c1: the camera's status in speech (REQ-CON-07, its SPOKEN half; REQ-CON-09).

Requirement (docs/testing/REQUIREMENTS.md): when the camera can't be reached (absent at start-up or
disconnected while running) the device says "Critical Error: Camera cannot be reached" and repeats
it every 10 seconds (the *beep beep* half is T-03c2 and is NOT tested here); when the camera comes
back the device says the camera has been reconnected and returns to normal operation. The camera
handling itself (no crash, vision restarts) is REQ-CON-08, tested in tests/test_camera_loss.py and
tests/test_startup_without_camera.py, which must keep passing.

Two levels in this file.

Unit (runs in the Poetry environment and in my_hailo_env): the REAL AlertScheduler and PriorityMailbox
on a virtual clock with tests/test_alerts.py's speech model (World), and the REAL SecondVisionApp
methods that carry the hooks (_start_without_camera, _on_camera_lost, _wait_for_camera,
_start_on_camera, _rebuild_pipeline, _rebuild_on_camera). What is replaced on the app instance:
everything that touches GStreamer (the pipeline parking, the teardown, the launch of a pipeline on a
camera, the watcher thread) and GLib's timers. So these show WHAT is said and WHEN for any sequence
of losses and returns; they show nothing about GStreamer.

Level `gst` (my_hailo_env only; skips with a reason elsewhere): the REAL app, the real GStreamer
bus, watcher thread and staged pipeline start of T-03a/T-03b, with the stand-ins of
tests/test_camera_loss.py for the camera and the Hailo chip, plus the real alert scheduler and the
real alert_worker thread. The speech worker is a stand-in that takes through the scheduler like
tts_worker does, so the sentence order and timing are seen without espeak. The cadence is shortened
(CAMERA_MISSING_SECONDS 0.5 s) so a test takes seconds. The real 10 s cadence, the real tts_worker
with a stub espeak-ng, the real camera and the Hailo chip are in tests/test_camera_alerts_device.py.
"""

import functools
import logging
import os
import random
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

import gst_helpers  # noqa: E402
import test_pipeline_modes  # noqa: E402,F401  (installs the hailo/gi stand-ins where the real modules are missing)
from test_alerts import World  # noqa: E402
from test_camera_loss import (  # noqa: E402,F401  (the fixtures are used by name)
    CAMERA_STANDIN, MISSING_NODE, UNOPENABLE_CAMERA, UNOPENABLE_CAMERA_WITH_HAILO,
    FakeCameraLookup, HailoStandIn, app_mod, build_app, frames_flowing, lose_camera,
    made_apps, record_shutdowns, state,
)
from test_startup_without_camera import run_until_stopped  # noqa: E402

from second_vision.core.alerts import SPOKEN, AlertScheduler  # noqa: E402
from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.workers.alert_worker import alert_worker  # noqa: E402

MISSING = "Critical Error: Camera cannot be reached"
BACK = "Camera has been reconnected"
MISSING_NAME = "camera-missing"
BACK_NAME = "camera-reconnected"
CAMERA_NODE = "/dev/video0"
SPEECH_SECONDS = 2.5           # roughly how long espeak-ng takes for the longer sentence
RUNNING = "running"            # app.START_RUNNING
NO_CAMERA = "camera"           # app.START_NO_CAMERA
OTHER = "other"                # app.START_FAILED
STOPPED = "stopped"            # app.START_STOPPED


# ============================================================
# Unit level: the real app methods, GStreamer replaced
# ============================================================
class Msg:
    """Stands for the bus message of a camera loss; the app only hands it to _describe_message."""


def unit_app(app_mod, monkeypatch, alerts, *, mode="both", node_missing=False, with_alerts=True):
    """
    A SecondVisionApp built with __new__ whose GStreamer-touching methods are recorders.

    `app.calls` lists what the app did ("park", "watch", "motors", "release", "launch", ...) and
    `app.glib.timeouts` the (delay in ms, callback) pairs it asked GLib for. The scripted answers of
    _launch_on_camera are `app.outcomes` (consumed in order; START_RUNNING when it runs out).
    """
    glib = SimpleNamespace(timeouts=[], idles=[])
    glib.timeout_add = lambda delay_ms, fn, *a: (
        glib.timeouts.append((delay_ms, functools.partial(fn, *a))) or len(glib.timeouts))
    glib.idle_add = lambda fn, *a: glib.idles.append(fn) or len(glib.idles)
    glib.MainLoop = lambda: object()
    monkeypatch.setattr(app_mod, "GLib", glib)
    monkeypatch.setattr(app_mod, "Gst", SimpleNamespace(init=lambda *a: None))

    app = app_mod.SecondVisionApp.__new__(app_mod.SecondVisionApp)
    user_data = SimpleNamespace(get_count=lambda: 0, shutdown_event=threading.Event())
    if with_alerts:
        user_data.alerts = alerts
    app.user_data = user_data
    app.config = SystemConfig()
    app.config.update(pipeline_mode=mode)
    app.video_source = CAMERA_NODE
    app.source_type = "usb"
    app.pipeline = None
    app.loop = None
    app.error_occurred = False
    app.watchdog_paused = False
    app.rebuild_count = 0
    app._shutting_down = False
    app._rebuild_in_flight = False
    app._last_rebuild_at = 0.0
    app._built_mode = mode
    app._swap_started_at = None
    app.glib = glib
    app.calls = []
    app.outcomes = []
    app.node_missing = node_missing

    def record(name):
        return lambda *a, **k: app.calls.append(name)

    def launch(node):
        app.calls.append("launch")
        outcome = app.outcomes.pop(0) if app.outcomes else (RUNNING, None)
        if outcome[0] == RUNNING:
            app._built_mode = app.current_mode()
        return outcome

    app._park_empty_pipeline = record("park")
    app._start_camera_watch = record("watch")
    app._stop_motors = record("motors")
    app._release_old_pipeline = record("release")
    app._release_old_pipeline_with_alarm = record("release")
    app._shut_down_after_failed_start = record("shut-down")
    app.trigger_rebuild = record("trigger-rebuild")
    app._describe_message = lambda message: "error from source: test"
    app._camera_node_missing = lambda: app.node_missing
    app._launch_on_camera = launch
    return app


def unit_world(app_mod, monkeypatch, *, duration=SPEECH_SECONDS, **kwargs):
    """(world, app): a virtual clock, the real scheduler and speech model, and the app wired to them."""
    w = World(duration=duration)
    app = unit_app(app_mod, monkeypatch, w.sched, **kwargs)
    return w, app


def lose(w, app, t, hold=2.0):
    """
    At `t` the bus reports the camera lost (_on_camera_lost); the teardown the app schedules runs
    `hold` s later, as when it is held back after a recent rebuild (_release_after_camera_loss).
    """
    def run():
        before = len(app.glib.timeouts)
        app._on_camera_lost(Msg())
        assert len(app.glib.timeouts) == before + 1, "the teardown was not scheduled"
        w.at(w.clock.t + hold, app.glib.timeouts[-1][1])
    w.at(t, run)


def returns(w, app, t, *outcomes):
    """
    At `t` the watcher hands the app a camera and it starts vision on it with the given outcome(s).
    A main-loop timer the start asks for (the confirmation of the return) runs after its delay.
    """
    def run():
        app.outcomes.extend(outcomes or [(RUNNING, None)])
        before = len(app.glib.timeouts)
        app._start_on_camera(CAMERA_NODE)
        for delay_ms, fn in app.glib.timeouts[before:]:
            w.at(w.clock.t + delay_ms / 1000.0, fn)
    w.at(t, run)


def texts(w):
    return [e["text"] for e in w.sim.log]


def assert_exact_items(w):
    """Rule 2: what reaches the speech queue is exactly {"announce": text}, and nothing is cut."""
    for e in w.sim.log:
        assert e["item"] == {"announce": e["text"]}, e["item"]
        assert e["outcome"] == SPOKEN, e


def test_the_spoken_words_and_the_interval_are_the_required_ones(app_mod):
    assert app_mod.CAMERA_MISSING_TEXT == MISSING
    assert app_mod.CAMERA_BACK_TEXT == BACK
    assert app_mod.CAMERA_MISSING_SECONDS == 10.0
    assert app_mod.CAMERA_ALERT_MISSING == MISSING_NAME and app_mod.CAMERA_ALERT_BACK == BACK_NAME


# ---- Criterion 1: absent at start-up ---------------------------------------------------------
def test_t03c1_con07_absent_at_start_up_says_it_at_once_and_every_10_s(app_mod, monkeypatch):
    """REQ-CON-07, TC-024-002: six utterances in 60 s, the first at once, 10 s start to start."""
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    w.run(65)

    starts = w.starts(MISSING)
    assert len(starts) >= 6, starts
    assert starts[0] <= 1.0, f"first utterance {starts[0]} s after the program started"
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(9.8 <= g <= 10.3 for g in gaps), gaps
    assert set(texts(w)) == {MISSING}, "something else was said"
    assert_exact_items(w)
    assert app._camera_state == "waiting"


# ---- Criterion 2: lost while running ---------------------------------------------------------
def test_t03c1_con07_lost_while_running_starts_at_once_not_after_the_teardown(app_mod, monkeypatch):
    """REQ-CON-07, TC-024-003: the alert begins at the loss, while the teardown is still held back."""
    w, app = unit_world(app_mod, monkeypatch)
    lose(w, app, 5.0, hold=3.0)
    w.run(40)

    assert w.sched.is_raised(MISSING_NAME)
    starts = w.starts(MISSING)
    assert 5.0 <= starts[0] <= 6.0, starts
    assert starts[0] < 5.0 + 3.0, "the alert waited for the teardown"
    assert "release" in app.calls, "the scheduled teardown did not run"
    assert [round(b - a, 1) for a, b in zip(starts, starts[1:])] == [10.0] * (len(starts) - 1)
    assert len(starts) == 4, starts           # 5, 15, 25, 35: the teardown's own raise adds none
    assert_exact_items(w)


def test_t03c1_con07_the_alert_is_raised_inside_the_loss_handler_before_anything_else(app_mod, monkeypatch):
    w, app = unit_world(app_mod, monkeypatch)
    app._on_camera_lost(Msg())

    assert w.sched.raised() == [MISSING_NAME]
    assert app._camera_state == "lost"
    assert app.calls == [], "the teardown ran inside the handler"
    assert len(app.glib.timeouts) == 1


# ---- Criterion 3: the return -----------------------------------------------------------------
def test_t03c1_con09_return_after_a_loss_stops_the_alert_and_says_reconnected_once(app_mod, monkeypatch):
    """REQ-CON-09, TC-024-004."""
    w, app = unit_world(app_mod, monkeypatch)
    lose(w, app, 5.0)
    returns(w, app, 30.3)
    w.run(120)

    assert w.starts(MISSING) == pytest.approx([5.0, 15.0, 25.0], abs=0.3)
    back = w.starts(BACK)
    assert len(back) == 1, back
    assert 30.3 <= back[0] <= 30.3 + 3.0, f"reconnected {back[0] - 30.3:.2f} s after the pipeline ran"
    assert texts(w) == [MISSING] * 3 + [BACK]
    assert app._camera_state == "ok" and app._camera_missing_since is None
    assert w.sched.raised() == [BACK_NAME], "the missing alert is still up"
    assert_exact_items(w)


def test_t03c1_con09_return_after_a_start_up_without_a_camera(app_mod, monkeypatch):
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    returns(w, app, 23.0)
    w.run(80)

    assert texts(w) == [MISSING] * 3 + [BACK]
    assert 23.0 <= w.starts(BACK)[0] <= 26.0
    assert_exact_items(w)


def test_t03c1_con09_the_utterance_under_way_is_finished_and_the_sentence_follows_it(app_mod, monkeypatch):
    """T-00a: clear() never cuts a running utterance, so the reconnect sentence waits for its end."""
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    returns(w, app, 11.0)                       # the second utterance (10.0 to 12.5) is being spoken
    w.run(60)

    assert texts(w) == [MISSING, MISSING, BACK]
    assert_exact_items(w)                       # nothing was cut
    delay = w.starts(BACK)[0] - 11.0
    print(f"reconnected {delay:.2f} s after the pipeline ran, with {SPEECH_SECONDS} s of the missing "
          f"sentence still to go (1.5 s left, then the 0.5 s gap between utterances)")
    assert 1.4 <= delay <= 2.5


def test_t03c1_con09_a_missing_sentence_not_yet_started_is_withdrawn_when_the_camera_is_back(app_mod, monkeypatch):
    """
    The speech channel is busy with something else for the whole loss (a long announcement), so the
    missing sentence was never started: it is withdrawn at the return and only "Camera has been
    reconnected" is said. (Pinned as it is today; the Lead's report note says so.)
    """
    w, app = unit_world(app_mod, monkeypatch)
    w.at(1.0, lambda: w.mailbox.offer({"announce": "depth mode"}))
    w.sim.duration = lambda text: 8.0 if text == "depth mode" else SPEECH_SECONDS
    lose(w, app, 2.0)
    returns(w, app, 6.0)
    w.run(40)

    assert texts(w) == ["depth mode", BACK], texts(w)


# ---- Criterion 4: a camera that appears but cannot start -------------------------------------
@pytest.mark.parametrize("failure", [NO_CAMERA, OTHER, STOPPED])
def test_t03c1_con07_a_start_that_fails_keeps_the_alert_and_says_nothing_about_reconnecting(
        app_mod, monkeypatch, failure):
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    for t in (7.0, 14.0, 21.0, 28.0):
        returns(w, app, t, (failure, None))
    w.run(55)

    assert set(texts(w)) == {MISSING}, texts(w)
    starts = w.starts(MISSING)
    assert [round(b - a, 1) for a, b in zip(starts, starts[1:])] == [10.0] * (len(starts) - 1), starts
    assert w.sched.is_raised(MISSING_NAME) and not w.sched.is_raised(BACK_NAME)
    if failure == NO_CAMERA:
        assert app._camera_state == "waiting"


def confirm_seconds(app_mod):
    # getattr: the same tests run against the code before the confirmation existed (red before green).
    return getattr(app_mod, "CAMERA_BACK_CONFIRM_SECONDS", 1.0)


@pytest.mark.parametrize("fails_after", [0.003, 0.08, 0.4, 0.9])
def test_t03c1_con07_a_camera_that_starts_and_fails_at_once_keeps_the_cadence_and_is_never_called_back(
        app_mod, monkeypatch, fails_after):
    """
    Criterion 4, the case found on the device (the camera is there but another process holds it): the
    pipeline reaches PLAYING, so the start is reported as running, and the camera's "Device is busy"
    error comes `fails_after` s later (3 ms on the device, 2026-10-18). Every 4 s the watcher tries
    again. The wearer must hear the missing sentence every 10 s from its own last utterance, as for any
    missing camera, and never "Camera has been reconnected": the camera has not come back.
    """
    assert fails_after < confirm_seconds(app_mod)
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    t = 3.0
    while t < 44.0:
        returns(w, app, t)
        lose(w, app, t + fails_after, hold=1.0)
        t += 4.0
    w.run(50)

    assert set(texts(w)) == {MISSING}, texts(w)
    starts = w.starts(MISSING)
    assert len(starts) == 5, starts                       # 0.05, 10.05, 20.05, 30.05, 40.05
    assert all(9.8 <= b - a <= 10.3 for a, b in zip(starts, starts[1:])), starts
    assert w.sched.is_raised(MISSING_NAME) and not w.sched.is_raised(BACK_NAME)
    assert_exact_items(w)


def test_t03c1_con09_the_missing_sentence_stops_repeating_when_the_pipeline_runs(app_mod, monkeypatch):
    """
    The 10 s repeat falls due 0.1 s after the pipeline runs: it is not said ("nothing more is spoken for
    the missing-camera alert"), and "reconnected" follows once the return has held for the confirmation
    time (CAMERA_BACK_CONFIRM_SECONDS), not before.
    """
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    returns(w, app, 9.95)
    w.run(40)

    assert texts(w) == [MISSING, BACK], texts(w)
    back = w.starts(BACK)[0]
    assert 9.95 + confirm_seconds(app_mod) <= back <= 9.95 + confirm_seconds(app_mod) + 0.5, back
    assert_exact_items(w)


def test_t03c1_con09_an_earlier_confirmation_does_not_announce_a_later_start_early(app_mod, monkeypatch):
    """A start that ran and failed leaves its confirmation timer behind; a second start must not be announced by it."""
    w, app = unit_world(app_mod, monkeypatch)
    c = confirm_seconds(app_mod)
    w.at(0.0, app._start_without_camera)
    returns(w, app, 3.0)                    # its confirmation is due at 3.0 + c
    lose(w, app, 3.2, hold=0.3)             # it failed first
    returns(w, app, 3.8)                    # a second start that holds: confirmation due at 3.8 + c
    w.run(40)

    assert texts(w) == [MISSING, BACK], texts(w)
    assert w.starts(BACK)[0] >= 3.8 + c, "the first start's timer announced the second start before it had held"


def test_t03c1_con09_a_confirmation_does_nothing_once_the_stop_began_or_the_camera_is_not_ok(app_mod, monkeypatch):
    w, app = unit_world(app_mod, monkeypatch)
    app._start_without_camera()
    app._start_on_camera(CAMERA_NODE)
    (_, confirm), = app.glib.timeouts
    app._shutting_down = True
    assert confirm() is False
    assert w.sched.is_raised(MISSING_NAME) and not w.sched.is_raised(BACK_NAME)

    app._shutting_down = False
    app._camera_state = "waiting"
    assert confirm() is False
    assert w.sched.is_raised(MISSING_NAME) and not w.sched.is_raised(BACK_NAME)

    app._camera_state = "ok"
    assert confirm() is False                       # the timer is one-shot; this one is current and the camera is ok
    assert w.sched.is_raised(BACK_NAME) and not w.sched.is_raised(MISSING_NAME)


def test_t03c1_con07_failed_starts_then_a_good_one_say_reconnected_once(app_mod, monkeypatch):
    w, app = unit_world(app_mod, monkeypatch)
    w.at(0.0, app._start_without_camera)
    returns(w, app, 4.0, (NO_CAMERA, None))
    returns(w, app, 9.0, (NO_CAMERA, None))
    returns(w, app, 14.0, (RUNNING, None))
    w.run(60)

    assert texts(w) == [MISSING, MISSING, BACK], texts(w)


# ---- Criterion 5: every cycle ----------------------------------------------------------------
@pytest.mark.parametrize("start_absent", [False, True], ids=["running-first", "absent-first"])
def test_t03c1_con09_every_cycle_gives_the_alert_then_exactly_one_reconnected(app_mod, monkeypatch, start_absent):
    """
    The mistake to avoid (brief, criterion 5): "Camera has been reconnected" is a once-only alert and
    the scheduler keeps a spent once-alert raised, so unless it is cleared on the next loss the second
    return would be silent.
    """
    w, app = unit_world(app_mod, monkeypatch)
    t = 0.0
    if start_absent:
        w.at(0.0, app._start_without_camera)
        returns(w, app, 14.0)
        t = 14.0
    for _ in range(3):
        lose(w, app, t + 10.0)
        returns(w, app, t + 35.0)
        t += 35.0
    w.run(t + 20)

    one_cycle = [MISSING] * 3 + [BACK]
    cycles = 3 + (1 if start_absent else 0)
    collapsed = []
    for text in texts(w):
        if not collapsed or text != MISSING or collapsed[-1] != MISSING:
            collapsed.append(text)
    assert collapsed == [MISSING, BACK] * cycles, texts(w)
    assert texts(w).count(BACK) == cycles
    assert_exact_items(w)


def test_t03c1_con09_losing_the_camera_withdraws_the_reconnected_sentence_still_in_the_queue(app_mod, monkeypatch):
    """
    A return confirmed, then a new loss before the reconnect sentence was said. A head turn mutes speech
    for a second, so "reconnected" (raised when the return is confirmed, at 3.6 s) waits in the mailbox
    when the camera is lost again at 3.8 s. It must go: while the camera is missing the device must not
    say it is back.
    """
    w, app = unit_world(app_mod, monkeypatch)
    seen = {}
    w.at(0.0, app._start_without_camera)
    w.at(3.2, w.sim.head_turn)                  # the first sentence ended at 2.55; muted 3.2 to 4.2
    returns(w, app, 2.6)
    w.at(3.7, lambda: seen.setdefault("queued", w.mailbox.peek()))
    lose(w, app, 3.8, hold=0.5)
    w.at(3.9, lambda: seen.setdefault("after_loss", w.mailbox.peek()))
    returns(w, app, 30.0)
    w.run(60)

    assert seen["queued"] == {"announce": BACK}, "the reconnected sentence was not waiting in the mailbox"
    assert seen["after_loss"] != {"announce": BACK}, "the loss left the reconnected sentence in the mailbox"
    assert texts(w).count(BACK) == 1, texts(w)
    assert w.starts(BACK)[0] >= 30.0, "the stale reconnected sentence was said while the camera was missing"
    assert texts(w)[:2] == [MISSING, MISSING], texts(w)     # the alert goes on after the new loss


def test_t03c1_con09_a_reconnected_sentence_under_way_is_not_cut_by_a_new_loss(app_mod, monkeypatch):
    w, app = unit_world(app_mod, monkeypatch)
    lose(w, app, 1.0, hold=1.0)
    returns(w, app, 2.2)                  # confirmed at 3.2, BACK at about 3.25 to 5.75
    lose(w, app, 5.0)                     # lost again while BACK is being said
    w.run(40)

    assert_exact_items(w)                 # nothing cut
    assert texts(w)[:3] == [MISSING, BACK, MISSING], texts(w)


@pytest.mark.parametrize("seed", range(40))
def test_t03c1_con09_random_histories_obey_the_rules(app_mod, monkeypatch, seed):
    """
    A random history of losses and returns (a start-up without a camera or not, 2 to 5 cycles, failed
    starts in the middle of a loss, teardowns held back): in every one, nothing is lost, doubled or out
    of order. Per loss window: the missing sentence starts within 1 s of the loss and then every 10 s,
    never after the return; per return: exactly one reconnected sentence, after the confirmation time and within 5 s,
    none while the camera is missing.
    """
    rng = random.Random(seed)
    w, app = unit_world(app_mod, monkeypatch)
    windows = []                                   # (loss time, return time)
    cycles = rng.randint(2, 5)
    t = rng.uniform(1.0, 10.0)
    if rng.random() < 0.5:                         # the program starts without a camera
        w.at(0.0, app._start_without_camera)
        windows.append((0.0, rng.uniform(0.5, 40.0)))
        t = windows[0][1] + rng.uniform(8.0, 30.0)
    while len(windows) < cycles:
        hold = rng.uniform(0.1, 3.0)
        lose(w, app, t, hold=hold)
        windows.append((t, t + hold + rng.uniform(0.5, 40.0)))
        t = windows[-1][1] + rng.uniform(8.0, 30.0)
    for loss, back in windows:
        for _ in range(rng.randint(0, 3)):         # a camera is found that cannot be started
            returns(w, app, rng.uniform(loss + 0.4, back - 0.1), (NO_CAMERA, None))
        returns(w, app, back)
    w.run(t + 5)

    log = [(e["start"], e["text"]) for e in w.sim.log]
    assert all(e["outcome"] == SPOKEN and e["item"] == {"announce": e["text"]} for e in w.sim.log)
    assert {x for _, x in log} <= {MISSING, BACK}
    for i, (loss, back) in enumerate(windows):
        nxt = windows[i + 1][0] if i + 1 < len(windows) else float("inf")
        missing = [s for s, x in log if x == MISSING and loss <= s < nxt]
        reconnected = [s for s, x in log if x == BACK and loss <= s < nxt]
        assert missing, f"window {i}: nothing said for the loss at {loss:.1f}: {log}"
        assert missing[0] - loss <= 1.0, (i, loss, missing)
        assert all(s <= back + 0.1 for s in missing), f"window {i}: the alert went on after the return: {missing}"
        assert all(9.8 <= b - a <= 10.3 for a, b in zip(missing, missing[1:])), (i, missing)
        assert len(reconnected) == 1, f"window {i}: {len(reconnected)} reconnected sentences: {log}"
        assert back + 0.9 <= reconnected[0] <= back + 5.0, (i, back, reconnected)


@pytest.mark.parametrize("seed", range(40))
def test_t03c1_con07_random_histories_with_cameras_that_start_and_fail_at_once(app_mod, monkeypatch, seed):
    """
    Like the random histories above, but the middle of every loss is a held or unstable camera: starts
    that report running and fail `d` s later (3 ms to 0.95 s, inside the confirmation time), tried again
    every 2 to 6 s. Per loss window: the missing sentence starts within 1 s of the loss, then every 10 s
    (a repeat that falls due while the pause of a start is on is said when the start fails: up to 1 s
    late), and never after the return; exactly one reconnected sentence per window, after the
    confirmation time and never before the return. Never "reconnected" while the camera is missing.
    """
    rng = random.Random(1000 + seed)
    w, app = unit_world(app_mod, monkeypatch)
    windows = []                                   # (loss time, return time)
    t = rng.uniform(1.0, 10.0)
    if rng.random() < 0.5:
        w.at(0.0, app._start_without_camera)
        loss = 0.0
        t = 2.0
    else:
        loss = t
        lose(w, app, t, hold=1.0)
        t += 2.5
    count = rng.randint(2, 4)
    for window in range(count):
        for _ in range(rng.randint(2, 9)):         # held-camera attempts
            fails_after = rng.uniform(0.003, 0.95)
            returns(w, app, t)
            lose(w, app, t + fails_after, hold=0.3)
            t += fails_after + 0.3 + rng.uniform(1.5, 5.5)
        back = t
        returns(w, app, back)
        windows.append((loss, back))
        if window + 1 < count:
            loss = back + rng.uniform(15.0, 35.0)   # the next loss, well after this return was said
            lose(w, app, loss, hold=rng.uniform(0.1, 3.0))
            t = loss + 3.5
    w.run(windows[-1][1] + 40)

    log = [(e["start"], e["text"]) for e in w.sim.log]
    assert all(e["outcome"] == SPOKEN and e["item"] == {"announce": e["text"]} for e in w.sim.log)
    for i, (loss, back) in enumerate(windows):
        nxt = windows[i + 1][0] if i + 1 < len(windows) else float("inf")
        missing = [s for s, x in log if x == MISSING and loss <= s < nxt]
        reconnected = [s for s, x in log if x == BACK and loss <= s < nxt]
        assert missing and missing[0] - loss <= 1.0, (i, loss, missing)
        assert all(s <= back + 0.1 for s in missing), f"window {i}: the alert went on after the return: {missing}"
        assert all(9.8 <= b - a <= 11.3 for a, b in zip(missing, missing[1:])), (i, missing)
        assert len(reconnected) == 1, f"window {i}: {len(reconnected)} reconnected sentences: {log}"
        assert back + 0.9 <= reconnected[0] <= back + 5.0, (i, back, reconnected)


# ---- Criterion 6: nothing new when nothing is wrong ------------------------------------------
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_t03c1_con07_a_camera_that_is_never_lost_says_nothing_even_across_mode_switches(
        app_mod, monkeypatch, mode):
    w, app = unit_world(app_mod, monkeypatch, mode=mode)
    app._camera_state = "ok"
    app._camera_node_missing = lambda: False
    for i, t in enumerate((5.0, 40.0, 70.0)):     # a panel switch: the mode changes, the pipeline is rebuilt on the camera
        def switch(i=i):
            app.config.update(pipeline_mode=("depth", "detection", "both")[i])
            app._rebuild_pipeline()
        w.at(t, switch)
    w.run(120)

    assert w.sim.log == [] and w.sched.raised() == []
    assert app._camera_state == "ok"
    assert app.calls.count("launch") == 3


def test_t03c1_con09_returning_without_ever_having_been_missing_says_nothing(app_mod, monkeypatch):
    w, app = unit_world(app_mod, monkeypatch)
    w.at(1.0, app._announce_camera_back)
    w.run(10)

    assert w.sim.log == [] and w.sched.raised() == []
    assert app.glib.timeouts == [], "a confirmation was scheduled for a camera that was never missing"


# ---- The loss paths that do not go through _on_camera_lost -----------------------------------
def test_t03c1_con07_a_camera_gone_when_a_mode_switch_rebuilds_is_announced(app_mod, monkeypatch):
    """The node vanished before the bus reported it: _rebuild_pipeline goes straight to waiting."""
    w, app = unit_world(app_mod, monkeypatch)
    app._camera_state = "ok"
    seen = {}

    def switch():
        app.node_missing = True
        app._rebuild_pipeline()
    w.at(4.0, switch)
    returns(w, app, 25.0)
    w.at(24.0, lambda: seen.setdefault("timers", list(app.glib.timeouts)))
    w.run(60)

    assert texts(w) == [MISSING] * 3 + [BACK], texts(w)
    assert 4.0 <= w.starts(MISSING)[0] <= 5.0
    assert seen["timers"] == [], "the bus loss handler did not run on this path"


def test_t03c1_con07_a_camera_that_vanishes_during_a_mode_switch_rebuild_is_announced(app_mod, monkeypatch):
    """The rebuild's own start finds no camera (START_NO_CAMERA): back to waiting, with the alert."""
    w, app = unit_world(app_mod, monkeypatch)
    app._camera_state = "ok"
    app.outcomes.append((NO_CAMERA, None))
    w.at(4.0, app._rebuild_pipeline)
    returns(w, app, 25.0)
    w.run(60)

    assert texts(w) == [MISSING] * 3 + [BACK], texts(w)
    assert 4.0 <= w.starts(MISSING)[0] <= 5.0
    assert app._camera_state == "ok"


# ---- Criterion 8 and the safety rules --------------------------------------------------------
def test_t03c1_rule4_the_hooks_do_nothing_when_user_data_has_no_scheduler(app_mod, monkeypatch):
    """The standalone user data of app.py has no `alerts`: the camera handling goes on, silently."""
    app = unit_app(app_mod, monkeypatch, None, with_alerts=False)
    app._start_without_camera()
    assert app._camera_state == "waiting"
    app._camera_state = "ok"
    app._on_camera_lost(Msg())
    assert app._camera_state == "lost" and len(app.glib.timeouts) == 1
    app._wait_for_camera()
    assert app._camera_state == "waiting"
    assert app._start_on_camera(CAMERA_NODE) is True
    assert app._camera_state == "ok"


class BrokenScheduler:
    """A scheduler whose every call fails: the camera handling must not notice."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError(f"scheduler.{name} failed")
        return fail


def test_t03c1_rule4_a_failing_scheduler_never_breaks_the_camera_handling(app_mod, monkeypatch, caplog):
    app = unit_app(app_mod, monkeypatch, BrokenScheduler())
    with caplog.at_level(logging.WARNING):
        app._start_without_camera()
        assert app._camera_state == "waiting" and app.calls[-1] == "motors"
        app._camera_state = "ok"
        app._on_camera_lost(Msg())
        assert app._camera_state == "lost" and len(app.glib.timeouts) == 1
        app._wait_for_camera()
        assert app._camera_state == "waiting" and app.calls[-1] == "watch"
        assert app._start_on_camera(CAMERA_NODE) is True
        assert app._camera_state == "ok" and app._camera_missing_since is None
    assert "Could not raise the missing-camera alert" in caplog.text
    assert "Could not announce that the camera is back" in caplog.text


class FlakyScheduler:
    """is_raised and set_repeat work; clear and raise_alert fail: the failure comes late, in the confirmation."""

    def is_raised(self, name):
        return True

    def set_repeat(self, name, repeat):
        return True

    def clear(self, name):
        raise RuntimeError("scheduler.clear failed")

    def raise_alert(self, *args, **kwargs):
        raise RuntimeError("scheduler.raise_alert failed")


def test_t03c1_rule4_a_scheduler_failing_in_the_confirmation_is_logged_and_nothing_else(
        app_mod, monkeypatch, caplog):
    app = unit_app(app_mod, monkeypatch, FlakyScheduler())
    app._camera_state = "waiting"
    with caplog.at_level(logging.WARNING):
        assert app._start_on_camera(CAMERA_NODE) is True
        (_, confirm), = app.glib.timeouts
        assert confirm() is False
    assert app._camera_state == "ok"
    assert "Could not announce that the camera is back" in caplog.text


def _slowest_hook_call(app_mod, monkeypatch) -> float:
    w, app = unit_world(app_mod, monkeypatch)
    slowest = 0.0
    for i in range(2000):
        hook = app._raise_camera_missing if i % 2 == 0 else app._announce_camera_back
        began = time.perf_counter()
        hook()
        slowest = max(slowest, time.perf_counter() - began)
        if i % 2 == 1:
            (_, confirm) = app.glib.timeouts[-1] if app.glib.timeouts else (None, None)
            if confirm is not None:
                began = time.perf_counter()
                confirm()
                slowest = max(slowest, time.perf_counter() - began)
    app._raise_camera_missing()
    began = time.perf_counter()
    app._announce_camera_back()
    return max(slowest, time.perf_counter() - began)


def test_t03c1_rule4_one_hook_call_takes_well_under_50_ms(app_mod, monkeypatch):
    """A hook that blocks (a sleep, a lock wait, I/O) is slow on every call of that path, and nothing else runs
    in this world to make it wait. So up to three attempts: a real block fails all three, while one preemption
    of the test by the system (50.8 ms once, 2026-10-19, under the full suite; 0.3 to 0.6 ms alone) does not
    repeat."""
    attempts = []
    for _ in range(3):
        attempts.append(_slowest_hook_call(app_mod, monkeypatch))
        if attempts[-1] < 0.05:
            break
    print(f"slowest of about 3000 hook calls (raise, announce, confirm), per attempt: "
          f"{', '.join(f'{s * 1000:.3f} ms' for s in attempts)}")
    assert attempts[-1] < 0.05, attempts


def test_t03c1_rule4_the_hooks_make_no_gstreamer_call_only_one_main_loop_timer_and_never_wait():
    import ast
    import inspect
    import textwrap

    from second_vision.pipeline import app as module

    glib_calls = []
    for name in ("_raise_camera_missing", "_announce_camera_back", "_confirm_camera_back"):
        tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(module.SecondVisionApp, name))))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert node.value.id != "Gst", f"{name} calls Gst.{node.attr}"
                if node.value.id == "GLib":
                    glib_calls.append((name, node.attr))
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("sleep", "wait", "join", "get", "put"), f"{name} calls .{node.attr}"
    # GLib.timeout_add only schedules a callback on the main loop; no state is changed through it.
    assert glib_calls == [("_announce_camera_back", "timeout_add")], glib_calls


def test_t03c1_the_f3_docstring_line_is_in_the_alert_scheduler():
    from second_vision.core import alerts

    assert "`repeat` callable runs on the scheduler's thread and must return at once" in alerts.__doc__


# ============================================================
# Level gst: the real app, bus, watcher and staged start
# ============================================================
class Speaker:
    """
    Stands in for the speech worker: takes through the scheduler as tts_worker does, 'speaks' for a
    moment, reports back SPOKEN. Runs the real alert_worker too. Both threads end with shutdown_event.
    `log` is [(monotonic start, sentence)].
    """

    def __init__(self, app, seconds=0.12):
        data = app.user_data
        data.alerts = self.alerts = AlertScheduler(data.tts_queue)
        self.log = []
        self.seconds = seconds
        self.stop = data.shutdown_event
        for target, args, name in ((alert_worker, (data, None), "alert-scheduler-test"),
                                   (self._run, (), "speaker-test")):
            threading.Thread(target=target, args=args, daemon=True, name=name).start()

    def _run(self):
        while not self.stop.is_set():
            item = self.alerts.take()
            if item is None:
                self.stop.wait(0.02)
                continue
            self.log.append((time.monotonic(), item["announce"]))
            self.stop.wait(self.seconds)
            self.alerts.finish(item, SPOKEN)

    def texts(self):
        return [text for _, text in list(self.log)]

    def count(self, text):
        return self.texts().count(text)

    def starts(self, text):
        return [t for t, x in list(self.log) if x == text]


def absent_app(app_mod, made_apps, *, mode="both", descs=(CAMERA_STANDIN,)):
    """
    A SecondVisionApp as the constructor leaves it before create_pipeline(), the camera node absent,
    with a Speaker attached; returns (app, speaker). create_pipeline() is the caller's to run (the
    scheduler must exist when it does).
    """
    assert not os.path.exists(MISSING_NODE), f"{MISSING_NODE} exists on this machine"
    app = build_app(app_mod, made_apps, mode=mode, source=MISSING_NODE, descs=descs, attach=False)
    app.show_fps = False
    app.pipeline = None
    app.loop = None
    app.built.clear()
    app._built_mode = None
    return app, Speaker(app)


def fast_cadence(monkeypatch, app_mod, seconds=0.5, confirm=0.3):
    # raising=False: the same tests run against the baseline (red before green), which lacks them.
    monkeypatch.setattr(app_mod, "CAMERA_MISSING_SECONDS", seconds, raising=False)
    monkeypatch.setattr(app_mod, "CAMERA_BACK_CONFIRM_SECONDS", confirm, raising=False)


@pytest.mark.gst
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_t03c1_con07_gst_absent_at_start_up_repeats_then_the_camera_appears_once(
        app_mod, made_apps, monkeypatch, mode):
    """TC-024-002 then TC-024-004's return, on the real create_pipeline() and watcher."""
    fast_cadence(monkeypatch, app_mod)
    present = threading.Event()
    lookup = FakeCameraLookup(lambda: [CAMERA_NODE] if present.is_set() else [])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app, speaker = absent_app(app_mod, made_apps, mode=mode)
    began = time.monotonic()
    app.create_pipeline()
    app._start_camera_watch()
    assert state(app) == "waiting"

    assert gst_helpers.run_main_loop(lambda: speaker.count(MISSING) >= 4, seconds=8), speaker.texts()
    starts = speaker.starts(MISSING)
    assert starts[0] - began <= 0.5, f"first utterance {starts[0] - began:.2f} s after the constructor"
    assert all(0.35 <= b - a <= 0.7 for a, b in zip(starts, starts[1:])), starts
    assert speaker.texts() == [MISSING] * len(starts)

    present.set()
    assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=8), "the camera was not started"
    returned = time.monotonic()
    assert gst_helpers.run_main_loop(lambda: speaker.count(BACK) >= 1, seconds=3), speaker.texts()
    gst_helpers.run_main_loop(seconds=1.5)                 # three more cadences: nothing may be said
    assert speaker.count(BACK) == 1
    assert [t for t in speaker.starts(MISSING) if t > returned + 0.15] == [], "the alert went on"
    assert speaker.alerts.raised() == [BACK_NAME]
    assert app.built == [(mode, CAMERA_NODE)]


@pytest.mark.gst
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_t03c1_con07_gst_lost_while_running_in_each_mode_alerts_before_the_teardown_and_reconnects(
        app_mod, made_apps, monkeypatch, mode):
    from gi.repository import Gst

    fast_cadence(monkeypatch, app_mod)
    present = threading.Event()
    lookup = FakeCameraLookup(lambda: [CAMERA_NODE] if present.is_set() else [])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, mode=mode)
    speaker = Speaker(app)
    record_shutdowns(app, monkeypatch)
    app.pipeline.set_state(Gst.State.PLAYING)
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, 3), seconds=5), "stand-in never ran"
    assert speaker.log == [], "something was said before the camera was lost"

    teardown_saw_alert = []
    real_release = app._release_old_pipeline_with_alarm

    def teardown():
        teardown_saw_alert.append(speaker.alerts.is_raised(MISSING_NAME))
        real_release()
    app._release_old_pipeline_with_alarm = teardown
    lost = time.monotonic()
    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: speaker.count(MISSING) >= 3, seconds=8), speaker.texts()
    assert speaker.starts(MISSING)[0] - lost <= 0.5
    assert teardown_saw_alert == [True], "the alert was not up when the teardown began"
    assert state(app) == "waiting"

    present.set()
    assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=8), "the camera was not restarted"
    returned = time.monotonic()
    assert gst_helpers.run_main_loop(lambda: speaker.count(BACK) >= 1, seconds=3), speaker.texts()
    gst_helpers.run_main_loop(seconds=1.5)
    assert speaker.count(BACK) == 1
    assert [t for t in speaker.starts(MISSING) if t > returned + 0.15] == []
    assert speaker.texts() == [MISSING] * speaker.count(MISSING) + [BACK]


@pytest.mark.gst
def test_t03c1_con09_gst_ten_loss_and_return_cycles_and_a_start_up_without_a_camera(
        app_mod, made_apps, monkeypatch):
    """Criterion 5 on the real path: start-up without a camera, then ten cycles; no sentence missing, doubled or misplaced."""
    fast_cadence(monkeypatch, app_mod, 5.0)
    gate = threading.Event()
    gate.set()
    lookup = FakeCameraLookup(lambda: [CAMERA_NODE] if gate.is_set() else [])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app, speaker = absent_app(app_mod, made_apps)
    gate.clear()
    app.create_pipeline()
    app._start_camera_watch()
    record_shutdowns(app, monkeypatch)

    def one_return(expected_back):
        gate.set()
        assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=8), "the camera was not started"
        assert gst_helpers.run_main_loop(lambda: speaker.count(BACK) >= expected_back, seconds=3), speaker.texts()

    assert gst_helpers.run_main_loop(lambda: speaker.count(MISSING) >= 1, seconds=3)
    one_return(1)
    for cycle in range(10):
        before = app.user_data.get_count()
        assert gst_helpers.run_main_loop(lambda: frames_flowing(app, before + 3), seconds=5), \
            f"cycle {cycle}: no frames"
        gate.clear()
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: speaker.count(MISSING) >= cycle + 2, seconds=5), \
            f"cycle {cycle}: nothing said for the loss: {speaker.texts()}"
        one_return(cycle + 2)

    gst_helpers.run_main_loop(seconds=0.5)
    assert speaker.texts() == [MISSING, BACK] * 11, speaker.texts()


@pytest.mark.gst
def test_t03c1_con07_gst_a_camera_that_cannot_start_keeps_the_alert_and_never_says_reconnected(
        app_mod, made_apps, monkeypatch):
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed, so a real failed open cannot be made")
    fast_cadence(monkeypatch, app_mod)
    lookup = FakeCameraLookup(lambda: [CAMERA_NODE])           # the node is "there" every time
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app, speaker = absent_app(app_mod, made_apps)
    pipeline_string = {"d": UNOPENABLE_CAMERA}

    def get_pipeline_string():
        app._built_mode = app.current_mode()
        app.built.append((app._built_mode, app.video_source))
        return pipeline_string["d"]
    app.get_pipeline_string = get_pipeline_string
    app.create_pipeline()
    app._start_camera_watch()

    assert gst_helpers.run_main_loop(lambda: len(app.built) >= 4 and speaker.count(MISSING) >= 3,
                                     seconds=10), (app.built, speaker.texts())
    assert state(app) in ("waiting", "starting")
    assert BACK not in speaker.texts() and speaker.count(MISSING) >= 3
    assert speaker.alerts.is_raised(MISSING_NAME) and not speaker.alerts.is_raised(BACK_NAME)

    pipeline_string["d"] = CAMERA_STANDIN                      # the camera can be opened now
    assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=10), "never started"
    assert gst_helpers.run_main_loop(lambda: speaker.count(BACK) >= 1, seconds=3)
    assert speaker.texts().count(BACK) == 1 and speaker.texts()[-1] == BACK


# A camera that reaches PLAYING and ends the stream a frame later: what the device shows when another
# process holds the camera ("Device '/dev/video0' is busy" is posted by v4l2src's streaming thread, after
# set_state(PLAYING) has returned without failing). An end-of-stream from a USB source is a camera failure.
CAMERA_FAILS_AT_ONCE = CAMERA_STANDIN.replace("is-live=true", "is-live=true num-buffers=1", 1)


@pytest.mark.gst
def test_t03c1_con07_gst_a_camera_that_starts_and_fails_at_once_keeps_the_cadence_and_is_never_called_back(
        app_mod, made_apps, monkeypatch):
    """
    Criterion 4 on the real path, the held-camera case: every start reports the pipeline running and the
    camera fails a frame later. The missing sentence goes on at its own cadence (not once per attempt) and
    "reconnected" is never said; when a camera that works appears, it is said once.
    """
    fast_cadence(monkeypatch, app_mod, seconds=1.0, confirm=0.5)
    lookup = FakeCameraLookup(lambda: [CAMERA_NODE])           # the node is "there" every time
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app, speaker = absent_app(app_mod, made_apps)
    record_shutdowns(app, monkeypatch)
    pipeline_string = {"d": CAMERA_FAILS_AT_ONCE}

    def get_pipeline_string():
        app._built_mode = app.current_mode()
        app.built.append((app._built_mode, app.video_source))
        return pipeline_string["d"]
    app.get_pipeline_string = get_pipeline_string
    app.create_pipeline()
    app._start_camera_watch()

    assert gst_helpers.run_main_loop(lambda: len(app.built) >= 8 and speaker.count(MISSING) >= 5,
                                     seconds=15), (app.built, speaker.texts())
    starts = speaker.starts(MISSING)
    attempts = len(app.built)
    assert speaker.texts() == [MISSING] * len(starts), speaker.texts()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(0.9 <= g <= 1.4 for g in gaps), f"{attempts} attempts, gaps {gaps}"
    assert speaker.alerts.is_raised(MISSING_NAME) and not speaker.alerts.is_raised(BACK_NAME)

    pipeline_string["d"] = CAMERA_STANDIN                      # a camera that works
    assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=10), "never started"
    assert gst_helpers.run_main_loop(lambda: speaker.count(BACK) >= 1, seconds=3), speaker.texts()
    gst_helpers.run_main_loop(seconds=2.0)
    assert speaker.texts().count(BACK) == 1 and speaker.texts()[-1] == BACK
    assert speaker.alerts.raised() == [BACK_NAME]


@pytest.mark.gst
@pytest.mark.parametrize("before,after", [("both", "depth"), ("depth", "detection"),
                                          ("detection", "none"), ("none", "both")])
def test_t03c1_con07_gst_a_camera_never_lost_says_nothing_across_a_mode_switch(
        app_mod, made_apps, monkeypatch, tmp_path, before, after):
    from gi.repository import Gst

    fast_cadence(monkeypatch, app_mod)
    node = tmp_path / "video3"                   # the camera's node, there for the whole test
    node.touch()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", FakeCameraLookup(lambda: [str(node)]), raising=False)
    app = build_app(app_mod, made_apps, mode=before, source=str(node))
    speaker = Speaker(app)
    record_shutdowns(app, monkeypatch)
    app.pipeline.set_state(Gst.State.PLAYING)
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, 3), seconds=5)

    app.config.update(pipeline_mode=after)
    app.trigger_rebuild()
    assert gst_helpers.run_main_loop(lambda: app.built == [(after, str(node))] and state(app) == "ok"
                                     and app._rebuild_in_flight is False, seconds=8), app.built
    gst_helpers.run_main_loop(seconds=1.5)

    assert speaker.log == [] and speaker.alerts.raised() == []


@pytest.mark.gst
def test_t03c1_con07_gst_a_mode_switch_onto_a_camera_that_cannot_open_is_announced(
        app_mod, made_apps, monkeypatch, tmp_path):
    """The rebuild's start fails on the camera (the node is there but cannot be opened): waiting, with the alert."""
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed, so a real failed open cannot be made")
    fast_cadence(monkeypatch, app_mod)
    node = tmp_path / "video3"
    node.touch()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", FakeCameraLookup(lambda: []), raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, UNOPENABLE_CAMERA_WITH_HAILO))
    app.video_source = str(node)
    HailoStandIn(monkeypatch, app)
    speaker = Speaker(app)
    record_shutdowns(app, monkeypatch)

    app.config.update(pipeline_mode="depth")
    app.trigger_rebuild()
    assert gst_helpers.run_main_loop(lambda: state(app) == "waiting", seconds=5)
    assert gst_helpers.run_main_loop(lambda: speaker.count(MISSING) >= 2, seconds=5), speaker.texts()
    assert speaker.texts() == [MISSING] * speaker.count(MISSING)


@pytest.mark.gst
def test_t03c1_con07_gst_a_node_that_vanished_before_the_mode_switch_rebuild_is_announced(
        app_mod, made_apps, monkeypatch):
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed")
    fast_cadence(monkeypatch, app_mod)
    monkeypatch.setattr(app_mod, "get_usb_video_devices", FakeCameraLookup(lambda: []), raising=False)
    app = build_app(app_mod, made_apps, source=MISSING_NODE, descs=(UNOPENABLE_CAMERA,))
    speaker = Speaker(app)
    record_shutdowns(app, monkeypatch)

    assert app._rebuild_pipeline() is False
    assert gst_helpers.run_main_loop(lambda: speaker.count(MISSING) >= 2, seconds=5), speaker.texts()
    assert state(app) == "waiting"


@pytest.mark.gst
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_t03c1_gst_a_stop_while_the_alert_is_raised_exits_0_at_once(app_mod, made_apps, monkeypatch, signum):
    """Criterion 7, in the app's real run(): the stop does not wait for the alert."""
    monkeypatch.setattr(app_mod, "get_usb_video_devices", FakeCameraLookup(lambda: []), raising=False)
    app, speaker = absent_app(app_mod, made_apps)
    app.create_pipeline()
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)
    sent = {}
    began = time.monotonic()

    def drive():
        if speaker.count(MISSING) >= 1 or time.monotonic() - began > 5.0:   # never wait unbounded
            sent["t"] = time.monotonic()
            os.kill(os.getpid(), signum)
            return False
        return True

    exited = run_until_stopped(app, drive, signum=signum)
    took = time.monotonic() - sent["t"]
    assert speaker.count(MISSING) >= 1, "the alert was never said, so the stop was not tested with it raised"
    assert exited.code == 0
    assert took < 2.0, f"the stop took {took:.2f} s"
    assert speaker.alerts.is_raised(MISSING_NAME)
