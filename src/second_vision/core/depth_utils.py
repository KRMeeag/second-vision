"""
Depth Utilities — Zone splitting, proximity curves, and ground hazard detection.

Pure functions, no I/O — safe to unit test without hardware.
"""

from typing import Tuple

import numpy as np

MAX_DEPTH_M = 5.0
MIN_DEPTH_M = 0.3
GROUND_STRIP_FRACTION = 0.25
HAZARD_THRESHOLD_RATIO = 5.0


def _filter_outliers(values: np.ndarray, low_pct: float = 5.0, high_pct: float = 95.0) -> np.ndarray:
    """Drop values outside the [low_pct, high_pct] percentile band."""
    lo, hi = np.percentile(values, [low_pct, high_pct])
    filtered = values[(values >= lo) & (values <= hi)]
    return filtered if filtered.size else values


def compute_proximity(zone_slice: np.ndarray, max_depth: float = MAX_DEPTH_M, min_depth: float = MIN_DEPTH_M) -> int:
    """
    Convert a zone's raw depth values into a 0-255 motor intensity.

    Uses an inverse-square falloff (closer = disproportionately stronger)
    instead of a linear mapping, which feels unnatural to users. Percentile
    filtering removes sensor noise/reflection outliers before averaging.
    """
    filtered = _filter_outliers(zone_slice.ravel())
    avg_depth = float(np.mean(filtered))

    clamped = max(min_depth, min(avg_depth, max_depth))
    falloff = (max_depth - clamped) / (max_depth - min_depth)
    return int(round(255 * falloff ** 2))


def compute_zone_intensities(depth_data: np.ndarray, width: int) -> dict[str, int]:
    """
    Split a depth frame into left/center/right zones (25/50/25) and
    return each zone's motor intensity (0-255).
    """
    left = depth_data[:, :width // 4]
    center = depth_data[:, width // 4: 3 * width // 4]
    right = depth_data[:, 3 * width // 4:]

    return {
        "left": compute_proximity(left),
        "center": compute_proximity(center),
        "right": compute_proximity(right),
    }


def detect_ground_hazard(
    depth_map: np.ndarray,
    frame_height: int,
    threshold_ratio: float = HAZARD_THRESHOLD_RATIO,
) -> Tuple[bool, int]:
    """
    Detect a ground-plane departure (stairs, ledge) in the bottom strip of the frame.

    Monocular depth reads a drop-off as "far" rather than "down", so this looks
    for a sudden gradient spike in row-wise ground depth instead of absolute
    distance. Returns (hazard_detected, severity), with severity pre-scaled to
    0-255 for the serial_queue "hazard_severity" field.
    """
    strip_start = int(frame_height * (1 - GROUND_STRIP_FRACTION))
    ground_strip = depth_map[strip_start:, :]

    if ground_strip.shape[0] < 2:
        return False, 0

    row_depths = np.mean(ground_strip, axis=1)
    gradient = np.abs(np.diff(row_depths))

    median_gradient = np.median(gradient)
    if median_gradient <= 1e-6:
        return False, 0

    ratio = float(np.max(gradient) / median_gradient)
    if ratio <= threshold_ratio:
        return False, 0

    severity = int(np.clip((ratio / threshold_ratio) * 255, 0, 255))
    return True, severity
