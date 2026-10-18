import gc
import os
import re
import subprocess
import queue
import sys
import threading
import time
from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import cv2
import gi
import numpy as np
import setproctitle

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

from hailo_apps.python.core.common.core import (
    get_pipeline_parser,
    get_resource_path,
    handle_list_models_flag,
    resolve_hef_path,
)
from hailo_apps.python.core.common.defines import (
    DEPTH_APP_TITLE,
    DEPTH_PIPELINE,
    DEPTH_POSTPROCESS_FUNCTION,
    DEPTH_POSTPROCESS_SO_FILENAME,
    RESOURCES_SO_DIR_NAME,
    RESOURCES_VIDEOS_DIR_NAME,
    DETECTION_APP_TITLE,
    DETECTION_PIPELINE,
    DETECTION_POSTPROCESS_FUNCTION,
    DETECTION_POSTPROCESS_SO_FILENAME,
)
from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
from hailo_apps.python.core.common.hef_utils import get_hef_labels_json
from hailo_apps.python.core.common.camera_utils import get_usb_video_devices

from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import (
    GStreamerApp,
    _internal_callback_wrapper,
)
from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import (
    INFERENCE_PIPELINE,
    INFERENCE_PIPELINE_WRAPPER,
    USER_CALLBACK_PIPELINE,
    TRACKER_PIPELINE,
    QUEUE,
)

# callbacks.py is a sibling module, not an installed package — make sure it
# resolves whether app.py is run directly or imported from elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# callbacks.py (and StandaloneUserData below) import second_vision.core.* — put
# src/ on the path too so those absolute imports resolve when app.py is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import callbacks
from second_vision.core.camera_view import VIEW_CHECK_SECONDS, CameraViewWatch
from second_vision.core.haptic_patterns import PATTERN_CAMERA
from second_vision.core.priority import PriorityMailbox
from second_vision.core.status_view import status_canvas

hailo_logger = get_logger(__name__)

# Pipeline modes. These are the values SystemConfig.pipeline_mode takes and the ones the
# Arduino sends as "M:<mode>" (see workers/config_reader.py).
MODE_BOTH = "both"
MODE_DETECTION = "detection"
MODE_DEPTH = "depth"

# ONE frame-rate cap for the whole pipeline, applied to the camera stream
# BEFORE the tee, so the depth and detection branches receive the same frames
# at the same rate in every mode. It used to sit on the depth branch alone
# (DEPTH_MAX_FPS = 20) while detection ran at the camera rate — two algorithms
# at two rates, which is the "FPS mismatch" this removes. Both branches now see
# identical buffers with identical timestamps; what each branch DROPS under
# load is still its own business (the leaky branch queues), which is why the
# [DEPTH] log prints both branches' FPS side by side.
#
# 30 by team decision (2026-09-19): higher frame rate. Overridable at launch
# with SV_FPS=<n> because the 30-vs-20 history is not settled, all measured on
# the device:
#   - fed at the camera rate (30) on an idle Pi, the depth branch managed 29.7
#     FPS and latency was 87-108 ms — but only because it happened to keep up;
#   - the same setting two days later with a person in frame (tracker + TTS +
#     overlay loading the CPU): the branch slipped to 27.2 FPS against a 28.6
#     FPS camera, every buffer between them filled, and latency was 1.0-1.2 s;
#   - fed at 15 FPS: 122-202 ms; at 20: ~120-200 ms, regardless of load.
# A branch running at exactly its input rate sits on a knife edge. Read the
# [DEPTH] "lat" figure during every rehearsal: the callback warns when it
# passes LATENCY_WARN_MS (callbacks.py), and the answer to that warning is
# `SV_FPS=20 ./scripts/sv-main.sh`, not a code change. Motors (50 ms spin-up,
# 4 levels, 0.18 s taps) cannot use more than ~20 updates/s either way.
PIPELINE_FPS_DEFAULT = 30


def pipeline_fps() -> int:
    """The shared cap: SV_FPS from the environment, else PIPELINE_FPS_DEFAULT."""
    try:
        return max(1, int(os.environ.get("SV_FPS", PIPELINE_FPS_DEFAULT)))
    except (TypeError, ValueError):
        return PIPELINE_FPS_DEFAULT
# Both rockers off. Neither speech nor motors are reachable, so running either
# model would burn Hailo and battery producing output nobody receives. This is
# "run nothing", NOT "run both and mute" — see PROTOCOL.md, mode truth table.
MODE_NONE = "none"
VALID_MODES = (MODE_BOTH, MODE_DETECTION, MODE_DEPTH, MODE_NONE)

# A mode swap is considered healthy if the new pipeline delivers its first frame within
# this long. Not enforced — exceeding it is logged as SLOW so it gets reported, since the
# cost of unloading/reloading a HEF on the Hailo device is the one genuinely unknown
# quantity in the swap.
SWAP_BUDGET_SECONDS = 3.0

# How often the --cycle-modes heartbeat reports frame flow / refreshes the debug banner.
HEARTBEAT_SECONDS = 2

# Give up waiting for the first frame of a swapped-in pipeline after this long, so a
# genuinely stalled swap is reported instead of polling forever.
_SWAP_POLL_TIMEOUT_SECONDS = 15.0

# Floor on how often the pipeline may be torn down and rebuilt. A rebuild takes roughly
# 0.4-1.1s (measured on the Pi 5 + Hailo-8), and tearing the v4l2 source down again while
# the previous teardown is still settling crashes GStreamer inside gst_object_unref.
# Requests arriving sooner are deferred, not dropped: trigger_rebuild() coalesces them and
# _rebuild_pipeline() re-checks the mode afterwards, so the pipeline always converges on
# the most recently requested mode.
MIN_REBUILD_INTERVAL_SECONDS = 2.0

# How long to wait for the old pipeline to reach NULL before giving up on a
# clean teardown. Generous: the alternative is losing the Hailo device.
TEARDOWN_TIMEOUT_SECONDS = 5

# After the old pipeline is unreferenced, how long to let the driver actually
# release the vdevice. Freeing is not synchronous with the last Python
# reference dropping.
DEVICE_SETTLE_SECONDS = 1.0

# How long a shutdown may spend tearing the pipeline down before the process
# exits anyway. A clean shutdown measures ~0.7 s. See shutdown().
SHUTDOWN_FORCE_EXIT_SECONDS = 5.0

# ---- Camera removed while running (REQ-CON-08, removal half) ----
# The camera element. SOURCE_PIPELINE names its v4l2src "source"
# (hailo_apps gstreamer_helper_pipelines.SOURCE_PIPELINE, name="source"), and
# an ERROR's message.src is the element that failed — so a failure is a camera
# failure when it comes from the element of that name at the top of the live
# pipeline. Never decided from the error text.
CAMERA_SOURCE_NAME = "source"

# Only a USB (v4l2) camera is watched for and restarted. get_source_type()
# says "usb" for every /dev/video* input, which is what the boot launcher
# passes (sv-main.sh: --input /dev/video0). Any other source (a file, rpi,
# rtsp ...) keeps the library's behaviour exactly.
CAMERA_SOURCE_TYPE = "usb"

# How often to look for a USB camera while none is running. One look is
# hailo_apps' get_usb_video_devices(): one `udevadm info` per /dev/video*
# node, ~0.1 s on this Pi (21 nodes, measured 2026-10-16), run on the camera
# watcher thread so the GLib loop never waits for it. Every 2 s costs ~5 % of
# one core only while the camera is missing, finds a re-plugged camera within
# ~2 s, and is never shorter than MIN_REBUILD_INTERVAL_SECONDS, so restart
# attempts on a camera that is present but failing stay spaced out.
CAMERA_POLL_SECONDS = 2.0

# If tearing the dead pipeline down after a camera loss takes longer than this,
# the motors are zeroed from a timer thread and the delay is logged. A normal
# teardown is ~0.5 s plus DEVICE_SETTLE_SECONDS. This is an alarm, not a
# timeout: GStreamer cannot interrupt a synchronous state change.
CAMERA_TEARDOWN_ALARM_SECONDS = 3.0

# While no camera is found, one INFO line this often says the program is still
# looking (each look is logged at DEBUG only). Not every look: the journal is
# already flooded by the Hailo driver's warnings.
CAMERA_STILL_LOOKING_LOG_SECONDS = 60.0

# ---- Camera status in speech (REQ-CON-07 spoken half, REQ-CON-09) ----
# Two alerts on the alert scheduler (core/alerts.py, user_data.alerts). While no camera is
# there the device says CAMERA_MISSING_TEXT at once and again every CAMERA_MISSING_SECONDS
# (the interval counts from the START of each utterance); when the vision pipeline runs on a
# camera again that alert is cleared and CAMERA_BACK_TEXT is said once. The names key the
# alerts: the "back" alert is once-only, and the scheduler keeps a spent once-alert raised
# until it is cleared, so it is cleared on every loss (_raise_camera_missing) or the second
# return would be silent. Like every scheduler alert, both are failsafe alerts (D41): spoken
# even with the speech switch off, at max(volume knob, 30 %). The scheduler and the speech worker
# do that; nothing here.
#
# "Running" is not yet "back": set_state(PLAYING) of a live camera returns before the camera
# has streamed, and a camera that another process holds fails a few milliseconds later
# ("Device '/dev/video0' is busy", measured on the device 2026-10-18), as does one that
# vanishes again in the first moments. Both reach the bus as a new camera loss. So when the
# pipeline runs the missing sentence only stops repeating, and the return is confirmed, and
# "Camera has been reconnected" said, CAMERA_BACK_CONFIRM_SECONDS later if no loss came in
# between (_announce_camera_back, _confirm_camera_back). A loss in the window leaves the
# missing alert on its own cadence, and nothing about reconnecting is said.
CAMERA_ALERT_MISSING = "camera-missing"
CAMERA_MISSING_TEXT = "Critical Error: Camera cannot be reached"
CAMERA_MISSING_SECONDS = 10.0
CAMERA_ALERT_BACK = "camera-reconnected"
CAMERA_BACK_TEXT = "Camera has been reconnected"
CAMERA_BACK_CONFIRM_SECONDS = 1.0

# ---- Camera view check: blocked, too dark, too bright (F5; core/camera_view.py) ----
# Where the frame is taken: the src pad of the last element of hailo_apps' SOURCE_PIPELINE, its
# frame-rate capsfilter (gstreamer_helper_pipelines.SOURCE_PIPELINE: `capsfilter name={name}_fps_caps`,
# name "source"). Every mode passes through it (_build_dual, _build_detection_only, _build_depth_only,
# _build_idle) before any inference, and its caps are video/x-raw, format=RGB, at the app's width and
# height (hailo_apps' get_caps_from_pad / get_numpy_from_buffer read them, as their docs show).
CAMERA_VIEW_ELEMENT = f"{CAMERA_SOURCE_NAME}_fps_caps"

# Camera states. Written only on the GLib main loop; other threads only read.
CAMERA_OK = "ok"              # a pipeline is running on a camera
CAMERA_LOST = "lost"          # the camera failed; its pipeline is not torn down yet
CAMERA_WAITING = "waiting"    # torn down, Hailo released; looking for a camera
CAMERA_STARTING = "starting"  # a camera was found; a pipeline is being started on it

# What SecondVisionApp._launch_on_camera() reports.
START_RUNNING = "running"     # the new pipeline is PLAYING on the camera
START_NO_CAMERA = "camera"    # the camera could not be opened or started (or building raised)
START_FAILED = "other"        # an element that is not the camera failed: today's error path
START_STOPPED = "stopped"     # shutdown began during the start; nothing more is done

# serial_queue's contract dict (AGENTS.md Rule 2) with every motor off. The
# glasses board holds its last command, so silence is not "off".
MOTORS_OFF = {"left": 0, "center": 0, "right": 0, "hazard": False, "hazard_severity": 0}

# The team's custom-trained detector (14 classes, NMS on-chip, hailo8). Absolute,
# built from this file's location, because resolve_hef_path() resolves a
# relative path against the CWD — fine under run.sh, wrong anywhere else.
# hef_deploy is the runtime build; hef_eval is the same network compiled with a
# 0.001 NMS score threshold for mAP evaluation. At runtime the difference is
# moot: hailonet's nms-score-threshold (thresholds_str below) overrides the
# threshold baked into either HEF.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DET_HEF_PATH = REPO_ROOT / "models" / "hef_deploy" / "best.hef"

# Sidecar label file looked for next to the detection HEF. The yolo_hailortpp
# postprocess falls back to the 80 COCO names when it gets no labels JSON, which
# for a custom HEF means class 1 ("Vehicle") is emitted as "bicycle", class 3
# ("Pole") as "motorcycle", and so on — and get_hef_labels_json() only knows
# hailo's own HEF names (barcode, visdrone, 4_classes), not "best.hef".
DET_LABELS_SIDECAR = "labels.json"


def _hailort_service_available():
    """
    True when HailoRT's daemon is running and can own the device for us.

    With multi-process-service the hailort_service process holds the physical
    Hailo permanently and our pipeline connects as a client. Rebuilds then never
    acquire or release hardware at all.

    Without it, each hailonet takes the device exclusively — and on a mode
    switch the outgoing pipeline had not let go before the incoming one asked:

        Failed to create vdevice. there are not enough free devices.
        requested: 1, found: 0   ->  HAILO_OUT_OF_PHYSICAL_DEVICES(74)  ->  segfault

    Checked at runtime rather than assumed: the property is only valid while the
    service is active, so on a machine without it we must fall back to exclusive
    ownership rather than build a pipeline that cannot start.
    """
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", "hailort.service"],
            timeout=2,
        ).returncode == 0
    except Exception:      # no systemd, no systemctl, timeout — assume not
        return False


# Depth of the inference wrapper's BYPASS queue on the DEPTH branch: the
# original frames parked while their crop goes through hailonet, re-joined at
# the aggregator. hailo_apps sizes it at 20, and that is where the 1.0-1.2 s
# depth latency lived — _low_latency_queues() only shrinks the 3-buffer queues,
# so this one stayed at 20 and, once the branch fell a hair behind the camera,
# filled up: 20 frames at 30 FPS is 0.67 s, plus hailonet's own in-flight
# frames. Measured on 2026-09-21 with the desktop preview on: p50 1103 ms.
# Four keeps cropper and hailonet pipelined and bounds the backlog to ~130 ms.
#
# Short, not leaky: a dropped frame here leaves the aggregator waiting forever
# for a crop that never arrives. The drop happens once, upstream, in the leaky
# depth_branch_q, and once more at the tail in depth_callback_q (see there).
# The detection branch keeps hailo_apps' default 20; it is not touched.
DEPTH_BYPASS_QUEUE_BUFFERS = 4

# hailonet scheduler-priority for the DEPTH model (0-31, hailonet default 16).
# With hailort_service owning the device both hailonets are clients of ONE
# scheduler, and it is the depth model that loses device time (depth 22-24 FPS
# while detection holds 29). Raising depth's priority reclaims time the
# scheduler was leaving idle between model switches, not time detection was
# using: detection's rate did not move when this was measured (2026-09-20).
DEPTH_SCHEDULER_PRIORITY = 31


def _low_latency_queues(fragment: str) -> str:
    """
    Shrink every default 3-buffer queue in a pipeline fragment to 1 buffer.

    hailo_apps' helpers put `leaky=no max-size-buffers=3` between every pair of
    elements. When the branch is throughput-bound each of those holds 3 stale
    frames, and there are ~9 of them between the tee and the callback: that is
    where the seconds of delay live. One buffer per queue is all the decoupling
    a linear branch needs. Deliberately NOT made leaky: inside the cropper ->
    aggregator pair a dropped crop leaves the aggregator waiting forever for a
    result that never arrives; backpressure is safe, dropping there is not. The
    dropping happens once, upstream, in the leaky rate-limit queue.
    """
    return fragment.replace("leaky=no max-size-buffers=3", "leaky=no max-size-buffers=1")


def _rate_cap(fps: int) -> str:
    """
    The shared frame-rate cap, placed once on the camera stream before it
    fans out. drop-only: never duplicate a frame to pad the rate, only discard
    to reduce it. The 1-buffer leaky queue in front takes the drop when
    videorate is momentarily blocked, so the source is never held up.
    """
    return (
        f"{QUEUE(name='rate_q', max_size_buffers=1, leaky='downstream')} ! "
        f"videorate name=rate_videorate drop-only=true ! "
        f"video/x-raw, framerate={fps}/1"
    )


class SecondVisionApp(GStreamerApp):
    def __init__(self, app_callback, user_data, config=None, parser=None):
        if parser is None:
            parser = get_pipeline_parser()

        parser.add_argument(
            "--labels-json",
            default=None,
            help="Path to custom labels JSON file",
        )

        parser.add_argument(
            "--det-hef-path",
            default=str(DEFAULT_DET_HEF_PATH),
            help="Specific HEF model to use for detection "
                 "(default: models/hef_deploy/best.hef, the custom 14-class model)",
        )

        # DEBUG ONLY — temporary. Cycles pipeline_mode on a timer so mode switching can be
        # exercised before the Arduino control panel exists. Omit the flag and nothing
        # changes: no cycler thread, mode stays "both". See mock/mode_cycler.py.
        parser.add_argument(
            "--cycle-modes",
            nargs="?",
            type=float,
            const=10.0,
            default=0.0,
            metavar="SECONDS",
            help="DEBUG: cycle both->depth->detection every SECONDS "
                 "(bare flag = 10s, omitted = disabled)",
        )

        # Must come AFTER every add_argument above: handle_list_models_flag uses
        # parse_known_args internally, so arguments registered after it never appear in
        # --help (hailo-apps .hailo/memory/common_pitfalls.md).
        handle_list_models_flag(parser, DEPTH_PIPELINE)
        handle_list_models_flag(parser, DETECTION_PIPELINE)

        hailo_logger.info("Initializing Parallel Depth & Detection App V4...")

        super().__init__(parser, user_data)

        # Adjust dimensions for detection defaults
        if self.video_width == 1280:
            self.video_width = 640
        if self.video_height == 720:
            self.video_height = 640

        # Adjust batch size for detection defaults
        if self.batch_size == 1:
            self.batch_size = 2

        self.app_callback = app_callback
        self.config = config
        setproctitle.setproctitle("Parallel-Depth-Detection-V4")

        # Let the HailoRT daemon own the physical device when it is available.
        # Decided once, at construction: every rebuilt pipeline must agree, and
        # re-probing per rebuild would let the answer change underneath a
        # half-built pipeline. See _hailort_service_available().
        self._multi_process_service = _hailort_service_available()
        hailo_logger.info(
            "Hailo device ownership: %s",
            "hailort_service (survives pipeline rebuilds)"
            if self._multi_process_service
            else "exclusive, in-process — mode switches will contend for the device",
        )

        # Set once shutdown begins, so a rebuild already queued on the idle loop can't
        # resurrect a torn-down pipeline while the app is exiting. See shutdown().
        self._shutting_down = False

        # Rebuild serialization. _built_mode is the mode the live pipeline was
        # actually built for, which is how a rebuild detects that the requested
        # mode moved on while it was running.
        self._rebuild_in_flight = False
        self._last_rebuild_at = 0.0
        self._built_mode = None

        # Swap timing state (only meaningful once a rebuild has been requested).
        self._swap_started_at = None
        self._swap_frame_baseline = 0
        self._swap_target_mode = None
        self._hb_last_count = 0

        # ---- Depth App Parameters ----
        self.depth_hef_path = resolve_hef_path(
            self.hef_path, app_name=DEPTH_PIPELINE, arch=self.arch
        )
        self.depth_post_process_so = get_resource_path(
            DEPTH_PIPELINE, RESOURCES_SO_DIR_NAME, self.arch, DEPTH_POSTPROCESS_SO_FILENAME
        )
        self.depth_post_function_name = DEPTH_POSTPROCESS_FUNCTION

        # ---- Detection Parameters ----
        self.det_hef_path = resolve_hef_path(
            self.options_menu.det_hef_path, app_name=DETECTION_PIPELINE, arch=self.arch
        )
        self.det_post_process_so = get_resource_path(
            DETECTION_PIPELINE, RESOURCES_SO_DIR_NAME, self.arch, DETECTION_POSTPROCESS_SO_FILENAME
        )
        self.det_post_function_name = DETECTION_POSTPROCESS_FUNCTION

        self.labels_json = self.options_menu.labels_json
        if self.labels_json is None and self.det_hef_path is not None:
            # A labels.json shipped beside the HEF wins — see DET_LABELS_SIDECAR.
            sidecar = Path(self.det_hef_path).with_name(DET_LABELS_SIDECAR)
            if sidecar.exists():
                self.labels_json = str(sidecar)
                hailo_logger.info("Using labels JSON beside the HEF: %s", self.labels_json)
        if self.labels_json is None: # if no labels JSON file is provided, try auto-detect it from the HEF file
            self.labels_json = get_hef_labels_json(self.det_hef_path)
            if self.labels_json is not None:
                hailo_logger.info("Auto detected Labels JSON: %s", self.labels_json)
            else:
                hailo_logger.warning(
                    "No labels JSON for %s — the postprocess will emit COCO names. "
                    "callbacks.DETECTION_CLASS_MAP is keyed on the custom model's "
                    "labels, so every detection would be dropped.", self.det_hef_path)

        nms_score_threshold = 0.3
        nms_iou_threshold = 0.45
        self.thresholds_str = (
            f"nms-score-threshold={nms_score_threshold} "
            f"nms-iou-threshold={nms_iou_threshold} "
            f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
        )

        # Validate resource paths
        for path, name in [
            (self.depth_hef_path, "Depth HEF"),
            (self.depth_post_process_so, "Depth Postprocess SO"),
            (self.det_hef_path, "Detection HEF"),
            (self.det_post_process_so, "Detection Postprocess SO")
        ]:
            if path is None or not Path(path).exists():
                hailo_logger.error(f"{name} path is invalid or missing: %s", path)

        self.create_pipeline()
        hailo_logger.debug("Pipeline created successfully")

    # ------------------------------------------------------------------
    # Pipeline construction
    #
    # get_pipeline_string() is called fresh by the framework on every
    # _rebuild_pipeline(), so branching it on config.pipeline_mode is all that's
    # needed to hot-swap which models run. The branch fragments below are shared
    # by all three builders so a single-mode pipeline is always the exact same
    # branch the dual pipeline uses, just without the tee.
    # ------------------------------------------------------------------

    # Class-level default so get_pipeline_string() works on an instance built
    # without __init__ — tests/test_pipeline_modes.py does exactly that to check
    # the mode branching without touching hardware. __init__ overrides it with
    # the real probe.
    _multi_process_service = False

    # Camera-loss state (see "Camera removed while running" below). Class-level
    # defaults for the same reason: an instance built without __init__ starts in
    # the normal state. All are rebound on the instance, never mutated in place.
    _camera_state = CAMERA_OK
    _camera_watch_thread = None
    _camera_bad_nodes = frozenset()     # nodes that would not start, or started and gave no frame
    _camera_good_node = None            # the last node frames actually came from
    _frames_at_camera_start = 0         # frame count when the pipeline last started on a camera
    _camera_missing_since = None        # monotonic time the program began waiting for a camera
    _placeholder_watched = False        # run() is about to attach its bus watch to the parked pipeline
    _camera_back_token = 0              # which "camera is back" confirmation is current (see _announce_camera_back)
    _camera_view = None                 # the view check (core/camera_view.py), made on first use
    _view_probe = None                  # (pad, probe id) of the frame request still waiting, or None
    _view_lock = threading.Lock()       # guards _view_probe: the main loop sets it, the streaming thread clears it
    _view_errors = 0                    # failed checks, for a log line that does not flood the journal

    def _depth_branch(self):
        """Depth branch fragments: (wrapper, callback, sink)."""
        depth_pipeline = INFERENCE_PIPELINE(
            hef_path=self.depth_hef_path,
            post_process_so=self.depth_post_process_so,
            post_function_name=self.depth_post_function_name,
            name="depth_inference",
            multi_process_service=self._multi_process_service,
            scheduler_priority=DEPTH_SCHEDULER_PRIORITY,
        )
        depth_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(
            depth_pipeline,
            bypass_max_size_buffers=DEPTH_BYPASS_QUEUE_BUFFERS,
            name="inference_wrapper_depth",
        ).replace("use-letterbox=true", "use-letterbox=false")
        depth_pipeline_wrapper = _low_latency_queues(depth_pipeline_wrapper)
        # No rate cap here any more: the cap sits on the shared stream in front
        # of the tee (_rate_cap), so this branch is fed at exactly the rate the
        # detection branch is.
        #
        # The tail queue, right in front of the Python callback, is LEAKY and
        # one buffer deep. The slow stage of this branch is on_depth_frame
        # itself (~15 ms of GIL-holding Python per frame, shared with the
        # detection callback, the preview and every worker thread). With the
        # library's non-leaky queue here, the moment the callback falls behind
        # the camera every queue between the tee and this one fills and stays
        # full, and the frame the callback sees is as old as their combined
        # depth. Leaky and one deep, the callback always takes the NEWEST
        # fully-inferred frame and nothing upstream can accumulate. Safe: this
        # sits after the aggregator, so a dropped buffer is a whole frame, not
        # a crop the aggregator is waiting for.
        depth_callback = (
            f"{QUEUE(name='depth_callback_q', max_size_buffers=1, leaky='downstream')} ! "
            f"identity name=depth_callback "
        )
        # No DISPLAY_PIPELINE here — that opens its own native GStreamer window
        # with hailo's own overlay, on top of the cv2 window callbacks.py
        # already draws (via use_frame/set_frame), which was showing up as two
        # redundant video windows. Display is handled by cv2 in callbacks.py.
        depth_sink = "fakesink name=depth_sink sync=false"
        return depth_pipeline_wrapper, depth_callback, depth_sink

    def _detection_branch(self):
        """Detection branch fragments: (wrapper, tracker, callback, sink)."""
        detection_pipeline = INFERENCE_PIPELINE(
            hef_path=self.det_hef_path,
            post_process_so=self.det_post_process_so,
            post_function_name=self.det_post_function_name,
            batch_size=self.batch_size,
            config_json=self.labels_json,
            additional_params=self.thresholds_str,
            name="det_inference",
            multi_process_service=self._multi_process_service,
        )
        detection_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(
            detection_pipeline, name="inference_wrapper_det"
        )
        tracker_pipeline = TRACKER_PIPELINE(
            class_id=-1,
            kalman_dist_thr=0.7,
            iou_thr=0.8,              # Slightly stricter IoU matching (default: 0.9)
            init_iou_thr=0.6,         # Pickier about new object matching to reduce phantom IDs (default: 0.7)
            keep_new_frames=3,         # 100ms grace period for new detections to stabilize (default: 2)
            keep_tracked_frames=10,    # 333ms before tracked→lost, reduces ghost duration (default: 15)
            keep_lost_frames=4,        # 133ms grace for brief occlusions like walking behind a pole (default: 2)
            name="det_tracker"
        )
        det_callback = USER_CALLBACK_PIPELINE(name="det_callback")
        det_sink = "fakesink name=det_sink sync=false"
        return detection_pipeline_wrapper, tracker_pipeline, det_callback, det_sink

    def _build_dual(self):
        """Both models in parallel off a tee — the default, full-system pipeline."""
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)
        depth_pipeline_wrapper, depth_callback, depth_sink = self._depth_branch()
        detection_pipeline_wrapper, tracker_pipeline, det_callback, det_sink = self._detection_branch()

        # Parallel tee architecture (display handled by cv2 in callbacks.py)
        return (
            f"{source_pipeline} ! {_rate_cap(pipeline_fps())} ! tee name=t "
            f"t. ! {QUEUE(name='depth_branch_q', max_size_buffers=1, leaky='downstream')} ! {depth_pipeline_wrapper} ! {depth_callback} ! {depth_sink} "
            f"t. ! {QUEUE(name='det_branch_q', leaky='downstream')} ! {detection_pipeline_wrapper} ! {tracker_pipeline} ! {det_callback} ! {det_sink}"
        )

    def _build_detection_only(self):
        """Detection alone — no tee, no depth inference on the device."""
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)
        detection_pipeline_wrapper, tracker_pipeline, det_callback, det_sink = self._detection_branch()

        # The leaky branch queues exist only to decouple the two parallel branches
        # from each other, so a single-branch pipeline doesn't need them.
        return (
            f"{source_pipeline} ! {_rate_cap(pipeline_fps())} ! {detection_pipeline_wrapper} "
            f"! {tracker_pipeline} ! {det_callback} ! {det_sink}"
        )

    def _build_depth_only(self):
        """Depth alone — no tee, no detection inference on the device."""
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)
        depth_pipeline_wrapper, depth_callback, depth_sink = self._depth_branch()

        return (f"{source_pipeline} ! {_rate_cap(pipeline_fps())} ! {depth_pipeline_wrapper} "
                f"! {depth_callback} ! {depth_sink}")

    def _build_idle(self):
        """
        Camera only — no inference at all.

        The SOURCE stays up deliberately. Tearing it down would save a little
        idle power, but coming back would then cost a camera cold-start on top
        of the rebuild blackout (D21), and a rocker is exactly the control a
        user flips straight back.

        A callback identity is still present with nothing in front of it: the
        wrapper it gets connected to is what increments the frame counter, and
        without one --enable-watchdog reads an idle pipeline as a stalled one.
        It keeps the name "det_callback" so _connect_callback needs no special
        lookup — see the det_disabled note there.
        """
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)
        idle_callback = USER_CALLBACK_PIPELINE(name="det_callback")

        return (
            f"{source_pipeline} ! {idle_callback} "
            f"! fakesink name=idle_sink sync=false"
        )

    def current_mode(self) -> str:
        """
        The pipeline mode to build for.

        Falls back to "both" when there is no config (app.py's own main() and
        main2.py both construct SecondVisionApp without one) or when the mode is
        unrecognised, so an unexpected value can never leave the device with no
        pipeline at all.
        """
        mode = self.config.get("pipeline_mode") if self.config is not None else None
        if mode is None:
            return MODE_BOTH
        if mode not in VALID_MODES:
            hailo_logger.warning("Unknown pipeline_mode %r — falling back to %r", mode, MODE_BOTH)
            return MODE_BOTH
        return mode

    def get_pipeline_string(self):
        mode = self.current_mode()
        if mode == MODE_DETECTION:
            pipeline_str = self._build_detection_only()
        elif mode == MODE_DEPTH:
            pipeline_str = self._build_depth_only()
        elif mode == MODE_NONE:
            pipeline_str = self._build_idle()
        else:
            pipeline_str = self._build_dual()

        # Record what the live pipeline is actually running, so _rebuild_pipeline()
        # can tell whether the requested mode moved on while it was rebuilding.
        self._built_mode = mode
        hailo_logger.info("Generated Pipeline string (mode=%s):\n%s", mode, pipeline_str)
        return pipeline_str

    def _connect_callback(self):
        """
        Wire the branches present in the current mode to their callbacks.py handlers.

        This pipeline exposes two USER_CALLBACK_PIPELINE identities
        ("det_callback" and "depth_callback") instead of the single
        "identity_callback" the base GStreamerApp expects, so the default
        _connect_callback can't find either one — this override replaces it.

        Exactly one branch is routed through _internal_callback_wrapper, which is
        what increments user_data's frame counter (feeding get_det_fps() and the
        --enable-watchdog stall detector). In "both" mode that's the detection
        branch, and depth connects directly: the tee gives both branches every
        frame, so wrapping both would double-count. In a single-branch mode the
        one branch present has to be the wrapped one, otherwise the frame counter
        freezes and the watchdog reads a healthy pipeline as a stall.

        Nothing to wire while the program waits for a camera: the library's
        run() calls this on the empty placeholder of a start without a camera
        (_start_without_camera), which has no callback identity, and a warning
        for each would read as a fault. The pipeline that starts when a camera
        appears is wired by _launch_on_camera(), in the state STARTING.
        """
        if self._camera_state == CAMERA_WAITING:
            return

        disable_callback = self.options_menu.disable_callback
        mode = self.current_mode()

        wire_det = mode in (MODE_BOTH, MODE_DETECTION, MODE_NONE)
        wire_depth = mode in (MODE_BOTH, MODE_DEPTH)
        # Depth is the frame-counting branch only when detection isn't there to do it.
        depth_counts_frames = mode == MODE_DEPTH

        # MODE_NONE wires the identity but not the handler: there is no inference
        # ahead of it, so on_det_frame would parse metadata that does not exist.
        # _internal_callback_wrapper still runs and still counts frames when the
        # callback is disabled — which is the whole reason idle can be told
        # apart from a stall by --enable-watchdog.
        det_disabled = disable_callback or mode == MODE_NONE

        if wire_det:
            det_identity = self.pipeline.get_by_name("det_callback")
            if det_identity:
                det_identity.set_property("signal-handoffs", True)
                det_identity.connect(
                    "handoff", _internal_callback_wrapper, self.user_data, callbacks.on_det_frame, det_disabled
                )
                hailo_logger.debug("Connected detection callback.")
            else:
                hailo_logger.warning("det_callback identity not found in pipeline")

        if wire_depth:
            depth_identity = self.pipeline.get_by_name("depth_callback")
            if depth_identity:
                depth_identity.set_property("signal-handoffs", True)
                if depth_counts_frames:
                    depth_identity.connect(
                        "handoff", _internal_callback_wrapper, self.user_data, callbacks.on_depth_frame, disable_callback
                    )
                elif not disable_callback:
                    depth_identity.connect("handoff", callbacks.on_depth_frame, self.user_data)
                hailo_logger.debug("Connected depth callback.")
            else:
                hailo_logger.warning("depth_callback identity not found in pipeline")

    def _on_pipeline_rebuilt(self):
        """
        Clear per-pipeline state after a rebuild.

        The rebuilt pipeline contains a brand-new hailotracker whose IDs restart
        from scratch, but user_data survives the swap. Without this, a fresh
        track_id can land on a dead track's entry and inherit its zone,
        last_announced and first_seen — callbacks.py's prev["label"] == label
        guard doesn't help when the label repeats, which for "person" is most of
        the time. Symptom would be a just-appeared object announced as
        "still <zone>", or silently suppressed by an inherited repeat floor.

        Also resets the FPS windows so each mode's reported rate reflects that
        mode rather than being averaged with the previous one.

        Guarded with getattr/hasattr: StandaloneUserData and the mock user_data
        don't carry the detection tracking attributes.
        """
        user_data = self.user_data

        if hasattr(user_data, "track_history"):
            user_data.track_history.clear()
        if hasattr(user_data, "IDs_changed_zones"):
            user_data.IDs_changed_zones.clear()
        if hasattr(user_data, "head_turn_cooldown_until"):
            user_data.head_turn_cooldown_until = 0.0

        now = time.monotonic()
        if hasattr(user_data, "fps_start_time"):
            user_data.fps_start_time = now
        if hasattr(user_data, "depth_fps_start_time"):
            user_data.depth_fps_start_time = now
        if hasattr(user_data, "depth_frame_count"):
            user_data.depth_frame_count = 0

        # Depth-side equivalent of the track_history clear above: drops the EMA
        # history and the latched ground hazard, so neither the smoothed
        # intensities nor an asserted drop-off from the mode that just went away
        # carries into the first frames of the new one. Same getattr guard as the
        # rest of this method — mock and standalone user_data don't have it.
        if hasattr(user_data, "reset_depth_state"):
            user_data.reset_depth_state()

        hailo_logger.info("Pipeline rebuilt — mode=%s, per-pipeline state reset", self.current_mode())

        # Time the swap from here (main loop) rather than from the requesting thread.
        if self._swap_started_at is not None:
            GLib.timeout_add(50, self._poll_swap_complete)

    def trigger_rebuild(self):
        """
        Schedule a pipeline rebuild after a mode change.

        Called by config_reader_worker (and, for debugging, mock/mode_cycler.py)
        from their own threads. Rebuilds must go through GLib.idle_add — GStreamer
        state changes aren't safe to make directly from a thread other than the
        main loop's.

        get_pipeline_string() is mode-aware, so this genuinely swaps which models
        run, based on config.pipeline_mode.

        While the camera is missing nothing is scheduled: there is no camera to
        build on, and the library's rebuild ends the program when the new
        pipeline cannot start. The mode is already in config, and the pipeline
        that starts when a camera appears is built for it (_start_on_camera).
        """
        if self._shutting_down:
            hailo_logger.debug("Rebuild requested during shutdown — ignoring")
            return

        if self._camera_state != CAMERA_OK:
            hailo_logger.info(
                "Mode %s recorded while the camera is missing — nothing is rebuilt "
                "now; the pipeline starts in this mode when a camera appears",
                self.current_mode())
            return

        if self._rebuild_in_flight:
            # Don't stack teardowns. The in-flight rebuild re-reads the mode when
            # it finishes and rebuilds again if it changed, so this request is
            # coalesced rather than lost.
            hailo_logger.debug("Rebuild already in flight — request coalesced")
            return

        self._rebuild_in_flight = True
        self._swap_started_at = time.monotonic()
        self._swap_frame_baseline = self.user_data.get_count()
        self._swap_target_mode = self.current_mode()

        # Hold off if the previous rebuild only just finished — see
        # MIN_REBUILD_INTERVAL_SECONDS.
        since_last = time.monotonic() - self._last_rebuild_at
        delay_ms = max(1, int((MIN_REBUILD_INTERVAL_SECONDS - since_last) * 1000))
        GLib.timeout_add(delay_ms, self._rebuild_pipeline)

    def _rebuild_pipeline(self):
        """
        Serialize rebuilds and converge on the latest requested mode.

        Guards the framework's rebuild two ways: one queued while the app is
        exiting must not resurrect a torn-down pipeline, and two rebuilds must
        never overlap — tearing the v4l2 source down again mid-teardown segfaults
        GStreamer in gst_object_unref.

        And two camera guards, because the framework's rebuild quits the main
        loop (ending the program) when the new pipeline fails to start: no
        rebuild while the camera is lost (a mode switch or the watchdog may have
        queued one), and if the camera node vanished while the request waited,
        go to "waiting for a camera" instead of building on it.

        A USB camera pipeline is then built and started by _rebuild_on_camera(),
        not by the framework: the camera can still go between the node check and
        the open, and the framework would quit the loop on that failure. Any
        other source (a file looping on EOS) keeps the framework's rebuild.
        """
        if self._shutting_down:
            hailo_logger.debug("Skipping rebuild — shutdown in progress")
            self._rebuild_in_flight = False
            return False

        if self._camera_state != CAMERA_OK:
            hailo_logger.info("Skipping rebuild — the camera is missing; the pipeline "
                              "starts in mode %s when one appears", self.current_mode())
            self._rebuild_in_flight = False
            self._swap_started_at = None
            return False

        # MUST happen before super() builds the new pipeline. See the method.
        self._release_old_pipeline()

        if self._camera_node_missing():
            hailo_logger.warning(
                "Camera %s disappeared before the mode switch could rebuild — "
                "waiting for a camera instead", self.video_source)
            self._rebuild_in_flight = False
            self._swap_started_at = None
            self._wait_for_camera()
            self._stop_motors()
            return False

        try:
            if self.source_type == CAMERA_SOURCE_TYPE:
                self._rebuild_on_camera()
                return False
            return super()._rebuild_pipeline()
        finally:
            self._last_rebuild_at = time.monotonic()
            self._rebuild_in_flight = False
            # The mode may have changed again while this rebuild was running
            # (coalesced above). Converge on it now.
            if not self._shutting_down and self.current_mode() != self._built_mode:
                hailo_logger.debug("Mode changed during rebuild — rebuilding again")
                self.trigger_rebuild()

    @staticmethod
    def _hailonet_elements(pipeline):
        """
        Every hailonet in the pipeline, including inside nested bins.

        The inference branches are wrapped in hailocropper/hailoaggregator bins,
        so a flat iterate_elements() misses the hailonets that actually hold the
        device. Recurse.
        """
        found = []

        def walk(bin_):
            it = bin_.iterate_elements()
            while True:
                ok, element = it.next()
                if ok != Gst.IteratorResult.OK:
                    break
                factory = element.get_factory()
                if factory is not None and factory.get_name() == "hailonet":
                    found.append(element)
                elif isinstance(element, Gst.Bin):
                    walk(element)

        try:
            walk(pipeline)
        except Exception as exc:  # never let discovery block a teardown
            hailo_logger.warning("Could not enumerate hailonet elements: %r", exc)
        return found

    def _release_old_pipeline(self):
        """
        Tear the old pipeline down and let the Hailo device actually go.

        The framework's _rebuild_pipeline() does its own teardown, but it keeps
        a local `bus` variable referencing the OLD pipeline's bus alive across
        the Gst.parse_launch() of the NEW one:

            bus = self.pipeline.get_bus()   # ref to the old pipeline
            ...
            self.pipeline = None            # not enough — bus still holds it
            self.pipeline = Gst.parse_launch(...)   # new hailonet wants the device
            bus = self.pipeline.get_bus()   # only NOW is the old bus dropped

        A GstBus holds its parent, so the old hailonet — and the vdevice it
        owns — are still alive when the new hailonet asks for hardware. There is
        exactly one Hailo-8, so it fails:

            Failed to create vdevice. there are not enough free devices.
            requested: 1, found: 0
            HAILO_OUT_OF_PHYSICAL_DEVICES(74)

        and then segfaults inside GStreamer. It fires on EVERY mode switch,
        which is every time the user flips a rocker.

        Doing the teardown here fixes it because every reference dies before we
        return: super() then finds self.pipeline None and skips its own block,
        so no stale bus survives into parse_launch.
        """
        if self.pipeline is None:
            return

        pipeline, self.pipeline = self.pipeline, None
        try:
            # Take the hailonet elements to NULL FIRST, individually.
            #
            # Setting the pipeline to NULL is meant to cascade, but it did not
            # release the Hailo vdevice here: switching both -> none appeared to
            # work only because the idle pipeline has no hailonet and so asked
            # for no hardware. The very next switch that DID need the device got
            #     Failed to create vdevice ... requested: 1, found: 0
            # because the previous pipeline's hailonet still owned it.
            #
            # hailonet releases the vdevice on its own NULL transition, so drive
            # that explicitly and wait for each one rather than trusting the
            # cascade.
            for element in self._hailonet_elements(pipeline):
                name = element.get_name()
                element.set_state(Gst.State.NULL)
                ret, _, _ = element.get_state(TEARDOWN_TIMEOUT_SECONDS * Gst.SECOND)
                if ret != Gst.StateChangeReturn.SUCCESS:
                    hailo_logger.warning(
                        "hailonet %s did not reach NULL (%s) — the device may "
                        "still be held", name, ret)
                else:
                    hailo_logger.debug("hailonet %s released", name)

            pipeline.set_state(Gst.State.NULL)
            # Block until it really is NULL. Returning early would defeat the
            # whole point — the device is released on the state change, not on
            # the request for it.
            pipeline.get_state(TEARDOWN_TIMEOUT_SECONDS * Gst.SECOND)
            bus = pipeline.get_bus()
            if bus is not None:
                bus.remove_signal_watch()
                del bus
        except Exception as exc:  # never let teardown kill the rebuild
            hailo_logger.warning("Error tearing down old pipeline: %r", exc)
        finally:
            del pipeline
            # Refcounting alone is not enough — GStreamer objects can sit in
            # reference cycles that only the collector breaks.
            gc.collect()
            # And the driver frees the vdevice slightly after the last
            # reference goes.
            time.sleep(DEVICE_SETTLE_SECONDS)

    def shutdown(self, signum=None, frame=None):
        """
        Shut down cleanly even if the signal arrives mid-swap.

        The framework's shutdown() calls self.pipeline.set_state(...), but
        _rebuild_pipeline() sets self.pipeline = None while tearing the old
        pipeline down. A Ctrl+C landing in that window raises AttributeError —
        rare normally, but mode cycling passes through that window on every
        swap. Handled here in the subclass; the library is not patched.

        Safe first, graceful second. The framework's PAUSED->READY can deadlock
        in this pipeline: a streaming thread parked inside hailoaggregator or
        hailonet on its own condition variable never sees the flush, so pad
        deactivation waits on it forever (about 1 stop in 4, 2026-09-30). So
        the workers are told to stop BEFORE the teardown starts — the serial
        worker zeroes the motors and closes the port whatever GStreamer does —
        and a timer ends the process if it is still stuck
        SHUTDOWN_FORCE_EXIT_SECONDS later. Bus errors from any element but the
        camera land here too (a camera failure is handled by bus_call without a
        shutdown), so without the timer a stuck teardown could leave a process
        that is alive to systemd (no restart) and useless to the wearer.
        """
        first_call = not self._shutting_down
        self._shutting_down = True

        if first_call:
            shutdown_event = getattr(self.user_data, "shutdown_event", None)
            if shutdown_event is not None:
                shutdown_event.set()
            threading.Thread(
                target=self._force_exit_if_stuck, daemon=True, name="shutdown-timer"
            ).start()

        if self.pipeline is None:
            hailo_logger.warning("Shutdown during pipeline rebuild — quitting main loop directly")
            if self.loop is not None:
                GLib.idle_add(self.loop.quit)
            return

        super().shutdown(signum, frame)

    def _force_exit_if_stuck(self):
        """
        End the process if shutdown has not finished within
        SHUTDOWN_FORCE_EXIT_SECONDS. A daemon thread, so a clean exit simply
        takes it down; it only fires when the teardown is deadlocked. The main
        thread does not hold the GIL while blocked in set_state (the workers
        kept running through the hangs this was written for), so this gets to run.
        """
        time.sleep(SHUTDOWN_FORCE_EXIT_SECONDS)
        hailo_logger.warning(
            "Pipeline teardown still stuck after %.0fs — forcing exit "
            "(workers were already told to stop)", SHUTDOWN_FORCE_EXIT_SECONDS)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1 if self.error_occurred else 0)

    # ------------------------------------------------------------------
    # Camera removed while running (REQ-CON-08, removal half)
    #
    # The library turns every bus error, and an end-of-stream from a live
    # source, into shutdown(): unplugging the camera used to end the whole
    # program (speech, glasses link, panel), and only systemd's restart loop
    # brought it back. A camera failure now goes:
    #   1. bus_call -> _on_camera_lost: state LOST                  (main loop)
    #   2. _release_after_camera_loss: tear down through
    #      _release_old_pipeline(), park an empty pipeline, queue one zero
    #      motor dict, state WAITING                               (main loop)
    #   3. _camera_watch: look for a USB camera every
    #      CAMERA_POLL_SECONDS, hand what it finds to the main loop   (thread)
    #   4. _on_camera_found -> _start_on_camera: build and start the pipeline
    #      for the current mode on that node; if it cannot start, back to
    #      WAITING. Never loop.quit(), never shutdown().          (main loop)
    # A mode-switch (or watchdog) rebuild on a USB camera starts its pipeline
    # the same way (_rebuild_on_camera), so a camera that goes during the
    # rebuild also ends in WAITING rather than in the library's loop.quit().
    #
    # Every such start goes through _launch_on_camera(), which opens the camera
    # before any Hailo element changes state and gives each hailonet its first
    # buffer before the camera streams. HailoRT 4.23.0's hailonet crashes when
    # it is taken down before its first buffer (see _launch_on_camera), and a
    # camera that fails at the start, or goes in the first half second, did
    # exactly that.
    # Nothing here starts mock mode. The only speech is the two camera alerts
    # (_raise_camera_missing, _announce_camera_back, _confirm_camera_back), and the only
    # vibration is the camera pattern that goes with the missing-camera sentence (F4: its
    # on_speak, played by the serial worker; nothing here touches the motors).
    # ------------------------------------------------------------------

    def bus_call(self, bus, message, loop):
        """
        The library's bus handler, with camera failures taken out of it.

        hailo_apps' bus_call sets error_occurred and calls shutdown() on ANY
        error, and its on_eos() shuts down on an end-of-stream from any non-file
        source (gstreamer_app.py bus_call / on_eos). The library connects this
        method by name, on the first pipeline (run) and on every rebuilt one
        (_rebuild_pipeline), so this override sees every pipeline's messages.

        Two cases are handled here; everything else goes to super() untouched,
        so an error from any other element still shuts down as before:
          - an ERROR from the camera element, or an end-of-stream, on a USB
            camera pipeline: the camera failed (_is_camera_failure);
          - any ERROR or end-of-stream after that, from the pipeline that has
            already lost its camera. A failing source posts its own "Internal
            data stream error" and then an end-of-stream, and other elements
            may complain as the stream stops. These are logged and dropped, never
            turned into shutdown() or error_occurred, so a later clean stop
            still exits 0.
        """
        if message.type in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
            if self._camera_state != CAMERA_OK:
                log = (hailo_logger.warning if message.type == Gst.MessageType.ERROR
                       else hailo_logger.info)
                log("Camera lost — ignoring %s", self._describe_message(message))
                return True
            if not self._shutting_down and self._is_camera_failure(message):
                self._on_camera_lost(message)
                return True
        return super().bus_call(bus, message, loop)

    def _is_camera_failure(self, message) -> bool:
        """
        True if this ERROR or end-of-stream means the USB camera stopped.

        ERROR: decided by the element that sent it (message.src), never by its
        text. The camera is the element named CAMERA_SOURCE_NAME directly in the
        live pipeline. SOURCE_PIPELINE adds decode elements only for compressed
        webcams, and this app asks for raw video (no_webcam_compression=True),
        so there are none to count.

        End-of-stream: a bus EOS is posted by the pipeline itself, so it names no
        element. With a USB camera only the camera can end the stream: v4l2src is
        live and never ends on its own (SOURCE_PIPELINE gives it no num-buffers),
        nothing in this program sends an EOS event, and a source that fails
        pushes EOS after its error. A file source is not "usb", so it keeps the
        library's looping.
        """
        if self.source_type != CAMERA_SOURCE_TYPE:
            return False
        if message.type == Gst.MessageType.EOS:
            return True
        return self._is_camera_element(message.src)

    def _is_camera_element(self, element, pipeline=None) -> bool:
        """
        True if `element` is the camera: named CAMERA_SOURCE_NAME, directly in
        `pipeline` (default self.pipeline; a pipeline being started is not
        self.pipeline yet).
        """
        pipeline = self.pipeline if pipeline is None else pipeline
        return (element is not None and pipeline is not None
                and element.get_name() == CAMERA_SOURCE_NAME
                and element.get_parent() == pipeline)

    @staticmethod
    def _describe_message(message) -> str:
        """'error from <element>: <text> (<debug>)', or 'end-of-stream from <element>'."""
        src = message.src.get_name() if message.src is not None else "?"
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            return f"error from {src}: {err.message} ({debug})"
        return f"end-of-stream from {src}"

    def _on_camera_lost(self, message):
        """
        Main loop (bus watch). Record the loss and schedule the teardown.

        The teardown is not done inside this handler: it removes this very bus's
        watch. It is also held back until MIN_REBUILD_INTERVAL_SECONDS after the
        last rebuild, for the reason trigger_rebuild() waits.
        """
        self._camera_state = CAMERA_LOST
        self._raise_camera_missing()   # at once: not after the teardown, which can be held back
        hailo_logger.warning(
            "Camera lost (%s) — stopping the vision pipeline. Speech, the glasses "
            "link and the panel keep running.", self._describe_message(message))
        since_last = time.monotonic() - self._last_rebuild_at
        delay_ms = max(1, int((MIN_REBUILD_INTERVAL_SECONDS - since_last) * 1000))
        GLib.timeout_add(delay_ms, self._release_after_camera_loss)

    def _release_after_camera_loss(self):
        """
        Main loop. Stop the dead pipeline, free the Hailo device, stop the
        motors, and start looking for a camera. Returns False (one-shot).
        """
        if self._shutting_down or self._camera_state != CAMERA_LOST:
            return False  # shutdown() takes the pipeline down itself
        started = time.monotonic()
        self._release_old_pipeline_with_alarm()

        node = self.video_source
        if self.user_data.get_count() > self._frames_at_camera_start:
            # Frames came from this node: try it first when a camera appears, and
            # forget earlier failed attempts, which belong to an earlier loss.
            self._camera_good_node = node
            self._camera_bad_nodes = frozenset()
        elif os.path.exists(node):
            # Started on this node, which is still there, and never got a frame
            # from it: maybe the wrong node of the camera (see _pick_camera).
            self._camera_bad_nodes = self._camera_bad_nodes | {node}

        self._wait_for_camera()
        # After the teardown, not before: until the pipeline is down, frames
        # already inside it can still reach on_depth_frame and queue a reading
        # behind the zero, and the glasses hold whatever came last.
        self._stop_motors()
        hailo_logger.warning(
            "Camera lost: vision pipeline stopped and Hailo device released in "
            "%.1f s, motors set to zero. Looking for a USB camera every %g s.",
            time.monotonic() - started, CAMERA_POLL_SECONDS)
        return False

    def _release_old_pipeline_with_alarm(self):
        """
        _release_old_pipeline(), with an alarm if it runs long.

        The teardown cannot be given a timeout: a synchronous GStreamer state
        change cannot be interrupted, and shutdown() documents a PAUSED->READY
        deadlock in this pipeline. If the teardown is still running after
        CAMERA_TEARDOWN_ALARM_SECONDS, a timer thread zeroes the motors (a queue
        put, no GStreamer) and logs it, so a stuck teardown neither leaves the
        glasses on a stale reading nor goes unnoticed. It does not end anything.
        """
        alarm = threading.Timer(CAMERA_TEARDOWN_ALARM_SECONDS, self._on_teardown_slow)
        alarm.daemon = True
        alarm.name = "camera-teardown-alarm"
        alarm.start()
        try:
            self._release_old_pipeline()
        finally:
            alarm.cancel()

    def _on_teardown_slow(self):
        """Timer thread. The camera-loss teardown is still running."""
        self._stop_motors()
        hailo_logger.error(
            "Camera lost: tearing the pipeline down has taken over %g s — motors "
            "set to zero. The camera can only restart once the teardown finishes.",
            CAMERA_TEARDOWN_ALARM_SECONDS)

    def _wait_for_camera(self):
        """
        Main loop, once the old pipeline is released (self.pipeline is None).

        Parks an EMPTY pipeline in self.pipeline instead of leaving None. It
        holds no element and no device, and its state changes are immediate, so
        a stop while the camera is missing takes the normal shutdown() and the
        library's run() cleanup (which calls self.pipeline.set_state) rather than
        their None paths, and the program exits 0 with nothing misleading logged.
        """
        self._park_empty_pipeline()
        self._last_rebuild_at = time.monotonic()
        self._camera_state = CAMERA_WAITING
        if self._camera_missing_since is None:
            self._camera_missing_since = time.monotonic()
        # Not every way here passed _on_camera_lost: a camera that is gone when a mode switch
        # (or the watchdog) rebuilds, or that vanishes during that rebuild's start, comes
        # straight here. Raising again is a no-op when the alert is already up.
        self._raise_camera_missing()
        if not self._shutting_down:
            self._start_camera_watch()

    def _park_empty_pipeline(self):
        """
        Main loop. Put an EMPTY pipeline in self.pipeline: no element, no device,
        immediate state changes. What a stop (shutdown(), run()'s cleanup) finds
        while there is no camera pipeline, or while a new one is being started
        and is not yet safe to take down (_launch_on_camera).
        """
        self.pipeline = Gst.Pipeline.new("no_camera")

    def _stop_motors(self):
        """
        Queue one all-zero motor dict on serial_queue: the existing contract,
        not a new channel. Never blocks: on a full queue the oldest reading is
        dropped, as callbacks._publish_motors does. Safe from any thread.
        """
        serial_queue = getattr(self.user_data, "serial_queue", None)
        if serial_queue is None:
            return
        packet = dict(MOTORS_OFF)
        try:
            serial_queue.put_nowait(packet)
        except queue.Full:
            try:
                serial_queue.get_nowait()
                serial_queue.put_nowait(packet)
            except (queue.Empty, queue.Full):
                hailo_logger.warning("Could not queue the motor stop: serial_queue stayed full")

    def _raise_camera_missing(self):
        """
        Main loop (or the constructor thread). The camera is missing: say so now and every
        CAMERA_MISSING_SECONDS, and withdraw "Camera has been reconnected" from an earlier
        return (a spent once-alert stays raised, so without this the next return would be
        silent). Raising an alert that is already up changes nothing but its repeat flag, so
        every way into "no camera" may call this, and it also takes back the pause that
        _announce_camera_back put on the repeats: the cadence goes on from the last utterance,
        it does not start over. It cancels a return that is waiting for its confirmation. Does
        nothing more when user_data has no scheduler (the standalone user data). Talks to the
        alert scheduler only: no GStreamer call, no wait; an error is logged and never stops
        the camera handling.

        F4: the camera *beep beep* (core/haptic_patterns.py PATTERN_CAMERA) goes with the
        sentence: the alert carries it as its on_speak, so the scheduler asks the pattern
        player for it each time the speech worker starts saying "Critical Error: ...", at the
        loss and on every repeat, not only when this method runs. Without a pattern player on
        user_data the sentence is said as before.

        F5: a "camera is blocked / too bright" alert of the view check is withdrawn silently here:
        with no camera there is no view to judge, and this alert says what matters.
        """
        self._camera_back_token += 1
        alerts = getattr(self.user_data, "alerts", None)
        if alerts is None:
            return
        patterns = getattr(self.user_data, "patterns", None)
        try:
            if self._camera_view is not None:
                self._camera_view.reset()      # F5: the view alerts give way to this one
            alerts.clear(CAMERA_ALERT_BACK)
            alerts.raise_alert(CAMERA_ALERT_MISSING, CAMERA_MISSING_TEXT,
                               interval=CAMERA_MISSING_SECONDS,
                               on_speak=(None if patterns is None
                                         else lambda: patterns.play(PATTERN_CAMERA)))
        except Exception as exc:  # the camera handling must go on
            hailo_logger.warning("Could not raise the missing-camera alert: %r", exc)

    def _announce_camera_back(self):
        """
        Main loop, a pipeline is PLAYING on a camera. If the missing-camera alert is up, stop it
        repeating and confirm the return CAMERA_BACK_CONFIRM_SECONDS later
        (_confirm_camera_back). The alert is not cleared yet: if the camera fails at once (held
        by another process, or gone again) the loss puts the repeats back on the cadence they
        were on. An utterance of the alert under way or owed is not cut or dropped here (the
        scheduler's rule). Nothing is said, and no timer started, when the camera was never
        reported missing. The one call outside the scheduler is the main-loop timer
        (GLib.timeout_add): it changes no GStreamer state. Same safety as _raise_camera_missing.
        """
        alerts = getattr(self.user_data, "alerts", None)
        if alerts is None:
            return
        try:
            if alerts.is_raised(CAMERA_ALERT_MISSING):
                self._camera_back_token += 1
                alerts.set_repeat(CAMERA_ALERT_MISSING, False)
                GLib.timeout_add(int(CAMERA_BACK_CONFIRM_SECONDS * 1000),
                                 self._confirm_camera_back, self._camera_back_token)
        except Exception as exc:  # the camera handling must go on
            hailo_logger.warning("Could not announce that the camera is back: %r", exc)

    def _confirm_camera_back(self, token: int) -> bool:
        """
        Main loop timer, one-shot (returns False). The pipeline started by _announce_camera_back
        has run CAMERA_BACK_CONFIRM_SECONDS without a camera loss: stop the missing-camera alert
        and say "Camera has been reconnected" once. Does nothing if the confirmation is stale (a
        loss, or a newer start, came since: `token`), the camera is no longer ok, or the program
        is stopping. An utterance of the missing alert already under way is not cut, so the new
        sentence follows it (the scheduler's rule).
        """
        if self._shutting_down or token != self._camera_back_token or self._camera_state != CAMERA_OK:
            return False
        self._camera_back_token += 1          # one announcement per confirmation
        alerts = getattr(self.user_data, "alerts", None)
        if alerts is None:
            return False
        try:
            alerts.clear(CAMERA_ALERT_MISSING)
            alerts.raise_alert(CAMERA_ALERT_BACK, CAMERA_BACK_TEXT)
        except Exception as exc:  # the camera handling must go on
            hailo_logger.warning("Could not announce that the camera is back: %r", exc)
        return False

    # ---- Camera view check (F5) ----------------------------------------------------------
    def _camera_view_watch(self):
        """
        The view check: user_data.camera_view when main.py made one (the status worker reads the same
        object for the screen), else one made here on first use for user_data's alert scheduler; None
        without a scheduler.
        """
        if self._camera_view is None:
            shared = getattr(self.user_data, "camera_view", None)
            alerts = getattr(self.user_data, "alerts", None)
            if shared is not None:
                self._camera_view = shared
            elif alerts is not None:
                self._camera_view = CameraViewWatch(alerts)
        return self._camera_view

    def _camera_view_tick(self) -> bool:
        """
        Main loop timer, every VIEW_CHECK_SECONDS (run() starts it). Ask for one camera frame for the
        view check: a ONE-SHOT buffer probe on CAMERA_VIEW_ELEMENT's src pad, which removes itself on
        the next buffer (_on_view_buffer returns REMOVE). So the camera's streaming thread runs no
        Python at all between checks; a probe on every frame would make it wait for the GIL behind the
        depth callback (~15 ms of Python per frame) 30 times a second. Adding a probe is not a state
        change and is safe while the pipeline plays (this file's priming uses add_probe the same way).

        Nothing is asked for while the camera is not running (state not OK: the camera-missing alert
        takes over) or while both rockers are off (mode none: there are no obstacle warnings to miss,
        so the view alerts are withdrawn). Never raises; returns False (the timer ends) only once the
        program is stopping.
        """
        if self._shutting_down:
            return False
        try:
            self._request_view_frame()
        except Exception as exc:  # the main loop must go on
            self._log_view_error("could not ask for a frame", exc)
        try:
            self._push_status_screen()
        except Exception as exc:  # a display aid: never worth stopping the timer for
            self._log_view_error("could not draw the status screen", exc)
        return True

    def _push_status_screen(self):
        """
        Main loop, once a second. The failsafe status (user_data.status, core/status_board.py) is drawn
        on the live camera picture by the detection overlay (callbacks._draw_detection_overlay). When no
        live picture is drawn, a dark screen with the status is shown instead, so the window never sits
        frozen on an old frame: no camera, depth-only (the overlay is the detection branch's), or both
        rockers off. Only when the display process runs (--use-frame); set_frame() never waits.
        """
        if not getattr(getattr(self, "options_menu", None), "use_frame", False):
            return
        status = getattr(self.user_data, "status", None)
        if status is None:
            return
        mode = self.current_mode()
        if self._camera_state != CAMERA_OK:
            note = "No camera picture: looking for the camera"
        elif mode == MODE_DEPTH:
            note = "Depth only: the camera picture is in the depth window"
        elif mode == MODE_NONE:
            note = "Both rockers off: nothing runs"
        else:
            return          # the detection overlay draws the status on the live picture
        self.user_data.set_frame(status_canvas(self.video_width, self.video_height, status,
                                               time.monotonic(), note))

    def _request_view_frame(self):
        """Main loop. Put the one-shot probe on the live pipeline's source, unless one still waits."""
        watch = self._camera_view_watch()
        if watch is None or self._camera_state != CAMERA_OK:
            return
        if self.current_mode() == MODE_NONE:
            watch.reset()
            return
        pipeline = self.pipeline
        element = pipeline.get_by_name(CAMERA_VIEW_ELEMENT) if pipeline is not None else None
        pad = element.get_static_pad("src") if element is not None else None
        if pad is None:
            return
        with self._view_lock:
            stale = self._view_probe
            if stale is not None and stale[0] is pad:
                return                  # the last request has had no frame yet: do not stack another
            self._view_probe = (pad, pad.add_probe(Gst.PadProbeType.BUFFER, self._on_view_buffer))
        if stale is not None:           # waiting on a pipeline that a rebuild has since replaced
            try:
                stale[0].remove_probe(stale[1])
            except Exception:
                pass

    def _on_view_buffer(self, pad, info):
        """
        The camera's streaming thread, one buffer about once a second. Hands that frame to the view
        check and removes the probe. The buffer itself goes on untouched and at once: the frame is
        copied (get_numpy_from_buffer), the check works on 1,200 of its pixels. Any error is logged
        and the probe still removed; it never stops the stream.
        """
        with self._view_lock:
            if self._view_probe is not None and self._view_probe[1] == info.id:
                self._view_probe = None
        try:
            watch = self._camera_view
            buffer = info.get_buffer()
            if (watch is not None and buffer is not None and not self._shutting_down
                    and self._camera_state == CAMERA_OK):
                fmt, width, height = get_caps_from_pad(pad)
                watch.observe_frame(get_numpy_from_buffer(buffer, fmt, width, height))
        except Exception as exc:  # never let the check touch the stream
            self._log_view_error("could not check a frame", exc)
        return Gst.PadProbeReturn.REMOVE

    def _log_view_error(self, what, exc):
        """A warning for the first failure and then one a minute, not one a second."""
        self._view_errors += 1
        if self._view_errors == 1 or self._view_errors % 60 == 0:
            hailo_logger.warning("Camera view check %s (%r; %d so far)", what, exc, self._view_errors)

    def _start_camera_watch(self):
        """Main loop. Start the camera watcher thread unless it is already running."""
        thread = self._camera_watch_thread
        if thread is not None and thread.is_alive():
            return
        self._camera_watch_thread = threading.Thread(
            target=self._camera_watch, daemon=True, name="camera-watch")
        self._camera_watch_thread.start()

    def _camera_watch(self):
        """
        Thread. Every CAMERA_POLL_SECONDS while the state is WAITING, look for a
        USB camera and hand what is found to the main loop.

        A thread because one look runs udevadm once per /dev/video* node (see
        CAMERA_POLL_SECONDS); on the GLib loop that would hold up the bus and
        every timer. It never touches GStreamer: the pipeline is only built on
        the main loop (_on_camera_found, through GLib.idle_add). One hand-over is
        in flight at a time. The thread lives until shutdown and idles while the
        camera works, so a second loss needs no second thread.

        While no camera is found it says so at INFO about every
        CAMERA_STILL_LOOKING_LOG_SECONDS, counted from when the waiting began.
        """
        stop = getattr(self.user_data, "shutdown_event", None) or threading.Event()
        reported_for, next_report = None, 0.0   # which wait the INFO lines belong to
        while not self._shutting_down:
            if stop.wait(CAMERA_POLL_SECONDS) or self._shutting_down:
                return
            if self._camera_state != CAMERA_WAITING:
                continue
            try:
                devices = list(get_usb_video_devices())
            except Exception as exc:  # keep looking; a failed look is not fatal
                hailo_logger.warning("Looking for a USB camera failed: %r", exc)
                continue
            if not devices:
                hailo_logger.debug("No USB camera yet")
                since = self._camera_missing_since
                if since is not None:
                    if since != reported_for:
                        reported_for, next_report = since, CAMERA_STILL_LOOKING_LOG_SECONDS
                    waited = time.monotonic() - since
                    if waited >= next_report:
                        hailo_logger.info("Still no USB camera after %.0f s — still looking "
                                          "every %g s", waited, CAMERA_POLL_SECONDS)
                        next_report = (waited // CAMERA_STILL_LOOKING_LOG_SECONDS + 1) \
                            * CAMERA_STILL_LOOKING_LOG_SECONDS
                continue
            handled = threading.Event()
            GLib.idle_add(self._on_camera_found, devices, handled)
            while not handled.wait(0.5):
                if stop.is_set() or self._shutting_down:
                    return

    def _on_camera_found(self, devices, handled):
        """Main loop (idle). Start the pipeline on a camera the watcher found."""
        try:
            if self._shutting_down or self._camera_state != CAMERA_WAITING:
                return False
            if (self._rebuild_in_flight
                    or time.monotonic() - self._last_rebuild_at < MIN_REBUILD_INTERVAL_SECONDS):
                return False  # too soon after the last teardown; the next look retries
            self._start_on_camera(self._pick_camera(devices))
        finally:
            handled.set()
        return False

    def _pick_camera(self, devices):
        """
        The node to start on, from the watcher's list of USB capture nodes.

        get_usb_video_devices() lists nodes in os.listdir() order, and one camera
        can expose several capture nodes: this Pi's Arducam gives /dev/video0 and
        /dev/video2, listed video2 first (udev, 2026-10-17). The pipeline asks
        for raw video, which a UVC camera serves on the node it registers first,
        so order matters: the node frames last came from (_camera_good_node)
        first, then the lowest number. A node that would not start, or started
        and gave no frame, goes last; once every node has, all are tried again.
        Not self.video_source: that is the node last TRIED, which after a failed
        attempt is the wrong one.
        """
        def number(node):
            match = re.search(r"(\d+)$", node)
            return int(match.group(1)) if match else sys.maxsize

        preferred = self._camera_good_node
        ordered = sorted(set(devices),
                         key=lambda node: (node != preferred, number(node), node))
        fresh = [node for node in ordered if node not in self._camera_bad_nodes]
        if not fresh:
            self._camera_bad_nodes = frozenset()
            fresh = ordered
        return fresh[0]

    def _start_on_camera(self, node) -> bool:
        """
        Main loop. Build the pipeline for the current mode on `node`, start it.

        Through _launch_on_camera(), which follows hailo_apps' _rebuild_pipeline
        (parse_launch, bus watch with bus_call, _connect_callback,
        _on_pipeline_rebuilt, disable_qos, PLAYING) with the camera opened first
        and the hailonets primed. Where the library would call loop.quit() on a
        failed start, a pipeline the CAMERA cannot start (the node exists but
        cannot be opened yet, or the camera vanished again) sends the app back to
        WAITING. A start that another element fails takes today's path for that
        element's error: shutdown. A failure after PLAYING arrives on the bus:
        the camera's as a new camera loss, any other element's as before.

        get_source_pipeline() reads self.video_source, so a camera that comes
        back under another /dev/video* node is used where it is.
        """
        self._camera_state = CAMERA_STARTING
        hailo_logger.info("Camera found on %s — starting the vision pipeline (mode=%s)",
                          node, self.current_mode())
        self._swap_started_at = None  # not a mode swap; nothing to time
        outcome, failure = self._launch_on_camera(node)

        if outcome == START_RUNNING:
            self._camera_state = CAMERA_OK
            self._camera_missing_since = None
            self.watchdog_paused = False
            self._announce_camera_back()
            hailo_logger.info("Camera back: vision pipeline running on %s (mode=%s)",
                              node, self._built_mode)
            # The mode may have changed while this was building (trigger_rebuild
            # only records it while the camera is missing). Converge on it now.
            if not self._shutting_down and self.current_mode() != self._built_mode:
                self.trigger_rebuild()
            return True
        if outcome == START_STOPPED:
            return False  # shutdown() is taking the program down; nothing to retry
        if outcome == START_FAILED:
            self._shut_down_after_failed_start(node, failure)
            return False

        self._back_to_waiting(node)
        hailo_logger.warning("The vision pipeline did not start on %s%s — still looking "
                             "for a camera", node, self._failure_note(failure))
        return False

    def _rebuild_on_camera(self):
        """
        Main loop, from _rebuild_pipeline() (a mode switch, or the watchdog), the
        old pipeline already released. hailo_apps' _rebuild_pipeline() for a USB
        camera, started through _launch_on_camera(): a camera that cannot be
        opened, or goes, during the rebuild leaves the program waiting for a
        camera, where the library calls loop.quit() and the program ends (F3).
        Same bookkeeping as the library: the watchdog is paused until the new
        pipeline runs, and rebuild_count counts the rebuild. A start that another
        element fails takes today's path: shutdown.
        """
        self.watchdog_paused = True
        self.rebuild_count += 1
        node = str(self.video_source)
        self._park_empty_pipeline()   # what a stop sees until the new pipeline is safe
        outcome, failure = self._launch_on_camera(node)

        if outcome == START_RUNNING:
            self.watchdog_paused = False
        elif outcome == START_FAILED:
            self._shut_down_after_failed_start(node, failure)
        elif outcome == START_NO_CAMERA:
            self._swap_started_at = None
            self._back_to_waiting(node)
            self._stop_motors()
            hailo_logger.warning(
                "The vision pipeline for mode %s did not start on %s%s — waiting for a "
                "camera instead", self.current_mode(), node, self._failure_note(failure))

    def _launch_on_camera(self, node):
        """
        Main loop. Build the pipeline for the current mode on `node` and start it
        in an order that never takes a hailonet down before its first buffer.

        Why. The hailonet of HailoRT 4.23.0 (the installed version) unrefs its
        buffer allocator on READY->NULL (gst_hailonet_free). The pointer is not
        initialised by the HailoNetImpl constructor and is set only when the
        first buffer arrives (gst_hailonet_sink_probe -> allocate_infer_resources
        -> init_allocator). A hailonet taken down before any buffer unrefs
        whatever its memory held: a segfault in gst_object_unref, or a
        "ref_count > 0" CRITICAL. This is read in the HailoRT sources, tag
        v4.23.0, hailort/libhailort/bindings/gstreamer/gst-hailo/gsthailonet.cpp
        (github.com/hailo-ai/hailort), which is not part of this repository, and
        measured on this Pi with the app's own pipeline: a hailonet torn down
        with no buffer crashed in 7 of 10 runs after an earlier pipeline had run
        in the process and 10 of 10 in a fresh one, and never when it had had a
        buffer or its buffer was fed by _prime_hailonets (dev-report-2.md). A
        start that fails on the camera, and any teardown in the ~0.6 s before the
        first frame reaches the hailonets, take a hailonet down that way.

        Steps:
          1. The camera element alone to READY. v4l2src opens its device in
             NULL->READY (measured: a node that does not exist fails here, in
             under a millisecond, with every hailonet still in NULL), so this is
             the step in which a camera that cannot be opened fails, and when it
             does no other element has left NULL: nothing Hailo is taken down.
          2. The whole pipeline to PAUSED. A live source sends nothing before
             PLAYING, so no frame moves yet.
          3. Every hailonet gets its first buffer now (_prime_hailonets), with no
             stop check in between: from here any teardown is safe.
          4. Only now does the pipeline become self.pipeline; until then a stop
             sees the empty placeholder, never a half-started Hailo pipeline (the
             library's shutdown() would set it PAUSED first and then down again).
             Then, as the library's rebuild: callbacks, _on_pipeline_rebuilt,
             disable_qos, PLAYING. A camera that goes after this is an ordinary
             camera loss on the bus.

        Returns (outcome, failure): one of START_*, and the ERROR message that
        explains a failure, if there is one. self.pipeline is the new pipeline on
        START_RUNNING, and on START_FAILED or START_STOPPED once step 4 is
        reached; on START_NO_CAMERA it is None if step 4 was reached (the
        pipeline is released), otherwise unchanged (the new pipeline is torn
        down here).
        """
        # The library's own rebuild uses it. Imported here, not at the top:
        # gstreamer_common needs the real GObject bindings at import time.
        from hailo_apps.python.core.gstreamer.gstreamer_common import disable_qos

        self.video_source = node
        pipeline = None
        try:
            pipeline = Gst.parse_launch(self.get_pipeline_string())
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.bus_call, self.loop)
            camera = pipeline.get_by_name(CAMERA_SOURCE_NAME)
            if camera is None:
                raise RuntimeError(f"the pipeline has no element named {CAMERA_SOURCE_NAME!r}")

            # 1. The camera alone.
            if self._shutting_down:
                return self._abandon_start(pipeline, START_STOPPED)
            if camera.set_state(Gst.State.READY) == Gst.StateChangeReturn.FAILURE:
                return self._abandon_start(pipeline, START_NO_CAMERA, self._pop_error(pipeline))
            if self._shutting_down:
                return self._abandon_start(pipeline, START_STOPPED)

            # 2. Everything else, then 3. the hailonets' first buffer.
            if pipeline.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
                failure = self._pop_error(pipeline)
                camera_failed = failure is not None and self._is_camera_element(failure.src, pipeline)
                return self._abandon_start(pipeline, START_NO_CAMERA if camera_failed else START_FAILED,
                                           failure)
            self._prime_hailonets(pipeline)

            # 4. Safe to take down at any moment: the app's pipeline from here.
            self.pipeline = pipeline
            self._connect_callback()
            self._on_pipeline_rebuilt()
            disable_qos(pipeline)
            if self._shutting_down:
                hailo_logger.info("Shutdown during the camera start — not starting the pipeline")
                return START_STOPPED, None
            self._frames_at_camera_start = self.user_data.get_count()
            if pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE:
                return START_RUNNING, None
            failure = self._pop_error(pipeline)
            if failure is not None and self._is_camera_element(failure.src, pipeline):
                self._release_old_pipeline()
                return START_NO_CAMERA, failure
            return START_FAILED, failure
        except Exception as exc:  # a camera restart must never end the program
            hailo_logger.warning("Could not build the vision pipeline on %s: %r", node, exc)
            if pipeline is not None and pipeline is self.pipeline:
                self._release_old_pipeline()
            elif pipeline is not None:
                self._abandon_start(pipeline, START_NO_CAMERA)
            return START_NO_CAMERA, None
        finally:
            self._last_rebuild_at = time.monotonic()

    @staticmethod
    def _drop_buffer(_pad, _info):
        """Pad probe that drops the buffer: the priming buffer of _prime_hailonets()."""
        return Gst.PadProbeReturn.DROP

    def _prime_hailonets(self, pipeline):
        """
        Main loop, `pipeline` PAUSED, no camera frame yet. Give every hailonet its
        first buffer now, so that from here it can be taken down at any moment.

        No hailo-apps example does this: it is a use of the plugin worked out
        from the HailoRT sources (tag v4.23.0, gsthailonet.cpp, see
        _launch_on_camera) and checked on the device. The hailonet gets ready to
        stream in a probe on its sink pad that runs on the first buffer
        (gst_hailonet_sink_probe: configure the network, then create the output
        pools and the buffer allocator that READY->NULL frees). One empty buffer
        chained into the sink pad here runs that probe as the first camera frame
        would. A probe added after the element's own (a pad calls its probes in
        the order they were added) drops the buffer, so the element's chain
        function never sees it and nothing downstream does either;
        gst_pad_chain() then returns OK. Observed on the device in all three
        modes: each hailonet's sink pad sees one empty buffer, its src pad and
        the callbacks see only camera frames, and the first frame is processed
        as without priming.

        Before the buffer: a caps query, which makes the element load its network
        if it has not yet (gst_hailonet_get_caps), so the probe has a model to
        configure; and stream-start, caps and segment stored on the pad without
        reaching the element (gst_pad_store_sticky_event), so that GStreamer's
        data-flow checks do not warn about a buffer arriving before them. The
        camera's own events reach the element when the stream starts.

        If it cannot be done (no input format, an exception, a flow other than
        OK) a warning says so and the start goes on: that element is then exposed
        to a teardown before its first frame as it was before this existed.

        The network is configured here instead of in PAUSED->PLAYING
        (gst_hailonet_change_state), which then finds it configured: the same
        work, done earlier.
        """
        for element in self._hailonet_elements(pipeline):
            name = element.get_name()
            began = time.monotonic()
            try:
                pad = element.get_static_pad("sink")
                caps = pad.query_caps(None)
                if caps is None or caps.is_empty() or caps.is_any():
                    hailo_logger.warning(
                        "hailonet %s reports no input format (%s) — not primed; taking it "
                        "down before its first frame may crash it", name, caps)
                    continue
                segment = Gst.Segment.new()
                segment.init(Gst.Format.TIME)
                for event in (Gst.Event.new_stream_start(f"second-vision-prime-{name}"),
                              Gst.Event.new_caps(caps.fixate()),
                              Gst.Event.new_segment(segment)):
                    pad.store_sticky_event(event)
                drop = pad.add_probe(Gst.PadProbeType.BUFFER, self._drop_buffer)
                try:
                    flow = pad.chain(Gst.Buffer.new())
                finally:
                    pad.remove_probe(drop)
            except Exception as exc:  # never let priming stop a start
                hailo_logger.warning("hailonet %s could not be primed (%r); taking it down "
                                     "before its first frame may crash it", name, exc)
                continue
            if flow != Gst.FlowReturn.OK:
                hailo_logger.warning("hailonet %s could not be primed (%s); taking it down "
                                     "before its first frame may crash it", name, flow.value_nick)
            else:
                hailo_logger.debug("hailonet %s primed in %.0f ms", name,
                                   (time.monotonic() - began) * 1000)

    def _abandon_start(self, pipeline, outcome, failure=None):
        """
        Main loop. Tear down a pipeline that never became self.pipeline: only its
        camera element may have left NULL (or the bin's own rollback ran, see
        _launch_on_camera), so there is nothing to wait for. Returns
        (outcome, failure) for _launch_on_camera to pass on.
        """
        try:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(TEARDOWN_TIMEOUT_SECONDS * Gst.SECOND)
            bus = pipeline.get_bus()
            if bus is not None:
                bus.remove_signal_watch()
                del bus
        except Exception as exc:  # never let a teardown stop the program
            hailo_logger.warning("Error tearing down the pipeline that did not start: %r", exc)
        del pipeline
        gc.collect()
        return outcome, failure

    @staticmethod
    def _pop_error(pipeline):
        """
        The first ERROR waiting on `pipeline`'s bus, or None. An element that
        fails a state change posts one naming itself before set_state() returns
        FAILURE, and nothing has dispatched it yet (this runs on the main loop).
        """
        bus = pipeline.get_bus()
        return bus.pop_filtered(Gst.MessageType.ERROR) if bus is not None else None

    def _failure_note(self, failure) -> str:
        """' (<the error>)' for a log line, or '' when there is no ERROR message."""
        return f" ({self._describe_message(failure)})" if failure is not None else ""

    def _back_to_waiting(self, node):
        """
        Main loop, after a start the camera could not make (the new pipeline is
        already gone). Mark the node if it is still there, see _pick_camera, and
        wait for a camera.
        """
        if os.path.exists(node):
            self._camera_bad_nodes = self._camera_bad_nodes | {node}
        self._wait_for_camera()

    def _shut_down_after_failed_start(self, node, failure):
        """
        Main loop. A start that an element other than the camera failed (in these
        pipelines that means a Hailo element): today's path for that element's
        error, the library's bus_call (error_occurred, shutdown(); the boot
        service then restarts the program, which also gives the Hailo device back
        cleanly). Retrying it every CAMERA_POLL_SECONDS would leave a program that
        is alive to systemd and has no vision. A failure with no ERROR message,
        after the camera opened, takes the same path.
        """
        if failure is not None:
            hailo_logger.error(
                "The vision pipeline did not start on %s because of an element that is "
                "not the camera (%s) — handled as before: shutdown",
                node, self._describe_message(failure))
            super().bus_call(None, failure, self.loop)
        else:
            hailo_logger.error(
                "The vision pipeline did not start on %s although the camera opened, and no "
                "element said why — handled as another element's error: shutdown", node)
            self.error_occurred = True
            self.shutdown()

    def _camera_node_missing(self) -> bool:
        """True if this is a USB camera pipeline and the camera's node is gone."""
        return (self.source_type == CAMERA_SOURCE_TYPE
                and not os.path.exists(str(self.video_source)))

    # ------------------------------------------------------------------
    # Camera absent at start-up (REQ-CON-08, absent half)
    #
    # The boot launcher passes --input /dev/video0 whether or not a camera is
    # attached. With no such node the library's run() would set a pipeline with
    # Hailo elements to PAUSED: the camera element fails, and the Hailo elements
    # that had already left NULL are rolled back before their first buffer, which
    # HailoRT 4.23.0 does not survive (see _launch_on_camera). Measured with the
    # code before this change on this Pi, 2026-10-17, 25 fresh starts without a
    # camera: SIGSEGV in 4; the other 21 survived only because the bus error
    # reached the camera-loss path, which tears the unfed Hailo pipeline down.
    # So when the node is not there at launch no pipeline is built at all:
    #   1. create_pipeline() (the constructor): _start_without_camera() parks the
    #      empty pipeline, state WAITING, one zero motor dict queued     (main thread)
    #   2. run() (the library's, unchanged otherwise): a one-shot idle starts
    #      the camera watcher from the loop's first iteration            (main loop)
    #   3. from here on exactly the camera-loss path: _camera_watch ->
    #      _on_camera_found -> _start_on_camera -> _launch_on_camera, the staged
    #      and primed start, in the mode current at that moment.
    # A camera that is there at launch keeps the library's path untouched.
    # ------------------------------------------------------------------

    def create_pipeline(self):
        """
        The library's create_pipeline(), except when this is a USB camera and its
        node does not exist: then the program starts without a pipeline and waits
        for a camera (_start_without_camera). Called once, by the constructor.
        """
        if self._camera_node_missing():
            self._start_without_camera()
            return
        super().create_pipeline()

    def _start_without_camera(self):
        """
        Constructor time. What create_pipeline() does instead of building the
        pipeline: what the library's create_pipeline() leaves behind (GStreamer
        initialised, the main loop) plus the state a camera loss ends in: an
        empty pipeline parked in self.pipeline (so a stop takes the normal
        shutdown() and run() cleanup), state WAITING, one zero motor dict (the
        glasses hold their last command, and nothing else says "off" until a
        pipeline runs). The watcher thread is started later, by run().

        trigger_rebuild() already records a mode switch while the state is not
        OK, so a panel switch from the first second is kept and the pipeline that
        starts when a camera appears is built for it. Nothing is built here, so
        no Hailo element exists to be taken down.
        """
        Gst.init(None)
        self.loop = GLib.MainLoop()
        self._park_empty_pipeline()
        self._camera_state = CAMERA_WAITING
        self._camera_missing_since = time.monotonic()
        self._stop_motors()
        self._raise_camera_missing()
        hailo_logger.warning(
            "No camera at start-up: %s does not exist. The vision pipeline is not built. "
            "Speech, the glasses link and the panel run; the program looks for a USB camera "
            "every %g s and starts the vision pipeline, in the mode current then, when one "
            "appears.", self.video_source, CAMERA_POLL_SECONDS)

    def run(self):
        """
        The library's run(). When the program started without a camera
        (_start_without_camera) it runs on the parked empty pipeline: the
        library sets that PAUSED and PLAYING, which changes nothing, and runs the
        main loop, and a stop ends it with the library's cleanup and exit code.

        Three additions. In every state, the camera view check's timer (F5,
        _camera_view_tick). And two only in that state. The camera watcher is started
        from the loop's first iteration (_begin_waiting), the same thread and the
        same main loop as after a camera loss, and after the library has started
        the optional display process, so that process is never forked from a
        process with a second thread mid-look. And a stop that arrived before
        this point (a signal during the constructor: shutdown() then finds no
        pipeline and no loop and only records itself) would never reach
        loop.quit(), and the program would run until the shutdown timer forced
        it out; so the loop is told to quit as soon as it runs.
        """
        if self._camera_state == CAMERA_WAITING:
            if self._shutting_down:
                GLib.idle_add(self.loop.quit)
            else:
                self._placeholder_watched = True
                GLib.idle_add(self._begin_waiting)
        if getattr(self.user_data, "alerts", None) is not None:
            # F5: the camera view check, once a second for the whole run (it pauses by itself while
            # no camera runs). Without an alert scheduler there is nobody to tell, so no timer.
            GLib.timeout_add(int(VIEW_CHECK_SECONDS * 1000), self._camera_view_tick)
        super().run()

    def _begin_waiting(self):
        """
        Main loop, first iteration of a start without a camera. One-shot.

        Undoes what the library's run() did to the parked pipeline and starts
        the watcher. run() set the placeholder PLAYING and gave its bus a watch;
        the pipeline that starts on a camera replaces it in self.pipeline, and
        GStreamer reports a CRITICAL when an element is dropped while still
        PLAYING (the placeholder a camera loss parks is never started, so
        _launch_on_camera does not set it NULL). So: NULL (immediate, it holds
        nothing), and the watch removed once, only if run() attached it.
        """
        if self._shutting_down or self._camera_state != CAMERA_WAITING:
            return False
        placeholder = self.pipeline
        placeholder.set_state(Gst.State.NULL)
        if self._placeholder_watched:
            self._placeholder_watched = False
            bus = placeholder.get_bus()
            if bus is not None:
                bus.remove_signal_watch()
        self._start_camera_watch()
        return False

    # ------------------------------------------------------------------
    # DEBUG ONLY — mode monitoring, active only under --cycle-modes.
    # Everything below is observability for the mode-switching demo and can be
    # removed with mock/mode_cycler.py once the Arduino control panel drives
    # mode changes for real.
    # ------------------------------------------------------------------

    def start_mode_monitor(self):
        """Begin the periodic mode heartbeat. Registered on the GLib main loop."""
        self._hb_last_count = self.user_data.get_count()
        GLib.timeout_add_seconds(HEARTBEAT_SECONDS, self._mode_heartbeat)
        hailo_logger.info("Mode monitor started (every %ss)", HEARTBEAT_SECONDS)

    def _mode_heartbeat(self):
        """
        Report frame flow for whichever mode is active.

        Depth-only mode produces no console output of its own — the depth
        callback is still a placeholder — so this is the only evidence that its
        pipeline is genuinely running rather than silently stalled.
        """
        if self._shutting_down:
            return False

        mode = self.current_mode()
        count = self.user_data.get_count()
        fps = (count - self._hb_last_count) / HEARTBEAT_SECONDS
        self._hb_last_count = count

        print(f"[MODE] {mode} pipeline active — {count} frames, {fps:.1f} FPS")
        self._push_debug_banner(mode)
        return True

    def _poll_swap_complete(self):
        """
        Time a swap by when the new pipeline delivers its first frame — the point
        the device is actually useful again, rather than when GStreamer reports
        PLAYING. Registered from _on_pipeline_rebuilt(), so it runs on the main loop.
        """
        if self._shutting_down or self._swap_started_at is None:
            return False

        elapsed = time.monotonic() - self._swap_started_at

        if self.user_data.get_count() > self._swap_frame_baseline:
            verdict = "OK" if elapsed <= SWAP_BUDGET_SECONDS else "SLOW"
            print(f"[SWAP] -> {self._swap_target_mode}: first frame after {elapsed:.2f}s "
                  f"({verdict}, budget {SWAP_BUDGET_SECONDS:.0f}s)")
            self._swap_started_at = None
            return False

        # Safety cap: stop polling rather than spin forever if frames never resume.
        if elapsed > _SWAP_POLL_TIMEOUT_SECONDS:
            print(f"[SWAP] -> {self._swap_target_mode}: NO FRAMES after {elapsed:.1f}s "
                  f"— pipeline appears stalled")
            self._swap_started_at = None
            return False

        return True

    def _push_debug_banner(self, mode):
        """
        Explain a frozen cv2 window.

        The debug overlay is drawn by the detection callback, so in depth-only
        mode nothing calls set_frame() and the display process keeps showing its
        last detection frame — indistinguishable from a hang. Push a labelled
        frame instead. set_frame() is a non-blocking put and the display process
        holds the last imshow, so one push per heartbeat is enough.
        """
        if mode != MODE_DEPTH:
            return  # detection overlay is live in the other modes
        if not getattr(self.options_menu, "use_frame", False):
            return  # no display process running
        if getattr(self.user_data, "status", None) is not None:
            return  # the status screen (_push_status_screen) already explains it, once a second

        canvas = np.zeros((self.video_height, self.video_width, 3), dtype=np.uint8)
        lines = [
            "[DEBUG]: depth-only pipeline active.",
            "Detection visual debug frozen",
        ]
        y = max(40, self.video_height // 2 - 20)
        for line in lines:
            cv2.putText(canvas, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (255, 255, 255), 2)
            y += 40

        self.user_data.set_frame(canvas)

class StandaloneUserData(callbacks.user_app_callback_class):
    """
    user_data for running this app directly (`python3 app.py`), outside of
    main.py's full SecondVisionUserData wiring — adds the queues callbacks.py's
    tts/serial contract needs on top of user_app_callback_class.
    """
    def __init__(self):
        super().__init__()
        # TTS uses a priority mailbox (offer/take/peek); serial stays FIFO.
        self.tts_queue = PriorityMailbox()
        self.serial_queue = queue.Queue(maxsize=10)
        self.shutdown_event = threading.Event()

def main():
    hailo_logger.info("Starting SV Dual Pipeline App")
    user_data = StandaloneUserData()
    # Pass None for app_callback because we explicitly connect them in _connect_callback override
    app = SecondVisionApp(None, user_data)
    app.run()

if __name__ == "__main__":
    main()
