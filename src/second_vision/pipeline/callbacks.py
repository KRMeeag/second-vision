"""
Pipeline Callbacks — Process inference results from GStreamer pipeline.
These run in GStreamer streaming threads. They MUST be fast.
Rule: extract data, put_nowait into queue, return. No blocking.
"""

import queue
import time

# ============================================================
# INTERFACE CONTRACT (do not change):
#   on_det_frame:   reads hailo detections → puts dict into tts_queue
#   on_depth_frame: reads hailo depth mask → puts dict into serial_queue
# ============================================================
# Try importing hailo — in mock mode this won't be available

try:
    import cv2
    # pyrefly: ignore [missing-import]
    import hailo
    import numpy as np
    from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
    from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

# ---- Object detection tuning ----
CONFIDENCE_THRESHOLD = 0.70
STATIONARY_ANNOUNCE_SECONDS = 10.0   # Minimum gap between repeated "still there" reminders
STALE_TRACK_FRAMES = 15              # Frames of tracker silence before a track is forgotten
HEAD_TURN_DELTA = 0.02               # Per-frame center_x shift (fraction of width) counted as movement
HEAD_TURN_RATIO = 0.7                # Fraction of tracked objects that must shift together
HEAD_TURN_MIN_TRACKED = 3            # Below this, one object's own motion can look like "everyone moved"
HEAD_TURN_SUPPRESS_SECONDS = 2.0     # Suppress "leaving center" announcements after a detected pan
DISPLAY_PHRASE_HOLD_SECONDS = 2.0    # How long an event phrase stays on the debug overlay before
                                      # falling back to the generic "{label} {zone}" text — event
                                      # phrases only fire for one frame otherwise, too brief to read

# ---- Zone boundaries (fractions of frame width) ----
LEFT_BOUNDARY = 0.22          # Below this -> "left"
CENTER_LEFT_BOUNDARY = 0.25   # Between this and CENTER_RIGHT_BOUNDARY -> "center"
CENTER_RIGHT_BOUNDARY = 0.75
RIGHT_BOUNDARY = 0.78         # Above this -> "right"


def on_det_frame(element, buffer, user_data):
    """Process detection results. Runs in GStreamer thread — must be fast."""
    if buffer is None:
        return

    if HAILO_AVAILABLE:
        _process_real_detections(element, buffer, user_data)
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

    Enter left:    center_x < LEFT_BOUNDARY (0.22)
    Enter center:  CENTER_LEFT_BOUNDARY < center_x < CENTER_RIGHT_BOUNDARY (0.25-0.75)
    Enter right:   center_x > RIGHT_BOUNDARY (0.78)
    Hysteresis:    LEFT_BOUNDARY-CENTER_LEFT_BOUNDARY and
                   CENTER_RIGHT_BOUNDARY-RIGHT_BOUNDARY keep the previous zone
                   instead of dropping the detection.
    """
    center_x = (bbox.xmin() + bbox.xmax()) / 2.0

    if center_x < LEFT_BOUNDARY:
        return "left"
    if center_x > RIGHT_BOUNDARY:
        return "right"
    if CENTER_LEFT_BOUNDARY < center_x < CENTER_RIGHT_BOUNDARY:
        return "center"

    if LEFT_BOUNDARY <= center_x <= CENTER_LEFT_BOUNDARY:
        if previous_zone in ("left", "center"):
            return previous_zone
        return "left"

    if CENTER_RIGHT_BOUNDARY <= center_x <= RIGHT_BOUNDARY:
        if previous_zone in ("center", "right"):
            return previous_zone
        return "right"

    return "center"


def _get_track_id(det) -> int:
    """Return the tracker-assigned unique ID for a detection, or 0 if untracked."""
    track = det.get_objects_typed(hailo.HAILO_UNIQUE_ID)
    return track[0].get_id() if len(track) == 1 else 0


# user_app_callback_class subclasses the real app_callback_class, which is
# only bound above when the hailo/gi import succeeded — define it inside the
# same guard so mock-mode environments still import this module cleanly
# instead of hitting a NameError partway through module load.
if HAILO_AVAILABLE:
    class user_app_callback_class(app_callback_class):
        def __init__(self):
            super().__init__()
            # Structure: {track_id: {'direction': str, 'label': str, 'last_frame': int}, 'time': int}
            self.track_history = {}
            self.fps_start_time = time.monotonic()
            # Set of track_ids that have moved from center to a side zone
            self.IDs_changed_zones = set()
            self.head_turn_cooldown_until = 0.0

            # Depth-branch counterpart to frame_count/fps_start_time above.
            # Kept fully separate rather than sharing the base class's
            # frame_count: that one is only ever incremented by the detection
            # branch (see _connect_callback's comment on why depth is
            # connected directly, bypassing _internal_callback_wrapper, to
            # avoid double-incrementing it) and is watchdog plumbing besides
            # — not a meaningful per-branch throughput figure. The two
            # branches never converge in the pipeline and can fall behind
            # each other independently (separate models, separate
            # leaky='downstream' queues), so depth needs its own counter to
            # report its own real rate instead of borrowing detection's.
            # Not wired up to anything yet — call self.increment_depth() from
            # wherever the real depth processing ends up, once that's built.
            self.depth_frame_count = 0
            self.depth_fps_start_time = time.monotonic()

        def get_det_fps(self):
            elapsed = time.monotonic() - self.fps_start_time
            if elapsed > 0:
                return self.get_count() / elapsed
            return 0.0

        def increment_depth(self):
            """Call this once per processed depth frame — mirrors the base class's increment()."""
            self.depth_frame_count += 1

        def get_depth_count(self):
            return self.depth_frame_count

        def get_depth_fps(self):
            elapsed = time.monotonic() - self.depth_fps_start_time
            if elapsed > 0:
                return self.depth_frame_count / elapsed
            return 0.0


def _is_head_turning(detections, track_history: dict) -> bool:
    """
    Detect a fast camera pan: if most tracked objects shift sideways together
    between frames, the wearer likely turned their head rather than every
    object independently moving the same direction.

    Requires at least HEAD_TURN_MIN_TRACKED objects before it can fire at all —
    with only one or two tracked, that object's own ordinary motion (e.g. it
    walking from center out to a side, which is exactly the transition this
    is meant to avoid misreading) trivially satisfies "100% moved together"
    and would otherwise suppress its own "leaving center" announcement.
    """
    total = shifted_left = shifted_right = 0
    for det in detections:
        prev = track_history.get(_get_track_id(det))
        if prev is None:
            continue

        bbox = det.get_bbox()
        center_x = bbox.xmin() + (bbox.width() / 2.0)
        delta_x = center_x - prev.get("center_x", center_x)

        if delta_x > HEAD_TURN_DELTA:
            shifted_right += 1
        elif delta_x < -HEAD_TURN_DELTA:
            shifted_left += 1
        total += 1

    if total < HEAD_TURN_MIN_TRACKED:
        return False
    return (shifted_right / total) >= HEAD_TURN_RATIO or (shifted_left / total) >= HEAD_TURN_RATIO


def _process_real_detections(element, buffer, user_data):
    """
    Extract detections from the hailo buffer, update per-track zone/timing
    state, and queue the single most salient event for the TTS worker.

    Priority when multiple detections qualify: higher confidence first,
    then larger bbox (closer object).
    """
    roi = hailo.get_roi_from_buffer(buffer)
    detections = list(roi.get_objects_typed(hailo.HAILO_DETECTION))

    # Note: deliberately not returning early when `detections` is empty — the
    # stale-track cleanup below still needs to run on empty frames, otherwise
    # an object that abruptly leaves the frame keeps its zone/cooldown state
    # alive until some *other* detection happens to show up later.
    track_history = user_data.track_history
    now = time.time()
    frame_count = user_data.get_count()

    # A fast head turn makes every tracked object appear to slide sideways
    # together; suppress "leaving center" announcements for a bit so it isn't
    # misread as every object simultaneously walking out of frame.
    if _is_head_turning(detections, track_history):
        user_data.head_turn_cooldown_until = now + HEAD_TURN_SUPPRESS_SECONDS
    is_head_turning = now < user_data.head_turn_cooldown_until

    candidates = []
    active_zones = set()
    det_labels = {}  # (zone, label) -> (display_text, bbox, area, track_id) — debug overlay only

    for det in detections:
        confidence = det.get_confidence()
        if confidence < CONFIDENCE_THRESHOLD:
            continue

        label = det.get_label()
        bbox = det.get_bbox()
        track_id = _get_track_id(det)
        center_x = bbox.xmin() + (bbox.width() / 2.0)

        prev = track_history.get(track_id)
        previous_zone = prev["zone"] if prev else None
        zone = _get_detection_zone(bbox, previous_zone)

        zone_since = now
        last_announced = 0.0
        phrase = None
        should_announce = True
        announced_stationary = False
        display_phrase = None
        display_phrase_until = 0.0

        if prev is not None and prev["label"] == label:
            zone_since = prev.get("zone_since", now)
            last_announced = prev.get("last_announced", 0.0)
            display_phrase = prev.get("display_phrase")
            display_phrase_until = prev.get("display_phrase_until", 0.0)

            if prev["zone"] != zone:
                # Direction changed — restart the "how long has it been here" timer.
                zone_since = now

                if prev["zone"] == "center" and zone != "center":
                    if not is_head_turning:
                        phrase = f"{label} leaving to {zone}"
                        user_data.IDs_changed_zones.add(track_id)
                elif zone == "center":
                    user_data.IDs_changed_zones.discard(track_id)
            else:
                stationary_for = now - zone_since
                if stationary_for > STATIONARY_ANNOUNCE_SECONDS:
                    if now - last_announced < STATIONARY_ANNOUNCE_SECONDS:
                        # Already reminded recently about this stationary object — stay quiet.
                        should_announce = False
                    else:
                        phrase = f"{label} still {zone}"
                        announced_stationary = True

        if phrase:
            display_phrase = phrase
            display_phrase_until = now + DISPLAY_PHRASE_HOLD_SECONDS

        track_history[track_id] = {
            "zone": zone,
            "label": label,
            "last_frame": frame_count,
            "zone_since": zone_since,
            "center_x": center_x,
            # Only the stationary reminder needs its own cooldown clock — a
            # plain per-frame sighting must not keep refreshing this, or the
            # very first reminder would always find itself "just announced".
            "last_announced": now if announced_stationary else last_announced,
            "display_phrase": display_phrase,
            "display_phrase_until": display_phrase_until,
        }

        area = _bbox_area(bbox)
        # Event phrases (leaving/still) only fire for the one frame they're
        # decided on — hold them on screen for a bit instead of instantly
        # reverting to the generic text before anyone can read them.
        showing_event_phrase = display_phrase is not None and now < display_phrase_until
        display_text = display_phrase if showing_event_phrase else f"{label} {zone}"
        key = (zone, label)
        if key in det_labels:
            prev_text, prev_bbox, prev_area, prev_track_id = det_labels[key]
            if not prev_text.startswith("multiple"):
                prev_text = f"multiple {prev_text}"
            if area > prev_area:
                det_labels[key] = (f"multiple {display_text}", bbox, area, track_id)
            else:
                det_labels[key] = (prev_text, prev_bbox, prev_area, prev_track_id)
        else:
            det_labels[key] = (display_text, bbox, area, track_id)
        active_zones.add(zone)

        if should_announce:
            candidates.append((confidence, area, label, zone, phrase))

    # Forget tracks the tracker hasn't updated recently, so zone hysteresis and
    # cooldown state don't leak onto an unrelated object that later reuses the ID.
    stale_ids = [
        track_id for track_id, hist in track_history.items()
        if frame_count - hist["last_frame"] > STALE_TRACK_FRAMES
    ]
    for track_id in stale_ids:
        track_history.pop(track_id, None)
        user_data.IDs_changed_zones.discard(track_id)

    if user_data.use_frame:
        _draw_detection_overlay(element, buffer, user_data, active_zones, det_labels, user_data.get_det_fps())

    if not candidates:
        return

    confidence, _area, label, zone, phrase = max(candidates, key=lambda c: (c[0], c[1]))
    payload = {"label": label, "zone": zone, "confidence": confidence}
    if phrase:
        payload["phrase"] = phrase

    try:
        user_data.tts_queue.put_nowait(payload)
    except queue.Full:
        pass  # TTS busy — drop, don't block pipeline


def _draw_detection_overlay(element, buffer, user_data, active_zones: set, det_labels: dict, fps: float) -> None:
    """
    Debug visualization: tint active zones, draw zone-divider lines, and draw
    a labeled bounding box per (zone, label) group.

    Only runs when the app requests frame display (user_data.use_frame) — it's
    a developer visual-confirmation aid, not part of the "must be fast" path.
    """
    pad = element.get_static_pad("src")
    fmt, width, height = get_caps_from_pad(pad)
    if fmt is None or width is None or height is None:
        return

    frame = get_numpy_from_buffer(buffer, fmt, width, height)
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)  # Hailo gives RGB, cv2 wants BGR

    left_x = int(width * LEFT_BOUNDARY)
    left_hyst_x = int(width * CENTER_LEFT_BOUNDARY)
    right_hyst_x = int(width * CENTER_RIGHT_BOUNDARY)
    right_x = int(width * RIGHT_BOUNDARY)

    if active_zones:
        overlay = frame_bgr.copy()
        tint_color = (0, 0, 255)  # Red in BGR
        if "left" in active_zones:
            cv2.rectangle(overlay, (0, 0), (left_x, height), tint_color, -1)
        if "center" in active_zones:
            cv2.rectangle(overlay, (left_hyst_x, 0), (right_hyst_x, height), tint_color, -1)
        if "right" in active_zones:
            cv2.rectangle(overlay, (right_x, 0), (width, height), tint_color, -1)
        frame_bgr = cv2.addWeighted(overlay, 0.3, frame_bgr, 0.7, 0)

    cv2.line(frame_bgr, (left_x, 0), (left_x, height), (255, 0, 0), 2)
    cv2.line(frame_bgr, (left_hyst_x, 0), (left_hyst_x, height), (0, 255, 0), 2)
    cv2.line(frame_bgr, (right_hyst_x, 0), (right_hyst_x, height), (0, 255, 0), 2)
    cv2.line(frame_bgr, (right_x, 0), (right_x, height), (0, 0, 255), 2)

    for display_text, bbox, _area, track_id in det_labels.values():
        x1, y1 = int(bbox.xmin() * width), int(bbox.ymin() * height)
        x2, y2 = int(bbox.xmax() * width), int(bbox.ymax() * height)
        bbox_color = (0, 165, 255) if track_id in user_data.IDs_changed_zones else (0, 255, 0)

        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), bbox_color, 2)
        cv2.putText(frame_bgr, display_text, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        cv2.putText(frame_bgr, f"ID: {track_id}", (x1, y1 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    cv2.putText(frame_bgr, f"FPS: {fps:.1f}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

    user_data.set_frame(frame_bgr)


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
