'''
NOTE:
Prototyping for object detection is currently being done
in another separate repo within the RPI. 
This file aims to backup the progress of the development
of the callbacks for the Object Detection in the event
that the SD Card corrupts.

Once finalized, the object detection callback will be
migrated to callbacks.py for integration testing and
this file will be DELETED

'''

import os
from pathlib import Path
import cv2
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

import hailo
import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection.sv_pipeline_v4 import GStreamerParallelApp
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class, _internal_callback_wrapper
from hailo_apps.python.core.common.hailo_logger import get_logger

from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer

LEFT_BOUNDARY = 0.30
CENTER_LEFT_BOUNDARY = 0.36
CENTER_RIGHT_BOUNDARY = 0.64
RIGHT_BOUNDARY = 0.70

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
        # print(f"[DEPTH] Frame {frame_count} | Avg: {avg_d:.2f} | Min: {min_d:.2f} | Max: {max_d:.2f}")


def cv2_draw_det(frame_bgr, active_zones, det_labels):
    left_line_x = int(width * LEFT_BOUNDARY)
    center_left_line_x = int(width * CENTER_LEFT_BOUNDARY)
    center_right_line_x = int(width * CENTER_RIGHT_BOUNDARY)
    right_line_x = int(width * RIGHT_BOUNDARY)

    # Draw semi-transparent red tint on active zones
    overlay = frame_bgr.copy()
    tint_color = (0, 0, 255)  # Red in BGR

    if "left" in active_zones:
        cv2.rectangle(overlay, (0, 0), (left_line_x, height), tint_color, -1)
    if "center" in active_zones:
        cv2.rectangle(overlay, (center_left_line_x, 0), (center_right_line_x, height), tint_color, -1)
    if "right" in active_zones:
        cv2.rectangle(overlay, (right_line_x, 0), (width, height), tint_color, -1)

    # Blend: 30% tint + 70% original
    if active_zones:
        frame_bgr = cv2.addWeighted(overlay, 0.3, frame_bgr, 0.7, 0)

    # Draw zone divider lines on top of the blended frame
    cv2.line(frame_bgr, (left_line_x, 0), (left_line_x, height), (255, 0, 0), 2)
    cv2.line(frame_bgr, (center_left_line_x, 0), (center_left_line_x, height), (0, 255, 0), 2)
    cv2.line(frame_bgr, (center_right_line_x, 0), (center_right_line_x, height), (0, 255, 0), 2)
    cv2.line(frame_bgr, (right_line_x, 0), (right_line_x, height), (0, 0, 255), 2)

    # Draw bounding boxes and text labels for each person detection
    for (direction, label), (display_text, bbox) in det_labels.items():
        x1 = int(bbox.xmin() * width)
        y1 = int(bbox.ymin() * height)
        x2 = int(bbox.xmax() * width)
        y2 = int(bbox.ymax() * height)

        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            img=frame_bgr,
            text=display_text,
            org=(x1, y1 - 10),
            fontFace=cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=0.7,
            color=(0, 0, 0),
            thickness=2
        )

    user_data.set_frame(frame_bgr)

def on_det_frame(element, buffer, user_data):
    if buffer is None:
        return

    frame_count = user_data.get_count()

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
    
    # Get the frame from the GStreamer buffer (not from the queue!)
    pad = element.get_static_pad("src")
    fmt, width, height = get_caps_from_pad(pad)

    if not user_data.use_frame or fmt is None or width is None or height is None:
        # No frame available — still print detection info below
        frame_bgr = None
    else:
        frame = get_numpy_from_buffer(buffer, fmt, width, height)
        # Hailo provides RGB, but CV2 expects BGR
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    # --- Pass 1: Collect active zones and detection info ---
    active_zones = set()
    det_labels = {}  # Store (display_text, bbox) for drawing later

    for det in detections:
        label = det.get_label()
        confidence = det.get_confidence()
        bbox = det.get_bbox()

        center_x = bbox.xmin() + (bbox.width() / 2.0)

        display_text = f"{label} ({confidence*100:.0f}%)"
        if label == "person":
            if center_x <= LEFT_BOUNDARY:
                display_text = "Person at the left!"
                direction = "left"
            elif center_x >= CENTER_LEFT_BOUNDARY and center_x <= CENTER_RIGHT_BOUNDARY:
                display_text = "Person in the middle!"
                direction = "center"
            elif center_x >= RIGHT_BOUNDARY:
                display_text = "Person in the right!"
                direction = "right"
            else:
                display_text = "Person not identifiable!"
                continue

            if (direction, label) in det_labels:
                curr_text, curr_bbox = det_labels[(direction, label)]
                if (bbox.width() * bbox.height()) > (curr_bbox.width() * curr_bbox.height()):
                    det_labels[(direction, label)] = (display_text, bbox)
                else:
                    det_labels[(direction, label)] = (curr_text, curr_bbox)
            else:
                det_labels[(direction, label)] = (display_text, bbox)
            
            active_zones.add(direction)

    # --- Pass 2: Draw zone tints, lines, and text ---
    if frame_bgr is not None:
        left_line_x = int(width * LEFT_BOUNDARY)
        center_left_line_x = int(width * CENTER_LEFT_BOUNDARY)
        center_right_line_x = int(width * CENTER_RIGHT_BOUNDARY)
        right_line_x = int(width * RIGHT_BOUNDARY)

        # Draw semi-transparent red tint on active zones
        overlay = frame_bgr.copy()
        tint_color = (0, 0, 255)  # Red in BGR

        if "left" in active_zones:
            cv2.rectangle(overlay, (0, 0), (left_line_x, height), tint_color, -1)
        if "center" in active_zones:
            cv2.rectangle(overlay, (center_left_line_x, 0), (center_right_line_x, height), tint_color, -1)
        if "right" in active_zones:
            cv2.rectangle(overlay, (right_line_x, 0), (width, height), tint_color, -1)

        # Blend: 30% tint + 70% original
        if active_zones:
            frame_bgr = cv2.addWeighted(overlay, 0.3, frame_bgr, 0.7, 0)

        # Draw zone divider lines on top of the blended frame
        cv2.line(frame_bgr, (left_line_x, 0), (left_line_x, height), (255, 0, 0), 2)
        cv2.line(frame_bgr, (center_left_line_x, 0), (center_left_line_x, height), (0, 255, 0), 2)
        cv2.line(frame_bgr, (center_right_line_x, 0), (center_right_line_x, height), (0, 255, 0), 2)
        cv2.line(frame_bgr, (right_line_x, 0), (right_line_x, height), (0, 0, 255), 2)

        # Draw bounding boxes and text labels for each person detection
        for (direction, label), (display_text, bbox) in det_labels.items():
            x1 = int(bbox.xmin() * width)
            y1 = int(bbox.ymin() * height)
            x2 = int(bbox.xmax() * width)
            y2 = int(bbox.ymax() * height)

            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                img=frame_bgr,
                text=display_text,
                org=(x1, y1 - 10),
                fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                fontScale=0.7,
                color=(0, 0, 0),
                thickness=2
            )

        user_data.set_frame(frame_bgr)

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
