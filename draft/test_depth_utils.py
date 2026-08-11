"""
Unit tests for depth_utils — the depth post-processing logic.

Runs with NO dependencies (pytest not required on the Pi):
    python3 -m hailo_apps.python.pipeline_apps.custom_depth_detection.test_depth_utils
    python3 .../test_depth_utils.py
and is also plain-pytest discoverable if pytest is installed:
    pytest test_depth_utils.py

These lock in behaviour before threshold calibration starts, so tuning a constant
can't silently break a detector. Two tests (marked REGRESSION) guard bugs actually
found during development — the drop-off false negative and the vectorization drift.
"""
import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection import depth_utils as du
from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_utils import (
    MIN_DEPTH_M, MAX_DEPTH_M, SUBGRID_SHAPE, ZONE_NAMES,
    crop_border, compute_proximity, detect_blank_wall, detect_floor_to_wall,
    subgrid_proximity, subgrid_cell_proximities, zone_warning, zone_warning_breakdown,
    compute_zone_intensities, to_motor_intensity, detect_ground_hazard, DepthPostProcessor,
)

RNG = np.random.default_rng(0)


# --------------------------------------------------------------------------- #
# Scene builders
# --------------------------------------------------------------------------- #

def flat(value, h=48, w=32, noise=0.0, seed=0):
    z = np.full((h, w), float(value), dtype=np.float32)
    if noise:
        z += np.random.default_rng(seed).normal(0, noise, z.shape).astype(np.float32)
    return z


def zone_from_profile(prof_bottom_up, w=16, noise=0.3, seed=0):
    """Zone whose row medians follow prof (bottom row first)."""
    rows_top_down = list(reversed(list(prof_bottom_up)))
    z = np.array([[v] * w for v in rows_top_down], dtype=np.float32)
    z += np.random.default_rng(seed).normal(0, noise, z.shape).astype(np.float32)
    return z


def _old_subgrid(zone, grid=SUBGRID_SHAPE):
    """Reference implementation: the per-cell loop the vectorized path replaced."""
    rows, cols = grid
    if zone.shape[0] < rows or zone.shape[1] < cols:
        return compute_proximity(zone)
    worst = 0.0
    for rb in np.array_split(zone, rows, axis=0):
        for cell in np.array_split(rb, cols, axis=1):
            worst = max(worst, compute_proximity(cell))
    return worst


# --------------------------------------------------------------------------- #
# compute_proximity
# --------------------------------------------------------------------------- #

def test_proximity_far_is_zero():
    assert compute_proximity(flat(MAX_DEPTH_M + 5)) == 0.0


def test_proximity_nearest_is_max():
    assert compute_proximity(flat(MIN_DEPTH_M)) == 1.0


def test_proximity_is_monotonic_with_closeness():
    vals = [compute_proximity(flat(d)) for d in (29, 26, 23, 20)]
    assert all(a < b for a, b in zip(vals, vals[1:])), vals


def test_proximity_safe_zone_boundary():
    assert compute_proximity(flat(MAX_DEPTH_M)) == 0.0
    assert compute_proximity(flat(MAX_DEPTH_M - 0.5)) > 0.0


# --------------------------------------------------------------------------- #
# crop_border
# --------------------------------------------------------------------------- #

def test_crop_removes_border_ring():
    d = np.full((240, 320), 50.0, np.float32)
    d[0, :] = d[-1, :] = d[:, 0] = d[:, -1] = 1.0   # spurious near ring
    cropped = crop_border(d)
    assert cropped.shape[0] < d.shape[0] and cropped.shape[1] < d.shape[1]
    assert cropped.min() == 50.0                    # ring gone


def test_crop_leaves_tiny_map_untouched():
    tiny = np.ones((5, 5), np.float32)
    assert crop_border(tiny).shape == (5, 5)        # never emptied


# --------------------------------------------------------------------------- #
# subgrid pooling (thin objects)
# --------------------------------------------------------------------------- #

def test_subgrid_catches_thin_pole():
    zone = flat(46.0)                # open, far
    zone[:, zone.shape[1] // 2] = MIN_DEPTH_M   # one near column
    assert subgrid_proximity(zone) > 0.9


def test_subgrid_rejects_single_noise_pixel():
    zone = flat(46.0)
    zone[10, 7] = MIN_DEPTH_M        # one lone near pixel
    assert subgrid_proximity(zone) < 0.05


def test_subgrid_quiet_on_open_scene():
    assert subgrid_proximity(flat(46.0)) == 0.0


def test_subgrid_handles_odd_sizes():
    subgrid_proximity(flat(30.0, h=47, w=15))   # must not raise


def test_subgrid_cells_match_reference_bit_exact():  # REGRESSION: vectorization drift
    bad = 0
    for t in range(200):
        z = np.clip(RNG.normal(33, 9, (48, int(RNG.choice([16, 32])))),
                    MIN_DEPTH_M, 55).astype(np.float32)
        if t % 3 == 0:
            z[:, z.shape[1] // 2] = MIN_DEPTH_M
        if _old_subgrid(z) != subgrid_proximity(z):   # exact equality required
            bad += 1
    assert bad == 0, f"{bad}/200 zones drifted from the reference loop"


# --------------------------------------------------------------------------- #
# blank-wall detection (variance)
# --------------------------------------------------------------------------- #

def test_blank_wall_close_flat_warns():
    assert detect_blank_wall(flat(22.0, noise=1.0)) > 0.0


def test_blank_wall_far_flat_is_silent():
    assert detect_blank_wall(flat(40.0, noise=1.0)) == 0.0


def test_blank_wall_textured_scene_is_silent():
    assert detect_blank_wall(flat(38.0, noise=8.0, seed=3)) == 0.0


def test_calibration_measures_what_detectors_threshold():  # REGRESSION: logger/detector drift
    # The logger once recorded a zone-GLOBAL std while detect_blank_wall compared
    # a median per-cell std — ~2x apart on a real wall and on opposite sides of
    # the threshold, so calibrating from it would have degraded the detector.
    # The logger must report the detectors' own measures, not its own copies.
    from hailo_apps.python.pipeline_apps.custom_depth_detection.calibration import zone_depth_stats

    h, w = 48, 64
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((yy - h / 2) / (h / 2)) ** 2 + ((xx - w / 2) / (w / 2)) ** 2)
    wall = (29.7 - 14.5 * np.clip(r, 0, 1) ** 1.5
            + np.random.default_rng(0).normal(0, 0.35, (h, w))).astype(np.float32)

    logged = zone_depth_stats(wall)
    centre = wall[:, w // 4: 3 * w // 4]
    assert logged["center_flatness"] == du.local_flatness(centre)
    assert logged["center_coverage"] == zone_warning_breakdown(centre)["coverage"]
    for key, val in du.ground_break_stats(wall, h).items():
        assert logged[f"ground_{key}"] == val
    # and the recorded flatness must be the measure the threshold is compared to
    assert (logged["center_flatness"] <= du.WALL_VARIANCE_MAX) == (detect_blank_wall(centre) > 0)


def test_blank_wall_saturates_at_point_blank():  # REGRESSION: point-blank wall buzzed only 186/255
    # A confirmed solid wall whose nearest credible cluster is deep in warning
    # range must saturate to 1.0 — the washed-out middle must not soften it.
    close = flat(17.0, noise=0.8)
    assert detect_blank_wall(close) == 1.0
    # …but a confirmed wall at moderate distance stays graded, not maxed.
    moderate = flat(26.5, noise=0.8)
    val = detect_blank_wall(moderate)
    assert 0.0 < val < 1.0


def test_blank_wall_grades_from_near_cluster_not_median():
    # Dome-like wall: near band at the bottom, washed-out "far" middle dragging
    # the median up. The warning must follow the near cluster, not the median.
    zone = flat(27.0, noise=0.5)          # washed-out bulk
    zone[40:, :] = 17.0                    # near band (bottom sixth, ~17 units)
    med_val = du._proximity_curve(float(np.median(zone)))
    assert detect_blank_wall(zone) > med_val


def test_blank_wall_dome_gradient_wall_warns():  # REGRESSION: real Pi wall reads as a dome
    # Live capture showed a blank wall rendered as a smooth radial gradient
    # (p1/p50/p99 ~ 15.2/22.3/30.2, near edges -> "far" mid-wall). Zone-global
    # std rejected it; local flatness must accept it in EVERY zone.
    h, w = 48, 64
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((yy - h / 2) / (h / 2)) ** 2 + ((xx - w / 2) / (w / 2)) ** 2)
    dome = 29.7 - (29.7 - 15.2) * np.clip(r, 0, 1) ** 1.5
    small = (dome + np.random.default_rng(0).normal(0, 0.35, (h, w))).astype(np.float32)
    for zone in (small[:, :w // 4], small[:, w // 4:3 * w // 4], small[:, 3 * w // 4:]):
        assert detect_blank_wall(zone) > 0.05, "dome-rendered wall must register as a wall"
    # and a genuinely busy scene must still NOT look like a wall
    busy = np.clip(np.random.default_rng(1).normal(30, 8, (h, w)), 15, 50).astype(np.float32)
    assert detect_blank_wall(busy[:, 16:48]) == 0.0


# --------------------------------------------------------------------------- #
# floor-to-wall (wall the model reads as far)
# --------------------------------------------------------------------------- #

def test_floor_to_wall_near_wall_read_as_far():
    z = zone_from_profile([19, 20, 21, 22, 23, 46, 46, 46, 46, 46, 46, 46])
    # Curve-independent check: must fire, graded as the curve grades a wall base
    # at ~23 units (don't hardcode a number that breaks on threshold rescales).
    expected = du._proximity_curve(23.0)
    got = detect_floor_to_wall(z)
    assert got > 0.5 * expected, f"f2w {got:.3f} vs expected ~{expected:.3f}"


def test_floor_to_wall_open_corridor_silent():
    z = zone_from_profile([19, 22, 25, 28, 31, 34, 37, 40, 43, 46, 48, 50])
    assert detect_floor_to_wall(z) < 0.05


def test_floor_to_wall_distant_dropoff_silent():
    z = zone_from_profile([19, 24, 29, 34, 40, 52, 52, 52, 52, 52])
    assert detect_floor_to_wall(z) < 0.05


def test_floor_to_wall_no_floor_visible():
    assert detect_floor_to_wall(flat(44.0)) == 0.0


def test_floor_to_wall_smooth_wall_noise_is_silent():  # REGRESSION: live Pi false positive
    # Camera inches from a wall, NO floor in frame: a smooth quasi-flat dome
    # slice whose noise wiggles (~0.8 units) scored f=1.00 because the relative
    # baseline collapsed. Must be 0.0 — there is no floor-wall break here.
    h, w = 48, 16
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((yy - h / 2) / (h / 2)) ** 2 + ((xx - 8) / 32) ** 2)
    dome = 17.0 - 1.8 * np.clip(r, 0, 1)
    zone = (dome + np.random.default_rng(0).normal(0, 0.35, (h, w))).astype(np.float32)
    assert detect_floor_to_wall(zone) == 0.0


def test_floor_to_wall_ignores_sub_noise_jumps():
    # a break smaller than FLOOR_WALL_MIN_JUMP is sensor noise regardless of ratio
    z = zone_from_profile([19.0, 19.2, 19.4, 19.6, 20.8, 20.9, 21.0, 21.1], noise=0.05)
    assert detect_floor_to_wall(z) == 0.0


# --------------------------------------------------------------------------- #
# ground hazard (dropoffs / stairs)
# --------------------------------------------------------------------------- #

def test_hazard_flat_floor_is_safe():
    assert detect_ground_hazard(flat(25.0, h=48, w=64), 48)[0] is False


def test_hazard_noisy_flat_floor_is_safe():
    assert detect_ground_hazard(flat(25.0, h=48, w=64, noise=0.4), 48)[0] is False


def test_hazard_smooth_receding_floor_is_safe():
    floor = np.repeat(np.linspace(40, 19, 48)[:, None], 64, axis=1).astype(np.float32)
    assert detect_ground_hazard(floor, 48)[0] is False


def test_hazard_big_close_dropoff_detected():  # REGRESSION: flat-void median==0 bail-out
    d = np.zeros((48, 64), np.float32)
    d[:44, :] = 52.0                              # void fills most of the strip
    d[44:, :] = np.linspace(24, 19, 4)[:, None]   # near floor at the very bottom
    detected, severity, direction = detect_ground_hazard(d, 48)
    assert detected is True and severity > 0, "large drop-off silently missed"
    assert direction == "down"


def test_hazard_direction_down_on_dropoff():
    # walking forward the floor recedes, then depth jumps FARTHER (the void)
    prof = np.concatenate([np.linspace(19, 24, 6), np.full(6, 52.0)])   # bottom-up
    d = np.repeat(prof[::-1][:, None], 64, axis=1).astype(np.float32)
    full = np.vstack([np.full((36, 64), 52.0, np.float32), d])          # strip = last 12 rows
    detected, _, direction = detect_ground_hazard(full, 48)
    assert detected is True and direction == "down"


def test_hazard_direction_up_on_curb():
    # walking forward the floor recedes, then depth jumps NEARER (riser face).
    # Jump of -8 against ~1.0/row floor steps -> ratio 8, clear of the 5x
    # threshold (a jump of exactly 5x sits ON the boundary and is rejected).
    prof = np.concatenate([np.linspace(19, 27, 6), np.full(6, 19.0)])   # bottom-up
    d = np.repeat(prof[::-1][:, None], 64, axis=1).astype(np.float32)
    full = np.vstack([np.full((36, 64), 19.0, np.float32), d])
    detected, _, direction = detect_ground_hazard(full, 48)
    assert detected is True and direction == "up"


def test_hazard_direction_none_when_safe():
    assert detect_ground_hazard(flat(25.0, h=48, w=64), 48)[2] == "none"


def test_hazard_severity_stable_under_noise():  # REGRESSION: sev flapped 26<->255 on a static scene
    # Same physical break, different sensor noise each frame: severity must not
    # swing wildly (the old ratio-based grading did, because its denominator was
    # the noise median of unrelated rows).
    sevs = []
    for seed in range(12):
        d = np.full((48, 64), 16.0, np.float32)          # near "desk" surface
        d[:40, :] = 36.0                                  # far room beyond the edge
        d += np.random.default_rng(seed).normal(0, 0.35, d.shape).astype(np.float32)
        detected, sev, direction = detect_ground_hazard(d, 48)
        assert detected is True and direction == "down"
        sevs.append(sev)
    assert max(sevs) - min(sevs) <= 40, f"severity unstable across noise: {sevs}"


def test_hazard_severity_scales_with_drop_size():
    # a bigger cliff must grade at least as severe as a smaller one
    def scene(far):
        d = np.full((48, 64), 19.0, np.float32)
        d[:42, :] = far
        return d
    small = detect_ground_hazard(scene(24.0), 48)[1]   # 5-unit break
    big = detect_ground_hazard(scene(39.0), 48)[1]     # 20-unit break
    assert 0 < small < big
    assert big == 255                                   # >= HAZARD_MAX_JUMP saturates


def test_hazard_tiny_step_below_noise_floor_ignored():
    d = flat(25.0, h=48, w=64)
    d[36:, :] += np.linspace(0, 1.0, 12)[:, None]   # <2.0 total, sub-noise
    assert detect_ground_hazard(d, 48)[0] is False


# --------------------------------------------------------------------------- #
# zone_warning — the fail-safe max() invariant
# --------------------------------------------------------------------------- #

def test_zone_warning_never_suppresses_any_detector():
    for _ in range(100):
        base = float(RNG.uniform(19, 45))
        z = flat(base, noise=float(RNG.uniform(0.2, 6.0)), seed=int(RNG.integers(1e6)))
        w = zone_warning(z)
        assert w >= subgrid_proximity(z) - 1e-9
        assert w >= detect_blank_wall(z) - 1e-9
        assert w >= detect_floor_to_wall(z) - 1e-9


def test_zone_warning_breakdown_structure():
    bd = zone_warning_breakdown(flat(22.0, noise=1.0))
    assert set(bd["parts"]) == {"sub", "wall", "f2w"}
    assert bd["winner"] in bd["parts"]
    assert bd["value"] == max(bd["parts"].values())
    assert 0.0 <= bd["coverage"] <= 1.0
    assert bd["shape"] in ("thin", "broad", "none")


def test_breakdown_shape_thin_vs_broad():  # REGRESSION: wall was mislabelled "thin object"
    # thin pole on open background -> localized: few danger cells
    pole = flat(46.0)
    pole[:, pole.shape[1] // 2] = MIN_DEPTH_M
    bd = zone_warning_breakdown(pole)
    assert bd["shape"] == "thin" and bd["coverage"] <= 0.4

    # close wall filling the zone -> broad: cells blanket the grid
    bd = zone_warning_breakdown(flat(21.0, noise=0.8))
    assert bd["shape"] == "broad" and bd["coverage"] > 0.4

    # moderate-distance wall (~0.4 in every cell, none past 0.5) must ALSO be
    # broad — an absolute cutoff mislabelled exactly this as "thin"
    bd = zone_warning_breakdown(flat(23.5, noise=0.5))
    assert bd["shape"] == "broad" and bd["coverage"] > 0.9

    # open scene -> nothing near at all
    bd = zone_warning_breakdown(flat(46.0))
    assert bd["shape"] == "none" and bd["coverage"] == 0.0


# --------------------------------------------------------------------------- #
# to_motor_intensity + zone split + EMA pipeline
# --------------------------------------------------------------------------- #

def test_to_motor_intensity_range_and_clamp():
    assert to_motor_intensity(0.0) == 0
    assert to_motor_intensity(1.0) == 255
    assert to_motor_intensity(-1.0) == 0 and to_motor_intensity(2.0) == 255


def test_compute_zone_intensities_returns_three_zones():
    out = compute_zone_intensities(flat(30.0, h=48, w=64), 64)
    assert set(out) == set(ZONE_NAMES)
    assert all(0.0 <= v <= 1.0 for v in out.values())


def test_pipeline_outputs_0_255_ints():
    out = DepthPostProcessor().process(flat(21.0, h=48, w=64))
    assert set(out) == set(ZONE_NAMES)
    assert all(isinstance(v, int) and 0 <= v <= 255 for v in out.values())


def test_ema_smooths_a_step_change():
    proc = DepthPostProcessor()
    near = proc.process(flat(MIN_DEPTH_M, h=48, w=64))["center"]   # ~255
    after = proc.process(flat(MAX_DEPTH_M + 10, h=48, w=64))["center"]  # raw would be 0
    assert 0 < after < near, f"EMA should lag: near={near} after={after}"


def test_pipeline_reset_clears_state():
    proc = DepthPostProcessor()
    proc.process(flat(MIN_DEPTH_M, h=48, w=64))
    proc.reset()
    # after reset the first frame is taken raw (no lag from the old near value)
    out = proc.process(flat(MAX_DEPTH_M + 10, h=48, w=64))["center"]
    assert out == 0


# --------------------------------------------------------------------------- #
# Zero-dependency runner (used when pytest isn't installed)
# --------------------------------------------------------------------------- #

def _run():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed" + (f", {len(failed)} FAILED" if failed else " — all green"))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(_run())
