import os
import sys
from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

import hailo
import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection.sv_pipeline_v3 import GStreamerParallelApp
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class, _internal_callback_wrapper
from hailo_apps.python.core.common.hailo_logger import get_logger

hailo_logger = get_logger(__name__)

class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()

    def get_depth_stats(self, depth_mat):
        depth_values = np.array(depth_mat).flatten()
        try:
            m_depth_values = depth_values[depth_values <= np.percentile(depth_values, 95)]
        except Exception:
            m_depth_values = np.array([])
            
        if len(m_depth_values) > 0:
            avg_d = np.mean(m_depth_values)
            min_d = np.min(m_depth_values)
            max_d = np.max(m_depth_values)
            return avg_d, min_d, max_d
        return 0, 0, 0

def on_depth_frame(element, buffer, user_data):
    if buffer is None:
        return
    
    # Throttle printing to every 15 frames to reduce terminal overhead
    frame_count = user_data.get_count()
    if frame_count % 15 != 0:
        return

    roi = hailo.get_roi_from_buffer(buffer)
    depth_mask = roi.get_objects_typed(hailo.HAILO_DEPTH_MASK)
    
    if len(depth_mask) > 0:
        avg_d, min_d, max_d = user_data.get_depth_stats(depth_mask[0].get_data())
        print(f"[DEPTH] Frame {frame_count} | Avg: {avg_d:.2f} | Min: {min_d:.2f} | Max: {max_d:.2f}")

def on_det_frame(element, buffer, user_data):
    if buffer is None:
        return
        
    # Throttle printing to every 15 frames to reduce terminal overhead
    frame_count = user_data.get_count()
    if frame_count % 15 != 0:
        return

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
    
    print(f"[DETECTION] Frame {frame_count} | Found {len(detections)} objects")
    for det in detections:
        label = det.get_label()
        confidence = det.get_confidence()
        bbox = det.get_bbox()
        print(f"  -> {label} ({confidence*100:.1f}%) | BBox: xmin={bbox.xmin():.2f}, ymin={bbox.ymin():.2f}, xmax={bbox.xmax():.2f}, ymax={bbox.ymax():.2f}")

class GStreamerDualApp(GStreamerParallelApp):
    def _connect_callback(self):
        disable_callback = self.options_menu.disable_callback
        
        # Connect Detection branch using the internal wrapper to handle frame counting and watchdog
        det_identity = self.pipeline.get_by_name("det_callback")
        if det_identity:
            det_identity.set_property("signal-handoffs", True)
            det_identity.connect(
                "handoff", _internal_callback_wrapper, self.user_data, on_det_frame, disable_callback
            )
            hailo_logger.debug("Connected detection callback.")

        # Connect Depth branch directly (to avoid double-incrementing the frame counter)
        depth_identity = self.pipeline.get_by_name("depth_callback")
        if depth_identity:
            depth_identity.set_property("signal-handoffs", True)
            if not disable_callback:
                depth_identity.connect("handoff", on_depth_frame, self.user_data)
            hailo_logger.debug("Connected depth callback.")

def main():
    hailo_logger.info("Starting SV Dual Pipeline App")
    user_data = user_app_callback_class()
    # Pass None for app_callback because we explicitly connect them in _connect_callback override
    app = GStreamerDualApp(None, user_data)
    app.run()

if __name__ == "__main__":
    main()
