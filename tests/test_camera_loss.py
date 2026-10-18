"""
T-03a: the USB camera is removed while the program runs (REQ-CON-08, "removed while running").

These tests cover ONLY the removal half of UAT case TC-024-003: the program stays up, the other
parts keep running, the motors stop, the camera is looked for, and the vision pipeline restarts on
a camera that comes back. The case's alert half (vibration pulses and "Critical Error: Camera
cannot be reached" every 10 s, REQ-CON-07) is task T-03c and is NOT built, so a PASS line for
TC-024-003 in an evidence log made from this file says nothing about the alerts.

Level `gst`: real GStreamer and the real hailo_apps GStreamerApp, no camera, no Hailo chip, no
boards. They run in my_hailo_env and skip, with a reason, in the Poetry environment:

    my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -v

What is real and what is a stand-in:
  * SecondVisionApp is built with __new__ (as tests/test_pipeline_modes.py does), so argv, the
    HEF files and the Hailo chip are never touched. Every method under test is the real one.
  * The pipeline is a stand-in: `videotestsrc name=source` plays the camera (the element name the
    app looks for) and fakesinks replace the Hailo branches. The camera failure is an ERROR posted
    from that element (tests/gst_helpers.post_error), the shape the 2026-10-15 system log showed:
    "Could not read from resource" from GstV4l2Src:source.
  * get_usb_video_devices (the camera lookup) is replaced by a scripted answer.
  * Where a start must FAIL for real, the pipeline is `v4l2src device=/dev/video99`, a node that
    does not exist: v4l2src cannot open it. No real camera node is ever opened.
  * The serial and panel workers are the real ones, on fake ports.
  * Attempt 2 (the hailonet crashes, F1-F3): an `identity` named "stand_in_hailonet" plays the
    hailonet, and HailoStandIn records what happens to it. These tests show the ORDER the app
    imposes (the camera opens before a Hailo element moves; every Hailo element has its first
    buffer before anything can take it down), never that the real plugin does not crash: that
    crash needs the Hailo chip and is the Tester's device level.
A real unplug, the Hailo chip and the real boards are the Tester's device level, not this file.
"""

import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))

import gst_helpers  # noqa: E402

pytestmark = pytest.mark.gst

# Plays the camera: a live source named "source", and both callback identities so that the
# frame counter runs in every mode (_connect_callback wires whichever the mode uses).
CAMERA_STANDIN = (
    "videotestsrc name=source is-live=true ! video/x-raw,width=64,height=48,framerate=30/1 ! "
    "identity name=det_callback ! identity name=depth_callback ! fakesink name=sink sync=false"
)
MISSING_NODE = "/dev/video99"
UNOPENABLE_CAMERA = f"v4l2src device={MISSING_NODE} name=source ! fakesink name=sink"
MOTORS_OFF = {"left": 0, "center": 0, "right": 0, "hazard": False, "hazard_severity": 0}
POLL = 0.1     # CAMERA_POLL_SECONDS in these tests

# A Hailo element's stand-in, and pipelines that carry one. The capsfilter after it gives its sink
# pad a fixed format, as a hailonet's network input does.
STANDIN = "stand_in_hailonet"
_HAILO_TAIL = (f"identity name={STANDIN} ! video/x-raw,format=RGB,width=64,height=48 ! "
               "identity name=det_callback ! identity name=depth_callback ! fakesink name=sink sync=false")
# A camera that cannot be opened (no such node), with a Hailo stand-in behind it.
UNOPENABLE_CAMERA_WITH_HAILO = f"v4l2src device={MISSING_NODE} name=source ! {_HAILO_TAIL}"
# A camera that opens but has sent no frame yet (a live appsrc nobody feeds): the first ~0.6 s of
# a real start, held open for as long as the test needs.
SILENT_CAMERA_WITH_HAILO = (
    "appsrc name=source is-live=true format=time "
    f"caps=video/x-raw,format=RGB,width=64,height=48,framerate=30/1 ! {_HAILO_TAIL}")
# A camera that streams, with a Hailo stand-in behind it.
CAMERA_WITH_HAILO = (
    "videotestsrc name=source is-live=true ! video/x-raw,format=RGB,width=64,height=48,framerate=30/1 ! "
    f"{_HAILO_TAIL}")


# ============================================================
# Stand-ins
# ============================================================
class FakeUserData:
    """What the app, _internal_callback_wrapper and the workers read from user_data."""

    def __init__(self):
        from second_vision.core.priority import PriorityMailbox

        self.frame_count = 0
        self.use_frame = False
        self.running = True
        self.callback_times = []
        self.callback_max_time = 0.0
        self.serial_queue = queue.Queue(maxsize=10)
        self.tts_queue = PriorityMailbox()
        self.shutdown_event = threading.Event()

    def increment(self):
        self.frame_count += 1

    def get_count(self):
        return self.frame_count


class RecordingLoop:
    """app.loop where no loop is run: records quit(), which is how the library's rebuild ends the program."""

    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


class FakeCameraLookup:
    """Replaces get_usb_video_devices: `answer()` gives the nodes; counts the looks."""

    def __init__(self, answer=lambda: []):
        self.answer = answer
        self.calls = 0
        self.threads = set()

    def __call__(self):
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        return list(self.answer())


class FakeMotorPort:
    """The glasses board's serial port: records every packet written and ACKs each one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._acks = b""
        self.written = []

    def write(self, data):
        with self._lock:
            self.written.append(bytes(data))
            self._acks += b"\xAA\xFF\x01"
        return len(data)

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._acks)

    def read(self, size):
        with self._lock:
            out, self._acks = self._acks[:size], self._acks[size:]
        return out

    def close(self):
        pass


class FakePanelPort:
    """The control panel's serial port: hands out the given lines, then stays quiet."""

    def __init__(self, lines):
        self.lines = list(lines)

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        time.sleep(0.05)
        return b""

    def close(self):
        pass


class HailoStandIn:
    """
    Plays the hailonet in every pipeline the app builds from here on, and records, per pipeline and
    in order, what happens to the element named STANDIN:
      ("state", old, new)  each state change, from the bus synchronously, so that a bin's own
                           rollback inside set_state() is seen too;
      ("buffer", size)     each buffer arriving at its sink pad, from a probe added as soon as the
                           pipeline exists (hailonet adds its own first-buffer probe at creation);
      ("chain", size)      each buffer its chain function processed (identity's handoff).
    The app is pointed at the stand-in by replacing its _hailonet_elements lookup on the instance.

    Why it matters (HailoRT 4.23.0, gsthailonet.cpp; see app._launch_on_camera): a real hailonet
    that goes READY -> NULL before any buffer reached it crashes the program. This records the
    order only; nothing here can crash the way the plugin does.
    """

    def __init__(self, monkeypatch, app):
        from gi.repository import Gst

        self.logs = []                      # one list of events per pipeline built
        real_parse = Gst.parse_launch

        def parse_and_watch(description):
            pipeline = real_parse(description)
            self._watch(pipeline)
            return pipeline

        monkeypatch.setattr(Gst, "parse_launch", parse_and_watch)
        monkeypatch.setattr(app, "_hailonet_elements",
                            lambda pipeline: [e for e in [pipeline.get_by_name(STANDIN)] if e is not None])

    def _watch(self, pipeline):
        from gi.repository import Gst

        element = pipeline.get_by_name(STANDIN)
        if element is None:
            return
        log = []
        self.logs.append(log)

        def on_message(_bus, message):
            if (message.type == Gst.MessageType.STATE_CHANGED and message.src is not None
                    and message.src.get_name() == STANDIN):
                old, new, _pending = message.parse_state_changed()
                log.append(("state", old.value_nick, new.value_nick))
            return Gst.BusSyncReply.PASS

        def on_buffer(_pad, info):
            log.append(("buffer", info.get_buffer().get_size()))
            return Gst.PadProbeReturn.OK

        pipeline.get_bus().set_sync_handler(on_message)
        element.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, on_buffer)
        element.set_property("signal-handoffs", True)
        element.connect("handoff", lambda _el, buffer: log.append(("chain", buffer.get_size())))

    def taken_down_before_a_buffer(self):
        """Every pipeline in which the stand-in went READY -> NULL before any buffer reached it."""
        bad = []
        for index, log in enumerate(self.logs):
            had_buffer = False
            for event in list(log):
                if event[0] == "buffer":
                    had_buffer = True
                elif event == ("state", "ready", "null") and not had_buffer:
                    bad.append((index, list(log)))
                    break
        return bad

    def state_changes(self, index):
        return [event for event in self.logs[index] if event[0] == "state"]


# ============================================================
# Fixtures and helpers
# ============================================================
@pytest.fixture
def app_mod(monkeypatch):
    """The app module, with timings shortened so a test takes seconds, not minutes."""
    from second_vision.pipeline import app as module

    monkeypatch.setattr(module, "DEVICE_SETTLE_SECONDS", 0.05)
    monkeypatch.setattr(module, "MIN_REBUILD_INTERVAL_SECONDS", 0.2)
    # raising=False: the same tests run against the baseline (red-before-green), which lacks it.
    monkeypatch.setattr(module, "CAMERA_POLL_SECONDS", POLL, raising=False)
    return module


@pytest.fixture
def made_apps():
    """Apps built by a test; stopped and cleaned up afterwards, even if the test failed."""
    from gi.repository import Gst

    apps = []
    yield apps
    for app in apps:
        app._shutting_down = True
        app.user_data.shutdown_event.set()
        watcher = app.__dict__.get("_camera_watch_thread")
        if watcher is not None:
            watcher.join(timeout=3)
        pipeline = app.pipeline
        if pipeline is not None and pipeline.get_by_name("source") is not None:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(2 * Gst.SECOND)
            # A pipeline the app built itself always has a watch; the first one only if attached.
            if pipeline is not app._test_initial_pipeline or app._test_initial_watched:
                pipeline.get_bus().remove_signal_watch()
    # Let anything still queued on the GLib loop see _shutting_down and finish.
    gst_helpers.run_main_loop(seconds=0.4)


def build_app(app_mod, made_apps, *, mode="both", source="/dev/video0", descs=(CAMERA_STANDIN,),
              loop=None, attach=True):
    """
    A real SecondVisionApp without __init__, running the stand-in pipeline.

    `descs` are the pipeline strings handed out by get_pipeline_string, in order (the last one
    repeats): the first builds app.pipeline here, the rest any pipeline the app builds itself.
    `app.built` records (mode, video_source) at each of those later builds. `attach` connects the
    bus the way the library's run() does; leave it off when the test calls run() itself.
    """
    from gi.repository import Gst
    from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import get_source_type
    from second_vision.core.config import SystemConfig

    app = app_mod.SecondVisionApp.__new__(app_mod.SecondVisionApp)
    # GStreamerApp.__init__ state that the code under test reads.
    app.options_menu = SimpleNamespace(disable_callback=True, use_frame=False, dump_dot=False, ui=False)
    app.video_source = source
    app.source_type = get_source_type(source)
    app.video_width, app.video_height, app.frame_rate = 640, 480, 30
    app.sync = "false"
    app.horizontal_mirror = app.vertical_mirror = False
    app.user_data = FakeUserData()
    app.loop = loop if loop is not None else RecordingLoop()
    app.error_occurred = False
    app.threads = []
    app.pipeline_latency = 300
    app.watchdog_enabled = app.watchdog_running = app.watchdog_paused = False
    app.watchdog_thread = None
    app.rebuild_count = 0
    # SecondVisionApp.__init__ state.
    app.config = SystemConfig()
    app.config.update(pipeline_mode=mode)
    app._shutting_down = False
    app._rebuild_in_flight = False
    app._last_rebuild_at = 0.0
    app._built_mode = mode
    app._swap_started_at = None
    app._swap_frame_baseline = 0
    app._swap_target_mode = None
    app._hb_last_count = 0

    pending = list(descs)
    app.built = []

    def get_pipeline_string():
        app._built_mode = app.current_mode()
        app.built.append((app._built_mode, app.video_source))
        return pending.pop(0) if len(pending) > 1 else pending[0]

    app.pipeline = Gst.parse_launch(pending.pop(0) if len(pending) > 1 else pending[0])
    app.get_pipeline_string = get_pipeline_string
    app._test_initial_pipeline = app.pipeline
    app._test_initial_watched = attach
    if attach:                       # what the library's run() does before PLAYING
        bus = app.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", app.bus_call, app.loop)
        app._connect_callback()
    made_apps.append(app)
    return app


def record_shutdowns(app, monkeypatch):
    """Replace app.shutdown with a recorder (the real one would stop the test process's workers)."""
    calls = []
    monkeypatch.setattr(app, "shutdown", lambda *args, **kwargs: calls.append(args))
    return calls


def lose_camera(app, text="Could not read from resource.",
                debug="gst_v4l2_object_poll (): poll error 1: Invalid argument (22)"):
    """Post the camera's error, as the 2026-10-15 log showed it, from the element named `source`."""
    assert gst_helpers.post_error(app.pipeline, "source", text, debug)


def state(app):
    return app.__dict__.get("_camera_state", "ok")


def drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def mock_threads():
    return [t.name for t in threading.enumerate() if t.name.startswith("mock")]


def frames_flowing(app, more_than):
    return app.user_data.get_count() > more_than


# ============================================================
# Criterion 1: no crash, in every mode; criterion 3: motors to zero
# ============================================================
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_tc024_003_con08_camera_error_keeps_program_up_stops_motors_and_looks_for_camera(
        app_mod, made_apps, monkeypatch, mode):
    """Removal half of TC-024-003 only (the alert half is T-03c). REQ-CON-08: no crash; motors zero."""
    from gi.repository import Gst

    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, mode=mode)
    shutdowns = record_shutdowns(app, monkeypatch)
    dead = app.pipeline
    dead.set_state(Gst.State.PLAYING)
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, 3), seconds=5), "stand-in never ran"

    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 2, seconds=5), \
        "the program never started looking for a camera"

    assert shutdowns == [], "a camera error still calls shutdown()"
    assert app.error_occurred is False
    assert not app.user_data.shutdown_event.is_set(), "the workers were told to stop"
    assert state(app) == "waiting"
    assert dead.get_state(0)[1] == Gst.State.NULL, "the dead pipeline was not stopped"
    assert app.pipeline is not dead and app.pipeline.get_by_name("source") is None
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF], \
        "exactly one all-zero contract dict must reach serial_queue"
    assert lookup.threads == {"camera-watch"}, "the camera lookup must not run on the GLib loop"
    assert app.config.get("pipeline_mode") == mode
    assert mock_threads() == []


def test_tc024_003_con08_eos_from_live_camera_is_a_camera_loss(app_mod, made_apps, monkeypatch):
    """Removal half of TC-024-003 only. A bus EOS names no element; on a USB camera it means the camera stopped."""
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)

    assert gst_helpers.post_eos(app.pipeline, app.pipeline)     # posted by the pipeline, as for real
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 1, seconds=5)

    assert shutdowns == [] and app.error_occurred is False
    assert state(app) == "waiting"
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]


def test_tc024_003_con08_messages_after_the_camera_error_are_one_loss(app_mod, made_apps, monkeypatch):
    """
    Removal half of TC-024-003 only. A failing source posts its error, then "Internal data stream
    error", then the pipeline posts EOS, and other elements may complain: one loss, no shutdown.
    """
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)

    lose_camera(app)
    gst_helpers.post_error(app.pipeline, "source", "Internal data stream error.", "streaming stopped")
    gst_helpers.post_error(app.pipeline, "sink", "a downstream element complains", "after the camera")
    gst_helpers.post_eos(app.pipeline, app.pipeline)
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 2, seconds=5)

    assert shutdowns == [] and app.error_occurred is False
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]


# ============================================================
# Today's behaviour kept: other elements' errors, file sources
# ============================================================
def test_tc024_003_con08_error_from_another_element_still_shuts_down(app_mod, made_apps, monkeypatch):
    """Removal half of TC-024-003 only. Guard: an error that is not the camera's keeps the library path."""
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)

    gst_helpers.post_error(app.pipeline, "sink", "a hailonet failed", "not the camera")
    assert gst_helpers.run_main_loop(lambda: bool(shutdowns), seconds=5)

    assert len(shutdowns) == 1 and app.error_occurred is True
    assert state(app) == "ok"
    assert drain(app.user_data.serial_queue) == []
    assert app.__dict__.get("_camera_watch_thread") is None and lookup.calls == 0


def test_tc024_003_con08_file_source_eos_keeps_library_looping(app_mod, made_apps, monkeypatch):
    """Removal half of TC-024-003 only. Guard: EOS on a FILE source still loops through _rebuild_pipeline."""
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, source="/tmp/sv_test_clip.mp4")
    assert app.source_type == "file"
    shutdowns = record_shutdowns(app, monkeypatch)
    rebuilds = []
    monkeypatch.setattr(app, "_rebuild_pipeline", lambda: rebuilds.append(1) or False)

    gst_helpers.post_eos(app.pipeline, app.pipeline)
    assert gst_helpers.run_main_loop(lambda: bool(rebuilds), seconds=5)

    assert shutdowns == [] and rebuilds == [1]
    assert state(app) == "ok" and lookup.calls == 0
    assert drain(app.user_data.serial_queue) == []


# ============================================================
# Criterion 2: a mode switch during the loss is announced and recorded, never built
# ============================================================
def test_tc024_003_con08_mode_switch_during_loss_is_recorded_not_built(app_mod, made_apps, monkeypatch):
    """Removal half of TC-024-003 only. trigger_rebuild() from the panel's thread builds nothing while the camera is gone."""
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)
    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 1, seconds=5)

    def panel_switch():           # what config_reader_worker does on "M:depth"
        app.config.update(pipeline_mode="depth")
        app.trigger_rebuild()

    panel = threading.Thread(target=panel_switch, name="panel-stand-in")
    panel.start()
    panel.join(timeout=2)
    gst_helpers.run_main_loop(seconds=0.6)

    assert app.built == [], "a pipeline was built on the missing camera"
    assert app._rebuild_in_flight is False
    # A rebuild queued earlier (a mode switch, or the watchdog) also builds nothing.
    assert app._rebuild_pipeline() is False
    assert app.built == [] and app.pipeline.get_by_name("source") is None
    assert app.loop.quit_calls == 0 and shutdowns == []
    assert app.current_mode() == "depth"


def test_tc024_003_con08_panel_mode_switch_during_loss_is_announced(app_mod, made_apps, monkeypatch):
    """
    Removal half of TC-024-003 only. The REAL panel worker, on a fake port, during the loss: the
    switch is spoken ("depth mode") and recorded, the worker stays alive, nothing is built.
    """
    from second_vision.workers import config_reader

    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)
    lose_camera(app)
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
    assert app.built == [] and shutdowns == [] and app.loop.quit_calls == 0
    app.user_data.shutdown_event.set()
    worker.join(timeout=3)
    assert not worker.is_alive()


# ============================================================
# Criterion 2 and 3 with the real glasses-link worker
# ============================================================
def test_tc024_003_con08_glasses_link_keeps_heartbeat_and_motors_stop(app_mod, made_apps, monkeypatch, capsys):
    """
    Removal half of TC-024-003 only. The REAL serial worker on a fake ESP32 port: after the camera
    error the zero motor packet is written behind the last reading, heartbeats go on, and the link
    never reports "ESP32 stopped acknowledging".
    """
    from second_vision.workers import serial_worker

    port = FakeMotorPort()
    monkeypatch.setattr(serial_worker, "_open_serial_port", lambda config: port)
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)
    worker = threading.Thread(target=serial_worker.serial_worker, args=(app.user_data, app.config),
                              daemon=True, name="serial-writer")
    worker.start()

    reading = serial_worker._pack_motor_update(200, 0, 90)
    app.user_data.serial_queue.put_nowait(
        {"left": 200, "center": 0, "right": 90, "hazard": False, "hazard_severity": 0})
    assert gst_helpers.run_main_loop(lambda: reading in port.written, seconds=3)

    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 1, seconds=5)
    stop = serial_worker._pack_motor_update(0, 0, 0)
    assert gst_helpers.run_main_loop(lambda: stop in port.written, seconds=3)
    after_stop = port.written.index(stop)
    gst_helpers.run_main_loop(seconds=3.0)       # past ACK_TIMEOUT_SECONDS and two heartbeats

    written = list(port.written)
    assert written.index(reading) < after_stop
    motor_packets = [p for p in written if p[:2] == b"\xAA\x01"]
    assert motor_packets[-1] == stop, "the last motor command is not zero"
    heartbeats = [p for p in written[after_stop:] if p == serial_worker._pack_heartbeat()]
    assert len(heartbeats) >= 2, "the heartbeat stopped during the loss"
    assert worker.is_alive() and shutdowns == []
    assert "stopped acknowledging" not in capsys.readouterr().out
    app.user_data.shutdown_event.set()
    worker.join(timeout=3)


# ============================================================
# Criterion 4: keeps looking; restarts on a camera, in the current mode, on whatever node
# ============================================================
def test_tc024_003_con08_camera_return_restarts_in_current_mode_on_new_node(app_mod, made_apps, monkeypatch):
    """
    Removal half of TC-024-003 only. The camera comes back under other nodes (another port) after
    the panel switched to depth during the loss: the pipeline restarts in depth mode, on the lowest
    of the new nodes, in this process, and frames flow again.
    """
    from gi.repository import Gst

    present = threading.Event()
    lookup = FakeCameraLookup(lambda: ["/dev/video6", "/dev/video4"] if present.is_set() else [])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, mode="both")
    shutdowns = record_shutdowns(app, monkeypatch)
    app.watchdog_paused = True          # the watchdog pauses itself when it asks for a rebuild

    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: lookup.calls >= 3, seconds=5), "stopped looking"
    assert app.built == []
    app.config.update(pipeline_mode="depth")       # the panel, during the loss
    app.trigger_rebuild()
    present.set()                                   # the camera is plugged into another port
    assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=5), "never restarted"
    started_at = app.user_data.get_count()
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, started_at + 3), seconds=5), \
        "the restarted pipeline delivers no frames"

    assert app.built == [("depth", "/dev/video4")]
    assert app.video_source == "/dev/video4"
    assert "v4l2src device=/dev/video4 name=source" in app_mod.SecondVisionApp._build_idle(app), \
        "the real source string does not use the returned node"
    assert app.pipeline.get_state(0)[1] == Gst.State.PLAYING
    assert app.watchdog_paused is False
    assert shutdowns == [] and app.error_occurred is False and app.loop.quit_calls == 0
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    assert mock_threads() == []


def test_tc024_003_con08_camera_node_order_prefers_previous_then_lowest(app_mod, made_apps):
    """
    Removal half of TC-024-003 only. This Pi's camera lists /dev/video2 before /dev/video0 (both
    capture nodes; the raw stream is on the first one a UVC camera registers). Order: the node
    frames last came from, then the lowest number; a node that failed goes last, then all again.
    The node last TRIED (video_source) is not preferred: after a failed try it is the wrong one.
    """
    app = build_app(app_mod, made_apps, attach=False)

    assert app._pick_camera(["/dev/video2", "/dev/video0"]) == "/dev/video0"
    assert app._pick_camera(["/dev/video6", "/dev/video4"]) == "/dev/video4"
    app._camera_good_node = "/dev/video6"
    assert app._pick_camera(["/dev/video4", "/dev/video6"]) == "/dev/video6"
    app._camera_good_node = None
    app._camera_bad_nodes = frozenset({"/dev/video4"})
    assert app._pick_camera(["/dev/video6", "/dev/video4"]) == "/dev/video6"
    app._camera_bad_nodes = frozenset({"/dev/video4", "/dev/video6"})
    assert app._pick_camera(["/dev/video6", "/dev/video4"]) == "/dev/video4"
    assert app._camera_bad_nodes == frozenset()
    # Both nodes failed, the last try was video2: start over on video0, not on video2 again.
    app.video_source = "/dev/video2"
    app._camera_bad_nodes = frozenset({"/dev/video0", "/dev/video2"})
    assert app._pick_camera(["/dev/video2", "/dev/video0"]) == "/dev/video0"


def test_tc024_003_con08_loss_records_which_node_gave_frames(app_mod, made_apps, monkeypatch, tmp_path):
    """
    Removal half of TC-024-003 only. At each loss: frames came from the node, so it is preferred
    next time and earlier failures are forgotten; no frame from a node that is still there marks
    it as failed; a node that is gone (unplugged) is not marked.
    """
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    present = tmp_path / "video7"          # stands in for a node that still exists
    present.touch()

    def lost_on_camera_pipeline():
        """A loss as the program has it: a camera pipeline with a watched bus, state "lost"."""
        from gi.repository import Gst
        if app.pipeline.get_by_name("source") is None:      # the empty "no camera" pipeline
            app.pipeline = Gst.parse_launch(CAMERA_STANDIN)
            app.pipeline.get_bus().add_signal_watch()        # as _start_on_camera does
        app._camera_state = "lost"

    app.video_source = str(present)
    app._camera_bad_nodes = frozenset({"/dev/video4"})
    app.user_data.frame_count = 5           # frames came after the start (count was 0)
    lost_on_camera_pipeline()
    app._release_after_camera_loss()
    assert app._camera_good_node == str(present) and app._camera_bad_nodes == frozenset()

    app._frames_at_camera_start = 5         # started again on it, and no frame came
    lost_on_camera_pipeline()
    app._release_after_camera_loss()
    assert app._camera_bad_nodes == frozenset({str(present)})

    app.video_source = str(tmp_path / "video9")    # no frame, and the node is gone
    lost_on_camera_pipeline()
    app._release_after_camera_loss()
    assert app._camera_bad_nodes == frozenset({str(present)})
    assert state(app) == "waiting"
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF] * 3


def test_tc024_003_con08_failed_restart_goes_back_to_waiting_and_retries(app_mod, made_apps, monkeypatch, caplog):
    """
    Removal half of TC-024-003 only (R4). The first restart fails for real (v4l2src cannot open the
    node): the program does not quit, goes back to looking, and starts on the next camera found.
    """
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed, so a real failed start cannot be made")
    assert not os.path.exists(MISSING_NODE)
    lookup = FakeCameraLookup(lambda: [MISSING_NODE] if not app.built else ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, UNOPENABLE_CAMERA, CAMERA_STANDIN))
    shutdowns = record_shutdowns(app, monkeypatch)

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: len(app.built) == 2 and state(app) == "ok",
                                         seconds=8), "never recovered"

    assert app.built == [("both", MISSING_NODE), ("both", "/dev/video5")]
    assert app.loop.quit_calls == 0 and shutdowns == [] and app.error_occurred is False
    assert f"did not start on {MISSING_NODE}" in caplog.text
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]


def test_tc024_003_con08_restart_failed_by_another_element_shuts_down_as_before(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Removal half of TC-024-003 only. Guard (brief: errors from any element other than the camera
    keep today's path). A camera is found, but the restart fails in an element that is not the
    camera: here a filesink that cannot open its file, standing in for a hailonet that cannot get
    the Hailo device. That is the library's error path (error_occurred, shutdown; the boot
    service then restarts the program), once, not a retry every poll.
    """
    hailo_standin_fails = (
        "videotestsrc name=source is-live=true ! "
        "filesink name=det_inference_hailonet location=/nonexistent_t03a_dir/out")
    lookup = FakeCameraLookup(lambda: ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, hailo_standin_fails))
    shutdowns = record_shutdowns(app, monkeypatch)

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: bool(shutdowns), seconds=5), \
            "a restart failed by a non-camera element did not take the shutdown path"
        gst_helpers.run_main_loop(seconds=4 * POLL)

    assert len(shutdowns) == 1 and app.error_occurred is True
    assert app.built == [("both", "/dev/video5")], \
        "expected exactly one restart attempt, on /dev/video5, and no retry after it failed"
    assert app.loop.quit_calls == 0
    assert "det_inference_hailonet" in caplog.text and "Could not open file" in caplog.text
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]


def test_tc024_003_con08_three_losses_in_a_row_each_recover(app_mod, made_apps, monkeypatch):
    """Removal half of TC-024-003 only. Unplug, replug, three times over: one motor stop each, never a shutdown."""
    lookup = FakeCameraLookup(lambda: ["/dev/video0"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    shutdowns = record_shutdowns(app, monkeypatch)
    from gi.repository import Gst
    app.pipeline.set_state(Gst.State.PLAYING)

    for round_ in range(3):
        before = app.user_data.get_count()
        assert gst_helpers.run_main_loop(lambda: frames_flowing(app, before + 3), seconds=5), \
            f"round {round_}: no frames before the unplug"
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: state(app) != "ok", seconds=2), \
            f"round {round_}: the camera loss was not handled"
        assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=5), \
            f"round {round_}: never restarted"

    assert len(app.built) == 3
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF] * 3
    assert shutdowns == [] and app.error_occurred is False and app.loop.quit_calls == 0


def test_tc024_003_con08_mode_switch_rebuild_after_camera_vanished_waits(app_mod, made_apps, monkeypatch):
    """
    Removal half of TC-024-003 only (R4). A mode-switch rebuild that runs after the camera node
    vanished, before its error reached the bus: wait for a camera, never the library's loop.quit().
    """
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed")
    assert not os.path.exists(MISSING_NODE)
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, source=MISSING_NODE, descs=(UNOPENABLE_CAMERA,))
    shutdowns = record_shutdowns(app, monkeypatch)

    assert app._rebuild_pipeline() is False       # what the queued GLib timeout runs
    gst_helpers.run_main_loop(lambda: lookup.calls >= 1, seconds=3)

    assert app.loop.quit_calls == 0, "the library's rebuild quit the main loop (the program would end)"
    assert app.built == []
    assert state(app) == "waiting" and lookup.calls >= 1
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    assert shutdowns == []


def test_tc024_003_con08_slow_teardown_still_stops_motors(app_mod, made_apps, monkeypatch, caplog):
    """Removal half of TC-024-003 only (R1). A teardown that runs long zeroes the motors from a timer and says so."""
    monkeypatch.setattr(app_mod, "CAMERA_TEARDOWN_ALARM_SECONDS", 0.1, raising=False)
    app = build_app(app_mod, made_apps, attach=False)

    monkeypatch.setattr(app, "_release_old_pipeline", lambda: time.sleep(0.4))
    with caplog.at_level(logging.INFO):
        app._release_old_pipeline_with_alarm()
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    assert "tearing the pipeline down has taken over" in caplog.text

    # A teardown that finishes in time cancels its alarm: nothing fires later.
    monkeypatch.setattr(app, "_release_old_pipeline", lambda: None)
    app._release_old_pipeline_with_alarm()
    time.sleep(0.3)
    assert drain(app.user_data.serial_queue) == []


# ============================================================
# Attempt 2: the Hailo plugin's crash windows (F1, F2, F3) and a stop during a start.
# Gst level: HailoStandIn shows the ORDER the app imposes on a Hailo element. That a real
# hailonet then does not crash needs the Hailo chip: the Tester's device level.
# ============================================================
def test_tc024_003_con08_camera_that_cannot_open_never_moves_a_hailo_element(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Removal half of TC-024-003 only (the alert half is T-03c). F1: a camera is found but cannot be
    opened (the node vanished again, or cannot be opened yet). The camera element is opened on its
    own first, so the Hailo element never leaves NULL and no bin rollback takes it READY -> NULL
    before it had a buffer. The program goes back to looking and starts on the next camera, where
    the Hailo element gets its first buffer before the camera streams.
    Cannot show: that the real hailonet does not crash (Hailo chip; device level).
    """
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed, so a real failed open cannot be made")
    assert not os.path.exists(MISSING_NODE)
    lookup = FakeCameraLookup(lambda: [MISSING_NODE] if not app.built else ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps,
                    descs=(CAMERA_STANDIN, UNOPENABLE_CAMERA_WITH_HAILO, CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: len(app.built) == 2 and state(app) == "ok",
                                         seconds=8), "never recovered"

    assert hailo.state_changes(0) == [], \
        "a Hailo element changed state for a camera that could not be opened"
    assert hailo.taken_down_before_a_buffer() == []
    restarted = hailo.logs[1]
    assert restarted.index(("buffer", 0)) < restarted.index(("state", "paused", "playing")), \
        "the Hailo element had no buffer before the camera streamed"
    assert f"did not start on {MISSING_NODE}" in caplog.text
    assert app.loop.quit_calls == 0 and shutdowns == [] and app.error_occurred is False


def test_tc024_003_con08_camera_lost_before_its_first_frame_never_tears_down_an_unfed_hailo_element(
        app_mod, made_apps, monkeypatch, capfd):
    """
    Removal half of TC-024-003 only. F2 and the loose-connector bounce: the camera returns, the
    pipeline starts on it, and the camera goes again before its first frame reached the Hailo
    element (here a live source that never sends one). The Hailo element got one buffer from the
    app while the pipeline was PAUSED; that buffer was dropped before the element's chain function
    and went nowhere. So the teardown after the loss takes down an element that has had its buffer,
    and GStreamer printed no data-flow warning or CRITICAL for it.
    Cannot show: that the real hailonet does not crash (Hailo chip; device level).
    """
    from gi.repository import Gst

    returned = threading.Event()
    lookup = FakeCameraLookup(lambda: [] if returned.is_set() else ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, SILENT_CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)

    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=5), "never restarted"
    returned.set()
    # PLAYING was asked for, but a live source that has sent nothing leaves that change pending
    # (the sink waits for its first buffer to preroll): the real pipeline answers "async" too, in
    # every device log. So the state is PAUSED with PLAYING pending, or PLAYING.
    _ret, current, pending = app.pipeline.get_state(0)
    assert Gst.State.PLAYING in (current, pending), (current, pending)
    lose_camera(app)                       # before any camera frame
    assert gst_helpers.run_main_loop(lambda: state(app) == "waiting", seconds=5), "loss not handled"

    log = hailo.logs[0]
    assert log.index(("buffer", 0)) < log.index(("state", "paused", "playing"))
    assert [event for event in log if event[0] == "chain"] == [], \
        "the app's buffer reached the Hailo element's chain function"
    assert ("state", "ready", "null") in log, "the Hailo element was not taken down"
    assert hailo.taken_down_before_a_buffer() == []
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF, MOTORS_OFF]
    assert shutdowns == [] and app.loop.quit_calls == 0
    err = capfd.readouterr().err
    assert "Got data flow before" not in err and "CRITICAL" not in err, err[-2000:]


def test_tc024_003_con08_hailo_element_that_cannot_be_primed_is_warned_about_and_the_start_goes_on(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Removal half of TC-024-003 only. What priming does when it cannot be done: here the Hailo
    stand-in reports no input format (its sink pad accepts any caps, which a real hailonet whose
    network could not be loaded would also answer). The app says so in a warning, gives that
    element no buffer, and the start goes on: the camera is back and frames flow. Nothing else is
    changed on that path, so the element is exposed to a teardown before its first frame exactly as
    before this attempt (its start order still keeps the camera first).
    Cannot show: the real plugin (Hailo chip; device level). Not covered here: the two other
    "not primed" branches (the chain call raising, or not returning OK), which log the same warning.
    """
    unprimeable = (
        "videotestsrc name=source is-live=true ! video/x-raw,format=RGB,width=64,height=48,framerate=30/1 ! "
        f"identity name={STANDIN} ! identity name=det_callback ! identity name=depth_callback ! "
        "fakesink name=sink sync=false")
    lookup = FakeCameraLookup(lambda: ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, unprimeable))
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: state(app) == "ok", seconds=5), "never restarted"
    started_at = app.user_data.get_count()
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, started_at + 3), seconds=5)

    assert f"hailonet {STANDIN} reports no input format" in caplog.text
    assert ("buffer", 0) not in hailo.logs[0], "an element that cannot be primed was given a buffer"
    assert [e for e in hailo.logs[0] if e[0] == "chain"], "no frame reached the element after the start"
    assert shutdowns == [] and app.loop.quit_calls == 0 and app.error_occurred is False


def test_tc024_003_con08_mode_switch_onto_a_camera_that_cannot_open_waits(
        app_mod, made_apps, monkeypatch, caplog, tmp_path):
    """
    Removal half of TC-024-003 only. F3: a panel mode switch, and when its rebuild opens the camera
    it cannot (the node is still there, so the node check passes; the camera goes, or is not ready).
    The library's rebuild quits the main loop there and the program ends. Now: the program stays
    up and waits for a camera, the new mode stays recorded, the motors are zeroed once, and the
    Hailo element never moved.
    Cannot show: the real unplug timing (device level).
    """
    from gi.repository import Gst

    if Gst.ElementFactory.find("v4l2src") is None:
        pytest.skip("the v4l2src element is not installed, so a real failed open cannot be made")
    node = tmp_path / "video3"             # the camera's node, still there
    node.touch()
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, UNOPENABLE_CAMERA_WITH_HAILO))
    app.video_source = str(node)           # source_type stays "usb"
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)

    def panel_switch():                    # what config_reader_worker does on "M:depth"
        app.config.update(pipeline_mode="depth")
        app.trigger_rebuild()

    with caplog.at_level(logging.INFO):
        panel = threading.Thread(target=panel_switch, name="panel-stand-in")
        panel.start()
        panel.join(timeout=2)
        assert gst_helpers.run_main_loop(lambda: state(app) == "waiting", seconds=5), \
            f"not waiting for a camera after the failed rebuild (quit calls: {app.loop.quit_calls})"

    assert app.loop.quit_calls == 0, "the rebuild quit the main loop (the program would end)"
    assert app.built == [("depth", str(node))]
    assert app.current_mode() == "depth"
    assert shutdowns == [] and app.error_occurred is False
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    assert "waiting for a camera instead" in caplog.text
    assert hailo.state_changes(0) == []
    assert app._rebuild_in_flight is False


def test_tc024_003_con08_mode_switch_rebuild_feeds_hailo_first_and_keeps_loss_handling(
        app_mod, made_apps, monkeypatch, capfd):
    """
    Removal half of TC-024-003 only. A mode switch with the camera present: the app now builds and
    starts the new pipeline itself (_rebuild_on_camera) instead of the library's rebuild. It runs in
    the new mode, the rebuild is counted and the watchdog resumed as the library does, frames flow,
    the Hailo element had one buffer before the camera streamed (never processed: the first buffer
    its chain function saw is a frame), with no GStreamer warning; and a camera error on the rebuilt
    pipeline is still a camera loss, not a shutdown.
    Cannot show: the real network configuration on the Hailo chip (device level).
    """
    from gi.repository import Gst

    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    shutdowns = record_shutdowns(app, monkeypatch)
    app.pipeline.set_state(Gst.State.PLAYING)
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, 3), seconds=5)

    app.config.update(pipeline_mode="depth")
    app.trigger_rebuild()
    assert gst_helpers.run_main_loop(lambda: app.built and not app._rebuild_in_flight, seconds=5)
    count = app.user_data.get_count()
    assert gst_helpers.run_main_loop(lambda: frames_flowing(app, count + 3), seconds=5), \
        "the rebuilt pipeline delivers no frames"

    assert app.built == [("depth", "/dev/video0")]
    assert app.rebuild_count == 1 and app.watchdog_paused is False
    assert app.pipeline.get_state(0)[1] == Gst.State.PLAYING
    log = hailo.logs[0]
    assert log.index(("buffer", 0)) < log.index(("state", "paused", "playing"))
    chained = [event[1] for event in log if event[0] == "chain"]
    assert chained and 0 not in chained, f"the app's buffer reached the chain function: {chained[:5]}"

    lose_camera(app)
    assert gst_helpers.run_main_loop(lambda: state(app) == "waiting", seconds=5)
    assert hailo.taken_down_before_a_buffer() == []
    assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    assert shutdowns == [] and app.loop.quit_calls == 0 and app.error_occurred is False
    err = capfd.readouterr().err
    assert "Got data flow before" not in err and "CRITICAL" not in err, err[-2000:]


@pytest.mark.parametrize("when", ["while-building", "after-hailo-fed"])
def test_tc024_003_con08_stop_during_a_camera_restart_never_tears_down_an_unfed_hailo_element(
        app_mod, made_apps, monkeypatch, when):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression (brief criterion 6). Ctrl+C or SIGTERM
    runs app.shutdown() on the main thread between two Python steps of whatever the main loop is
    doing, so a stop can land inside a camera restart. The library's shutdown() sets the pipeline
    PAUSED, READY, then NULL: on a new Hailo pipeline that had no buffer yet, that is the crash.
    Here the real shutdown() runs inside the restart: while the pipeline string is being built,
    or once the Hailo element was fed (from _on_pipeline_rebuilt). Expected: the restart goes no
    further (never PLAYING), the main loop is asked to quit, and no Hailo element is ever taken
    down before it had a buffer. "while-building" is a guard: it held before attempt 2 too.
    Cannot show: the real plugin (device level); real signal delivery (the Tester's stop tests).
    """
    from gi.repository import Gst

    lookup = FakeCameraLookup(lambda: ["/dev/video5"])
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, descs=(CAMERA_STANDIN, SILENT_CAMERA_WITH_HAILO))
    hailo = HailoStandIn(monkeypatch, app)
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: None)
    stopped = []

    def stop():                            # what SIGINT / SIGTERM run, here mid-restart
        if not stopped:
            stopped.append(time.monotonic())
            app.shutdown()

    if when == "while-building":
        build = app.get_pipeline_string

        def build_then_stop():
            description = build()
            if app.built and state(app) == "starting":
                stop()
            return description
        monkeypatch.setattr(app, "get_pipeline_string", build_then_stop)
    else:
        rebuilt = app._on_pipeline_rebuilt

        def stop_then_rebuilt():
            stop()
            rebuilt()
        monkeypatch.setattr(app, "_on_pipeline_rebuilt", stop_then_rebuilt)

    saved = signal.getsignal(signal.SIGINT)   # the library's shutdown() resets SIGINT
    try:
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: app.loop.quit_calls >= 1, seconds=8), \
            f"the stop never asked the main loop to quit (stopped: {bool(stopped)})"
        app.pipeline.set_state(Gst.State.NULL)           # what run()'s cleanup does next
    finally:
        signal.signal(signal.SIGINT, saved)

    assert stopped and app.user_data.shutdown_event.is_set()
    assert all(("state", "paused", "playing") not in log for log in hailo.logs), \
        "the restart went on to PLAYING after the stop"
    assert hailo.taken_down_before_a_buffer() == [], hailo.taken_down_before_a_buffer()
    assert app.error_occurred is False


def test_tc024_003_con08_still_looking_for_a_camera_is_logged_about_once_a_minute(
        app_mod, made_apps, monkeypatch, caplog):
    """
    Removal half of TC-024-003 only. Lead's answer Q5 (decision-1): while no camera is found, one
    INFO line about every 60 s says the program is still looking; each look itself stays at DEBUG.
    Here the period is shortened to 0.3 s and a look runs every 0.1 s.
    """
    monkeypatch.setattr(app_mod, "CAMERA_STILL_LOOKING_LOG_SECONDS", 0.3, raising=False)
    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps)
    record_shutdowns(app, monkeypatch)

    with caplog.at_level(logging.INFO):
        lose_camera(app)
        assert gst_helpers.run_main_loop(lambda: lookup.calls >= 12, seconds=5)

    lines = [r for r in caplog.records if "Still no USB camera after" in r.getMessage()]
    assert 2 <= len(lines) <= lookup.calls // 2, \
        f"{len(lines)} 'still looking' lines for {lookup.calls} looks"
    assert all(r.levelno == logging.INFO for r in lines)


# ============================================================
# Criteria 5 and 6 through the library's real run(): a clean stop while the camera is missing
# ============================================================
@pytest.mark.parametrize("when", ["waiting", "lost"],
                         ids=["while-looking-for-camera", "before-teardown"])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_tc024_003_con08_clean_stop_while_camera_missing_exits_0(app_mod, made_apps, monkeypatch,
                                                                 caplog, capsys, signum, when):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression. hailo_apps' real run() on the stand-in:
    the camera fails, then Ctrl+C (SIGINT, also what the boot service sends) or SIGTERM ends it
    through app.shutdown: exit code 0, workers told to stop, no mock mode. Stopped either while the
    program looks for a camera, or in the short window after the error and before the dead
    pipeline is torn down (the teardown must then not run, and no watcher may start).
    """
    from gi.repository import GLib

    lookup = FakeCameraLookup()
    monkeypatch.setattr(app_mod, "get_usb_video_devices", lookup, raising=False)
    app = build_app(app_mod, made_apps, loop=GLib.MainLoop(), attach=False)
    app._test_initial_watched = True          # run() attaches the bus itself
    forced_exits = []
    monkeypatch.setattr(app, "_force_exit_if_stuck", lambda: forced_exits.append(1))

    steps = {"lost": False, "signalled": False, "source": None}

    def drive():
        if not steps["lost"]:
            if frames_flowing(app, 3):
                # As if a mode switch had just rebuilt: the teardown then waits
                # MIN_REBUILD_INTERVAL_SECONDS, which leaves the "lost" window open.
                app._last_rebuild_at = time.monotonic()
                lose_camera(app)
                steps["lost"] = True
            return True
        ready = (state(app) == "lost" if when == "lost"
                 else lookup.calls >= 1 and state(app) == "waiting")
        if ready:
            steps["signalled"] = True
            os.kill(os.getpid(), signum)          # handled by app.shutdown, below
            steps["source"] = None
            return False
        return True

    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    # What GStreamerApp.__init__ does for SIGINT and main.py does for SIGTERM.
    signal.signal(signum, app.shutdown)
    steps["source"] = GLib.timeout_add(50, drive)
    try:
        with caplog.at_level(logging.INFO):
            with pytest.raises(SystemExit) as exited:
                app.run()
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
        if steps["source"] is not None:
            GLib.source_remove(steps["source"])

    assert steps["lost"] and steps["signalled"], \
        f"the run ended (exit {exited.value.code}) before the stop could be sent (state {when!r})"
    assert exited.value.code == 0, "a clean stop after a camera loss must exit 0"
    assert app.user_data.shutdown_event.is_set(), "the workers were not told to stop"
    gst_helpers.run_main_loop(seconds=0.4)    # past the teardown's timer, had it been left to run
    watcher = app.__dict__.get("_camera_watch_thread")
    if when == "lost":
        # The serial worker zeroes the motors on its way out (unchanged); the camera-loss
        # teardown never ran, so it queued nothing and started no watcher.
        assert watcher is None and lookup.calls == 0
        assert drain(app.user_data.serial_queue) == []
    else:
        assert watcher is not None
        watcher.join(timeout=3)
        assert not watcher.is_alive(), "the camera watcher outlived the stop"
        assert drain(app.user_data.serial_queue) == [MOTORS_OFF]
    assert "Error during cleanup" not in caplog.text and "Exiting successfully" in caplog.text
    assert mock_threads() == [] and "MOCK" not in capsys.readouterr().out
