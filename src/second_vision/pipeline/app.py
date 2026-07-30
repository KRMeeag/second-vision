import os
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
from hailo_apps.python.core.common.hef_utils import get_hef_labels_json

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
from second_vision.core.priority import PriorityMailbox

hailo_logger = get_logger(__name__)

# Pipeline modes. These are the values SystemConfig.pipeline_mode takes and the ones the
# Arduino sends as "M:<mode>" (see workers/config_reader.py).
MODE_BOTH = "both"
MODE_DETECTION = "detection"
MODE_DEPTH = "depth"
VALID_MODES = (MODE_BOTH, MODE_DETECTION, MODE_DEPTH)

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
            default="yolov8s.hef",
            help="Specific HEF model to use for detection (default: yolov8s.hef)",
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
        if self.labels_json is None: # if no labels JSON file is provided, try auto-detect it from the HEF file
            self.labels_json = get_hef_labels_json(self.det_hef_path)
            if self.labels_json is not None:
                hailo_logger.info("Auto detected Labels JSON: %s", self.labels_json)

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

    def _depth_branch(self):
        """Depth branch fragments: (wrapper, callback, sink)."""
        depth_pipeline = INFERENCE_PIPELINE(
            hef_path=self.depth_hef_path,
            post_process_so=self.depth_post_process_so,
            post_function_name=self.depth_post_function_name,
            name="depth_inference",
        )
        depth_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(
            depth_pipeline, name="inference_wrapper_depth"
        ).replace("use-letterbox=true", "use-letterbox=false")
        depth_callback = USER_CALLBACK_PIPELINE(name="depth_callback")
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
            name="det_inference"
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
            f"{source_pipeline} ! tee name=t "
            f"t. ! {QUEUE(name='depth_branch_q', leaky='downstream')} ! {depth_pipeline_wrapper} ! {depth_callback} ! {depth_sink} "
            f"t. ! {QUEUE(name='det_branch_q', leaky='downstream')} ! {detection_pipeline_wrapper} ! {tracker_pipeline} ! {det_callback} ! {det_sink}"
        )

    def _build_detection_only(self):
        """Detection alone — no tee, no depth inference on the device."""
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)
        detection_pipeline_wrapper, tracker_pipeline, det_callback, det_sink = self._detection_branch()

        # The leaky branch queues exist only to decouple the two parallel branches
        # from each other, so a single-branch pipeline doesn't need them.
        return (
            f"{source_pipeline} ! {detection_pipeline_wrapper} ! {tracker_pipeline} "
            f"! {det_callback} ! {det_sink}"
        )

    def _build_depth_only(self):
        """Depth alone — no tee, no detection inference on the device."""
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)
        depth_pipeline_wrapper, depth_callback, depth_sink = self._depth_branch()

        return f"{source_pipeline} ! {depth_pipeline_wrapper} ! {depth_callback} ! {depth_sink}"

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
        """
        disable_callback = self.options_menu.disable_callback
        mode = self.current_mode()

        wire_det = mode in (MODE_BOTH, MODE_DETECTION)
        wire_depth = mode in (MODE_BOTH, MODE_DEPTH)
        # Depth is the frame-counting branch only when detection isn't there to do it.
        depth_counts_frames = mode == MODE_DEPTH

        if wire_det:
            det_identity = self.pipeline.get_by_name("det_callback")
            if det_identity:
                det_identity.set_property("signal-handoffs", True)
                det_identity.connect(
                    "handoff", _internal_callback_wrapper, self.user_data, callbacks.on_det_frame, disable_callback
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

        # NOTE for the depth owner: this is where DepthPostProcessor.reset() belongs
        # once depth post-processing lands, so EMA-smoothed readings from the previous
        # mode don't bleed into the first frames of the new one.

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
        """
        if self._shutting_down:
            hailo_logger.debug("Rebuild requested during shutdown — ignoring")
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
        """
        if self._shutting_down:
            hailo_logger.debug("Skipping rebuild — shutdown in progress")
            self._rebuild_in_flight = False
            return False

        try:
            return super()._rebuild_pipeline()
        finally:
            self._last_rebuild_at = time.monotonic()
            self._rebuild_in_flight = False
            # The mode may have changed again while this rebuild was running
            # (coalesced above). Converge on it now.
            if not self._shutting_down and self.current_mode() != self._built_mode:
                hailo_logger.debug("Mode changed during rebuild — rebuilding again")
                self.trigger_rebuild()

    def shutdown(self, signum=None, frame=None):
        """
        Shut down cleanly even if the signal arrives mid-swap.

        The framework's shutdown() calls self.pipeline.set_state(...), but
        _rebuild_pipeline() sets self.pipeline = None while tearing the old
        pipeline down. A Ctrl+C landing in that window raises AttributeError —
        rare normally, but mode cycling passes through that window on every
        swap. Handled here in the subclass; the library is not patched.
        """
        self._shutting_down = True

        if self.pipeline is None:
            hailo_logger.warning("Shutdown during pipeline rebuild — quitting main loop directly")
            if self.loop is not None:
                GLib.idle_add(self.loop.quit)
            return

        super().shutdown(signum, frame)

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
