"""
F5: camera blocked, too dark or too bright (docs/testing/URGENT-TESTS.md section 1): core/camera_view.py and
the frame request in pipeline/app.py.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_camera_view.py -v
    my_hailo_env/bin/python -m pytest tests/test_camera_view.py -v

Requirement (the user's decisions, 2026-10-17): once a second, before any AI runs, a tiny grey sample of a camera
frame. "Blocked or dark": 95th percentile under 40 or mean under 14. "Too bright": mean over 240. Three checks in a
row to raise, three good ones to clear. "Camera is blocked or it is too dark. Obstacle warnings may be missing." /
"Camera is facing very bright light. Obstacle warnings may be missing." every 30 s; "Camera view is back." once.
Not while the camera is missing (the camera-missing alert takes over). Must not lag.

Four layers:
  * the check itself, on synthetic frames (black, dark, a covered lens, a normal room, a plain wall, a lamp in a dark
    room, bright, noise; the exact boundaries; the frame formats hailo_apps hands out);
  * the three-in-a-row rules and the alerts, on a virtual clock (tests/test_alerts.py's World: the real scheduler
    and a speech model);
  * the REAL app methods (the one-shot probe on the source's last element), GStreamer replaced by fakes
    (tests/test_camera_alerts.py's unit_app);
  * level `gst` (my_hailo_env only): the same methods on a REAL GStreamer pipeline (videotestsrc switched between
    black, white and a test card), proving that the probe takes one frame per tick, removes itself, does not
    slow the stream, and how much a check costs.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

import gst_helpers  # noqa: E402
import test_pipeline_modes  # noqa: E402,F401  (installs the hailo/gi stand-ins where the real modules are missing)
from test_alerts import World  # noqa: E402
from test_camera_alerts import MISSING, Msg, lose, returns, unit_world  # noqa: E402,F401
from test_camera_loss import (  # noqa: E402,F401  (the fixtures are used by name)
    CAMERA_STANDIN, MISSING_NODE, UNOPENABLE_CAMERA, UNOPENABLE_CAMERA_WITH_HAILO,
    FakeCameraLookup, HailoStandIn, app_mod, build_app, frames_flowing, lose_camera,
    made_apps, record_shutdowns, state,
)

from second_vision.core import camera_view as cv  # noqa: E402
from second_vision.core.alerts import AlertScheduler  # noqa: E402
from second_vision.core.camera_view import (  # noqa: E402
    VIEW_BACK_ALERT, VIEW_BACK_TEXT, VIEW_BRIGHT, VIEW_BRIGHT_ALERT, VIEW_BRIGHT_TEXT, VIEW_DARK, VIEW_DARK_ALERT,
    VIEW_DARK_TEXT, VIEW_OK, CameraViewWatch, classify, grey_sample, view_stats,
)
from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.core.connections import EVERYTHING_CONNECTED_TEXT, MISSING_ALERTS, glasses_back, glasses_lost  # noqa: E402
from second_vision.core.priority import PriorityMailbox  # noqa: E402

H, W = 480, 640
RNG = np.random.default_rng(20261019)


def rgb(level):
    """A uniform RGB frame of one grey level."""
    return np.full((H, W, 3), level, dtype=np.uint8)


def with_patch(base, level, fraction):
    """A frame of grey `base` with a bright square block covering about `fraction` of it (a lamp, a window)."""
    frame = rgb(base)
    side_h, side_w = int(H * fraction ** 0.5), int(W * fraction ** 0.5)
    frame[:side_h, :side_w] = level
    return frame


def near(actual, expected, tol=0.6):
    return len(actual) == len(expected) and all(abs(a - b) <= tol for a, b in zip(actual, expected))


# ============================================================
# The check itself
# ============================================================
def test_the_wording_and_the_numbers_are_the_agreed_ones():
    assert VIEW_DARK_TEXT == "Camera is blocked or it is too dark. Obstacle warnings may be missing."
    assert VIEW_BRIGHT_TEXT == "Camera is facing very bright light. Obstacle warnings may be missing."
    assert VIEW_BACK_TEXT == "Camera view is back."
    # "Too bright" was agreed as "mean over 240". This camera's white tops out near 241, so that could never
    # fire (the user's phone-flash test, 2026-10-19): it became "40 % of the view at white (230+)".
    assert (cv.DARK_P95_BELOW, cv.DARK_MEAN_BELOW, cv.WHITE_LEVEL, cv.BRIGHT_WHITE_SHARE) == (40.0, 14.0, 230.0, 0.40)
    assert (cv.VIEW_CONFIRM_CHECKS, cv.VIEW_ALERT_SECONDS, cv.VIEW_CHECK_SECONDS) == (3, 30.0, 1.0)


def test_the_sample_is_tiny_and_grey():
    grey = grey_sample(rgb(100))
    assert grey.shape == (30, 40) and grey.dtype == np.float32      # 1,200 block averages of 307,200 pixels
    assert np.allclose(grey, 100.0, atol=0.01)


def test_each_value_is_the_average_of_its_16_by_16_block():
    frame = np.zeros((H, W), np.uint8)
    frame[:16, :16] = 160                                          # one block, half of it bright...
    frame[:8, :16] = 0
    assert grey_sample(frame)[0, 0] == pytest.approx(80.0, abs=0.6)
    assert grey_sample(frame)[0, 1] == 0.0


def test_block_averages_cancel_the_noise_a_covered_low_light_camera_makes():
    """Gain turned up behind a finger: mean 20, but bright speckles everywhere."""
    noisy = np.clip(RNG.normal(20, 40, (H, W, 3)), 0, 255).astype(np.uint8)
    one_pixel_each = noisy[::16, ::16].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    assert np.percentile(one_pixel_each, 95) > cv.DARK_P95_BELOW, "single pixels would call this a lit scene"
    assert classify(grey_sample(noisy)) == VIEW_DARK


def test_the_numpy_way_gives_the_same_blocks_as_opencv(monkeypatch):
    frame = RNG.integers(0, 256, (H, W, 3), dtype=np.uint8)
    with_cv2 = grey_sample(frame)
    monkeypatch.setattr(cv, "cv2", None)
    assert np.allclose(grey_sample(frame), with_cv2, atol=0.6)     # OpenCV rounds each block to a whole number


def test_odd_sizes_leave_out_the_partial_edge_blocks_and_a_tiny_frame_is_an_error():
    assert grey_sample(np.zeros((442, 639, 3), np.uint8)).shape == (27, 39)
    with pytest.raises(ValueError):
        grey_sample(np.zeros((10, 10, 3), np.uint8))


# The first real footage (the user's screenshots of the cv2 window, 2026-10-19; the picture area, with the
# overlay's divider lines painted over from the next columns). The first rules missed both.
DATA = TESTS_DIR / "data"


def real_frame(name):
    import cv2
    return cv2.cvtColor(cv2.imread(str(DATA / name)), cv2.COLOR_BGR2RGB)


def test_a_finger_over_the_real_camera_is_blocked():
    frame = real_frame("camera-view-finger.png")
    mean, p95, white = view_stats(grey_sample(frame))
    assert classify(grey_sample(frame)) == VIEW_DARK, (mean, p95, white)
    assert p95 < cv.DARK_P95_BELOW - 8, "the margin it was set with: 28 against 40"
    pixels = frame[::16, ::16].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    assert pixels.mean() > 14 and np.percentile(pixels, 95) > 40, "why the first rules missed it: the noise"


def test_a_phone_flash_into_the_real_camera_is_too_bright():
    frame = real_frame("camera-view-phone-flash.png")
    mean, p95, white = view_stats(grey_sample(frame))
    assert classify(grey_sample(frame)) == VIEW_BRIGHT, (mean, p95, white)
    assert white >= cv.BRIGHT_WHITE_SHARE + 0.15, "the margin it was set with: 62 % against 40 %"
    assert grey_sample(frame).max() < 245 and mean < 240, "why the first rule missed it: this camera's white is about 241"


def test_every_check_is_logged_only_when_asked(monkeypatch, capsys):
    watch = CameraViewWatch(AlertScheduler(PriorityMailbox()))
    watch.observe_frame(rgb(120))
    assert "[VIEW] check" not in capsys.readouterr().out
    monkeypatch.setattr(cv, "VIEW_LOG_EVERY_CHECK", True)
    watch.observe_frame(rgb(120))
    assert "[VIEW] check: ok (mean 120.0, 95th percentile 120.0, 0% at white)" in capsys.readouterr().out


def test_the_grey_level_weighs_green_most_as_the_eye_does():
    red, green, blue = (np.zeros((H, W, 3), np.uint8) for _ in range(3))
    red[..., 0], green[..., 1], blue[..., 2] = 255, 255, 255
    assert grey_sample(green).mean() > grey_sample(red).mean() > grey_sample(blue).mean()
    assert grey_sample(red).mean() == pytest.approx(0.299 * 255, abs=0.01)


def test_every_frame_format_hailo_apps_hands_out_is_read():
    """get_numpy_from_buffer: RGB (H, W, 3); YUYV (H, W, 2), brightness first; NV12 (Y, UV); or plain grey."""
    yuyv = np.zeros((H, W, 2), np.uint8)
    yuyv[..., 0], yuyv[..., 1] = 200, 17
    nv12 = (np.full((H, W), 60, np.uint8), np.zeros((H // 2, W // 2, 2), np.uint8))
    assert grey_sample(yuyv).mean() == pytest.approx(200)
    assert grey_sample(nv12).mean() == pytest.approx(60)
    assert grey_sample(np.full((H, W), 33, np.uint8)).mean() == pytest.approx(33)
    with pytest.raises(ValueError):
        grey_sample(np.zeros((H, W, 1, 1), np.uint8))


@pytest.mark.parametrize("name,frame,expected", [
    ("black (lens capped)", rgb(0), VIEW_DARK),
    ("very dark room", rgb(10), VIEW_DARK),
    ("a hand over the lens, a little light leaking", np.clip(RNG.normal(20, 6, (H, W, 3)), 0, 255).astype(np.uint8),
     VIEW_DARK),
    ("a normal room", np.clip(RNG.normal(120, 40, (H, W, 3)), 0, 255).astype(np.uint8), VIEW_OK),
    ("a plain grey wall", rgb(150), VIEW_OK),
    ("a plain white wall in daylight", rgb(220), VIEW_OK),
    ("a dark room with a lamp filling a tenth of the view", with_patch(5, 255, 0.10), VIEW_OK),
    ("a bright window filling a third of the view", with_patch(120, 241, 0.33), VIEW_OK),
    ("a bright window filling more than half of it", with_patch(120, 241, 0.55), VIEW_BRIGHT),
    ("facing a bright light", rgb(250), VIEW_BRIGHT),
    ("pure white (sun in the lens)", rgb(255), VIEW_BRIGHT),
    ("noise", RNG.integers(0, 256, (H, W, 3), dtype=np.uint8), VIEW_OK),
])
def test_synthetic_scenes(name, frame, expected):
    assert classify(grey_sample(frame)) == expected, (name, view_stats(grey_sample(frame)))


def test_a_lamp_too_small_to_light_the_room_still_counts_as_dark():
    """Under 5 % of the view: the 95th percentile stays dark. The models cannot see in that room either."""
    assert classify(grey_sample(with_patch(5, 255, 0.02))) == VIEW_DARK


def test_the_boundaries():
    grey = np.full((30, 40), 40.0, np.float32)                     # 95th percentile exactly 40: not dark
    assert classify(grey) == VIEW_OK
    grey[:, :] = 39.9
    assert classify(grey) == VIEW_DARK
    mostly_black = np.zeros((30, 40), np.float32)                  # the mean rule on its own: p95 stays high
    mostly_black[:3, :] = 140.0                                    # 10 % at 140: mean 14.0, p95 140
    assert view_stats(mostly_black) == pytest.approx((14.0, 140.0, 0.0))
    assert classify(mostly_black) == VIEW_OK
    mostly_black[:3, :] = 139.0                                    # mean 13.9
    assert classify(mostly_black) == VIEW_DARK
    scene = np.full((30, 40), 120.0, np.float32)                   # "too bright" counts blocks at white
    scene[:12, :] = 230.0                                          # 12 rows of 30: exactly 40 %
    assert view_stats(scene)[2] == pytest.approx(0.40) and classify(scene) == VIEW_BRIGHT
    scene[11, :] = 229.9                                           # just under white: 36.7 %
    assert classify(scene) == VIEW_OK


def test_an_empty_sample_is_an_error_not_a_verdict():
    with pytest.raises(ValueError):
        classify(np.zeros((0, 0), np.float32))


def test_one_check_on_a_full_frame_costs_well_under_a_millisecond_of_numpy():
    frame = np.clip(RNG.normal(120, 40, (H, W, 3)), 0, 255).astype(np.uint8)
    n = 200
    start = time.perf_counter()
    for _ in range(n):
        classify(grey_sample(frame))
    each_ms = (time.perf_counter() - start) / n * 1000
    print(f"grey_sample + classify on 640x480: {each_ms:.3f} ms; once a second = {each_ms / 10:.4f} % of one core")
    assert each_ms < 2.0


# ============================================================
# Three in a row, and the alerts (virtual clock)
# ============================================================
def feed(w, watch, kinds, start=0.0, every=1.0):
    for i, kind in enumerate(kinds):
        w.at(start + i * every, lambda kind=kind: watch.observe(kind))


def view_world():
    w = World(duration=3.0)
    return w, CameraViewWatch(w.sched)


def test_three_dark_checks_raise_it_and_it_is_said_at_once_then_every_30_s():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK] * 100)
    w.run(100)
    assert near(w.starts(VIEW_DARK_TEXT), [2, 32, 62, 92]), w.starts(VIEW_DARK_TEXT)
    assert watch.state == VIEW_DARK and w.sched.raised() == [VIEW_DARK_ALERT]


def test_two_dark_checks_are_not_enough():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK, VIEW_DARK, VIEW_OK, VIEW_DARK, VIEW_DARK, VIEW_OK] * 10)
    w.run(70)
    assert w.sim.log == [] and w.sched.raised() == [] and watch.state == VIEW_OK


def test_it_never_flaps_on_a_scene_that_alternates():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK, VIEW_OK] * 30 + [VIEW_BRIGHT, VIEW_OK] * 30)
    w.run(130)
    assert w.sim.log == []


def test_three_good_checks_clear_it_and_say_the_view_is_back_once():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK] * 5 + [VIEW_OK] * 40)
    w.run(50)
    assert near(w.starts(VIEW_DARK_TEXT), [2]) and near(w.starts(VIEW_BACK_TEXT), [7]), w.texts()
    assert w.sched.raised() == [VIEW_BACK_ALERT] and watch.state == VIEW_OK


def test_two_good_checks_do_not_clear_it():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK] * 3 + [VIEW_OK, VIEW_OK, VIEW_DARK] * 5)
    w.run(25)
    assert w.starts(VIEW_BACK_TEXT) == [] and w.sched.is_raised(VIEW_DARK_ALERT)


def test_from_dark_straight_to_bright_says_the_new_sentence_and_not_back():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK] * 4 + [VIEW_BRIGHT] * 10)
    w.run(20)
    assert near(w.starts(VIEW_DARK_TEXT), [2]) and near(w.starts(VIEW_BRIGHT_TEXT), [6]), w.texts()
    assert w.starts(VIEW_BACK_TEXT) == [] and w.sched.raised() == [VIEW_BRIGHT_ALERT]


def test_too_bright_then_back():
    w, watch = view_world()
    feed(w, watch, [VIEW_BRIGHT] * 3 + [VIEW_OK] * 3)
    w.run(10)
    assert w.texts() == [VIEW_BRIGHT_TEXT, VIEW_BACK_TEXT]


def test_dark_and_back_twice_says_back_both_times():
    """The spent "back" sentence is cleared when the next alert is raised, or the second would be silent."""
    w, watch = view_world()
    feed(w, watch, ([VIEW_DARK] * 3 + [VIEW_OK] * 7) * 2)
    w.run(25)
    assert near(w.starts(VIEW_BACK_TEXT), [5, 15]), w.texts()
    assert len(w.starts(VIEW_DARK_TEXT)) == 2


def test_reset_withdraws_its_alerts_without_a_word_and_starts_afresh():
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK] * 3)
    w.at(10.0, watch.reset)
    feed(w, watch, [VIEW_DARK] * 2, start=11.0)       # two after the reset: not enough
    w.run(40)
    assert w.texts() == [VIEW_DARK_TEXT], w.texts()
    assert w.sched.raised() == [] and watch.state == VIEW_OK


def test_the_view_alerts_are_not_a_missing_part():
    """The camera is connected: a blocked view does not hold back "Everything is connected"."""
    assert not {VIEW_DARK_ALERT, VIEW_BRIGHT_ALERT, VIEW_BACK_ALERT} & set(MISSING_ALERTS)
    w, watch = view_world()
    feed(w, watch, [VIEW_DARK] * 40)
    config = SystemConfig()
    w.at(5.0, lambda: glasses_lost(w.sched, config))
    w.at(20.0, lambda: glasses_back(w.sched, None))
    w.run(40)
    assert len(w.starts(EVERYTHING_CONNECTED_TEXT)) == 1, w.texts()


def test_a_scheduler_error_is_retried_at_the_next_check():
    w, watch = view_world()
    real, failed = w.sched.raise_alert, []

    def once(*args, **kwargs):
        if not failed:
            failed.append(1)
            raise RuntimeError("scheduler hiccup")
        return real(*args, **kwargs)

    w.sched.raise_alert = once
    for _ in range(2):
        watch.observe(VIEW_DARK)
    with pytest.raises(RuntimeError):
        watch.observe(VIEW_DARK)
    assert watch.state == VIEW_OK
    watch.observe(VIEW_DARK)
    assert watch.state == VIEW_DARK and w.sched.is_raised(VIEW_DARK_ALERT)


def test_checks_and_resets_from_several_threads_never_break_it():
    alerts = AlertScheduler(PriorityMailbox())
    watch = CameraViewWatch(alerts)
    errors = []

    def hammer(kinds):
        try:
            for i in range(2000):
                watch.observe(kinds[i % len(kinds)])
                if i % 97 == 0:
                    watch.reset()
        except Exception as exc:  # pragma: no cover - the failure being looked for
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(k,)) for k in
               ([VIEW_DARK], [VIEW_BRIGHT], [VIEW_OK, VIEW_DARK], [VIEW_DARK, VIEW_DARK, VIEW_DARK, VIEW_OK])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert errors == [] and watch.state in (VIEW_OK, VIEW_DARK, VIEW_BRIGHT)
    assert len([n for n in alerts.raised() if n in (VIEW_DARK_ALERT, VIEW_BRIGHT_ALERT)]) <= 1


# ============================================================
# The app's frame request, GStreamer replaced by fakes
# ============================================================
REMOVE = "remove"


class FakePad:
    """A src pad: keeps probes, and on fire() hands one buffer to each, removing those that return REMOVE."""

    def __init__(self):
        self.probes = {}
        self.removed = []
        self._next = 0

    def add_probe(self, kind, fn):
        self._next += 1
        self.probes[self._next] = (kind, fn)
        return self._next

    def remove_probe(self, probe_id):
        del self.probes[probe_id]
        self.removed.append(probe_id)

    def fire(self, buffer):
        results = []
        for probe_id, (_kind, fn) in list(self.probes.items()):
            result = fn(self, SimpleNamespace(id=probe_id, get_buffer=lambda: buffer))
            results.append(result)
            if result == REMOVE:
                self.probes.pop(probe_id, None)
        return results


class FakePipeline:
    def __init__(self, with_source=True):
        self.pad = FakePad()
        self._element = SimpleNamespace(get_static_pad=lambda name: self.pad if name == "src" else None)
        self.with_source = with_source

    def get_by_name(self, name):
        return self._element if self.with_source and name == "source_fps_caps" else None


def view_app(app_mod, monkeypatch, mode="both"):
    """(world, app, pipeline): unit_app with a fake pipeline; a "buffer" is the grey level of its frame."""
    w, app = unit_world(app_mod, monkeypatch, mode=mode)
    monkeypatch.setattr(app_mod, "Gst", SimpleNamespace(
        init=lambda *a: None, PadProbeType=SimpleNamespace(BUFFER="buffer"),
        PadProbeReturn=SimpleNamespace(REMOVE=REMOVE, OK="ok")))
    monkeypatch.setattr(app_mod, "get_caps_from_pad", lambda pad: ("RGB", W, H))
    monkeypatch.setattr(app_mod, "get_numpy_from_buffer", lambda buffer, fmt, width, height: rgb(buffer))
    app._view_probe = None
    app._camera_view = None
    app.pipeline = FakePipeline()
    return w, app, app.pipeline


def tick_and_frame(app, level):
    assert app._camera_view_tick() is True
    return app.pipeline.pad.fire(level)


def test_the_view_element_is_the_source_pipelines_last_one(app_mod):
    assert app_mod.CAMERA_VIEW_ELEMENT == "source_fps_caps"


def test_a_tick_asks_for_one_frame_and_never_stacks_requests(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    app._camera_view_tick()
    app._camera_view_tick()                              # no frame came in between
    assert len(pipeline.pad.probes) == 1
    # Left alone, not swapped for a new one: taking a probe off a live pad every second would race
    # with a buffer that is firing it (GLib then warns that the probe id is unknown).
    assert list(pipeline.pad.probes) == [1] and pipeline.pad.removed == []


def test_the_request_takes_one_frame_and_removes_itself(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    assert tick_and_frame(app, 0) == [REMOVE]
    assert pipeline.pad.probes == {} and app._view_probe is None
    assert pipeline.pad.fire(0) == [], "a second frame reached a probe that should be gone"
    tick_and_frame(app, 0)
    assert pipeline.pad.probes == {}


def test_three_dark_frames_raise_the_alert_and_three_good_ones_clear_it(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    for level in (0, 0, 0):
        tick_and_frame(app, level)
    assert w.sched.is_raised(VIEW_DARK_ALERT)
    for level in (120, 120, 120):
        tick_and_frame(app, level)
    assert not w.sched.is_raised(VIEW_DARK_ALERT) and w.sched.is_raised(VIEW_BACK_ALERT)


@pytest.mark.parametrize("camera_state", ["lost", "waiting", "starting"])
def test_nothing_is_asked_for_while_the_camera_is_not_running(app_mod, monkeypatch, camera_state):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    app._camera_state = camera_state
    assert app._camera_view_tick() is True
    assert pipeline.pad.probes == {}


def test_a_frame_that_arrives_after_the_camera_failed_is_not_judged(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    app._camera_view_tick()
    app._camera_state = "lost"
    assert pipeline.pad.fire(0) == [REMOVE]
    assert app._camera_view.state == VIEW_OK and app._camera_view._count == 0


def test_with_both_rockers_off_nothing_is_asked_for_and_the_alert_is_withdrawn(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    for _ in range(3):
        tick_and_frame(app, 0)
    assert w.sched.is_raised(VIEW_DARK_ALERT)
    app.config.update(pipeline_mode="none")
    app._camera_view_tick()
    assert pipeline.pad.probes == {} and w.sched.raised() == []


def test_a_pipeline_without_the_source_element_is_left_alone(app_mod, monkeypatch):
    """The parked placeholder of a start without a camera has no source."""
    w, app, pipeline = view_app(app_mod, monkeypatch)
    app.pipeline = FakePipeline(with_source=False)
    assert app._camera_view_tick() is True
    app.pipeline = None
    assert app._camera_view_tick() is True


def test_a_request_left_on_a_replaced_pipeline_is_taken_off_it(app_mod, monkeypatch):
    """A mode switch rebuilds the pipeline; the old pad's waiting probe goes, the new pad gets one."""
    w, app, old = view_app(app_mod, monkeypatch)
    app._camera_view_tick()
    new = FakePipeline()
    app.pipeline = new
    app._camera_view_tick()
    assert old.pad.probes == {} and old.pad.removed == [1]
    assert len(new.pad.probes) == 1


def test_a_failing_frame_is_logged_and_the_probe_still_goes(app_mod, monkeypatch, caplog):
    w, app, pipeline = view_app(app_mod, monkeypatch)

    def broken(*args):
        raise ValueError("Buffer mapping failed")

    monkeypatch.setattr(app_mod, "get_numpy_from_buffer", broken)
    for _ in range(3):
        assert tick_and_frame(app, 0) == [REMOVE]
    assert w.sched.raised() == [] and app._view_errors == 3


def test_the_error_log_does_not_flood(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    logged = []
    monkeypatch.setattr(app_mod.hailo_logger, "warning", lambda *a, **k: logged.append(a))
    for _ in range(130):
        app._log_view_error("could not check a frame", ValueError("x"))
    assert len(logged) == 3                              # the 1st, the 60th, the 120th


def test_the_timer_ends_when_the_program_stops(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    app._shutting_down = True
    assert app._camera_view_tick() is False
    assert pipeline.pad.probes == {}


def test_without_an_alert_scheduler_nothing_is_asked_for(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    del app.user_data.alerts
    assert app._camera_view_tick() is True and pipeline.pad.probes == {}


def test_losing_the_camera_withdraws_the_view_alert_silently_and_its_own_alert_follows(app_mod, monkeypatch):
    """"Not while the camera is missing": one alert at a time, the camera's."""
    w, app, pipeline = view_app(app_mod, monkeypatch)
    for _ in range(3):
        tick_and_frame(app, 0)
    w.run(5)
    assert w.texts() == [VIEW_DARK_TEXT]
    w.at(w.clock.t, lambda: app._on_camera_lost(Msg()))
    w.run(w.clock.t + 15)
    assert not w.sched.is_raised(VIEW_DARK_ALERT) and VIEW_BACK_TEXT not in w.texts()
    assert w.texts()[-1] == MISSING and app._camera_view.state == VIEW_OK


def test_run_starts_the_view_timer_only_when_there_is_someone_to_tell(app_mod, monkeypatch):
    w, app, pipeline = view_app(app_mod, monkeypatch)
    # raising=False: the Poetry environment's stand-in GStreamerApp (test_pipeline_modes) has no run() of its own
    monkeypatch.setattr(app_mod.GStreamerApp, "run", lambda self: None, raising=False)
    app.run()
    # unit_app's GLib stand-in records each timer as functools.partial(callback): the bound method is .func
    assert [(d, fn.func.__func__) for d, fn in app.glib.timeouts] == [(1000, app_mod.SecondVisionApp._camera_view_tick)]
    app.glib.timeouts.clear()
    del app.user_data.alerts
    app.run()
    assert app.glib.timeouts == []


# ============================================================
# Level gst: a real GStreamer pipeline
# ============================================================
class Stream:
    """videotestsrc (live, 30 fps, RGB 640x480) -> capsfilter "source_fps_caps" -> fakesink, counting frames."""

    def __init__(self, Gst, pattern="black"):
        self.pipeline = Gst.parse_launch(
            f"videotestsrc name=test_src is-live=true pattern={pattern} ! "
            f"video/x-raw,format=RGB,width={W},height={H},framerate=30/1 ! "
            "capsfilter name=source_fps_caps ! fakesink name=sink sync=false signal-handoffs=true")
        self.frames = 0
        self.pipeline.get_by_name("sink").connect("handoff", self._count)
        self.Gst = Gst

    def _count(self, *args):
        self.frames += 1

    def pattern(self, name):
        self.pipeline.get_by_name("test_src").set_property("pattern", name)

    def stop(self):
        self.pipeline.set_state(self.Gst.State.NULL)


def real_app(app_mod, stream):
    app = app_mod.SecondVisionApp.__new__(app_mod.SecondVisionApp)
    app.user_data = SimpleNamespace(alerts=AlertScheduler(PriorityMailbox()))
    app.config = SystemConfig()
    app.pipeline = stream.pipeline
    app._shutting_down = False
    app._camera_state = app_mod.CAMERA_OK
    app._camera_view = None
    app._view_probe = None
    app._view_errors = 0
    return app


@pytest.mark.gst
def test_gst_one_frame_per_tick_the_stream_unharmed_dark_then_bright_then_back(app_mod):
    Gst, GLib = gst_helpers.load_gst()
    stream = Stream(Gst)
    app = real_app(app_mod, stream)
    costs, ticks = [], [0]
    real_on = app._on_view_buffer

    def timed(pad, info):
        began = time.perf_counter()
        result = real_on(pad, info)
        costs.append((time.perf_counter() - began) * 1000)
        return result

    def tick():
        ticks[0] += 1
        return app._camera_view_tick()

    app._on_view_buffer = timed
    alerts = app.user_data.alerts
    source = GLib.timeout_add(250, tick)            # four times faster than the real 1 s, same code
    try:
        stream.pipeline.set_state(Gst.State.PLAYING)
        began = time.monotonic()
        assert gst_helpers.run_main_loop(lambda: alerts.is_raised(VIEW_DARK_ALERT), seconds=5), alerts.raised()
        stream.pattern("white")
        assert gst_helpers.run_main_loop(lambda: alerts.is_raised(VIEW_BRIGHT_ALERT), seconds=5), alerts.raised()
        assert not alerts.is_raised(VIEW_DARK_ALERT)
        stream.pattern("smpte")
        assert gst_helpers.run_main_loop(lambda: alerts.is_raised(VIEW_BACK_ALERT), seconds=5), alerts.raised()
        elapsed = time.monotonic() - began
    finally:
        GLib.source_remove(source)
        stream.stop()
    fps = stream.frames / elapsed
    print(f"{ticks[0]} ticks, {len(costs)} frames checked, {stream.frames} frames through in {elapsed:.1f} s "
          f"({fps:.1f} fps); one check: median {sorted(costs)[len(costs) // 2]:.2f} ms, max {max(costs):.2f} ms")
    assert ticks[0] - 1 <= len(costs) <= ticks[0], "one frame per tick: no stacked probes, none lost"
    assert fps >= 27, "the stream slowed down"
    assert app._view_errors == 0


@pytest.mark.gst
def test_gst_a_check_costs_far_less_than_one_percent_of_a_core_at_one_a_second(app_mod):
    """The extra CPU limit (URGENT-TESTS.md): under 1 % of one core. One check a second must take under 10 ms."""
    Gst, GLib = gst_helpers.load_gst()
    stream = Stream(Gst, pattern="smpte")
    app = real_app(app_mod, stream)
    costs = []
    real_on = app._on_view_buffer

    def timed(pad, info):
        began = time.perf_counter()
        result = real_on(pad, info)
        costs.append((time.perf_counter() - began) * 1000)
        return result

    app._on_view_buffer = timed
    source = GLib.timeout_add(100, app._camera_view_tick)
    try:
        stream.pipeline.set_state(Gst.State.PLAYING)
        assert gst_helpers.run_main_loop(lambda: len(costs) >= 30, seconds=10)
    finally:
        GLib.source_remove(source)
        stream.stop()
    median = sorted(costs)[len(costs) // 2]
    print(f"one check on a real 640x480 RGB buffer: median {median:.2f} ms, max {max(costs):.2f} ms "
          f"= {median / 10:.3f} % of one core at one a second")
    assert median < 10.0


@pytest.mark.gst
def test_gst_a_paused_stream_gets_no_stacked_requests_and_a_rebuild_takes_the_request_off(app_mod):
    """No frames flow while PAUSED: ticks must not pile probes up; a replaced pipeline's probe is taken off."""
    Gst, GLib = gst_helpers.load_gst()
    first, second = Stream(Gst), Stream(Gst)
    app = real_app(app_mod, first)
    try:
        first.pipeline.set_state(Gst.State.PAUSED)
        for _ in range(5):
            app._camera_view_tick()
        pad, probe_id = app._view_probe
        assert pad is first.pipeline.get_by_name("source_fps_caps").get_static_pad("src")
        app.pipeline = second.pipeline
        app._camera_view_tick()
        assert app._view_probe[0] is second.pipeline.get_by_name("source_fps_caps").get_static_pad("src")
        second.pipeline.set_state(Gst.State.PLAYING)
        assert gst_helpers.run_main_loop(lambda: app._view_probe is None, seconds=3)
        assert app._camera_view.state == VIEW_OK and app._camera_view._count == 1   # one black frame seen
    finally:
        first.stop()
        second.stop()
