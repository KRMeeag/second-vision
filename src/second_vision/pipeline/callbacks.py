"""
Pipeline Callbacks — Process inference results from GStreamer pipeline.
These run in GStreamer streaming threads. They MUST be fast.
Rule: extract data, put_nowait into queue, return. No blocking.
"""

import atexit
import multiprocessing
import os
import queue
import time

# ============================================================
# INTERFACE CONTRACT (do not change):
#   on_det_frame:   reads hailo detections → puts dict into tts_queue
#   on_depth_frame: reads hailo depth mask → puts dict into serial_queue
#
#   serial_queue item (5 fields, all always present):
#     left/center/right  int 0-255  MOTOR DUTY, not a perception score. Already
#                                   deadbanded, quantized, floored above the
#                                   motors' start threshold and pulse-gated by
#                                   core/haptics.py. 0 means "hold still"; any
#                                   non-zero value is meant to be felt.
#     hazard             bool       ground break, debounced and LATCHED
#     hazard_severity    int 0-255  break SIZE, not a distance — a different
#                                   scale from the zone values, never compare
#                                   the two. 0 whenever hazard is False.
#
#   Every exit path publishes. A frame is never silently skipped, because the
#   ESP32 holds its last command for 3 s and a stale "clear" reads as open space.
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

# Depth post-processing (core/depth_utils.py and friends) needs only numpy/cv2 —
# no hailo, no gi — so it gets its OWN import guard rather than riding on
# HAILO_AVAILABLE. Folding it into the block above would mean a missing depth
# module silently switched the *detection* path off too, which is exactly the
# kind of quiet degradation this project keeps getting bitten by.
_DEPTH_IMPORT_ERROR = None
try:
    from second_vision.core.calibration import CalibrationLogger
    from second_vision.core.capture import FrameCapture
    from second_vision.core.depth_utils import (
        DepthPostProcessor,
        HazardDebouncer,
        crop_border,
        detect_ground_hazard,
        downsample_depth,
    )
    # The perception -> motor-duty stage. Kept out of depth_utils.py because it
    # answers a different question (what the wearer should FEEL, not what is in
    # front of them) and out of serial_worker.py because it must stay testable
    # without a serial port.
    from second_vision.core.haptics import HapticMapper
    # Rendering lives in core/depth_view.py (no hardware deps) so the identical
    # view can be reproduced off-device from a captured .npy frame.
    from second_vision.core.depth_view import cv2_draw_depth
    DEPTH_POSTPROC_AVAILABLE = True
except ImportError as exc:  # pragma: no cover — mock machines without cv2/numpy
    DEPTH_POSTPROC_AVAILABLE = False
    _DEPTH_IMPORT_ERROR = exc

# priority.py is pure-Python (no hailo/gi) so it imports in mock mode too. It
# owns the scoring/preemption tunables; this module only computes per-detection
# priority/tier and offers the winner to the PriorityMailbox (user_data.tts_queue).
from second_vision.core.priority import (
    TIER_URGENT,
    compute_priority,
    compute_tier,
)

# ---- Object detection tuning ----
CONFIDENCE_THRESHOLD = 0.70
MIN_REPEAT_INTERVAL = 10.0   # Hard floor: min gap between repeated "still there"
                             # reminders for a track. Urgent-tier objects bypass it.
MIN_CONFIRMATION_SECONDS = 0.3       # A track must persist this long before its first
                                      # announcement — filters 1-2 frame tracker/NMS
                                      # glitches that would otherwise get announced as
                                      # a "first sighting" before disappearing
STALE_TRACK_FRAMES = 15              # Frames of tracker silence before a track is forgotten
HEAD_TURN_DELTA = 0.02               # Per-frame center_x shift (fraction of width) counted as movement
HEAD_TURN_RATIO = 0.7                # Fraction of tracked objects that must shift together
HEAD_TURN_MIN_TRACKED = 3            # Below this, one object's own motion can look like "everyone moved"
HEAD_TURN_SUPPRESS_SECONDS = 2.0     # Suppress "leaving center" announcements after a detected pan
DISPLAY_PHRASE_HOLD_SECONDS = 2.0    # How long an event phrase stays on the debug overlay before
                                      # falling back to the generic "{label} {zone}" text — event
                                      # phrases only fire for one frame otherwise, too brief to read

# ---- Depth post-processing tuning ----
# All the depth *math* tunables live in core/depth_utils.py; these only govern
# how often the callback talks to the console and the preview process.
DEPTH_LOG_EVERY = 15                 # Frames between throttled [DEPTH] console lines
DEPTH_PREVIEW_EVERY = 2              # Only ship every Nth frame to the viewer process — the
                                     # EMA-smoothed values barely move frame to frame
DEPTH_ERROR_LOG_SECONDS = 5.0        # Floor on repeat error logging; a bad frame at 30 FPS
                                     # must not bury the console in tracebacks

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
        if not DEPTH_POSTPROC_AVAILABLE:
            _log_depth_error(_DEPTH_IMPORT_ERROR)
            return
        # Convention (AGENTS.md): a callback catches, logs and returns — one bad
        # frame must never take the pipeline down with it.
        try:
            _process_real_depth(buffer, user_data)
        except Exception as exc:
            _log_depth_error(exc)
            # A swallowed exception must not also swallow the motors' last
            # command. Whatever went wrong, we no longer know what is in front
            # of the wearer, and the only defensible thing to keep vibrating for
            # is nothing. Guarded because publishing must not be able to raise
            # out of the exception handler and kill the pipeline after all.
            try:
                _publish_silence(user_data)
            except Exception:
                pass
    else:
        pass  # Mock mode — mock_depth_generator handles this


_last_depth_error_at = 0.0


def _log_depth_error(exc) -> None:
    """Report a depth-callback failure at most once every DEPTH_ERROR_LOG_SECONDS."""
    global _last_depth_error_at
    now = time.monotonic()
    if now - _last_depth_error_at < DEPTH_ERROR_LOG_SECONDS:
        return
    _last_depth_error_at = now
    print(f"[DEPTH] post-processing error (throttled): {exc!r}")


def _has_display() -> bool:
    """
    Is there a GUI to draw the depth preview on?

    cv2.imshow() with no X/Wayland session doesn't raise — Qt aborts the whole
    process — so on a plain ssh run the viewer would die instantly and leave a
    confusing "Qt platform plugin" wall of text. Checking first keeps a headless
    run quiet; post-processing and haptics are unaffected either way.
    """
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _depth_view_worker(view_queue) -> None:
    """
    Runs in a SEPARATE PROCESS: renders and shows the depth post-processing view.

    PERF: rendering happens here, not in the GStreamer callback. The callback
    only ships the small (64x48) depth grid plus the already-computed values, so
    the streaming thread stays free for inference — depth and YOLO share the CPU,
    and drawing on the callback thread visibly blurred the camera preview.
    """
    while True:
        try:
            payload = view_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            (small, intensities, hazard, severity, direction, thin, fps,
             motors, levels) = payload
            cv2.imshow(
                "Depth Post-Processing",
                cv2_draw_depth(small, intensities, hazard, severity, direction, thin,
                               fps=fps, motors=motors, levels=levels),
            )
            cv2.waitKey(1)
        except Exception as exc:  # never let the viewer process die on one frame
            print(f"[DEPTH VIEW] render error: {exc!r}")

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
            self.depth_frame_count = 0
            self.depth_fps_start_time = time.monotonic()

            # ---- Depth post-processing state ----
            # Built here rather than in the callback because __init__ runs once,
            # before the pipeline exists and before any worker thread starts —
            # which is also the only safe moment to fork the viewer process.
            self.depth_processor = None
            self.hazard_debouncer = None
            self.haptics = None
            self.depth_view_queue = None
            self.calibration = None
            self.capture = None
            self._depth_viewer = None
            if DEPTH_POSTPROC_AVAILABLE:
                self._init_depth_state()

        def _init_depth_state(self):
            """
            Wire up the depth post-processing chain and its optional side modes.

            Three modes, all independent and all headless-safe:
              * normal      — post-process + haptics + (unless SV_DEPTH_VIEW=0) a
                              preview window rendered in its own process
              * SV_CALIBRATE=1 — log raw per-zone stats to CSV for threshold tuning
                              and skip post-processing entirely (see core/calibration.py)
              * SV_CAPTURE=1   — dump labelled raw depth frames to .npy for offline
                              re-scoring (see core/capture.py); composes with either
                              of the above
            """
            self.depth_processor = DepthPostProcessor()
            # Debounces detect_ground_hazard, whose gate divides by the ground
            # strip's own noise median and so flickers on a marginal break. Held
            # here rather than inside DepthPostProcessor because the hazard strip
            # is a separate signal from the zone intensities and is reset on the
            # same events but graded on its own timebase.
            self.hazard_debouncer = HazardDebouncer()
            # Last stage before the queue: deadband, level quantization, PWM
            # floor and the anti-habituation pulse. Everything upstream of it
            # measures the scene; it alone decides what the motors do.
            self.haptics = HapticMapper()

            calibrating = os.environ.get("SV_CALIBRATE") == "1"

            # Fork the viewer FIRST, while this process is still single-threaded
            # and free of Hailo/GStreamer state: the child inherits everything
            # alive at fork time, and the capture/calibration writer threads below
            # do not survive into it anyway. Skipped in calibration mode, which is
            # deliberately headless and never sends a preview payload.
            if not calibrating and os.environ.get("SV_DEPTH_VIEW", "1") != "0":
                if _has_display():
                    self.depth_view_queue = multiprocessing.Queue(maxsize=2)
                    self._depth_viewer = multiprocessing.Process(
                        target=_depth_view_worker, args=(self.depth_view_queue,),
                        daemon=True, name="depth-view",
                    )
                    self._depth_viewer.start()
                else:
                    print("[DEPTH] no DISPLAY — preview window disabled "
                          "(post-processing and haptics still run)")

            self.calibration = CalibrationLogger() if calibrating else None
            self.capture = FrameCapture() if os.environ.get("SV_CAPTURE") == "1" else None

            # Both hold a background writer thread and an open file. main.py exits
            # by returning from main() (Ctrl-C is swallowed in _run_pipeline_mode),
            # so atexit is what guarantees a hand-stopped run still leaves a
            # complete CSV / corpus manifest behind.
            if self.calibration is not None or self.capture is not None:
                atexit.register(self.close_depth_capture)

        def close_depth_capture(self):
            """Flush and close the calibration CSV / capture corpus. Idempotent."""
            if self.capture is not None:
                self.capture.close()
                self.capture = None
            if self.calibration is not None:
                self.calibration.close()
                self.calibration = None

        def reset_depth_state(self):
            """
            Drop the EMA history after a pipeline rebuild or mode switch.

            Without it, smoothed intensities from the mode that just went away
            bleed into the first frames of the new one — the depth-side twin of
            the tracker-ID reset in app.py's _on_pipeline_rebuilt().

            The hazard debouncer is reset for the stronger reason: it LATCHES.
            A hazard still asserted when the pipeline was torn down would other-
            wise stay asserted across the rebuild and need HAZARD_OFF_FRAMES of
            the new mode to clear — i.e. the device would warn about a drop-off
            belonging to a scene it is no longer looking at.

            The haptic mapper is reset for both reasons at once: it holds a
            per-zone level (hysteresis) and a per-zone clock (pulse phase), and
            carrying either across a rebuild means the first frames of the new
            scene are graded against a scene that is gone.
            """
            if self.depth_processor is not None:
                self.depth_processor.reset()
            if self.hazard_debouncer is not None:
                self.hazard_debouncer.reset()
            if self.haptics is not None:
                self.haptics.reset()

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

    When multiple detections qualify, the single most salient one is chosen by
    the composite priority score (see core/priority.py) and offered to the
    PriorityMailbox with its priority/tier for the TTS worker to act on.
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
        area = _bbox_area(bbox)

        prev = track_history.get(track_id)
        previous_zone = prev["zone"] if prev else None
        zone = _get_detection_zone(bbox, previous_zone)

        zone_since = now
        first_seen = now
        last_announced = None
        phrase = None
        should_announce = True
        display_phrase = None
        display_phrase_until = 0.0

        if prev is not None and prev["label"] == label:
            zone_since = prev.get("zone_since", now)
            # Unlike zone_since, first_seen must NOT reset on a zone change —
            # it tracks how long this track_id has existed at all, so a real
            # object walking center -> left doesn't get re-treated as "new".
            first_seen = prev.get("first_seen", now)
            last_announced = prev.get("last_announced")
            display_phrase = prev.get("display_phrase")
            display_phrase_until = prev.get("display_phrase_until", 0.0)

        # Priority + urgency tier for this detection. Computed for every
        # detection (the frame's single winner is chosen by priority below), and
        # the tier decides whether an urgent object may bypass the repeat floor.
        priority = compute_priority(confidence, area, zone, label, last_announced, now)
        tier = compute_tier(priority, label, zone)

        if prev is not None and prev["label"] == label:
            if prev["zone"] != zone:
                # Direction changed — restart the "how long has it been here" timer.
                zone_since = now

                if prev["zone"] == "center" and zone != "center":
                    if not is_head_turning:
                        phrase = f"{label} leaving to {zone}"
                        user_data.IDs_changed_zones.add(track_id)
                elif zone == "center":
                    user_data.IDs_changed_zones.discard(track_id)
            elif (last_announced is not None
                  and now - last_announced < MIN_REPEAT_INTERVAL
                  and tier != TIER_URGENT):
                # Hard cooldown floor — suppress a too-soon "still there" repeat,
                # unless the object has escalated to the urgent tier.
                should_announce = False
            elif last_announced is not None:
                phrase = f"{label} still {zone}"
                should_announce = True

        if phrase:
            display_phrase = phrase
            display_phrase_until = now + DISPLAY_PHRASE_HOLD_SECONDS

        if now - first_seen < MIN_CONFIRMATION_SECONDS:
            should_announce = False

        track_history[track_id] = {
            "zone": zone,
            "label": label,
            "last_frame": frame_count,
            "zone_since": zone_since,
            "first_seen": first_seen,
            "center_x": center_x,
            "last_announced": now if should_announce else last_announced,
            "display_phrase": display_phrase,
            "display_phrase_until": display_phrase_until,
        }

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
            candidates.append((priority, tier, confidence, label, zone, phrase))

    # Forget tracks the tracker hasn't updated recently, so zone hysteresis and
    # cooldown state don't leak onto an unrelated object that later reuses the ID.
    stale_ids = [
        track_id for track_id, hist in track_history.items()
        if frame_count - hist["last_frame"] > STALE_TRACK_FRAMES
    ]
    for track_id in stale_ids:
        track_history.pop(track_id, None)
        user_data.IDs_changed_zones.discard(track_id)

    # (zone, label) -> count of confirmed, still-tracked objects in that group
    # — feeds the "multiple X" TTS wording. Built from track_history (after
    # stale cleanup above), NOT from this frame's raw detections: a track the
    # detector momentarily misses for a frame or two is still present here
    # (it isn't evicted until STALE_TRACK_FRAMES of absence), so one flickered
    # frame can't undercount a group that's genuinely still there. Also why
    # this can't be tallied inline in the loop above — the same information
    # isn't available per-detection, only once every live track is known.
    confirmed_zone_label_counts = {}
    for hist in track_history.values():
        if now - hist["first_seen"] >= MIN_CONFIRMATION_SECONDS:
            group_key = (hist["zone"], hist["label"])
            confirmed_zone_label_counts[group_key] = confirmed_zone_label_counts.get(group_key, 0) + 1

    if user_data.use_frame:
        _draw_detection_overlay(element, buffer, user_data, active_zones, det_labels, user_data.get_det_fps())

    if not candidates:
        return

    # One winner per frame, now chosen by composite priority (was confidence,
    # then bbox area). Cross-frame competition is handled downstream by the
    # PriorityMailbox + the worker's preemption, so a runner-up simply wins on a
    # later frame — persistent objects re-emit every frame from track_history.
    priority, tier, confidence, label, zone, phrase = max(candidates, key=lambda c: c[0])
    payload = {
        "label": label,
        "zone": zone,
        "confidence": confidence,
        "priority": priority,
        "tier": tier,
    }
    # "Multiple" composes with whatever phrase would otherwise be used —
    # including "leaving to X"/"still X" — the same way det_labels' overlay
    # text does; it must not only apply to the one-time first announcement,
    # or a persistent multi-object scene would say "multiple" exactly once
    # and go singular for every reminder after that.
    if confirmed_zone_label_counts.get((zone, label), 0) >= 2:
        payload["phrase"] = f"multiple {phrase or f'{label} {zone}'}"
    elif phrase:
        payload["phrase"] = phrase

    # offer() never blocks or raises: the mailbox keeps the higher-priority of
    # {pending, this}, so a busy TTS worker gets the most urgent item, not a stale one.
    user_data.tts_queue.offer(payload)


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


def _publish_motors(user_data, motors, hazard=False, severity=0) -> None:
    """
    Put one frame of MOTOR DUTIES on serial_queue.

    Drop-OLDEST on a full queue, not drop-newest. The queue is a real-time
    stream of "what should the motors be doing right now", so when the consumer
    falls behind, the frame worth keeping is the newest one — silently dropping
    it (the previous behaviour) meant that under load the device was driven by
    the stalest reading in the buffer rather than the freshest.
    """
    packet = {
        "left":   motors["left"],
        "center": motors["center"],
        "right":  motors["right"],
        "hazard": hazard,
        "hazard_severity": severity,
    }
    try:
        user_data.serial_queue.put_nowait(packet)
    except queue.Full:
        try:
            user_data.serial_queue.get_nowait()
            user_data.serial_queue.put_nowait(packet)
        except (queue.Empty, queue.Full):
            pass


def _publish_silence(user_data) -> None:
    """
    Emit an explicit all-zero frame after a bail-out.

    Every early return in the depth callback used to send NOTHING, which is not
    the same as sending zero: the ESP32 holds its last commanded duty until the
    3-second watchdog fires, so a dropped frame leaves the motors running on a
    reading that may no longer be true. Both directions of that are bad, but the
    dangerous one is a stale "clear" — the wearer feels nothing and infers open
    space. Zeroing is the honest failure: it says "no information", and no
    information is what we actually have.
    """
    if getattr(user_data, "haptics", None) is None:
        return
    _publish_motors(user_data, user_data.haptics.silence(time.monotonic()))


def _process_real_depth(buffer, user_data):
    """
    Run the depth post-processing chain (core/depth_utils.py) on one frame and
    publish per-zone MOTOR DUTIES to serial_queue.

        raw mask
          -> [SV_CAPTURE: dump the RAW frame to .npy]
          -> crop_border            (drop the model's unreliable edge ring)
          -> downsample             (64x48 grid for the cheap zone math)
          -> [SV_CALIBRATE: log raw stats and STOP — headless tuning mode]
          -> DepthPostProcessor.process(cropped, NATIVE resolution)
          -> detect_ground_hazard(small)
          -> HapticMapper.shape()   (perception -> what the motors do)
          -> serial_queue + throttled preview payload

    The processor is deliberately handed the CROPPED but NOT downsampled map:
    its thin-structure pass (cables, poles, railings) has to see those before the
    64x48 decimation erases them. Everything downstream of it re-downsamples
    internally, so every other detector still sees the same `small` grid.

    WHAT LEAVES THIS FUNCTION. The values on serial_queue are PWM duties, not
    perception scores: post-deadband, quantized, floored at the motors' start
    threshold and pulse-gated. `serial_worker.py` is transport and applies
    `config.motor_strength`; nothing else between here and the ESP32 reinterprets
    them. The distinction matters because a zone perceiving 60 and driving 0 is
    correct behaviour here, not a lost signal.

    Every exit path emits a frame. See _publish_silence().
    """
    # Count every delivered frame, including ones we bail on below, so
    # get_depth_fps() reports what the pipeline is actually delivering.
    user_data.increment_depth()
    frame_count = user_data.get_depth_count()

    roi = hailo.get_roi_from_buffer(buffer)
    depth_masks = roi.get_objects_typed(hailo.HAILO_DEPTH_MASK)

    if not depth_masks:
        _publish_silence(user_data)
        return

    mask = depth_masks[0]
    try:
        depth_data = np.array(mask.get_data()).reshape((mask.get_height(), mask.get_width()))
    except Exception:
        # Fallback if get_height/get_width is unavailable on this mask type.
        depth_data = np.array(mask.get_data())

    if depth_data.ndim != 2:
        if frame_count % DEPTH_LOG_EVERY == 0:
            print(f"[DEPTH] Frame {frame_count} | unusable mask shape {depth_data.shape} — skipped")
        _publish_silence(user_data)
        return

    # Capture the RAW frame before crop/downsample, so the corpus holds exactly
    # what the model emitted and stays valid if this chain changes.
    if user_data.capture is not None:
        user_data.capture.maybe_save(depth_data, frame_count)

    depth_data = crop_border(depth_data)
    small = downsample_depth(depth_data)

    if user_data.calibration is not None:
        # Calibration mode measures the model's raw output; running the detectors
        # here would only measure the placeholder thresholds we are trying to set.
        # Still emit zeros: calibration is a bench mode, the motors should be
        # still, and an explicit zero says so rather than leaving the board to
        # time out into it.
        user_data.calibration.log(small, frame_count)
        _publish_silence(user_data)
        return

    intensities = user_data.depth_processor.process(depth_data)
    # Debounced, not raw: detect_ground_hazard is memoryless and its gate divides
    # by the strip's own noise median, so on a marginal break it alternates
    # true/false frame to frame (131 flips in 400 frames on a static scene). The
    # motors must not be driven off that — a ~10 Hz stutter reads as a broken
    # device, not as a warning.
    hazard, severity, direction = user_data.hazard_debouncer.update(
        *detect_ground_hazard(small, small.shape[0])
    )

    # Perception -> what the wearer feels. Everything above this line measures
    # the scene; this line decides the device's behaviour, and it is the only
    # place that knows about deadbands, motor start thresholds or habituation.
    motors = user_data.haptics.shape(intensities, time.monotonic())

    # Haptics first, preview second — the device output must not queue behind a
    # display frame. Note the serial contract carries severity but no direction
    # field: "down" (drop-off) and "up" (step-up) currently reach the motors as
    # the same alert. That is an open question for the depth + firmware owners
    # (ARCHITECTURE.md "Depth Processing"), so nothing is invented here.
    _publish_motors(user_data, motors, hazard, severity)

    # The thin readings ride along so the HUD tags what the MOTORS actually got,
    # rather than a weaker value the display process would recompute from 64x48.
    # The FPS rides along for the same reason: it is the DEPTH branch's own rate
    # (get_depth_fps, counted in this callback), which is not the detection/user
    # frame rate the other overlay shows — the two branches run independently off
    # the tee and drift apart, so one number cannot stand for both.
    if user_data.depth_view_queue is not None and frame_count % DEPTH_PREVIEW_EVERY == 0:
        try:
            user_data.depth_view_queue.put_nowait((
                small, intensities, hazard, severity, direction,
                user_data.depth_processor.last_thin,
                user_data.get_depth_fps(),
                # The duties and levels this exact frame produced. Recomputing
                # them in the viewer is not an option: the mapper is stateful
                # (hysteresis, pulse phase), so a second instance fed the same
                # numbers would drift out of step with the real one and the HUD
                # would quietly stop describing the device.
                motors, dict(user_data.haptics.last_levels),
            ))
        except queue.Full:
            pass  # viewer busy — drop the frame, never stall the streaming thread

    if frame_count % DEPTH_LOG_EVERY == 0:
        d_lo, d_med, d_hi = np.percentile(small, [1, 50, 99])
        # Both rows on purpose: perception (what was seen) and motor (what the
        # wearer got). They are allowed to disagree, and when the device "does
        # nothing" the only way to tell a dead detector from a working deadband
        # is to see the pair side by side.
        print(f"[DEPTH] Frame {frame_count} | "
              f"L={intensities['left']} C={intensities['center']} R={intensities['right']} | "
              f"motor L={motors['left']} C={motors['center']} R={motors['right']} | "
              f"Hazard: {hazard} ({direction}, sev {severity}) | "
              f"raw p1/p50/p99: {d_lo:.2f}/{d_med:.2f}/{d_hi:.2f} | "
              f"{user_data.get_depth_fps():.1f} FPS")
