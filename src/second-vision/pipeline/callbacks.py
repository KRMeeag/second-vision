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

def _bbox_area(bbox) -> float:
    """Bounding-box area as a proximity proxy (larger = closer)."""
    return max(0.0, bbox.xmax() - bbox.xmin()) * max(0.0, bbox.ymax() - bbox.ymin())


def _get_detection_zone(bbox, previous_zone: str | None = None) -> str:
    """
    Determine zone from bounding box center with boundary hysteresis.

    Enter left:    center_x < 0.30
    Enter center:  0.36 < center_x < 0.64
    Enter right:   center_x > 0.70
    Hysteresis:    0.30-0.36 and 0.64-0.70 keep previous zone
    """
    center_x = (bbox.xmin() + bbox.xmax()) / 2.0

    if center_x < 0.30:
        return "left"
    if center_x > 0.70:
        return "right"
    if 0.36 < center_x < 0.64:
        return "center"

    if 0.30 <= center_x <= 0.36:
        if previous_zone in ("left", "center"):
            return previous_zone
        return "left"

    if 0.64 <= center_x <= 0.70:
        if previous_zone in ("center", "right"):
            return previous_zone
        return "right"

    return "center"


def _process_real_detections(buffer, user_data):
    """
    Extract detections from hailo buffer and queue the highest-priority one.

    Priority: higher confidence first, then larger bbox (closer object).
    """
    roi = hailo.get_roi_from_buffer(buffer)
    detections = list(roi.get_objects_typed(hailo.HAILO_DETECTION))
    if not detections:
        return

    if not hasattr(user_data, "_zone_cache"):
        user_data._zone_cache = {}

    best = max(
        detections,
        key=lambda det: (det.get_confidence(), _bbox_area(det.get_bbox())),
    )

    label = best.get_label()
    confidence = best.get_confidence()
    bbox = best.get_bbox()
    previous_zone = user_data._zone_cache.get(label)
    zone = _get_detection_zone(bbox, previous_zone)
    user_data._zone_cache[label] = zone

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
    STUB — Extract depth and compute zone proximities.
    
    Replace the zone splitting and proximity math as needed,
    but keep the queue.put_nowait interface.
    """
    roi = hailo.get_roi_from_buffer(buffer)
    depth_masks = roi.get_objects_typed(hailo.HAILO_DEPTH_MASK)
    
    if not depth_masks:
        return
    
    depth_data = np.array(depth_masks[0].get_data())
    
    # STUB: Simple zone splitting (replace with real depth_utils)
    if depth_data.ndim == 2:
        h, w = depth_data.shape
        left_avg   = float(np.mean(depth_data[:, :w//4]))
        center_avg = float(np.mean(depth_data[:, w//4:3*w//4]))
        right_avg  = float(np.mean(depth_data[:, 3*w//4:]))
    else:
        left_avg = center_avg = right_avg = 0.0
    
    # STUB: Linear proximity (replace with exponential curve)
    def to_intensity(avg, max_depth=5.0):
        clamped = max(0.0, min(avg, max_depth))
        return int((1.0 - clamped / max_depth) * 255)
    
    try:
        user_data.serial_queue.put_nowait({
            "left":   to_intensity(left_avg),
            "center": to_intensity(center_avg),
            "right":  to_intensity(right_avg),
            "hazard": False,  # STUB: replace with hazard detection
            "hazard_severity": 0,
        })
    except queue.Full:
        pass