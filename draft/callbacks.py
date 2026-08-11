"""
Pipeline Callbacks — Process inference results from GStreamer pipeline.
These run in GStreamer streaming threads. They MUST be fast.
Rule: extract data, put_nowait into queue, return. No blocking.
"""

import queue

# ============================================================
# INTERFACE CONTRACT (do not change):
#   on_det_frame:   reads hailo detections → puts dict into tts_queue
#   on_depth_frame: reads hailo depth mask → puts dict into serial_queue
# ============================================================
# Try importing hailo — in mock mode this won't be available

try:
    import hailo
    import numpy as np
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False
    
from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_utils import (
    compute_zone_intensities,
    detect_ground_hazard
)

def on_det_frame(element, buffer, user_data):
    """Process detection results. Runs in GStreamer thread — must be fast."""
    if buffer is None:
        return
    
    if HAILO_AVAILABLE:
        _process_real_detections(buffer, user_data)
    else:
        pass  # Mock mode — mock_detection_generator handles this

def on_depth_frame(element, buffer, user_data):
    """Process depth results. Runs in GStreamer thread — must be fast."""
    if buffer is None:
        return
    
    if HAILO_AVAILABLE:
        _process_real_depth(buffer, user_data)
    else:
        pass  # Mock mode — mock_depth_generator handles this

def _get_detection_zone(bbox) -> str:
    """Determine zone from bounding box center position."""
    center_x = (bbox.xmin() + bbox.xmax()) / 2.0
    if center_x < 0.33:
        return "left"
    elif center_x > 0.66:
        return "right"
    return "center"

def _process_real_detections(buffer, user_data):
    """
    STUB — Extract detections from hailo buffer.
    
    Replace the zone logic and filtering as needed,
    but keep the queue.put_nowait interface.
    """
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
    
    for det in detections:
        label = det.get_label()
        confidence = det.get_confidence()
        zone = _get_detection_zone(det.get_bbox())
        
        try:
            user_data.tts_queue.put_nowait({
                "label": label,
                "zone": zone,
                "confidence": confidence,
            })
        except queue.Full:
            pass  # TTS busy — drop, don't block pipeline

def _process_real_depth(buffer, user_data):
    """
    Extract depth and compute zone proximities using depth_utils.
    """
    roi = hailo.get_roi_from_buffer(buffer)
    depth_masks = roi.get_objects_typed(hailo.HAILO_DEPTH_MASK)
    
    if not depth_masks:
        return
    
    mask = depth_masks[0]
    depth_mat = mask.get_data()
    
    try:
        h = mask.get_height()
        w = mask.get_width()
        depth_data = np.array(depth_mat).reshape((h, w))
    except Exception as e:
        depth_data = np.array(depth_mat)

    # Zone splitting (25/50/25) with percentile-based outlier filtering and hazard detection
    if depth_data.ndim == 2:
        h, w = depth_data.shape
        intensities = compute_zone_intensities(depth_data, w)
        hazard_detected, severity, hazard_dir = detect_ground_hazard(depth_data, h)

        try:
            user_data.serial_queue.put_nowait({
                "left":   intensities["left"],
                "center": intensities["center"],
                "right":  intensities["right"],
                "hazard": hazard_detected,
                "hazard_severity": severity,
                # Advisory extra: "down" (drop-off/fall) or "up" (curb/trip).
                # Existing keys unchanged; serial worker may map this to the
                # HAZARD_ALERT pattern byte if the protocol owner wants it.
                "hazard_direction": hazard_dir,
            })
        except queue.Full:
            pass
    else:
        # Fallback if the data isn't a 2D array
        try:
            user_data.serial_queue.put_nowait({
                "left":   0,
                "center": 0,
                "right":  0,
                "hazard": False,
                "hazard_severity": 0,
            })
        except queue.Full:
            pass
