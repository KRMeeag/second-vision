"""
Unit tests for the depth post-processing math (core/depth_utils.py).

All hardware-independent: every test builds a synthetic depth map with numpy, so
this runs on any machine (no Hailo, no camera, no depth model). Run with:

    python3 -m pytest tests/test_depth_utils.py -v

WHAT THESE DO AND DO NOT CHECK. Every threshold in depth_utils.py is a
PLACEHOLDER in the model's relative units pending the calibration capture, so
asserting specific motor numbers would only pin down today's guesses and would
have to be rewritten the moment real values land. These tests instead pin the
INVARIANTS that must hold at any threshold:

  * monotonicity   — nearer must never buzz weaker
  * fail-safe      — each detector may only raise a warning, never suppress one
  * bounds/shape   — output is always 0-255 ints on the three named zones
  * stability      — the same scene twice must not produce two different answers

Those are the properties a wearer's safety actually rests on, and they survive
recalibration. Where a test does depend on a constant it reads it from the
module rather than hardcoding the number.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.core.depth_utils as du
from second_vision.core.depth_utils import (
    DepthPostProcessor,
    HazardDebouncer,
    ZONE_NAMES,
    compute_proximity,
    compute_zone_intensities,
    crop_border,
    detect_blank_wall,
    detect_floor_to_wall,
    detect_ground_hazard,
    downsample_depth,
    local_flatness,
    subgrid_proximity,
    thin_structure_mask,
    to_motor_intensity,
    wall_confidence,
)

NATIVE = (256, 320)     # scdepthv3 output resolution (h, w)
SMALL = (48, 64)        # the grid all zone math runs on


def flat(depth, shape=SMALL, sigma=0.0, seed=0):
    """A featureless scene at a uniform depth, optionally with sensor noise."""
    a = np.full(shape, float(depth), dtype=np.float32)
    if sigma:
        a += np.random.default_rng(seed).normal(0, sigma, shape).astype(np.float32)
    return a


def receding_floor(shape=SMALL, near=16.0, step=0.5):
    """Ground plane receding upward — depth grows toward the top of the frame."""
    h, w = shape
    col = near + step * np.arange(h)[::-1]      # bottom row = nearest
    return np.repeat(col[:, None], w, axis=1).astype(np.float32)


# ============================================================
# Proximity curve — the core near->strong mapping
# ============================================================
def test_proximity_is_zero_at_and_beyond_the_safe_cutoff():
    assert compute_proximity(flat(du.MAX_DEPTH_M)) == 0.0
    assert compute_proximity(flat(du.MAX_DEPTH_M + 10)) == 0.0


def test_proximity_saturates_at_the_near_floor():
    """At/below MIN_DEPTH the reading is clamped to full warning, not >1."""
    assert compute_proximity(flat(du.MIN_DEPTH_M)) == pytest.approx(1.0)
    assert compute_proximity(flat(du.MIN_DEPTH_M - 5)) == pytest.approx(1.0)


def test_proximity_is_monotonic_in_distance():
    """Nearer must never warn less. The one property a wearer's safety rests on."""
    depths = np.linspace(du.MIN_DEPTH_M, du.MAX_DEPTH_M, 25)
    readings = [compute_proximity(flat(d)) for d in depths]
    assert all(a >= b for a, b in zip(readings, readings[1:])), readings


def test_proximity_curve_is_non_linear_toward_the_near_end():
    """
    DECISIONS/ARCHITECTURE: 'linear feels unnatural to users' — intensity must
    spike only when critically close. So the midpoint distance must read BELOW
    the linear half, not at it.
    """
    mid = (du.MIN_DEPTH_M + du.MAX_DEPTH_M) / 2
    assert compute_proximity(flat(mid)) < 0.5


# ============================================================
# Zone splitting
# ============================================================
def test_zone_intensities_have_exactly_the_three_serial_queue_zones():
    out = compute_zone_intensities(flat(20.0), SMALL[1])
    assert set(out) == set(ZONE_NAMES) == {"left", "center", "right"}


def test_near_object_raises_only_its_own_zone():
    """A hazard on the left must not buzz the right temple."""
    scene = flat(du.MAX_DEPTH_M + 5)                 # everything safely far
    scene[:, : SMALL[1] // 8] = du.MIN_DEPTH_M       # something touching, hard left
    out = compute_zone_intensities(scene, SMALL[1])
    assert out["left"] > 0.5
    assert out["right"] == 0.0


def test_center_zone_is_the_wide_one():
    """25/50/25 split — the centre must own half the frame (D7)."""
    bounds = du._zone_bounds(64)
    widths = {z: hi - lo for z, (lo, hi) in bounds.items()}
    assert widths["center"] == widths["left"] + widths["right"] == 32


# ============================================================
# Fail-safe composition — the property that keeps the device honest
# ============================================================
@pytest.mark.parametrize("scene", [
    flat(18.0), flat(24.0), flat(20.0, sigma=1.0), receding_floor(),
    flat(28.0, sigma=2.0, seed=3),
])
def test_zone_warning_is_never_below_any_single_detector(scene):
    """
    zone_warning() is a max() of the detectors precisely so each can only RAISE
    a warning. If any detector could pull the combined value down, a scene that
    one detector understood would be silenced by another that did not.
    """
    combined = du.zone_warning(scene)
    for detector in (subgrid_proximity, detect_blank_wall, detect_floor_to_wall):
        assert combined >= detector(scene) - 1e-9, detector.__name__


def test_subgrid_is_never_less_sensitive_than_the_whole_zone():
    """A thin obstacle filling one cell must survive whole-zone averaging."""
    scene = flat(du.MAX_DEPTH_M - 1)
    scene[8:16, 8:20] = du.MIN_DEPTH_M            # near patch filling one sub-cell
    assert subgrid_proximity(scene) > compute_proximity(scene)


def test_subgrid_needs_an_object_to_fill_a_tenth_of_one_cell():
    """
    Documents the detector's real sensitivity floor, which is easy to misjudge.
    Each cell is aggregated at NEAR_PERCENTILE, so an object covering less than
    ~10% of a single sub-cell (1/16 of a zone) is rejected as speckle — that
    rejection is deliberate, but it means 'sub-grid pooling catches thin objects'
    has a size limit, and how far away a given object crosses it is one of the
    things the calibration capture still has to establish.
    """
    cell_h, cell_w = SMALL[0] // du.SUBGRID_SHAPE[0], SMALL[1] // du.SUBGRID_SHAPE[1]
    below = flat(du.MAX_DEPTH_M - 1)
    below[:2, :2] = du.MIN_DEPTH_M               # ~2% of a cell
    assert subgrid_proximity(below) == pytest.approx(compute_proximity(below))

    above = flat(du.MAX_DEPTH_M - 1)
    above[:cell_h, :cell_w] = du.MIN_DEPTH_M     # a full cell
    assert subgrid_proximity(above) > compute_proximity(above)


# ============================================================
# Blank wall
# ============================================================
def test_wall_confidence_is_a_ramp_not_a_cliff():
    """
    A hard gate turned an imperceptible texture change into a 100+ point motor
    swing, so confidence must fall off gradually rather than flip.
    """
    assert wall_confidence(du.WALL_VARIANCE_MAX * 0.5) == 1.0
    assert wall_confidence(du.WALL_VARIANCE_MAX) == 1.0
    mid = wall_confidence(du.WALL_VARIANCE_MAX * (1 + du.WALL_VARIANCE_SOFT) / 2)
    assert 0.0 < mid < 1.0
    assert wall_confidence(du.WALL_VARIANCE_MAX * du.WALL_VARIANCE_SOFT * 2) == 0.0


def test_flat_surface_reads_flatter_than_a_textured_one():
    """local_flatness is what WALL_VARIANCE_MAX is compared against."""
    assert local_flatness(flat(20.0)) < local_flatness(flat(20.0, sigma=4.0))


def test_blank_wall_never_suppresses_a_reading():
    """Returns 0.0 when it does not apply — never a value below the raw curve."""
    textured = flat(20.0, sigma=8.0, seed=1)
    assert detect_blank_wall(textured) >= 0.0


def test_distant_blank_wall_does_not_warn():
    assert detect_blank_wall(flat(du.MAX_DEPTH_M + 10)) == 0.0


# ============================================================
# Floor-to-wall
# ============================================================
def test_open_receding_floor_does_not_fire():
    """A smoothly receding floor is open space — the whole point of the ratio test."""
    assert detect_floor_to_wall(receding_floor()) == 0.0


def test_floor_terminating_in_a_near_wall_fires():
    scene = receding_floor()
    scene[: SMALL[0] // 2, :] = du.MAX_DEPTH_M + 20     # wall reads as 'far'
    assert detect_floor_to_wall(scene) > 0.0


def test_floor_to_wall_ignores_sub_noise_jumps():
    """A wiggle below FLOOR_WALL_MIN_JUMP must not score as a wall (live FP)."""
    scene = flat(20.0)
    scene[: SMALL[0] // 2, :] += du.FLOOR_WALL_MIN_JUMP * 0.4
    assert detect_floor_to_wall(scene) == 0.0


# ============================================================
# Ground hazard
# ============================================================
def test_flat_ground_is_not_a_hazard():
    detected, severity, direction = detect_ground_hazard(flat(20.0), SMALL[0])
    assert (detected, severity, direction) == (False, 0, "none")


def ground_break(base, jump, shape=SMALL):
    """
    A ground strip that breaks by `jump` partway along it.

    The break has to sit INSIDE the bottom GROUND_STRIP_FRACTION of the frame —
    that strip is all detect_ground_hazard looks at, so a break above it is
    invisible by design (the detector reads ground 1-3 m ahead, not the horizon).
    """
    h, _ = shape
    strip_start = int(h * (1 - du.GROUND_STRIP_FRACTION))
    scene = flat(base, shape)
    scene[: (strip_start + h) // 2, :] = base + jump     # midway up the strip
    return scene


def test_dropoff_reads_down_and_stepup_reads_up():
    """The sign of the break separates a fall hazard from a trip hazard."""
    drop = ground_break(20.0, +du.HAZARD_MAX_JUMP)       # ground falls away
    assert detect_ground_hazard(drop, SMALL[0])[::2] == (True, "down")

    step = ground_break(30.0, -du.HAZARD_MAX_JUMP)       # ground rises toward you
    assert detect_ground_hazard(step, SMALL[0])[::2] == (True, "up")


def test_hazard_severity_grows_with_the_break_and_stays_in_range():
    """Severity is graded on absolute jump size, so it must be monotone in it."""
    sevs = []
    for jump in (du.HAZARD_MIN_STEP * 1.5, du.HAZARD_MAX_JUMP / 2, du.HAZARD_MAX_JUMP):
        det, sev, _ = detect_ground_hazard(ground_break(20.0, jump), SMALL[0])
        assert det and 0 <= sev <= 255
        sevs.append(sev)
    assert sevs == sorted(sevs), sevs


def test_hazard_ignores_breaks_below_the_absolute_noise_floor():
    scene = ground_break(20.0, du.HAZARD_MIN_STEP * 0.5)
    assert detect_ground_hazard(scene, SMALL[0])[0] is False


def test_hazard_only_looks_at_the_ground_strip():
    """A break above the strip is the horizon, not a drop-off at the feet."""
    scene = flat(20.0)
    scene[: SMALL[0] // 4, :] = 20.0 + du.HAZARD_MAX_JUMP
    assert detect_ground_hazard(scene, SMALL[0])[0] is False


# ============================================================
# HazardDebouncer — the motors get a decision, not a per-frame opinion
# ============================================================
def test_debouncer_requires_consecutive_frames_to_assert():
    d = HazardDebouncer(on_frames=3, off_frames=5)
    assert [d.update(True, 200, "down")[0] for _ in range(4)] == [False, False, True, True]


def test_debouncer_ignores_a_single_frame_blip():
    """One frame of gait bounce or a reflective tile must not buzz the motors."""
    d = HazardDebouncer(on_frames=3, off_frames=5)
    assert d.update(True, 200, "down")[0] is False
    assert d.update(False, 0, "none")[0] is False
    assert d.update(True, 200, "down")[0] is False     # streak restarted, not resumed


def test_debouncer_rejects_the_alternating_pattern_it_exists_for():
    """The measured failure: true/false flapping at ~10 Hz. Must never assert."""
    d = HazardDebouncer(on_frames=3, off_frames=5)
    states = [d.update(i % 2 == 0, 200, "down")[0] for i in range(40)]
    assert not any(states)


def test_debouncer_release_is_slower_than_assert():
    """Fail-safe direction: a hazard that drops out keeps warning."""
    d = HazardDebouncer(on_frames=3, off_frames=5)
    for _ in range(3):
        d.update(True, 200, "down")
    assert d.update(False, 0, "none")[0] is True       # still latched
    for _ in range(3):
        d.update(False, 0, "none")
    assert d.update(False, 0, "none")[0] is False      # released on the 5th


def test_debouncer_latches_at_full_severity_immediately():
    """A drop-off must be felt at strength at once, not faded in over 0.3 s."""
    d = HazardDebouncer(on_frames=2, off_frames=5)
    d.update(True, 240, "down")
    _, severity, direction = d.update(True, 240, "down")
    assert severity == 240 and direction == "down"


def test_debouncer_reports_nothing_while_clear():
    d = HazardDebouncer()
    assert d.update(False, 0, "none") == (False, 0, "none")


def test_debouncer_reset_clears_a_latched_hazard():
    """After a pipeline rebuild the device must not warn about the old scene."""
    d = HazardDebouncer(on_frames=1)
    d.update(True, 200, "down")
    assert d.update(True, 200, "down")[0] is True
    d.reset()
    assert d.update(False, 0, "none") == (False, 0, "none")


# ============================================================
# Framing helpers
# ============================================================
def test_crop_border_trims_the_unreliable_edge_ring():
    cropped = crop_border(np.zeros(NATIVE, dtype=np.float32), ratio=0.04)
    assert cropped.shape[0] < NATIVE[0] and cropped.shape[1] < NATIVE[1]


def test_crop_border_leaves_a_tiny_map_alone():
    tiny = np.zeros((4, 4), dtype=np.float32)
    assert crop_border(tiny).shape == (4, 4)


def test_downsample_reaches_the_grid_and_is_idempotent():
    small = downsample_depth(np.zeros(NATIVE, dtype=np.float32))
    assert small.shape == SMALL
    assert downsample_depth(small).shape == SMALL      # no-op when already small


def test_border_artifacts_do_not_leak_into_zone_readings():
    """The spurious-near edge ring must not manufacture a side warning."""
    scene = flat(du.MAX_DEPTH_M + 5, shape=NATIVE)
    scene[0, :] = scene[-1, :] = du.MIN_DEPTH_M        # fake conv edge artifact
    scene[:, 0] = scene[:, -1] = du.MIN_DEPTH_M
    out = compute_zone_intensities(downsample_depth(crop_border(scene)), SMALL[1])
    assert all(v == 0.0 for v in out.values()), out


# ============================================================
# Thin structures
# ============================================================
def test_thin_mask_is_empty_on_a_broad_surface():
    """The top-hat is exactly 0 on anything wider than the kernel — by geometry."""
    assert not thin_structure_mask(flat(20.0, shape=NATIVE)).any()


def test_thin_mask_finds_a_cable_against_a_wall():
    scene = flat(26.0, shape=NATIVE)
    scene[120:123, :] = 18.0                            # 3 px cable, well in front
    assert thin_structure_mask(scene).any()


def test_thin_mask_ignores_contrast_below_the_threshold():
    scene = flat(26.0, shape=NATIVE)
    scene[120:123, :] = 26.0 - du.THIN_MIN_CONTRAST * 0.4
    assert not thin_structure_mask(scene).any()


def cable_scene():
    """A cable at collision range against a background that is safely far.

    Every magnitude-based detector reads this as empty — the background is beyond
    the safe-zone cutoff and the cable is 3 px of a 256-row frame — so whatever
    reaches the motors here came from the ridge pass and nothing else.
    """
    scene = flat(du.MAX_DEPTH_M + 5, shape=NATIVE)
    scene[120:123, :] = du.MIN_DEPTH_M
    return scene


def test_a_cable_alone_drives_the_motors_when_enabled(monkeypatch):
    """The reason the detector exists: nothing else in the module sees this."""
    monkeypatch.setattr(du, "THIN_DRIVES_MOTORS", True)
    assert DepthPostProcessor().process(cable_scene())["center"] > 0


def test_thin_is_off_by_default_and_still_auditable():
    """
    Live decision (2026-09-14): the T tag fired on furniture edges, so the
    reading no longer reaches the motors — but it must still be computed and
    published, or the HUD cannot show whether calibration has fixed it.
    """
    assert not du.THIN_DRIVES_MOTORS
    p = DepthPostProcessor()
    assert p.process(cable_scene())["center"] == 0
    assert p.last_thin["center"] > 0


def test_thin_only_ever_raises_a_zone(monkeypatch):
    """
    Fail-safe, the same invariant every other detector is held to: folding thin in
    may never produce a QUIETER zone than leaving it out. This is what keeps the
    combination safe while THIN_MIN_CONTRAST is still an uncalibrated placeholder
    — a noisy ridge pass can over-warn, but it cannot mask a real obstacle.
    """
    for scene in (cable_scene(), flat(20.0, shape=NATIVE), flat(24.0, shape=NATIVE, sigma=1.0)):
        monkeypatch.setattr(du, "THIN_DRIVES_MOTORS", False)
        without = DepthPostProcessor().process(scene)
        monkeypatch.setattr(du, "THIN_DRIVES_MOTORS", True)
        with_thin = DepthPostProcessor().process(scene)
        assert all(with_thin[z] >= without[z] for z in ZONE_NAMES), (without, with_thin)


def test_the_flag_off_restores_the_diagnostic_only_behaviour(monkeypatch):
    """
    The field-testing escape hatch: if the device buzzes on furniture, one
    constant returns it to the previous behaviour — motors quiet, reading still
    published for the HUD.
    """
    monkeypatch.setattr(du, "THIN_DRIVES_MOTORS", False)
    p = DepthPostProcessor()
    assert p.process(cable_scene())["center"] == 0
    assert p.last_thin["center"] > 0, "reading must stay auditable when gated off"


# ============================================================
# Motor output contract
# ============================================================
@pytest.mark.parametrize("value,expected", [
    (0.0, 0), (1.0, 255), (-5.0, 0), (5.0, 255), (0.5, 128),
])
def test_to_motor_intensity_clamps_into_the_byte_range(value, expected):
    assert to_motor_intensity(value) == expected


def test_to_motor_intensity_is_monotonic():
    vals = [to_motor_intensity(v) for v in np.linspace(0, 1, 50)]
    assert vals == sorted(vals)


# ============================================================
# DepthPostProcessor — the object the callback actually holds
# ============================================================
def test_process_returns_the_serial_queue_contract():
    out = DepthPostProcessor().process(flat(20.0, shape=NATIVE))
    assert set(out) == set(ZONE_NAMES)
    assert all(isinstance(v, int) and 0 <= v <= 255 for v in out.values())


def test_process_accepts_a_native_resolution_map():
    """The callback hands it a CROPPED but NOT downsampled map."""
    assert set(DepthPostProcessor().process(flat(22.0, shape=NATIVE))) == set(ZONE_NAMES)


def test_ema_smooths_rather_than_jumping():
    """
    A scene snapping from far to near must ramp, or the haptics flicker on any
    momentary misread. First frame seeds the filter; the step is smoothed after.
    """
    p = DepthPostProcessor(alpha=0.3)
    p.process(flat(du.MAX_DEPTH_M + 5, shape=NATIVE))
    first = p.process(flat(du.MIN_DEPTH_M, shape=NATIVE))["center"]
    second = p.process(flat(du.MIN_DEPTH_M, shape=NATIVE))["center"]
    assert first < 255, "step went straight to full — EMA not applied"
    assert second > first, "not converging toward the new value"


def test_ema_converges_to_the_steady_state():
    p = DepthPostProcessor(alpha=0.5)
    scene = flat(du.MIN_DEPTH_M, shape=NATIVE)
    for _ in range(40):
        out = p.process(scene)
    assert out["center"] == 255


def test_reset_clears_the_ema_history():
    """Without this, the previous mode's readings bleed across a rebuild."""
    p = DepthPostProcessor()
    for _ in range(10):
        p.process(flat(du.MIN_DEPTH_M, shape=NATIVE))
    p.reset()
    after = p.process(flat(du.MAX_DEPTH_M + 5, shape=NATIVE))
    assert after["center"] == 0, "stale near-field reading survived reset()"


def test_processing_is_deterministic():
    """Same scene, two processors, same answer — no hidden state or RNG."""
    scene = flat(21.0, shape=NATIVE, sigma=1.0, seed=7)
    assert DepthPostProcessor().process(scene) == DepthPostProcessor().process(scene)


def test_a_static_scene_does_not_flap():
    """
    The bug family this module keeps hitting is an unstable noise denominator
    making a motionless scene oscillate. Once settled, repeats must be constant.
    """
    p = DepthPostProcessor()
    scene = flat(22.0, shape=NATIVE, sigma=1.0, seed=11)
    for _ in range(30):
        p.process(scene)
    settled = [p.process(scene)["center"] for _ in range(10)]
    assert max(settled) - min(settled) <= 1, settled


# ============================================================
# Floor suppression — the ground plane is not an obstacle
# ============================================================
def open_floor(shape=NATIVE, near=16.0, far=45.0, horizon=0.4, sigma=1.0, seed=0):
    """
    A forehead-camera view of an empty floor: `near` at the bottom row, receding
    to `far` at `horizon` (fraction of the height from the top), `far` above it.
    The scene the device spends most of its life looking at.
    """
    h, w = shape
    yy = np.arange(h)[:, None]
    hz = int(h * horizon)
    d = np.where(yy > hz, near + (far - near) * (h - yy) / (h - hz), far)
    d = np.repeat(d, w, axis=1).astype(np.float32)
    if sigma:
        d += np.random.default_rng(seed).normal(0, sigma, shape).astype(np.float32)
    return d


def settled(scene, frames=30):
    p = DepthPostProcessor()
    out = None
    for _ in range(frames):
        out = p.process(scene)
    return out, p


def test_floor_profile_fits_a_receding_plane():
    scene = open_floor(shape=SMALL, sigma=0.0)
    floor = du.floor_profile(scene)
    bottom = scene.shape[0] - 1
    assert np.isfinite(floor[bottom])
    # Within the fit window the profile tracks the measured floor (to within
    # the margin the mask allows — the synthetic floor is linear in depth, the
    # fit is linear in 1/depth, so they part slightly at the window edge).
    fit_rows = range(bottom - int(SMALL[0] * du.FLOOR_FIT_FRACTION) + 1, bottom + 1)
    for r in fit_rows:
        assert abs(floor[r] - scene[r, 0]) < du.FLOOR_MARGIN_ABS, (r, floor[r], scene[r, 0])
    # And above the fit window it keeps growing: the plane recedes.
    assert floor[bottom - 20] > floor[bottom]


def test_floor_profile_refuses_a_flat_scene():
    """A wall filling the bottom of the frame must NOT be mistaken for a floor."""
    assert not np.isfinite(du.floor_profile(flat(20.0))).any()
    assert not np.isfinite(du.floor_profile(flat(20.0, sigma=1.0))).any()


def test_floor_profile_refuses_a_floor_that_recedes_too_little():
    scene = receding_floor(near=20.0, step=0.05)         # 20 -> 20.6 over the fit window
    assert not np.isfinite(du.floor_profile(scene)).any()


def test_empty_floor_drives_nothing():
    """
    THE bug behind "the centre rings when the person is on the right": with the
    floor in the zone math every zone read ~213/255 on an empty floor and the
    motors ran on the wearer's own feet. Empty floor -> silence.
    """
    for sigma in (0.0, 1.0, 1.5):
        out, _ = settled(open_floor(sigma=sigma, seed=3))
        assert out == {z: 0 for z in ZONE_NAMES}, (sigma, out)


def test_person_on_the_right_lights_only_the_right_zone():
    scene = open_floor(seed=4)
    scene[40:, 250:315] = du.MIN_DEPTH_M + 1.0           # inside the right zone (x >= 240)
    out, _ = settled(scene)
    assert out["right"] == 255, out
    assert out["left"] == 0 and out["center"] == 0, out


def test_a_person_at_moderate_range_still_raises_their_zone():
    """
    The inversion floor contamination produced: a person occluding the near
    floor behind them made their zone read FARTHER than the empty ones. Now a
    person in the warning band raises their zone and only their zone.
    """
    scene = open_floor(seed=5)
    scene[40:, 250:315] = 21.0
    out, _ = settled(scene)
    assert out["right"] > 100, out
    assert out["left"] == 0 and out["center"] == 0, out


def test_wall_at_the_end_of_the_floor_survives_suppression():
    """The floor is erased, not the thing it runs into."""
    scene = open_floor(seed=6)
    scene[:150, :] = du.MIN_DEPTH_M + 3.0                # wall across the whole width
    out, _ = settled(scene)
    assert all(out[z] > 200 for z in ZONE_NAMES), out


def test_low_obstacle_on_the_floor_is_kept():
    """A box on the floor is nearer than the floor at its row — it is kept."""
    scene = open_floor(seed=7)
    scene[200:, 250:310] = 17.0                          # bottom 20% of the frame only
    out, _ = settled(scene)
    assert out["right"] > 150 and out["center"] == 0, out


def test_suppression_leaves_rows_above_the_horizon_alone():
    """
    Rows with no floor model (inf) must keep every pixel. `inf - margin` is
    NaN, and a NaN limit compares False — the bug that would have erased
    everything above the horizon, which is where head-height hazards live.
    """
    scene = open_floor(seed=8)
    scene[10:40, 100:220] = du.MIN_DEPTH_M               # near thing well above the horizon
    small = downsample_depth(scene)
    floor = du.floor_profile(small)
    assert not np.isfinite(floor[:10]).any(), "horizon rows should have no floor"
    kept = du.suppress_floor(small, floor)
    top = slice(0, int(small.shape[0] * 0.15))
    assert np.array_equal(kept[top], small[top])


def test_wide_object_in_the_fit_window_disables_suppression_not_the_object():
    """
    A person filling most of the bottom rows bends the row medians flat; the
    fit is rejected and the frame is processed as before — the person fires.
    """
    scene = open_floor(seed=9)
    scene[40:, 64:256] = du.MIN_DEPTH_M + 1.0
    out, p = settled(scene)
    assert not np.isfinite(p.last_floor).any()
    assert out["center"] == 255, out


def test_suppression_can_only_lower_a_zone():
    """
    Fail-safe direction, checked the same way as every other stage: erasing the
    floor may never make a zone LOUDER than leaving it in. (It can make one
    quieter — that is the point.)
    """
    for seed in range(4):
        scene = open_floor(seed=seed)
        scene[60:, 200:300] = 19.0
        small = downsample_depth(crop_border(scene))
        with_floor = {z: du.zone_warning(small[:, a:b])
                      for z, (a, b) in du._zone_bounds(small.shape[1]).items()}
        without = compute_zone_intensities(small, small.shape[1])
        assert all(without[z] <= with_floor[z] + 1e-9 for z in ZONE_NAMES), (with_floor, without)


def test_the_floor_flag_off_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(du, "FLOOR_SUPPRESSION", False)
    out, _ = settled(open_floor(seed=10))
    assert all(out[z] > 150 for z in ZONE_NAMES), out      # the floor is back


def test_thin_reading_ignores_ridge_pixels_on_the_floor():
    """
    The top-hat fires on floor noise (7% of a flat frame at sigma=1.0) and its
    near-percentile then graded the floor as a cable in every zone. Gated to
    pixels in front of the floor, an empty floor reports no thin structure —
    while a cable ABOVE the floor still does.
    """
    _, p = settled(open_floor(sigma=1.0, seed=12))
    assert all(v == 0.0 for v in p.last_thin.values()), p.last_thin
    scene = open_floor(sigma=1.0, seed=12)
    scene[60:63, :] = du.MIN_DEPTH_M + 2.0               # cable above the horizon
    _, p = settled(scene)
    assert p.last_thin["center"] > 0.0
