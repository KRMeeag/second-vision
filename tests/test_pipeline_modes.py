"""
Unit tests for mode-aware pipeline construction (detection / depth / both).

The point of these tests is the guarantee that mode switching did NOT change the
normal run: `_build_dual()` must still produce the same dual-branch pipeline it
always did, and the single-mode pipelines must reuse the *same* branch fragments
rather than diverging copies.

Hardware-independent. `gi`, `hailo`, and the two hailo_apps modules that need
them are stubbed below (the mocked-hailo harness this project already uses for
callback logic); everything else — including the real INFERENCE_PIPELINE /
TRACKER_PIPELINE / QUEUE helpers that actually compose the strings — is imported
for real, so these assert against genuine GStreamer output. Run with:

    poetry run pytest tests/test_pipeline_modes.py -v
"""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ============================================================
# Mocked-hailo harness — stub only what genuinely needs the GStreamer runtime.
# The pipeline-string helpers are NOT stubbed; they are the code under test.
# ============================================================
def _install_hailo_stubs():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args, **kwargs: None
    repository = types.ModuleType("gi.repository")
    repository.Gst = SimpleNamespace(init=lambda *a: None)
    repository.GLib = SimpleNamespace(
        idle_add=lambda *a, **k: None,
        timeout_add=lambda *a, **k: None,
        timeout_add_seconds=lambda *a, **k: None,
    )
    gi.repository = repository
    sys.modules.setdefault("gi", gi)
    sys.modules.setdefault("gi.repository", repository)

    sys.modules.setdefault("hailo", types.ModuleType("hailo"))

    buffer_utils = types.ModuleType("hailo_apps.python.core.common.buffer_utils")
    buffer_utils.get_caps_from_pad = lambda pad: (None, None, None)
    buffer_utils.get_numpy_from_buffer = lambda *a: None
    sys.modules.setdefault("hailo_apps.python.core.common.buffer_utils", buffer_utils)

    gstreamer_app = types.ModuleType("hailo_apps.python.core.gstreamer.gstreamer_app")

    class _AppCallbackClass:
        def __init__(self):
            self.frame_count = 0
            self.use_frame = False
            self.running = True

        def increment(self):
            self.frame_count += 1

        def get_count(self):
            return self.frame_count

        def set_frame(self, frame):
            pass

    class _GStreamerApp:
        """Stand-in base. The builders under test never call up into it."""

    gstreamer_app.app_callback_class = _AppCallbackClass
    gstreamer_app.GStreamerApp = _GStreamerApp
    gstreamer_app._internal_callback_wrapper = lambda *a, **k: None
    sys.modules.setdefault("hailo_apps.python.core.gstreamer.gstreamer_app", gstreamer_app)


_install_hailo_stubs()

from second_vision.pipeline import app as app_mod  # noqa: E402


# ============================================================
# Fixtures
# ============================================================
class _FakeConfig:
    """Minimal stand-in for SystemConfig — only pipeline_mode is read here."""

    def __init__(self, mode):
        self._mode = mode

    def get(self, key):
        return self._mode if key == "pipeline_mode" else None


def _fake_app(mode="both", config=...):
    """
    A real SecondVisionApp instance with __init__ skipped.

    SecondVisionApp.__init__ parses argv and creates a live GStreamer pipeline, so
    it cannot be constructed under pytest. __new__ gives a genuine instance — real
    methods, real dispatch — onto which only the attributes the builders read are
    set. `get_source_pipeline` is shadowed with a stub since it belongs to the
    GStreamer base class.
    """
    app = app_mod.SecondVisionApp.__new__(app_mod.SecondVisionApp)
    app.depth_hef_path = "/fake/models/scdepthv3.hef"
    app.depth_post_process_so = "/fake/so/libdepth_postprocess.so"
    app.depth_post_function_name = "filter_scdepth"
    app.det_hef_path = "/fake/models/yolov8s.hef"
    app.det_post_process_so = "/fake/so/libyolo_hailortpp_postprocess.so"
    app.det_post_function_name = "filter_letterbox"
    app.batch_size = 2
    app.labels_json = None
    app.thresholds_str = "nms-score-threshold=0.3 nms-iou-threshold=0.45"
    app.config = _FakeConfig(mode) if config is ... else config
    app.get_source_pipeline = lambda **kwargs: "FAKE_SOURCE"
    app.video_width = 640
    app.video_height = 640
    return app


def _dual(app=None):
    return (app or _fake_app())._build_dual()


def _detection_only(app=None):
    return (app or _fake_app())._build_detection_only()


def _depth_only(app=None):
    return (app or _fake_app())._build_depth_only()


# ============================================================
# The dual pipeline is unchanged — normal runs are unaffected
# ============================================================
def test_dual_pipeline_has_tee_and_both_branches():
    pipeline = _dual()

    assert "tee name=t" in pipeline
    assert "identity name=det_callback" in pipeline
    assert "identity name=depth_callback" in pipeline
    assert "hailotracker" in pipeline
    assert "inference_wrapper_depth" in pipeline
    assert "inference_wrapper_det" in pipeline
    assert "fakesink name=depth_sink" in pipeline
    assert "fakesink name=det_sink" in pipeline


def test_dual_pipeline_keeps_leaky_branch_queues():
    # The leaky branch queues decouple the two parallel branches; losing them
    # would let a slow branch stall the other.
    pipeline = _dual()
    assert "queue name=depth_branch_q leaky=downstream" in pipeline
    assert "queue name=det_branch_q leaky=downstream" in pipeline


def test_depth_branch_disables_letterbox_in_every_mode():
    # The depth wrapper must not letterbox — it would distort the depth map.
    for pipeline in (_dual(), _depth_only()):
        assert "use-letterbox=false" in pipeline
        assert "use-letterbox=true" not in pipeline.split("inference_wrapper_det")[0]


# ============================================================
# Single-mode pipelines: one model, no tee
# ============================================================
def test_dual_loads_two_networks_single_modes_load_one():
    # "hailonet name=" is the element declaration itself — plain "hailonet" also
    # matches the queue feeding it (`..._hailonet_q`), which would overcount.
    assert _dual().count("hailonet name=") == 2
    assert _detection_only().count("hailonet name=") == 1
    assert _depth_only().count("hailonet name=") == 1


def test_single_modes_have_no_tee():
    assert "tee name=t" not in _detection_only()
    assert "tee name=t" not in _depth_only()


def test_detection_only_omits_the_depth_branch():
    pipeline = _detection_only()
    assert "identity name=det_callback" in pipeline
    assert "hailotracker" in pipeline
    assert "identity name=depth_callback" not in pipeline
    assert "depth_inference" not in pipeline


def test_depth_only_omits_the_detection_branch():
    pipeline = _depth_only()
    assert "identity name=depth_callback" in pipeline
    assert "identity name=det_callback" not in pipeline
    assert "hailotracker" not in pipeline
    assert "det_inference" not in pipeline


def test_single_modes_reuse_the_dual_branch_fragments():
    """
    A single-mode pipeline must be the *same* branch the dual pipeline uses, so
    tuning (tracker params, letterbox, thresholds) can never drift between modes.
    """
    app = _fake_app()
    dual = _dual(app)

    wrapper, tracker, callback, _sink = app._detection_branch()
    assert wrapper in dual and tracker in dual and callback in dual
    assert wrapper in _detection_only(app)

    depth_wrapper, depth_callback, _depth_sink = app._depth_branch()
    assert depth_wrapper in dual and depth_callback in dual
    assert depth_wrapper in _depth_only(app)


# ============================================================
# Mode dispatch and its fallbacks
# ============================================================
def test_dispatch_selects_the_matching_builder():
    both = _fake_app("both")
    assert both.get_pipeline_string() == _dual(both)

    det = _fake_app("detection")
    assert det.get_pipeline_string() == _detection_only(det)

    depth = _fake_app("depth")
    assert depth.get_pipeline_string() == _depth_only(depth)


def test_missing_config_falls_back_to_dual():
    # app.py's own main() and main2.py both construct SecondVisionApp without a
    # config; that must not leave the device with no pipeline.
    app = _fake_app(config=None)
    assert app.current_mode() == app_mod.MODE_BOTH
    assert app.get_pipeline_string() == _dual(app)


def test_unknown_mode_falls_back_to_dual():
    app = _fake_app("sideways")
    assert app.current_mode() == app_mod.MODE_BOTH
    assert app.get_pipeline_string() == _dual(app)


def test_none_mode_falls_back_to_dual():
    app = _fake_app(None)
    assert app.current_mode() == app_mod.MODE_BOTH


# ============================================================
# Each mode exposes exactly the callback identities _connect_callback() wires
# ============================================================
class _RecordingUserData:
    """Captures set_frame() calls the way the display process would consume them."""

    def __init__(self):
        self.frames = []

    def set_frame(self, frame):
        self.frames.append(frame)


def _banner_app(mode, use_frame):
    app = _fake_app(mode)
    app.user_data = _RecordingUserData()
    app.options_menu = SimpleNamespace(use_frame=use_frame)
    return app


def test_debug_banner_only_in_depth_mode():
    # Detection and both modes draw a live overlay from the detection callback,
    # so a banner would overwrite real video.
    for mode in (app_mod.MODE_BOTH, app_mod.MODE_DETECTION):
        app = _banner_app(mode, use_frame=True)
        app._push_debug_banner(mode)
        assert app.user_data.frames == [], mode

    app = _banner_app(app_mod.MODE_DEPTH, use_frame=True)
    app._push_debug_banner(app_mod.MODE_DEPTH)
    assert len(app.user_data.frames) == 1


def test_debug_banner_frame_matches_video_dimensions():
    app = _banner_app(app_mod.MODE_DEPTH, use_frame=True)
    app._push_debug_banner(app_mod.MODE_DEPTH)
    frame = app.user_data.frames[0]
    assert frame.shape == (app.video_height, app.video_width, 3)
    assert frame.any(), "banner text should have been drawn onto the canvas"


def test_debug_banner_skipped_without_use_frame():
    # No --use-frame means no display process is running to consume it.
    app = _banner_app(app_mod.MODE_DEPTH, use_frame=False)
    app._push_debug_banner(app_mod.MODE_DEPTH)
    assert app.user_data.frames == []


def test_each_mode_exposes_only_its_own_callback_identities():
    expected = {
        app_mod.MODE_BOTH: (True, True),
        app_mod.MODE_DETECTION: (True, False),
        app_mod.MODE_DEPTH: (False, True),
    }
    for mode, (has_det, has_depth) in expected.items():
        pipeline = _fake_app(mode).get_pipeline_string()
        assert ("identity name=det_callback" in pipeline) is has_det, mode
        assert ("identity name=depth_callback" in pipeline) is has_depth, mode
