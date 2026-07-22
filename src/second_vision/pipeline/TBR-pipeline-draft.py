import os
from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import gi
import setproctitle

gi.require_version("Gst", "1.0")
from gi.repository import Gst

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
    app_callback_class,
    dummy_callback,
)
from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import (
    DISPLAY_PIPELINE,
    INFERENCE_PIPELINE,
    INFERENCE_PIPELINE_WRAPPER,
    USER_CALLBACK_PIPELINE,
    TRACKER_PIPELINE,
    QUEUE,
)

hailo_logger = get_logger(__name__)

class GStreamerParallelApp(GStreamerApp):
    def __init__(self, app_callback, user_data, parser=None):
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
        depth_display = DISPLAY_PIPELINE(
            video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps, name="depth_display"
        )
        
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
        det_display = DISPLAY_PIPELINE(
            video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps, name="det_display"
        )

        # 3. Parallel tee architecture with separate display sinks
        pipeline_str = (
            f"{source_pipeline} ! tee name=t "
            f"t. ! {QUEUE(name='depth_branch_q', leaky='downstream')} ! {depth_pipeline_wrapper} ! {depth_callback} ! {depth_display} "
            f"t. ! {QUEUE(name='det_branch_q', leaky='downstream')} ! {detection_pipeline_wrapper} ! {tracker_pipeline} ! {det_callback} ! {det_display}"
        )
        
        hailo_logger.info("Generated Pipeline string:\n%s", pipeline_str)
        return pipeline_str

def main():
    hailo_logger.info("Creating user data for the app callback...")
    user_data = app_callback_class()
    app_callback = dummy_callback
    app = GStreamerParallelApp(app_callback, user_data)
    app.run()

if __name__ == "__main__":
    hailo_logger.info("Starting Parallel Depth & Detection App V3...")
    main()
