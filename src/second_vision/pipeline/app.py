import os
import queue
import sys
import threading
from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import gi
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
import callbacks

hailo_logger = get_logger(__name__)

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

        # Handle list models flags for both
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

    def get_pipeline_string(self):
        source_pipeline = self.get_source_pipeline(no_webcam_compression=True)

        # 1. Depth Branch
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

        # 2. Detection Branch
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

        # 3. Parallel tee architecture (display handled by cv2 in callbacks.py)
        pipeline_str = (
            f"{source_pipeline} ! tee name=t "
            f"t. ! {QUEUE(name='depth_branch_q', leaky='downstream')} ! {depth_pipeline_wrapper} ! {depth_callback} ! {depth_sink} "
            f"t. ! {QUEUE(name='det_branch_q', leaky='downstream')} ! {detection_pipeline_wrapper} ! {tracker_pipeline} ! {det_callback} ! {det_sink}"
        )

        hailo_logger.info("Generated Pipeline string:\n%s", pipeline_str)
        return pipeline_str

    def _connect_callback(self):
        """
        Wire the detection and depth branches to their callbacks.py handlers.

        This pipeline exposes two USER_CALLBACK_PIPELINE identities
        ("det_callback" and "depth_callback") instead of the single
        "identity_callback" the base GStreamerApp expects, so the default
        _connect_callback can't find either one — this override replaces it.
        """
        disable_callback = self.options_menu.disable_callback

        # Detection branch goes through the internal wrapper for frame
        # counting/watchdog support.
        det_identity = self.pipeline.get_by_name("det_callback")
        if det_identity:
            det_identity.set_property("signal-handoffs", True)
            det_identity.connect(
                "handoff", _internal_callback_wrapper, self.user_data, callbacks.on_det_frame, disable_callback
            )
            hailo_logger.debug("Connected detection callback.")
        else:
            hailo_logger.warning("det_callback identity not found in pipeline")

        # Depth branch connects directly — the tee means both branches see
        # every frame, so routing depth through the wrapper too would
        # double-increment the shared frame counter.
        depth_identity = self.pipeline.get_by_name("depth_callback")
        if depth_identity:
            depth_identity.set_property("signal-handoffs", True)
            if not disable_callback:
                depth_identity.connect("handoff", callbacks.on_depth_frame, self.user_data)
            hailo_logger.debug("Connected depth callback.")
        else:
            hailo_logger.warning("depth_callback identity not found in pipeline")

    def trigger_rebuild(self):
        """
        Schedule a pipeline rebuild, called by config_reader_worker after a
        mode change. Rebuilds must go through GLib.idle_add — the config
        reader runs on its own thread, and GStreamer state changes aren't
        safe to make directly from a thread other than the main loop's.

        Note: get_pipeline_string() isn't mode-aware yet, so this currently
        just tears down and rebuilds the same fixed dual-branch pipeline —
        it doesn't yet switch to a detection-only/depth-only pipeline based
        on self.config.pipeline_mode.
        """
        GLib.idle_add(self._rebuild_pipeline)

class StandaloneUserData(callbacks.user_app_callback_class):
    """
    user_data for running this app directly (`python3 app.py`), outside of
    main.py's full SecondVisionUserData wiring — adds the queues callbacks.py's
    tts/serial contract needs on top of user_app_callback_class.
    """
    def __init__(self):
        super().__init__()
        self.tts_queue = queue.Queue(maxsize=1)
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
