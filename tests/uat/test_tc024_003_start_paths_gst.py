"""
TC-024-003, gst level (mock-integration): the paths of the camera-restart start that the Developer's
own tests (tests/test_camera_loss.py) read but did not run. Task T-03a, attempt 2. Written by the
Tester (tests/uat/).

These cover ONLY the removal half of TC-024-003 (REQ-CON-08, "removed while running"); the alert half
(REQ-CON-07) is task T-03c and is not built, so a PASS line for TC-024-003 from this file says nothing
about alerts.

Workbook TC-024-003, expected result (verbatim, docs/testing/uat-snapshot.yaml, Test Cases!A263:G263):
    "There should be a quick set of vibration pulse alongside a "Critical Error: Camera cannot be
     reached" audio error. This should be repeated every 10 seconds. In terms of the pipeline, the
     system should catch the no devices exception and allow the device to proceed in mock mode
     temporarily. Device should also listen for cameras that may pop up in the list of devices to
     check if everything is okay."
The "mock mode" sentence is overruled by D-13 (REQ-CON-08: "It must never fall back to the testing
mock mode"); DEVIATIONS.md DV-001.

What is real: SecondVisionApp's own methods (_launch_on_camera, _prime_hailonets, _start_on_camera,
shutdown ...), real GStreamer. What is a stand-in: the camera (videotestsrc / appsrc) and the Hailo
element (an `identity` named stand_in_hailonet, see test_camera_loss.HailoStandIn). These tests show
the ORDER the app imposes on a Hailo element and that the program survives; they can never show that
the real plugin does not crash (that needs the Hailo chip: tests/uat/test_tc024_003_camera_removed.py
and the Tester's probes).

Run in my_hailo_env (they skip, with a reason, in the Poetry environment):
    my_hailo_env/bin/python -m pytest tests/uat/test_tc024_003_start_paths_gst.py -v
"""

import logging
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent / "src"))

import gst_helpers  # noqa: E402
from test_camera_loss import (  # noqa: E402,F401  (fixtures are used by name)
    CAMERA_STANDIN, CAMERA_WITH_HAILO, SILENT_CAMERA_WITH_HAILO, STANDIN, FakeCameraLookup,
    HailoStandIn, app_mod, build_app, drain, lose_camera, made_apps, record_shutdowns, state,
)

pytestmark = pytest.mark.gst

MOTORS_OFF = {"left": 0, "center": 0, "right": 0, "hazard": False, "hazard_severity": 0}


def test_tc024_003_con08_tester_exception_while_building_the_restart_goes_back_to_waiting(
        app_mod, made_apps, monkeypatch, caplog, capfd):
    """
    Removal half of TC-024-003 only. Brief-2 asked to walk "an exception in _on_pipeline_rebuilt()".
    The camera is found, the new pipeline is built, its Hailo element primed, and then building
    raises (here once, in _on_pipeline_rebuilt). Expected: the program does not exit and does not
    shut down; the half-started pipeline is taken down only after its Hailo element had its first
    buffer; the program goes back to looking and the next look starts normally. No GStreamer
    CRITICAL. Cannot show: the real plugin (Hailo chip).
    """
    lookup = FakeCameraLookup(lambda: ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)

    real_rebuilt = app._on_pipeline_rebuilt
    raised = []

    def rebuilt_once_raising():
        if not raised:
            raised.append(time.monotonic())
            raise RuntimeError("tester: building the restart failed")
        return real_rebuilt()
    monkeypatch.setattr(app, "_on_pipeline_rebuilt", rebuilt_once_raising)

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: raised and state(app) == "ok", seconds=10), \
            f"did not recover after the failed build (state {state(app)}, raised {bool(raised)})"

    assert shutdowns == [] and app.loop.quit_calls == 0 and app.error_occurred is False
    assert "Could not build the vision pipeline" in caplog.text
    assert "did not start on" in caplog.text
    assert len(hailo.logs) >= 2, "the failed first attempt and the successful second were not both built"
    assert hailo.taken_down_before_a_buffer() == [], hailo.taken_down_before_a_buffer()
    # the first (failed) pipeline's Hailo element was fed, then taken down
    assert ("buffer", 0) in hailo.logs[0] and ("state", "ready", "null") in hailo.logs[0], hailo.logs[0]
    assert drain(app.user_data.serial_queue).count(MOTORS_OFF) >= 1
    err = capfd.readouterr().err
    assert "CRITICAL" not in err and "Got data flow before" not in err, err[-1500:]


def test_tc024_003_con08_tester_stop_between_paused_and_priming_ends_the_restart_safely(
        app_mod, made_apps, monkeypatch, capfd):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression. Brief-2: "a stop during step 1/2/3". A
    stop (Ctrl+C / SIGTERM run app.shutdown() on the main thread between two Python steps) that
    lands after the whole pipeline reached PAUSED and before the Hailo elements are primed. At that
    moment self.pipeline is the empty parked pipeline, so shutdown() takes nothing Hailo down. The
    start then finishes priming (no stop check there, by design), becomes self.pipeline and returns
    without going to PLAYING; run()'s cleanup (NULL on self.pipeline) then takes down a pipeline
    whose Hailo element had its buffer. Expected: never PLAYING, the loop asked to quit, no Hailo
    element taken down before a buffer, no CRITICAL. Cannot show: the real plugin.
    """
    from gi.repository import Gst

    lookup = FakeCameraLookup(lambda: ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, SILENT_CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)
    stopped = []

    real_prime = app._prime_hailonets

    def stop_then_prime(pipeline):
        if not stopped:
            stopped.append(app.pipeline.get_name())   # the empty parked pipeline is app.pipeline here
            app.shutdown()
        return real_prime(pipeline)
    monkeypatch.setattr(app, "_prime_hailonets", stop_then_prime)

    saved = signal.getsignal(signal.SIGINT)
    try:
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: app.loop.quit_calls >= 1, seconds=8), \
            f"the stop never asked the main loop to quit (stopped: {stopped})"
        pipeline_at_exit = app.pipeline
        pipeline_at_exit.set_state(Gst.State.NULL)       # what run()'s cleanup does next
        pipeline_at_exit.get_state(2 * Gst.SECOND)
    finally:
        signal.signal(signal.SIGINT, saved)

    assert stopped == ["no_camera"], f"the stop did not land while the empty pipeline was parked: {stopped}"
    assert pipeline_at_exit.get_by_name(STANDIN) is not None, \
        "run()'s cleanup would not find the started pipeline (it would leave it un-NULLed)"
    assert all(("state", "paused", "playing") not in log for log in hailo.logs), "went on to PLAYING"
    assert hailo.taken_down_before_a_buffer() == [], hailo.taken_down_before_a_buffer()
    assert app.error_occurred is False
    err = capfd.readouterr().err
    assert "CRITICAL" not in err and "Got data flow before" not in err, err[-1500:]


def test_tc024_003_con08_tester_priming_that_raises_is_warned_about_and_the_start_goes_on(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Removal half of TC-024-003 only. Brief-2: "say what happens if priming fails". The Developer
    unit-tested only the "no input format" branch. This one makes the buffer chain call raise (the
    empty buffer cannot be created): the app logs the warning and still starts on the camera, and
    frames flow. Expected: no exception escapes, no shutdown, no quit, the stand-in got no priming
    buffer. (That the Hailo element is then exposed to an early teardown is the documented price;
    only the Hailo chip can show what the real plugin does.)
    """
    from gi.repository import Gst

    lookup = FakeCameraLookup(lambda: ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)

    def no_buffer():
        raise RuntimeError("tester: cannot create a buffer")
    monkeypatch.setattr(Gst.Buffer, "new", staticmethod(no_buffer))

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=8), "never restarted"
    before = app.user_data.get_count()
    assert gst_helpers.run_main_loop(lambda: app.user_data.get_count() > before + 3, seconds=5), \
        "no frames after the restart"

    assert f"hailonet {STANDIN} could not be primed" in caplog.text
    assert ("buffer", 0) not in hailo.logs[0]
    assert shutdowns == [] and app.loop.quit_calls == 0 and app.error_occurred is False


def test_tc024_003_con08_tester_loss_then_stop_while_restart_is_pending_exits_clean(
        app_mod, made_apps, monkeypatch):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression. A stop that arrives while the camera
    lookup thread has handed a found camera to the main loop but before the start ran (the idle
    callback is queued). Expected: the start does nothing (it sees _shutting_down), no pipeline
    is built, the watcher thread ends, shutdown_event is set.
    """
    gate = threading.Event()
    lookup = FakeCameraLookup(lambda: ["/dev/video5"] if gate.is_set() else [])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)

    saved = signal.getsignal(signal.SIGINT)
    try:
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: state(app) == "waiting", seconds=5)
        built_before = len(app.built)
        gate.set()                      # the watcher will find a camera on its next look ...
        time.sleep(0.25)                # ... (POLL is 0.1 s here) so the hand-over is queued or running
        app.shutdown()                  # ... and the stop arrives
        gst_helpers.run_main_loop(lambda: app.loop.quit_calls >= 1, seconds=3)
    finally:
        signal.signal(signal.SIGINT, saved)

    assert app.user_data.shutdown_event.is_set()
    watcher = app.__dict__.get("_camera_watch_thread")
    if watcher is not None:
        watcher.join(timeout=3)
        assert not watcher.is_alive(), "the watcher thread outlived the stop"
    # at most one start may have begun before the stop; none after it, and nothing left half-started
    assert len(app.built) <= built_before + 1
    assert hailo.taken_down_before_a_buffer() == [], hailo.taken_down_before_a_buffer()
