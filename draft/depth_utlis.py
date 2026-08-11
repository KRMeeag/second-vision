"""
Depth Utilities — Zone splitting, proximity curves, and ground hazard detection.

Post-processing pipeline (per SV-Docu/depth_estimation_handoff.md):
    downsample -> zone split -> per zone max(sub-grid pooling [thin objects],
    blank-wall detection [flat surfaces], floor-to-wall [walls read as far]) ->
    safe-zone threshold -> non-linear curve -> EMA smoothing -> 0-255 motor
    intensity.

All math is vectorized NumPy on a downsampled grid; safe to run every frame
inside the GStreamer callback. Pure functions except DepthPostProcessor,
which holds the EMA state — all unit-testable without hardware.
"""

from typing import Tuple

import cv2
import numpy as np

# NOTE on units: the scdepthv3 postprocess (.so) emits RELATIVE depth, not
# meters. Measured on live runs, the nearest representable value is ~18.4 and
# far field reads 50+. These cutoffs are in those model units.
MAX_DEPTH_M = 30.0           # safe-zone cutoff: at/beyond this output is 0.0
MIN_DEPTH_M = 15.0           # near-end floor of the model output range — measured:
                             # live captures consistently show p1 in the 15.0-15.5
                             # range (was 18.4, which clamped the whole 15-18.4
                             # "about to collide" band to one flat max reading)
NEAR_PERCENTILE = 10.0       # aggregate the closest ~10% pixel cluster per zone
CURVE_EXPONENT = 2.0         # intensity spikes only when critically close
EMA_ALPHA = 0.3              # ~0.2 s settle time at 30 FPS
DOWNSAMPLE_SIZE = (64, 48)   # (width, height) grid for all zone math
BORDER_CROP_RATIO = 0.04     # trim 4% off each edge to drop conv border artifacts
SUBGRID_SHAPE = (4, 4)       # per-zone (rows, cols) pooling grid for thin objects
DANGER_CELL = 0.5            # per-cell proximity (0-1) at/above which a cell is "danger"
# Fraction of a zone's sub-cells in danger above which the near reading is a
# BROAD surface (wall/door/furniture face) rather than a thin/localized object.
# A thin object physically cannot exceed ~a column of cells (~0.25); the live
# wall's centre zone measured ~0.35 and was mislabelled "thin" at 0.40, so the
# boundary sits between those. PLACEHOLDER pending calibration refinement.
BROAD_COVERAGE_MIN = 0.30
# Blank-wall detection: a textureless surface is LOCALLY smooth even when the
# model renders it as a washed-out gradient (live Pi walls read as a dome:
# ~15 at the edges -> ~30 mid-wall, so a zone-global std is useless).
# WALL_VARIANCE_MAX is the median per-sub-cell std (model units) below which a
# zone is treated as a solid surface. PLACEHOLDER — set from calibration
# (real wall cells ~1-1.5 here; open/textured scenes read several times that).
WALL_VARIANCE_MAX = 3.0
# Once a zone is CONFIRMED as a solid wall, a graded proximity at/above this
# level saturates to 1.0: a solid surface whose nearest credible part already
# reads strong-warning range is a collision about to happen, and the model's
# washed-out mid-wall guesses must not soften the buzz (live finding: a
# point-blank wall read only 186/255). PLACEHOLDER pending calibration.
WALL_CONFIRM_SATURATE = 0.5
# Floor-to-wall intersection: as the floor recedes upward its depth grows in
# small steps; where it meets a wall the model jumps to "far". A row-to-row jump
# larger than this ratio x the typical floor step marks that break. PLACEHOLDER.
FLOOR_WALL_JUMP_RATIO = 3.0
# Absolute minimum break size (model units). Without it, a smooth surface's
# near-zero baseline let ~0.8-unit sensor wiggles register as walls with full
# confidence (live Pi false positive). Same medicine as HAZARD_MIN_STEP.
# PLACEHOLDER pending calibration.
FLOOR_WALL_MIN_JUMP = 2.0
MIN_FLOOR_ROWS = 3           # need this many receding-floor rows below the break to trust it
GROUND_STRIP_FRACTION = 0.25
HAZARD_THRESHOLD_RATIO = 5.0
# Absolute row-to-row jump (model units) below which a ground break is noise.
# Needed because the ratio test alone is meaningless when the strip is flat: the
# baseline collapses toward 0, making the ratio either explode on specks or be
# skipped entirely. PLACEHOLDER pending calibration.
HAZARD_MIN_STEP = 2.0
# Break size (model units) that grades as maximum severity (255). Severity is
# graded on the ABSOLUTE jump, not the ratio: the ratio's denominator is scene
# noise, which made severity flap 26<->255 on a completely static scene (live
# finding). PLACEHOLDER pending calibration.
HAZARD_MAX_JUMP = 20.0

ZONE_NAMES = ("left", "center", "right")


def downsample_depth(depth_map: np.ndarray, size: Tuple[int, int] = DOWNSAMPLE_SIZE) -> np.ndarray:
    """Shrink the depth map to a small grid so all subsequent math is cheap."""
    target_w, target_h = size
    h, w = depth_map.shape
    if h <= target_h and w <= target_w:
        return depth_map
    return cv2.resize(depth_map, (target_w, target_h), interpolation=cv2.INTER_NEAREST)


def crop_border(depth_map: np.ndarray, ratio: float = BORDER_CROP_RATIO) -> np.ndarray:
    """
    Trim a border margin off the depth map to remove convolutional edge
    artifacts.

    SC-DepthV3 produces unreliable — usually spuriously-near — values in the
    outermost rows/columns because the convolutions run against zero-padding
    with a truncated receptive field there. Those pixels would otherwise pull
    the low-percentile zone aggregation toward "near" (false side intensities)
    and paint a red ring in the visualization. Cropping before any aggregation
    keeps them out of both. Returns the map unchanged if it is too small to
    crop safely.
    """
    h, w = depth_map.shape
    mh, mw = int(h * ratio), int(w * ratio)
    if mh == 0 and mw == 0:
        return depth_map
    if h - 2 * mh < 1 or w - 2 * mw < 1:
        return depth_map
    return depth_map[mh:h - mh, mw:w - mw]


def _proximity_curve(near_depth: float, max_depth: float = MAX_DEPTH_M, min_depth: float = MIN_DEPTH_M) -> float:
    """
    Map a single near-depth value to a 0.0-1.0 warning intensity: 0 at/beyond
    the safe-zone cutoff, rising along the non-linear curve as it gets closer.
    Shared by the percentile and blank-wall aggregations.
    """
    if near_depth >= max_depth:
        return 0.0
    clamped = max(min_depth, near_depth)
    falloff = (max_depth - clamped) / (max_depth - min_depth)
    return float(falloff ** CURVE_EXPONENT)


def compute_proximity(zone_slice: np.ndarray, max_depth: float = MAX_DEPTH_M, min_depth: float = MIN_DEPTH_M) -> float:
    """
    Convert a zone's depth values into a 0.0-1.0 warning intensity.

    Aggregates with a low percentile (the closest pixel cluster) rather than
    the zone mean, so a single nearby obstacle in an otherwise open zone is
    not averaged away, while still rejecting single-pixel noise.
    """
    near_depth = float(np.percentile(zone_slice, NEAR_PERCENTILE))
    return _proximity_curve(near_depth, max_depth, min_depth)


def local_flatness(zone_slice: np.ndarray) -> float:
    """
    The zone's LOCAL smoothness: median std across the sub-grid cells.

    This is the exact quantity detect_blank_wall compares against
    WALL_VARIANCE_MAX. Exposed so calibration measures what the detector
    actually uses — recording a zone-global std instead reads ~2x higher on a
    dome-rendered wall and would calibrate the threshold to the wrong side.
    """
    rows, cols = SUBGRID_SHAPE
    h, w = zone_slice.shape
    if h < rows or w < cols:
        return float(np.std(zone_slice))
    if h % rows == 0 and w % cols == 0:
        cells = (zone_slice.reshape(rows, h // rows, cols, w // cols)
                           .transpose(0, 2, 1, 3)
                           .reshape(rows * cols, -1))
        return float(np.median(np.std(cells, axis=1)))
    stds = [float(np.std(c))
            for band in np.array_split(zone_slice, rows, axis=0)
            for c in np.array_split(band, cols, axis=1) if c.size]
    return float(np.median(stds))


def ground_break_stats(depth_map: np.ndarray, frame_height: int) -> dict:
    """
    Row-gradient statistics of the ground strip — the raw quantities
    detect_ground_hazard thresholds on (HAZARD_MIN_STEP, HAZARD_MAX_JUMP) and
    the floor-profile break size behind FLOOR_WALL_MIN_JUMP.

    Diagnostics/calibration only; returns zeros when the strip is too thin.
    """
    strip_start = int(frame_height * (1 - GROUND_STRIP_FRACTION))
    strip = depth_map[strip_start:, :]
    if strip.shape[0] < 2:
        return {"max_grad": 0.0, "median_grad": 0.0, "signed_break": 0.0}
    profile = np.mean(strip, axis=1)[::-1]      # bottom row first
    diffs = np.diff(profile)
    gradient = np.abs(diffs)
    break_i = int(np.argmax(gradient))
    return {
        "max_grad": float(gradient[break_i]),
        "median_grad": float(np.median(gradient)),
        "signed_break": float(diffs[break_i]),   # + = drop-off, - = step-up
    }


def detect_blank_wall(zone_slice: np.ndarray, variance_max: float = WALL_VARIANCE_MAX) -> float:
    """
    Warning intensity for a textureless close surface (a blank wall).

    Per the handoff, walls render as "washed-out, uncertain gradients" — and live
    Pi captures confirm it: a real wall reads as a smooth DOME (near at the
    edges, "far" mid-wall), not a flat plane. So "low variance" must be judged
    LOCALLY: the flatness test is the median of per-sub-cell std over the 4x4
    grid. A wall is smooth in every small patch even when it bows globally;
    genuinely open/textured scenes are rough in every patch. (The old zone-global
    std failed on exactly this — the dome's spread pushed it over the limit and
    real walls went undetected outside the flattest zone.)

    When the zone is locally smooth — i.e. CONFIRMED as a solid surface — the
    warning is graded from its NEAREST credible cluster (the NEAR_PERCENTILE of
    the zone), not the median: a wall is one connected surface, so its collision
    distance is its nearest part, and the washed-out mid-wall readings are
    precisely where scale ambiguity inflates the distance. Grading from the
    median let that corruption soften the warning (live finding: point-blank
    wall buzzed 186/255 because the dome's middle dragged the median "far").

    Additionally, a confirmed wall already graded at/above WALL_CONFIRM_SATURATE
    saturates to 1.0 — a solid surface that near IS the collision case this edge
    case exists for, and the model's under-reading must not cap the buzz.

    Returns 0.0 for textured scenes (locally rough) and for smooth-but-far
    surfaces (near cluster beyond the safe-zone cutoff). Combined via max() with
    the other detectors, so it can only raise a warning, never suppress one —
    fail-safe.

    NOTE: variance_max is in relative model units and is a PLACEHOLDER pending
    the metric-calibration measurement. Walls whose readings land entirely
    beyond the cutoff (scale ambiguity) still need the separate floor-to-wall
    strategy.
    """
    if local_flatness(zone_slice) > variance_max:
        return 0.0
    near = float(np.percentile(zone_slice, NEAR_PERCENTILE))
    value = _proximity_curve(near)
    if value >= WALL_CONFIRM_SATURATE:
        return 1.0
    return value


def detect_floor_to_wall(zone_slice: np.ndarray, jump_ratio: float = FLOOR_WALL_JUMP_RATIO) -> float:
    """
    Warning intensity for a near wall the model renders as FAR (scale ambiguity).

    The wall's own depth is unreliable, but the textured floor leading up to it
    is not. Scanning a zone from the bottom up, the floor depth grows in small
    steps as it recedes; where it runs into the wall the model jumps to "far".
    We take the floor depth just BELOW that jump — the true distance to the wall
    base — and extrapolate it to the washed-out region above.

    Fires only when the floor terminates against something NEAR:
      - open corridor  -> floor recedes smoothly, no jump          -> 0.0
      - distant dropoff -> break happens at a far depth             -> ~0.0 (curve)
      - near wall/edge  -> floor stops short at a near depth        -> graded warning

    So it can only add a warning for a genuinely close obstruction, never
    suppress one. Thresholds are relative-unit PLACEHOLDERS pending calibration.
    """
    h = zone_slice.shape[0]
    if h < MIN_FLOOR_ROWS + 1:
        return 0.0

    # Row-wise median, bottom row first (median across columns rejects speckle).
    prof = np.median(zone_slice, axis=1).astype(float)[::-1]

    if prof[0] >= MAX_DEPTH_M:
        return 0.0                       # no near floor visible -> method N/A

    diffs = np.diff(prof)

    # Candidate break = the single largest FARTHER jump in the profile.
    break_i = int(np.argmax(diffs))
    jump = float(diffs[break_i])

    # Absolute noise floor FIRST: on a smooth surface the relative baseline
    # collapses toward zero, so without this a ~0.8-unit sensor wiggle scored as
    # a full-confidence wall (live Pi false positive, no floor in frame at all).
    if jump < FLOOR_WALL_MIN_JUMP:
        return 0.0

    if break_i < MIN_FLOOR_ROWS:
        return 0.0                       # break too close to the bottom -> no real floor run below it

    # Baseline from the FLOOR rows only (below the break). Rows above it belong
    # to the wall, whose dozens of near-zero noise steps used to drag the median
    # down and corrupt the ratio test in both directions.
    floor_steps = diffs[:break_i]
    rising = floor_steps[floor_steps > 0]
    if rising.size == 0:
        return 0.0                       # nothing receding below the break -> not a floor
    typical_step = max(float(np.median(rising)), 1e-6)

    if jump <= jump_ratio * typical_step:
        return 0.0                       # break not distinct from the floor's own slope -> open space

    floor_reach = float(prof[break_i])   # last reliable floor depth before the break
    return _proximity_curve(floor_reach)  # near -> warn, far -> ~0


def subgrid_proximity(zone_slice: np.ndarray, grid_shape: Tuple[int, int] = SUBGRID_SHAPE) -> float:
    """
    Sub-grid pooling for small / thin objects (poles, cables, chair legs).

    A thin obstacle can occupy far less than the NEAR_PERCENTILE fraction of a
    whole zone, so the zone-wide percentile averages it into the background and
    misses it. Splitting the zone into a small grid (default 4x4) and taking the
    *worst* (nearest) cell means an object that dominates even one cell still
    raises the alarm. Each cell is still aggregated with compute_proximity, so a
    single noisy pixel inside a cell is rejected rather than triggering a false
    max.

    For blind navigation, missing a thin hazard is worse than an occasional
    over-warn, so this deliberately errs toward higher sensitivity. It is never
    less sensitive than the whole-zone reading (the nearest cell's near-cluster
    is at least as close as the whole zone's), so it supersedes it.

    np.array_split tolerates zones that don't divide evenly; the 16-cell loop is
    not a per-pixel loop, so it stays cheap on the 64x48 grid.
    """
    return float(subgrid_cell_proximities(zone_slice, grid_shape).max())


def _curve_array(near: np.ndarray, max_depth: float = MAX_DEPTH_M, min_depth: float = MIN_DEPTH_M) -> np.ndarray:
    """Vectorized _proximity_curve over an array of near-depth values."""
    clamped = np.maximum(near, min_depth)
    falloff = (max_depth - clamped) / (max_depth - min_depth)
    return np.where(near >= max_depth, 0.0, falloff ** CURVE_EXPONENT)


def subgrid_cell_edges(h: int, w: int, grid_shape: Tuple[int, int] = SUBGRID_SHAPE):
    """Row/column edge indices matching subgrid_cell_proximities' split."""
    rows, cols = grid_shape
    if h % rows == 0 and w % cols == 0:
        return np.arange(rows + 1) * (h // rows), np.arange(cols + 1) * (w // cols)
    row_edges = np.cumsum([0] + [len(x) for x in np.array_split(np.arange(h), rows)])
    col_edges = np.cumsum([0] + [len(x) for x in np.array_split(np.arange(w), cols)])
    return row_edges, col_edges


def subgrid_cell_proximities(zone_slice: np.ndarray, grid_shape: Tuple[int, int] = SUBGRID_SHAPE) -> np.ndarray:
    """
    Per-cell proximity for a zone's sub-grid as a (rows, cols) array.

    PERF: when the zone divides evenly (the normal 64x48 case) this reshapes all
    cells into one 2-D block and takes a SINGLE np.percentile along an axis,
    instead of one call per cell — the streaming thread runs this every frame, so
    the 16x reduction in percentile calls matters. Falls back to array_split for
    odd sizes. The visualization reuses this instead of recomputing.
    """
    rows, cols = grid_shape
    h, w = zone_slice.shape
    if h < rows or w < cols:
        return np.full((1, 1), compute_proximity(zone_slice))

    if h % rows == 0 and w % cols == 0:
        cells = (zone_slice.reshape(rows, h // rows, cols, w // cols)
                           .transpose(0, 2, 1, 3)
                           .reshape(rows * cols, -1))
        # Percentile in the array's own dtype, curve widened to float64 — the same
        # order the per-cell scalar path used, so results match bit-for-bit.
        near = np.percentile(cells, NEAR_PERCENTILE, axis=1).astype(np.float64)
        return _curve_array(near).reshape(rows, cols)

    out = np.zeros((rows, cols), dtype=float)
    for r, row_band in enumerate(np.array_split(zone_slice, rows, axis=0)):
        for c, cell in enumerate(np.array_split(row_band, cols, axis=1)):
            if cell.size:
                out[r, c] = compute_proximity(cell)
    return out


def zone_warning(zone_slice: np.ndarray) -> float:
    """
    Combined per-zone warning: the worst of sub-grid pooling (thin objects),
    blank-wall detection (flat close surfaces), and floor-to-wall intersection
    (near walls the model reads as far). Each can only raise the warning, never
    lower it, so the device stays fail-safe.
    """
    return max(
        subgrid_proximity(zone_slice),
        detect_blank_wall(zone_slice),
        detect_floor_to_wall(zone_slice),
    )


def zone_warning_breakdown(zone_slice: np.ndarray) -> dict:
    """
    Itemised per-detector contributions for one zone — the same numbers
    zone_warning() takes the max of, but broken out so the live view can show
    WHICH edge-case detector is driving a zone.

    Also classifies the SHAPE of the near reading, because the sub-grid detector
    fires on anything close, not just thin objects (a wall at arm's length maxes
    it out too — live Pi finding). Coverage = fraction of sub-cells in danger:
      "thin"  — few cells fire (localized object: pole, cable, box edge)
      "broad" — cells blanket the zone (large close surface: wall, door)
      "none"  — sub-grid quiet.

    Diagnostics/visualisation only; the hot path uses zone_warning(). Runs in the
    display process, so it costs the streaming thread nothing.
    """
    cells = subgrid_cell_proximities(zone_slice)
    parts = {
        "sub": float(cells.max()),                # nearest-cluster (thin-object edge case)
        "wall": detect_blank_wall(zone_slice),    # locally-smooth close surface
        "f2w": detect_floor_to_wall(zone_slice),  # wall the model reads as far
    }
    # Coverage is judged RELATIVE to the zone's strongest cell, not an absolute
    # threshold: a moderate-distance wall reads ~0.4 in EVERY cell (widespread,
    # so broad) while a pole reads ~1.0 in one column and ~0 elsewhere
    # (concentrated, so thin). An absolute cutoff called that wall "thin".
    peak = parts["sub"]
    if peak <= 0.01:
        coverage, shape = 0.0, "none"
    else:
        coverage = float((cells >= 0.5 * peak).mean())
        shape = "broad" if coverage > BROAD_COVERAGE_MIN else "thin"
    winner = max(parts, key=parts.get)
    return {"parts": parts, "winner": winner, "value": parts[winner],
            "coverage": coverage, "shape": shape}


def compute_zone_intensities(depth_data: np.ndarray, width: int) -> dict[str, float]:
    """
    Split a depth frame into left/center/right zones (25/50/25) and
    return each zone's warning intensity (0.0-1.0).

    Each zone combines sub-grid pooling (a thin obstacle filling only part of the
    zone still triggers) with blank-wall detection (a flat, textureless close
    surface still triggers even when its per-cluster depth reads washed out).
    """
    left = depth_data[:, :width // 4]
    center = depth_data[:, width // 4: 3 * width // 4]
    right = depth_data[:, 3 * width // 4:]

    return {
        "left": zone_warning(left),
        "center": zone_warning(center),
        "right": zone_warning(right),
    }


def to_motor_intensity(value: float) -> int:
    """Convert a 0.0-1.0 intensity to the 0-255 int expected on serial_queue."""
    return int(round(255 * float(np.clip(value, 0.0, 1.0))))


class DepthPostProcessor:
    """
    Stateful frame-to-frame processor: runs the full post-processing pipeline
    and EMA-smooths the per-zone intensities so haptics don't flicker.

    Call process() once per frame from the depth callback.
    """

    def __init__(self, alpha: float = EMA_ALPHA):
        self.alpha = alpha
        self._smoothed: dict[str, float] | None = None

    def process(self, depth_map: np.ndarray) -> dict[str, int]:
        """
        Full pipeline: downsample -> zone intensities -> EMA -> 0-255 ints.

        Returns {"left": int, "center": int, "right": int} per the
        serial_queue contract.
        """
        small = downsample_depth(depth_map)
        raw = compute_zone_intensities(small, small.shape[1])

        if self._smoothed is None:
            self._smoothed = dict(raw)
        else:
            for zone in ZONE_NAMES:
                self._smoothed[zone] = (
                    self.alpha * raw[zone] + (1.0 - self.alpha) * self._smoothed[zone]
                )

        return {zone: to_motor_intensity(self._smoothed[zone]) for zone in ZONE_NAMES}

    def reset(self) -> None:
        """Clear EMA state (e.g., after a pipeline rebuild or mode switch)."""
        self._smoothed = None


def detect_ground_hazard(
    depth_map: np.ndarray,
    frame_height: int,
    threshold_ratio: float = HAZARD_THRESHOLD_RATIO,
) -> Tuple[bool, int, str]:
    """
    Detect a ground-plane departure (stairs, ledge, curb) in the bottom strip of
    the frame, and WHICH WAY the ground breaks.

    Monocular depth reads a drop-off as "far" rather than "down", so this looks
    for a sudden gradient spike in row-wise ground depth instead of absolute
    distance. The SIGN of that spike (walking-forward order, i.e. bottom row
    first) distinguishes the two opposite dangers:

      depth jumps FARTHER  -> "down"  (drop-off / descending stairs — fall hazard)
      depth jumps NEARER   -> "up"    (curb / step-up / riser — trip hazard)

    Returns (hazard_detected, severity, direction) with severity pre-scaled to
    0-255 for the serial_queue "hazard_severity" field and direction one of
    "down" / "up" / "none". A step-up shows as a weaker signature than a
    drop-off (a riser plateaus more than it spikes), so treat "up" as
    best-effort until live calibration.
    """
    strip_start = int(frame_height * (1 - GROUND_STRIP_FRACTION))
    ground_strip = depth_map[strip_start:, :]

    if ground_strip.shape[0] < 2:
        return False, 0, "none"

    # Bottom row first = walking-forward order, so the diff signs read naturally:
    # positive step = ground receding, big positive break = void of a drop-off.
    profile = np.mean(ground_strip, axis=1)[::-1]
    diffs = np.diff(profile)
    gradient = np.abs(diffs)
    break_i = int(np.argmax(gradient))
    max_gradient = float(gradient[break_i])

    # Absolute floor first: a step smaller than this is sensor noise no matter how
    # it compares to the baseline. Also stops a near-flat strip (tiny baseline)
    # from turning specks into hazards.
    if max_gradient < HAZARD_MIN_STEP:
        return False, 0, "none"

    median_gradient = float(np.median(gradient))
    if median_gradient > 1e-6:
        # Ratio test is the DETECTION GATE only: is this break distinct from the
        # strip's own texture?
        if max_gradient / median_gradient <= threshold_ratio:
            return False, 0, "none"
    # else: the strip is flat apart from this one break (e.g. a large drop-off
    # whose void drives the median to exactly 0) — trivially distinct, gate
    # passes. (Bailing out here used to silently miss big, close drop-offs.)

    direction = "down" if diffs[break_i] > 0 else "up"
    # Severity is graded on the ABSOLUTE break size, never the ratio: the
    # ratio's denominator is scene noise, so a static desk-edge scene flapped
    # between sev 26 and 255 as the noise median wandered (live finding — the
    # third instance of the unstable-median-denominator bug family). Jump size
    # is stable frame-to-frame and physically meaningful: how far the ground
    # falls (or rises).
    severity = int(np.interp(max_gradient, [HAZARD_MIN_STEP, HAZARD_MAX_JUMP], [1, 255]))
    return True, severity, direction
