"""
T-03b: the program is started with NO camera attached (REQ-CON-08, its "absent at start-up" half).

Requirement (docs/testing/REQUIREMENTS.md): when the device runs as deployed and the camera is
absent at start-up, the program does not crash; every other part keeps running; the device keeps
checking the USB ports for a camera; when a camera appears, the vision pipeline starts on it; and it
must never fall back to the testing mock mode. These tests cover the app side of that. The launcher
and main() are in tests/test_launcher.py and tests/test_main_startup.py. The alert half of the UAT
case (TC-024-002: beep and "Critical Error: Camera cannot be reached" every 10 s, REQ-CON-07) is
task T-03c and is NOT built: a PASS line from this file says nothing about it.

Level `gst`: real GStreamer and the real hailo_apps GStreamerApp.run(), no camera, no Hailo chip, no
boards. They run in my_hailo_env and skip, with a reason, in the Poetry environment:

    my_hailo_env/bin/python -m pytest tests/test_startup_without_camera.py -v

What is real and what is a stand-in: exactly as in tests/test_camera_loss.py (which this file takes
its helpers from): SecondVisionApp is built with __new__ and then goes through its REAL
create_pipeline() and run(); the camera and the Hailo element are stand-ins (videotestsrc named
`source`, an `identity` named stand_in_hailonet); the camera lookup is a scripted answer; the
node /dev/video99 does not exist (checked). What these tests can never show is that the real hailonet
survives: that needs the Hailo chip, which the device runs do.
"""

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

import gst_helpers  # noqa: E402
from test_camera_loss import (  # noqa: E402,F401  (the fixtures are used by name)
    CAMERA_STANDIN, CAMERA_WITH_HAILO, MISSING_NODE, MOTORS_OFF, UNOPENABLE_CAMERA,
    FakeCameraLookup, FakePanelPort, HailoStandIn, app_mod, build_app, drain, frames_flowing,
    made_apps, mock_threads, record_shutdowns, state,
)

pytestmark = pytest.mark.gst

CAMERA_NODE = "/dev/video0"      # what the lookup reports when "the camera is plugged in"


def start_without_camera(app_mod, made_apps, *, mode="both", descs=(CAMERA_STANDIN,)):
    """
    A SecondVisionApp as the constructor leaves it just before create_pipeline(), the camera node
    absent, and then the app's own create_pipeline() run for real. build_app() gives the instance
    the constructor state (and a pipeline, as if create_pipeline() had run: that is taken away
    again, so that what the app does by itself is what the test sees).
    """
    assert not os.path.exists(MISSING_NODE), f"{MISSING_NODE} exists on this machine"
    app = build_app(app_mod, made_apps, mode=mode, source=MISSING_NODE, descs=descs, attach=False)
    app.show_fps = False                 # GStreamerApp.__init__ state the library's create_pipeline() reads
    app.pipeline = None
    app.loop = None
    app.built.clear()
    app._built_mode = None
    app.create_pipeline()
    return app


def run_until_stopped(app, drive, *, signum=signal.SIGINT):
    """
    The library's real run() on the app, with `drive` called every 50 ms on the main loop. `drive`
    returns False when it has sent the stop. Returns the SystemExit run() ended with.
    """
    from gi.repository import GLib

    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    # What GStreamerApp.__init__ does for SIGINT and main.py does for SIGTERM.
    signal.signal(signum, app.shutdown)
    source = GLib.timeout_add(50, drive)
    try:
        with pytest.raises(SystemExit) as exited:
            app.run()
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
        if GLib.MainContext.default().find_source_by_id(source) is not None:
            GLib.source_remove(source)
    return exited.value


# ============================================================
# What the constructor does when the camera is absent
# ============================================================
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_t03b_con08_absent_camera_builds_no_pipeline_and_waits(app_mod, made_apps, mode):
    """
    No camera node at launch: no pipeline string is built (so no Hailo element exists to be taken
    down), an empty pipeline is parked, the state is WAITING, and one zero motor dict is queued.
    """
    from gi.repository import GLib

    app = start_without_camera(app_mod, made_apps, mode=mode)

    assert app.built == [], "a pipeline was built for a camera that is not there"
    assert state(app) == "waiting"
    assert app.pipeline is not None and app.pipeline.get_by_name("source") is None
    assert not app.pipeline.iterate_elements().next()[1], "the parked pipeline holds an element"
    assert isinstance(app.loop, GLib.MainLoop)
    assert app._camera_missing_since is not None
    assert app.__dict__.get("_camera_watch_thread") is None, "the watcher starts from run(), not here"
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]


def test_t03b_con08_present_camera_keeps_the_library_path(app_mod, made_apps, monkeypatch):
    """Criterion 7: the node is there, so create_pipeline() builds the pipeline exactly as before."""
    from gi.repository import GLib

    app = start_without_camera(app_mod, made_apps)       # builds nothing, as the node is absent ...
    app.pipeline = app.loop = None
    app.__dict__.pop("_camera_state", None)              # ... undo that, then let the node "exist"
    app.built.clear()
    monkeypatch.setattr(app, "_camera_node_missing", lambda: False)
    drain(app.user_data.serial_queue)
    app.create_pipeline()

    assert app.built == [("both", MISSING_NODE)]
    assert state(app) == "ok"
    assert app.pipeline.get_by_name("source") is not None
    assert isinstance(app.loop, GLib.MainLoop)
    assert drain(app.user_data.serial_queue) == []
    assert app.__dict__.get("_camera_watch_thread") is None


# ============================================================
# Start-up, camera appears, in every mode (through the library's real run())
# ============================================================
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_t03b_con08_camera_appearing_later_starts_vision_in_the_mode_current_then(
        app_mod, made_apps, monkeypatch, caplog, capfd, mode):
    """
    Criteria 1, 2 and 3. The program starts with no camera; the library's run() keeps it up; the
    watcher starts from the loop and looks; a panel switch to `mode` while waiting is recorded and
    builds nothing; then a camera appears on another node and the pipeline is built, for `mode`,
    on that node. Ctrl+C then exits 0.
    """
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = start_without_camera(app_mod, made_apps)
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)   # the real one ends the test process
    steps = {"phase": "wait-for-first-look", "recorded": None}

    def drive():
        if steps["phase"] == "wait-for-first-look":
            if lookup.calls >= 1:
                steps["waiting_at_first_look"] = (state(app), list(app.built))
                app.config.update(pipeline_mode=mode)          # what config_reader_worker does ...
                app.trigger_rebuild()                          # ... on "M:<mode>"
                steps["recorded"] = (list(app.built), app._rebuild_in_flight)
                lookup.answer = lambda: [CAMERA_NODE]          # the camera is plugged in
                steps["phase"] = "wait-for-vision"
            return True
        if steps["phase"] == "wait-for-vision":
            if state(app) == "ok" and frames_flowing(app, 3):
                steps["phase"] = "stopped"
                os.kill(os.getpid(), signal.SIGINT)
                return False
            return True
        return False

    with caplog.at_level(logging.INFO):
        exited = run_until_stopped(app, drive)

    assert steps["phase"] == "stopped", f"never reached a running pipeline: {steps}"
    assert steps["waiting_at_first_look"] == ("waiting", []), "the watcher did not look while waiting"
    assert steps["recorded"] == ([], False), "a panel switch while waiting built or scheduled a rebuild"
    assert app.built == [(mode, CAMERA_NODE)], "vision did not start once, in the current mode, on the node"
    assert app.video_source == CAMERA_NODE
    assert exited.code == 0
    assert app.user_data.shutdown_event.is_set()
    assert "identity not found" not in caplog.text, "run() on the parked pipeline warned about callbacks"
    assert "Camera back: vision pipeline running" in caplog.text
    assert "Exiting successfully" in caplog.text and "Error during cleanup" not in caplog.text
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    out = capfd.readouterr()
    assert mock_threads() == [] and "MOCK" not in out.out
    # GStreamer reports a CRITICAL when an element is dropped while still PLAYING (the parked
    # pipeline, which run() starts, is replaced by the camera's) and when a bus loses a watch twice.
    assert "CRITICAL" not in out.err, "a GStreamer CRITICAL on stderr"
    watcher = app.__dict__.get("_camera_watch_thread")
    assert watcher is not None
    watcher.join(timeout=3)
    assert not watcher.is_alive(), "the camera watcher outlived the stop"


# ============================================================
# A stop at start-up
# ============================================================
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_t03b_con08_clean_stop_while_waiting_from_start_exits_0(app_mod, made_apps, monkeypatch,
                                                                caplog, capsys, signum):
    """
    Criterion 6, REQ-DTA-08 regression. Ctrl+C (SIGINT, also what the boot service sends) or SIGTERM
    while the program waits for the camera it started without: exit 0 through the library's cleanup,
    workers told to stop, the watcher gone, no mock mode.
    """
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = start_without_camera(app_mod, made_apps)
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)
    sent = []

    def drive():
        if lookup.calls >= 2:
            sent.append(time.monotonic())
            os.kill(os.getpid(), signum)
            return False
        return True

    with caplog.at_level(logging.INFO):
        began = time.monotonic()
        exited = run_until_stopped(app, drive, signum=signum)
        took = time.monotonic() - sent[0]

    assert sent, "the stop was never sent"
    assert exited.code == 0
    assert took < 2.0, f"the stop took {took:.1f} s"
    assert app.user_data.shutdown_event.is_set()
    watcher = app.__dict__.get("_camera_watch_thread")
    assert watcher is not None
    watcher.join(timeout=3)
    assert not watcher.is_alive(), "the camera watcher outlived the stop"
    assert app.built == []
    assert "Exiting successfully" in caplog.text and "Error during cleanup" not in caplog.text
    assert mock_threads() == [] and "MOCK" not in capsys.readouterr().out


def test_t03b_con08_stop_during_construction_is_not_lost(app_mod, made_apps, monkeypatch, caplog):
    """
    Criterion 6. SIGINT lands after GStreamerApp.__init__ installed the handler and before the
    constructor made a pipeline and a loop: shutdown() finds neither and only records itself. The
    program must still end at once with exit 0, not run until the shutdown timer forces it out
    (that line, "forcing exit", is a failure in the device runs).
    """
    from gi.repository import GLib

    app = build_app(app_mod, made_apps, source=MISSING_NODE, attach=False)
    app.show_fps = False
    app.pipeline = None
    app.loop = None
    app.built.clear()
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)
    timed_out = []

    with caplog.at_level(logging.INFO):
        app.shutdown()                                  # the signal, too early for the loop
        assert app._shutting_down and app.pipeline is None and app.loop is None
        app.create_pipeline()                           # the constructor goes on
        source = GLib.timeout_add(3000, lambda: (timed_out.append(1), app.loop.quit(), False)[-1])
        try:
            with pytest.raises(SystemExit) as exited:
                app.run()
        finally:
            if not timed_out:
                GLib.source_remove(source)

    assert timed_out == [], "run() kept running after a stop that had already been asked for"
    assert exited.value.code == 0
    assert app.user_data.shutdown_event.is_set()
    assert app.built == []


# ============================================================
# While waiting: the panel, and a camera that does not open
# ============================================================
def test_t03b_con08_panel_mode_switch_while_waiting_from_start_is_announced(app_mod, made_apps, monkeypatch):
    """
    Criterion 2. The REAL panel worker, on a fake port, while the program waits for the camera it
    started without: the switch is spoken ("depth mode") and recorded, the worker stays alive,
    nothing is built, nothing is shut down.
    """
    from second_vision.workers import config_reader

    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = start_without_camera(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)
    app._begin_waiting()                                # what run()'s first idle does
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 1, seconds=5)

    monkeypatch.setattr(config_reader, "_open_config_port", lambda path: FakePanelPort([b"M:depth\n"]))
    worker = threading.Thread(target=config_reader.config_reader_worker,
                              args=(app.user_data, app.config, "/dev/fake-panel", app),
                              daemon=True, name="config-reader")
    worker.start()
    assert gst_helpers.run_main_loop(lambda: app.config.get("pipeline_mode") == "depth", seconds=5)
    gst_helpers.run_main_loop(seconds=0.5)

    assert app.user_data.tts_queue.peek() == {"announce": "depth mode"}
    assert worker.is_alive()
    assert app.built == [] and shutdowns == [] and state(app) == "waiting"
    app.user_data.shutdown_event.set()
    worker.join(timeout=3)
    assert not worker.is_alive()


def test_t03b_con08_camera_that_appears_but_cannot_open_goes_back_to_waiting_then_starts(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Criterion 4, reached from a start without a camera. The first camera found is a node that cannot
    be opened (v4l2src on a node that is not there): the program does not exit and does not shut
    down, goes back to looking, and starts on the next camera found.
    """
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed, so a real failed start cannot be made")
    app = None
    lookup = FakeCameraLookup(lambda: [MISSING_NODE] if not app.built else ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = start_without_camera(app_mod, made_apps,
                               descs=(CAMERA_STANDIN, UNOPENABLE_CAMERA, CAMERA_STANDIN))
    shutdowns = record_shutdowns(app, monkeypatch)
    app._begin_waiting()

    with caplog.at_level(logging.INFO):
        assert gst_helpers.run_main_loop(lambda: len(app.built) == 2 and state(app) == "ok",
                                         seconds=8), "never recovered"

    assert app.built == [("both", MISSING_NODE), ("both", "/dev/video5")]
    assert shutdowns == [] and app.error_occurred is False
    assert f"did not start on {MISSING_NODE}" in caplog.text


def test_t03b_con08_stop_right_after_the_camera_appears_never_takes_down_an_unfed_hailo_element(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Criterion 6, "right after the camera appears". The pipeline started by the watcher after a
    start without a camera is the staged one: a stop the moment it is running ends the program with
    exit 0, and the Hailo stand-in was fed its first buffer before it could be taken down.
    """
    lookup = FakeCameraLookup(lambda: [])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = start_without_camera(app_mod, made_apps, descs=(CAMERA_STANDIN, CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)
    steps = {"asked": False, "stopped": False}

    def drive():
        if not steps["asked"]:
            if lookup.calls >= 1:
                lookup.answer = lambda: [CAMERA_NODE]
                steps["asked"] = True
            return True
        if state(app) == "ok":                     # running: stop at once, before the first frame matters
            steps["stopped"] = True
            os.kill(os.getpid(), signal.SIGINT)
            return False
        return True

    with caplog.at_level(logging.INFO):
        exited = run_until_stopped(app, drive)

    assert steps["stopped"]
    assert exited.code == 0
    assert hailo.taken_down_before_a_buffer() == [], hailo.logs
    assert len(hailo.logs) == 1 and ("buffer", 0) in hailo.logs[0], "the stand-in was not primed"


# ============================================================
# The "still looking" line counts from the start of the program
# ============================================================
def test_t03b_con08_still_looking_line_counts_from_the_start(app_mod, made_apps, monkeypatch, caplog):
    """Criterion 1: about one INFO line a minute ("Still no USB camera after N s"), here shortened."""
    monkeypatch.setattr(app_mod, "CAMERA_STILL_LOOKING_LOG_SECONDS", 0.3, raising=False)
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = start_without_camera(app_mod, made_apps)
    record_shutdowns(app, monkeypatch)
    app._begin_waiting()

    with caplog.at_level(logging.INFO):
        assert gst_helpers.run_main_loop(lambda: lookup.calls >= 12, seconds=5)

    lines = [r for r in caplog.records if "Still no USB camera after" in r.getMessage()]
    assert 2 <= len(lines) <= lookup.calls // 2, f"{len(lines)} lines for {lookup.calls} looks"
    assert all(r.levelno == logging.INFO for r in lines)
