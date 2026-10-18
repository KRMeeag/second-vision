"""
The failsafe status on screen and in the terminal: core/status_board.py, core/status_view.py,
workers/status_worker.py, and where the app and the detection overlay show it.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_status_display.py -v
    my_hailo_env/bin/python -m pytest tests/test_status_display.py -v

What it is for (the user, 2026-10-19): a tester, and the panel watching the TV at the demo, must see the
failsafe checks (F0 to F6, the camera view of F5 above all) work live, in the cv2 window and in the terminal.
The earphones are bone conduction and the patterns are felt, so nobody but the wearer notices them otherwise.

Layers: the status read from the real alert scheduler and camera view check; the terminal lines; the layout
and drawing (every piece inside the frame); the worker thread; the app's dark status screen when there is no
live picture; the detection overlay drawing it; main.py starting it.
"""

import io
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC = TESTS_DIR.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TESTS_DIR))

import gst_helpers  # noqa: E402,F401
import test_pipeline_modes  # noqa: E402,F401  (installs the hailo/gi stand-ins where the real modules are missing)
from test_alerts import World  # noqa: E402
from test_camera_alerts import unit_world  # noqa: E402
from test_camera_loss import (  # noqa: E402,F401  (the fixtures are used by name)
    CAMERA_STANDIN, MISSING_NODE, UNOPENABLE_CAMERA, UNOPENABLE_CAMERA_WITH_HAILO,
    FakeCameraLookup, HailoStandIn, app_mod, build_app, frames_flowing, lose_camera,
    made_apps, record_shutdowns, state,
)

from second_vision.core import status_board as sb  # noqa: E402
from second_vision.core import status_view as sv  # noqa: E402
from second_vision.core.alerts import AlertScheduler  # noqa: E402
from second_vision.core.camera_view import VIEW_BRIGHT, VIEW_DARK, VIEW_OK, CameraViewWatch  # noqa: E402
from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.core.connections import (  # noqa: E402
    CAMERA_MISSING, earphones_lost, glasses_lost, panel_lost,
)
from second_vision.core.priority import PriorityMailbox  # noqa: E402
from second_vision.core.status_board import OFF, OK, PENDING, PROBLEM, Check, Status, StatusReporter, read_status  # noqa: E402
from second_vision.workers.status_worker import status_worker  # noqa: E402

H, W = 480, 640


def in_use_config(**extra):
    config = SystemConfig()
    config.update(serial_port="/dev/serial0", config_port="/dev/ttyAMA3", pipeline_mode="both", **extra)
    return config


def fresh(config=None):
    alerts = AlertScheduler(PriorityMailbox())
    view = CameraViewWatch(alerts)
    return alerts, view, (config if config is not None else in_use_config())


def by_key(status):
    return {c.key: c for c in status.checks}


# ============================================================
# The status, read from the real scheduler and view check
# ============================================================
def test_everything_working():
    alerts, view, config = fresh()
    view.observe(VIEW_OK, 118.0, 214.0)
    status = read_status(alerts, view, config)
    checks = by_key(status)
    assert [c.key for c in status.checks] == ["camera", "view", "glasses", "earphones", "panel"]
    assert all(c.state == OK for c in status.checks), status
    assert checks["view"].text == "clear" and "average 118" in checks["view"].detail
    assert status.problems() == [] and status.said is None


def test_each_part_that_is_out_shows_as_a_problem_in_words():
    alerts, view, config = fresh()
    alerts.raise_alert(CAMERA_MISSING, "Critical Error: Camera cannot be reached", interval=10.0)
    glasses_lost(alerts, config)
    earphones_lost(alerts, None)                 # a silent alert: still shown
    panel_lost(alerts)
    checks = by_key(read_status(alerts, view, config))
    assert (checks["camera"].state, checks["camera"].text) == (PROBLEM, "NOT CONNECTED")
    assert (checks["glasses"].state, checks["glasses"].text) == (PROBLEM, "NOT RESPONDING")
    assert (checks["earphones"].state, checks["earphones"].text) == (PROBLEM, "MISSING")
    assert (checks["panel"].state, checks["panel"].text) == (PROBLEM, "NOT RESPONDING")
    assert (checks["view"].state, checks["view"].text) == (OFF, "no picture"), "no view to judge without a camera"


def test_a_board_that_was_never_asked_for_is_not_in_use():
    alerts, view, _ = fresh()
    config = SystemConfig()
    config.update(serial_port=None, config_port=None)
    checks = by_key(read_status(alerts, view, config))
    assert checks["glasses"].state == OFF and checks["panel"].state == OFF
    assert checks["glasses"].text == "not in use"


def test_without_a_config_every_part_counts_as_in_use():
    alerts, view, _ = fresh()
    assert by_key(read_status(alerts, view, None))["panel"].state == OK


@pytest.mark.parametrize("kinds,state,text,short", [
    ([], OFF, "not checked yet", "-"),
    ([VIEW_OK], OK, "clear", "OK"),
    ([VIEW_DARK, VIEW_DARK], PENDING, "looks dark (2 of 3)", "2/3"),
    ([VIEW_DARK] * 3, PROBLEM, "BLOCKED OR TOO DARK", "BLOCKED"),
    ([VIEW_BRIGHT] * 3, PROBLEM, "TOO BRIGHT", "BRIGHT"),
    ([VIEW_DARK] * 3 + [VIEW_OK], PROBLEM, "BLOCKED OR TOO DARK (looks clear: 1 of 3)", "BLOCKED"),
    ([VIEW_BRIGHT, VIEW_OK], OK, "clear", "OK"),
])
def test_the_camera_view_in_words(kinds, state, text, short):
    alerts, view, config = fresh()
    for kind in kinds:
        view.observe(kind, 6.0, 11.0)
    check = by_key(read_status(alerts, view, config))["view"]
    assert (check.state, check.text, check.short) == (state, text, short)
    if kinds:
        assert check.detail == "brightness 0-255: average 6, top 5 %: 11"


def test_with_both_rockers_off_the_view_is_paused_and_without_a_pipeline_not_checked():
    alerts, view, config = fresh(in_use_config())
    config.update(pipeline_mode="none")
    assert by_key(read_status(alerts, view, config))["view"].text == "paused (both rockers off)"
    assert by_key(read_status(alerts, None, config))["view"].text == "not checked"


def test_the_last_spoken_alert_and_when():
    w = World(duration=2.0)
    view = CameraViewWatch(w.sched)
    assert read_status(w.sched, view, None).said is None
    w.at(3.0, lambda: w.sched.raise_alert("a", "Vibration is not working"))
    w.at(10.0, lambda: w.mailbox.offer({"announce": "depth mode"}))     # not an alert: not shown as said
    w.run(20)
    status = read_status(w.sched, view, None)
    assert (status.said, status.said_at) == ("Vibration is not working", pytest.approx(3.0, abs=0.1))


def test_reading_the_status_changes_nothing():
    alerts, view, config = fresh()
    glasses_lost(alerts, config)
    view.observe(VIEW_DARK, 5.0, 9.0)
    before = (alerts.raised(), view.snapshot(), config.snapshot())
    for _ in range(5):
        read_status(alerts, view, config)
    assert (alerts.raised(), view.snapshot(), config.snapshot()) == before


# ============================================================
# The terminal
# ============================================================
def test_the_terminal_gets_everything_once_then_only_the_changes():
    alerts, view, config = fresh()
    out = io.StringIO()
    reporter = StatusReporter(out)
    first = reporter.report(read_status(alerts, view, config))
    assert len(first) == 1 and first[0].startswith("[CHECK] Camera: connected | Camera view: not checked yet")
    assert reporter.report(read_status(alerts, view, config)) == []
    for _ in range(3):
        view.observe(VIEW_DARK, 6.0, 11.0)
    lines = reporter.report(read_status(alerts, view, config))
    assert lines == ["[CHECK] Camera view: BLOCKED OR TOO DARK (brightness 0-255: average 6, top 5 %: 11)"]
    assert out.getvalue().splitlines()[-1] == lines[0]


def test_brightness_numbers_alone_do_not_print_every_second():
    alerts, view, config = fresh()
    reporter = StatusReporter(io.StringIO())
    view.observe(VIEW_OK, 100.0, 200.0)
    reporter.report(read_status(alerts, view, config))
    view.observe(VIEW_OK, 101.0, 203.0)
    assert reporter.report(read_status(alerts, view, config)) == []


def test_every_part_lost_and_back_is_printed():
    alerts, view, config = fresh()
    reporter = StatusReporter(io.StringIO())
    reporter.report(read_status(alerts, view, config))
    glasses_lost(alerts, config)
    assert reporter.report(read_status(alerts, view, config)) == ["[CHECK] Glasses board: NOT RESPONDING"]
    alerts.clear("glasses-missing")
    assert reporter.report(read_status(alerts, view, config)) == ["[CHECK] Glasses board: connected"]


def test_colour_only_on_a_terminal():
    class Tty(io.StringIO):
        def isatty(self):
            return True

    alerts, view, config = fresh()
    glasses_lost(alerts, config)
    plain, tty = io.StringIO(), Tty()
    StatusReporter(plain).report(read_status(alerts, view, config))
    StatusReporter(tty).report(read_status(alerts, view, config))
    assert "\033[" not in plain.getvalue()
    assert "\033[1;31mNOT RESPONDING\033[0m" in tty.getvalue()


# ============================================================
# The layout and the drawing
# ============================================================
def checks(**over):
    base = {key: Check(key, name, label, OK, "connected", "OK") for key, name, label in (
        ("camera", "Camera", "CAMERA"), ("view", "Camera view", "VIEW"), ("glasses", "Glasses board", "GLASSES"),
        ("earphones", "Earphones", "EARPHONES"), ("panel", "Control panel", "PANEL"))}
    base.update(over)
    return tuple(base.values())


BLOCKED = Check("view", "Camera view", "VIEW", PROBLEM, "BLOCKED OR TOO DARK", "BLOCKED",
                "brightness 0-255: average 6, top 5 %: 11, at white: 0 %", "avg 6  top 5% 11  white 0%")
LONG = "Camera is blocked or it is too dark. Obstacle warnings may be missing."


def text_width(text, scale, thickness):
    import cv2
    return cv2.getTextSize(text, sv.FONT, scale, thickness)[0][0]


def all_inside(layout, width, height):
    for text, (x, y), scale, _color, thickness in layout["strip"] + layout["lines"]:
        assert 0 <= x and x + text_width(text, scale, thickness) <= width, (text, x, scale)
        assert 0 < y <= height, (text, y)


def test_all_well_is_one_quiet_strip():
    layout = sv.status_layout(Status(checks()), W, H, now=0.0)
    assert layout["lines"] == []
    assert [p[0] for p in layout["strip"]] == ["CAMERA", "OK", "VIEW", "OK", "GLASSES", "OK", "EARPHONES", "OK",
                                               "PANEL", "OK"]
    assert all(p[3] == sv.COLORS[OK] for p in layout["strip"][1::2])
    all_inside(layout, W, H)


def test_a_problem_is_a_red_banner_and_the_measurements_are_live_top_right():
    layout = sv.status_layout(Status(checks(view=BLOCKED)), W, H, now=0.0)
    texts = [(t, color) for t, _, _, color, _ in layout["lines"]]
    assert ("CAMERA VIEW: BLOCKED OR TOO DARK", sv.COLORS[PROBLEM]) in texts
    assert ("BLOCKED", sv.COLORS[PROBLEM]) in [(p[0], p[3]) for p in layout["strip"]]
    readout = [(t, org) for t, org, _, color, _ in layout["lines"] if t.startswith("VIEW ")]
    assert readout and readout[0][0] == "VIEW  avg 6  top 5% 11  white 0%"
    text, (x, y) = readout[0]
    assert y == sv.READOUT_Y and x > W // 2, "top right, clear of the overlay's FPS at the top left"
    all_inside(layout, W, H)


def test_the_live_readout_is_there_whatever_the_state():
    ok = Check("view", "Camera view", "VIEW", OK, "clear", "OK", readout="avg 118  top 5% 214  white 3%")
    layout = sv.status_layout(Status(checks(view=ok)), W, H, now=0.0)
    assert [t for t, *_ in layout["lines"]] == ["VIEW  avg 118  top 5% 214  white 3%"]


def test_a_change_being_confirmed_is_yellow():
    pending = Check("view", "Camera view", "VIEW", PENDING, "looks dark (2 of 3)", "2/3")
    layout = sv.status_layout(Status(checks(view=pending)), W, H, now=0.0)
    assert layout["lines"][0][0] == "Camera view: looks dark (2 of 3)" and layout["lines"][0][3] == sv.COLORS[PENDING]


def test_what_was_said_shows_for_a_few_seconds_and_wraps_inside_the_frame():
    status = Status(checks(view=BLOCKED), LONG, said_at=100.0)
    shown = sv.status_layout(status, W, H, now=100.0 + sb.SAID_SECONDS - 0.1)
    said = [t for t, _, _, color, _ in shown["lines"] if color == sv.SAID_COLOR]
    assert len(said) == 2 and said[0].startswith('Said: "Camera is blocked')
    assert " ".join(said).replace("..", "") .endswith('missing."')
    all_inside(shown, W, H)
    gone = sv.status_layout(status, W, H, now=100.0 + sb.SAID_SECONDS + 0.1)
    assert not [1 for _, _, _, color, _ in gone["lines"] if color == sv.SAID_COLOR]


def test_four_problems_at_once_still_fit_and_a_small_frame_shrinks_the_strip():
    lost = {k: Check(k, n, l, PROBLEM, "NOT RESPONDING", "LOST") for k, n, l in (
        ("glasses", "Glasses board", "GLASSES"), ("earphones", "Earphones", "EARPHONES"),
        ("panel", "Control panel", "PANEL"))}
    status = Status(checks(view=BLOCKED, **lost), LONG, 0.0)
    all_inside(sv.status_layout(status, W, H, now=1.0), W, H)
    small = sv.status_layout(status, 320, 240, now=1.0)
    all_inside(small, 320, 240)
    assert small["strip"][0][2] < sv.STRIP_SCALE
    assert [p[0] for p in small["strip"][0::2]][:4] == ["CAM", "VIEW", "GLS", "EAR"], "short labels"
    narrow = sv.status_layout(status, 400, 300, now=1.0)
    all_inside(narrow, 400, 300)
    assert [p[0] for p in narrow["strip"][0::2]] == ["CAM", "VIEW", "GLS", "EAR", "PNL"]
    assert narrow["strip"][0][2] > sv.MIN_STRIP_SCALE, "short labels come before the smallest font"
    tiny = sv.status_layout(status, 160, 120, now=1.0)
    all_inside(tiny, 160, 120)                       # what cannot fit is left out, never drawn past the edge
    said_top = min(y for t, (x, y), s, color, th in tiny["lines"] if color == sv.SAID_COLOR)
    assert all(y < said_top for t, (x, y), s, color, th in tiny["lines"] if color != sv.SAID_COLOR)


def test_drawing_changes_the_frame_in_place_and_nothing_else_about_it():
    frame = np.full((H, W, 3), 128, np.uint8)
    out = sv.draw_status(frame, Status(checks(view=BLOCKED)), now=0.0)
    assert out is frame and frame.shape == (H, W, 3) and frame.dtype == np.uint8
    assert frame[H - 5, W - 5].tolist() == [32, 32, 32], "the strip's plate"
    banner = frame[40:70, 8:400]
    assert ((banner[..., 2] > 200) & (banner[..., 0] < 120)).any(), "no red banner text"
    assert frame[200, 600].tolist() == [128, 128, 128], "the middle of the picture is left alone"


def test_the_dark_screen_carries_the_note_and_the_status():
    canvas = sv.status_canvas(W, H, Status(checks()), now=0.0, note="No camera picture: looking for the camera")
    assert canvas.shape == (H, W, 3) and canvas.dtype == np.uint8
    assert (canvas[H // 2 - 20:H // 2 + 5] > 150).any(), "the note"
    assert (canvas[H - 25:H] != canvas[5, 5]).any(), "the strip"


# ============================================================
# The worker
# ============================================================
def test_the_worker_keeps_the_latest_status_on_user_data_and_prints_changes(capsys):
    alerts, view, config = fresh()
    ud = SimpleNamespace(alerts=alerts, camera_view=view, shutdown_event=threading.Event(), status=None)
    worker = threading.Thread(target=status_worker, args=(ud, config), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while ud.status is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ud.status is not None and ud.status.get("glasses").state == OK
        glasses_lost(alerts, config)
        deadline = time.monotonic() + 3
        while ud.status.get("glasses").state != PROBLEM and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ud.status.get("glasses").state == PROBLEM
    finally:
        ud.shutdown_event.set()
        worker.join(2)
    assert not worker.is_alive()
    assert "[CHECK] Glasses board: NOT RESPONDING" in capsys.readouterr().out


def test_the_worker_survives_a_broken_scheduler_and_stops_at_once(capsys):
    class Broken:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError("scheduler broken")
            return fail

    ud = SimpleNamespace(alerts=Broken(), shutdown_event=threading.Event(), status=None)
    worker = threading.Thread(target=status_worker, args=(ud, None), daemon=True)
    worker.start()
    time.sleep(1.2)
    assert worker.is_alive()
    began = time.monotonic()
    ud.shutdown_event.set()
    worker.join(2)
    assert not worker.is_alive() and time.monotonic() - began < 1.0
    assert "[CHECK] Error reading the status" in capsys.readouterr().out


# ============================================================
# The app's dark screen, when no live picture carries the status
# ============================================================
def screen_app(app_mod, monkeypatch, mode="both", use_frame=True, status=Status(checks())):
    w, app = unit_world(app_mod, monkeypatch, mode=mode)
    pushed = []
    app.options_menu = SimpleNamespace(use_frame=use_frame)
    app.video_width, app.video_height = W, H
    app.user_data.status = status
    app.user_data.set_frame = pushed.append
    app.pipeline = None
    app._camera_view = None
    app._view_probe = None
    return app, pushed


@pytest.mark.parametrize("camera_state,mode", [("waiting", "both"), ("lost", "detection"), ("ok", "depth"),
                                               ("ok", "none")])
def test_the_dark_status_screen_shows_when_there_is_no_live_picture(app_mod, monkeypatch, camera_state, mode):
    app, pushed = screen_app(app_mod, monkeypatch, mode=mode)
    app._camera_state = camera_state
    assert app._camera_view_tick() is True
    assert len(pushed) == 1 and pushed[0].shape == (H, W, 3)


def test_with_a_live_picture_the_overlay_draws_it_and_no_screen_is_pushed(app_mod, monkeypatch):
    app, pushed = screen_app(app_mod, monkeypatch, mode="both")
    app._camera_view_tick()
    assert pushed == []


@pytest.mark.parametrize("use_frame,status", [(False, Status(checks())), (True, None)])
def test_no_screen_without_a_display_process_or_before_the_first_status(app_mod, monkeypatch, use_frame, status):
    app, pushed = screen_app(app_mod, monkeypatch, mode="depth", use_frame=use_frame, status=status)
    app._camera_view_tick()
    assert pushed == []


def test_the_debug_banner_gives_way_to_the_status_screen(app_mod, monkeypatch):
    app, pushed = screen_app(app_mod, monkeypatch, mode="depth")
    app._push_debug_banner("depth")
    assert pushed == []


def test_the_app_uses_the_view_check_main_made_so_the_screen_and_the_alerts_agree(app_mod, monkeypatch):
    app, _ = screen_app(app_mod, monkeypatch)
    shared = CameraViewWatch(app.user_data.alerts)
    app.user_data.camera_view = shared
    assert app._camera_view_watch() is shared


# ============================================================
# The detection overlay draws it on the live picture
# ============================================================
def overlay_frame(monkeypatch, status, draw=None):
    from second_vision.pipeline import callbacks
    if draw is not None:
        monkeypatch.setattr(callbacks, "draw_status", draw)
    monkeypatch.setattr(callbacks, "get_caps_from_pad", lambda pad: ("RGB", W, H), raising=False)
    monkeypatch.setattr(callbacks, "get_numpy_from_buffer",
                        lambda buffer, fmt, w, h: np.full((h, w, 3), 128, np.uint8), raising=False)
    shown = []
    ud = SimpleNamespace(status=status, set_frame=shown.append, IDs_changed_zones=set())
    element = SimpleNamespace(get_static_pad=lambda name: object())
    callbacks._draw_detection_overlay(element, object(), ud, set(), {}, 29.8)
    return shown


def test_the_overlay_draws_the_status_on_the_picture(monkeypatch):
    with_status = overlay_frame(monkeypatch, Status(checks(view=BLOCKED)))
    without = overlay_frame(monkeypatch, None)
    assert len(with_status) == 1 and len(without) == 1
    # Row means: the zone divider lines have zero channels, which stay zero when the plate darkens them.
    assert with_status[0][H - 5].mean() < 0.5 * without[0][H - 5].mean(), "the strip's plate"


def test_a_failing_status_drawing_never_costs_the_frame(monkeypatch, capsys):
    def boom(*args):
        raise RuntimeError("drawing broke")

    shown = overlay_frame(monkeypatch, Status(checks()), draw=boom)
    assert len(shown) == 1


# ============================================================
# main.py starts it
# ============================================================
def test_main_starts_the_status_worker_and_prints_the_status(tmp_path):
    """The real program path (--mock, a do-nothing espeak): started, the first [CHECK] line, a clean Ctrl+C."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "espeak-ng"
    stub.write_text("#!/bin/sh\ncat > /dev/null 2>&1\nexit 0\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PYTHONPATH=str(SRC), PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, str(SRC / "second_vision" / "main.py"), "--mock"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=tmp_path)
    out, deadline = "", time.monotonic() + 60
    # Both lines, in either order: the worker's first [CHECK] line can come before main's own print.
    while time.monotonic() < deadline and not ("[CHECK] Camera:" in out and "Generators running" in out):
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        out += line
    assert "Status worker started" in out and "[CHECK] Camera: connected" in out, out[-800:]
    assert "Camera view: not checked yet" in out, "mock mode has no camera pipeline: nothing was checked"
    proc.send_signal(signal.SIGINT)
    try:
        rest, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("main() did not stop within 10 s of Ctrl+C")
    assert proc.returncode == 0 and "[MAIN] Done." in rest, (proc.returncode, rest[-1500:])
