import os
import cv2
import time
import numpy as np

from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
import hailo
from hailo_apps.python.pipeline_apps.custom_depth_detection.sv_pipeline_v4 import GStreamerParallelApp
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class, _internal_callback_wrapper
from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer

# Constant values for object detection
LEFT_BOUNDARY = 0.22
CENTER_LEFT_BOUNDARY = 0.25
CENTER_RIGHT_BOUNDARY = 0.75
RIGHT_BOUNDARY = 0.78

CONFIDENCE_THRESHOLD = 0.70

hailo_logger = get_logger(__name__)

class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        # Structure: {track_id: {'direction': str, 'label': str, 'last_frame': int}, 'time': int}
        self.track_history = {}
        self.fps_start_time = time.monotonic()
        # Set of track_ids that have moved from center to a side zone
        self.IDs_changed_zones = set()
        self.head_turn_cooldown_until = 0.0

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

    def get_fps(self):
        elapsed = time.monotonic() - self.fps_start_time
        if elapsed> 0:
            return self.get_count() / elapsed
        return 0.0

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

def cv2_draw_det(frame_bgr, active_zones, det_labels, width, height, user_data):
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
    for (direction, label), (display_text, bbox, area, track_id) in det_labels.items():
        x1 = int(bbox.xmin() * width)
        y1 = int(bbox.ymin() * height)
        x2 = int(bbox.xmax() * width)
        y2 = int(bbox.ymax() * height)

        if track_id in user_data.IDs_changed_zones:
            bbox_color = (0, 165, 255)  # Orange for objects that left the center
        else:
            bbox_color = (0, 255, 0)    # Green for standard detections

        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), bbox_color, 2)
        cv2.putText(
            img=frame_bgr,
            text=display_text,
            org=(x1, y1 - 10),
            fontFace=cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=0.7,
            color=(0, 0, 0),
            thickness=2
        )
        cv2.putText(
            img=frame_bgr,
            text=f"ID: {track_id}",
            org=(x1, y1 + 15),
            fontFace=cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=0.6,
            color=(0, 255, 0),
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

    # --- Pass 0: Head Turn Suppression Logic ---
    total_tracked = 0
    shifted_left = 0
    shifted_right = 0
    
    for det in detections:
        track = det.get_objects_typed(hailo.HAILO_UNIQUE_ID)
        if len(track) == 1:
            track_id = track[0].get_id()
            if track_id in user_data.track_history:
                bbox = det.get_bbox()
                center_x = bbox.xmin() + (bbox.width() / 2.0)
                # Compare to previous frame
                prev_x = user_data.track_history[track_id].get("center_x", center_x)
                delta_x = center_x - prev_x
                
                # 0.02 threshold = 2% of frame width
                if delta_x > 0.02:
                    shifted_right += 1
                elif delta_x < -0.02:
                    shifted_left += 1
                total_tracked += 1

    # Check if >70% of tracked objects moved in the same direction
    is_head_turning = False
    if total_tracked > 0:
        if (shifted_right / total_tracked) >= 0.7 or (shifted_left / total_tracked) >= 0.7:
            user_data.head_turn_cooldown_until = time.time() + 2.0  # Suppress transitions for 2 seconds

    if time.time() < user_data.head_turn_cooldown_until:
        is_head_turning = True

    # --- Pass 1: Collect active zones and detection info ---
    active_zones = set()
    # Structure: {(direction, label): (display_text, bbox, area, track_id)}
    det_labels = {} 

    for det in detections:
        # Get object properties from detection
        label = det.get_label()
        confidence = det.get_confidence()
        bbox = det.get_bbox()
        track = det.get_objects_typed(hailo.HAILO_UNIQUE_ID)
        track_id = (track[0].get_id() if len(track) == 1 else 0)

        # Get the center x coordinate of the bounding box
        center_x = bbox.xmin() + (bbox.width() / 2.0)

        # Get the label for the detection
        direction_text = f"{label} direction cannot be determined!"
        # print(f"[DET] Confidence value of {label} is {confidence*100:.0f}%")

        # Filter detections based on confidence threshold
        if confidence >= CONFIDENCE_THRESHOLD:
            # Direction Assignment
            if center_x <= LEFT_BOUNDARY:
                direction_text = f"{label} on left!"
                direction = "left"
            elif center_x >= CENTER_LEFT_BOUNDARY and center_x <= CENTER_RIGHT_BOUNDARY:
                direction_text = f"{label} in front!"
                direction = "center"
            elif center_x >= RIGHT_BOUNDARY:
                direction_text = f"{label} on right!"
                direction = "right"
            else:
                direction_text = f"{label} not identifiable!"
                continue

            # Handle tracking state and stationary logic
            start_time = time.time() # Default for brand new objects
            
            if track_id in user_data.track_history:
                track_hist = user_data.track_history[track_id]
                
                # Carry over the original start time from the history
                start_time = track_hist.get("start_time", time.time())

                if track_hist["label"] == label:
                    # If direction changed, reset the stationary timer
                    if track_hist["direction"] != direction:
                        start_time = time.time()

                    # 1. Stationary Object Logic
                    if track_hist["direction"] == direction and (time.time() - start_time) > 10:
                        direction_text = f"{label} still on {direction}"
                    
                    # 2. Leaving Center Logic
                    elif track_hist["direction"] == "center" and direction != "center":
                        if not is_head_turning:
                            direction_text = f"center {label} leaving {direction}!"
                            user_data.IDs_changed_zones.add(track_id)
                        
                    # 3. Returning to Center Logic
                    elif track_hist["direction"] != "center" and direction == "center":
                        user_data.IDs_changed_zones.discard(track_id)

            # Update the history
            user_data.track_history[track_id] = {
                "direction": direction,
                "label": label,
                "last_frame": frame_count,
                "start_time": start_time,
                "center_x": center_x
            }

            # Add or update detection in the list
            area = bbox.width() * bbox.height()
            if (direction, label) in det_labels:
                prev_text, prev_bbox, prev_area, prev_track_id = det_labels[(direction, label)]

                # Make sure the current text says "multiple"
                if not prev_text.startswith("multiple"):
                    prev_text = f"multiple {prev_text}"

                if area > prev_area:
                    det_labels[(direction, label)] = (f"multiple {direction_text}", bbox, area, track_id)
                else:
                    det_labels[(direction, label)] = (prev_text, prev_bbox, prev_area, prev_track_id)
            else:
                det_labels[(direction, label)] = (direction_text, bbox, area, track_id)
            
            active_zones.add(direction)

    # --- Pass 2: Stale ID Related Logic ---
    stale_ids = []
    for track_id in user_data.track_history:
        if frame_count - user_data.track_history[track_id]["last_frame"] > 15:
            stale_ids.append(track_id)

    for track_id in stale_ids:
        user_data.track_history.pop(track_id, None)
        user_data.IDs_changed_zones.discard(track_id)
    
    # --- Pass 3: Draw zone tints, lines, and text ---
    if frame_bgr is not None:
        cv2_draw_det(frame_bgr, active_zones, det_labels, width, height, user_data)

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
