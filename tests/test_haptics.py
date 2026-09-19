"""
Unit tests for the perceptual/haptic mapping (core/haptics.py).

No hardware and no clock: HapticMapper.shape() takes `now` as an argument, so
the pulse behaviour is driven by passing timestamps rather than by sleeping.
Run with:

    python3 -m pytest tests/test_haptics.py -v

WHAT THESE CHECK. Like test_depth_utils.py, the constants in haptics.py are
placeholders (PWM_FLOOR in particular has to be measured on the real motors), so
these tests avoid asserting specific duty numbers. What they pin instead are the
properties the wearer's experience rests on, all of which survive retuning:

  * silence is reachable   — an unremarkable scene must produce exactly 0
  * felt-or-nothing        — any non-zero output is at or above the motor's
                             start threshold; there is no "buzzing so weakly it
                             reads as broken" band
  * monotonicity           — nearer must never drive weaker
  * direction beats magnitude — an asymmetric scene must read asymmetrically,
                             and a symmetric corridor must not dominate
  * no chatter             — a value parked on a level boundary must not
                             alternate frame to frame
  * nothing at frame rate  — the driven level changes only at pulse-cycle
                             boundaries or on a confirmed rise; a single
                             frame can neither start nor change a tap
  * rate is the reading    — nearer taps faster, point-blank is continuous
  * fail-safe              — failure paths produce zeros, not stale values

The direction / priority / quantizer tests run with pulse=False: they check
stages 1-5 by reading per-frame duties, which the pulse stage deliberately
hides. The pulse tests run the default (pulse on) path.
"""

import sys
from pathlib import Path

import pytest

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.core.haptics as hp
from second_vision.core.haptics import (
    HapticMapper,
    ZONE_NAMES,
    apply_lateral_contrast,
    level_to_pwm,
)


def zones(left, center, right):
    return {"left": left, "center": center, "right": right}


def steady(mapper, values, frames=1, start=0.0, step=1 / 30):
    """Feed the same reading for N frames at 30 FPS; return the last output."""
    out = None
    for i in range(frames):
        out = mapper.shape(values, start + i * step)
    return out


FPS = 20                     # a depth-frame rate to simulate at; the live cap is
                             # app.PIPELINE_FPS_DEFAULT (30) unless SV_FPS lowers it
DT = 1 / FPS


def run(mapper, readings, start=0.0):
    """
    Feed one reading per frame at the depth branch's rate. `readings` is a
    list of zone dicts (or one dict repeated `frames` times via `hold`).
    Returns the list of left-zone duties and the timestamps used.
    """
    duties, times = [], []
    for i, values in enumerate(readings):
        t = start + i * DT
        duties.append(mapper.shape(values, t)["left"])
        times.append(t)
    return duties, times


def hold(values, seconds):
    return [values] * int(round(seconds * FPS))


def taps(duties):
    """Count rising edges: how many separate taps a duty sequence contains."""
    return sum(1 for a, b in zip([0] + duties, duties) if a == 0 and b > 0)


# --- shape and bounds -------------------------------------------------------

def test_output_is_three_ints_in_range():
    out = HapticMapper().shape(zones(200, 40, 0), 0.0)
    assert set(out) == set(ZONE_NAMES)
    for zone in ZONE_NAMES:
        assert isinstance(out[zone], int)
        assert 0 <= out[zone] <= hp.PWM_MAX


def test_input_extremes_do_not_escape_the_range():
    # Nothing upstream should hand us out-of-contract values, but a clamp here
    # is cheaper than a motor driven by a garbage duty byte.
    out = HapticMapper().shape(zones(999, -50, 255), 0.0)
    for zone in ZONE_NAMES:
        assert 0 <= out[zone] <= hp.PWM_MAX


# --- silence is reachable ---------------------------------------------------

def test_empty_scene_is_exactly_silent():
    assert HapticMapper().shape(zones(0, 0, 0), 0.0) == {z: 0 for z in ZONE_NAMES}


def test_faint_reading_is_deadbanded_to_zero():
    faint = int(hp.DEADBAND * 255 * 0.5)
    out = HapticMapper().shape(zones(faint, faint, faint), 0.0)
    assert out == {z: 0 for z in ZONE_NAMES}


def test_uniform_corridor_stays_at_the_lowest_felt_level():
    """
    The corridor case. Both temples reading a moderate wall all the way down a
    hallway carries almost no directional information, so contrast has to
    collapse it to the quietest thing the device can say — not to the same
    reading an obstacle would get.
    """
    out = steady(HapticMapper(pulse=False), zones(120, 120, 120), frames=30)
    assert out["left"] == out["right"] <= hp.PWM_FLOOR


def test_uniform_corridor_does_not_buzz_continuously():
    """
    And it must not hold that level continuously: a device that hums for the
    length of every corridor in a mall is one the wearer switches off, and skin
    habituates to a gentle hum exactly as readily as to a strong one. With the
    rate code a moderate corridor is a slow tap, not a hum.
    """
    duties, _ = run(HapticMapper(), hold(zones(120, 0, 120), 6.0))
    assert 0 in duties, "hums flat down the whole corridor"
    assert taps(duties) >= 3, "goes silent instead of tapping"
    assert sum(1 for d in duties if d) < len(duties) / 2, "on more than off"


def test_head_on_obstacle_survives_contrast():
    """
    The guard on the test above: contrast must not be so aggressive that a wall
    filling the whole field reads the same as open space. A uniformly CLOSE
    frame still has to fire.
    """
    out = HapticMapper(pulse=False).shape(zones(255, 255, 255), 0.0)
    assert out["center"] > 0


# --- felt or nothing --------------------------------------------------------

def test_no_output_lands_below_the_motor_start_threshold():
    """The band between 1 and PWM_FLOOR is where a working device looks dead."""
    mapper = HapticMapper()
    for raw in range(0, 256, 5):
        out = mapper.shape(zones(raw, 0, 0), raw / 30.0)
        for zone in ZONE_NAMES:
            assert out[zone] == 0 or out[zone] >= hp.PWM_FLOOR


def test_level_to_pwm_spans_floor_to_ceiling():
    assert level_to_pwm(0) == 0
    assert level_to_pwm(1) == hp.PWM_FLOOR
    assert level_to_pwm(hp.LEVELS - 1) == hp.PWM_MAX


def test_level_to_pwm_is_monotonic():
    duties = [level_to_pwm(lvl) for lvl in range(hp.LEVELS)]
    assert duties == sorted(duties)


# --- monotonicity -----------------------------------------------------------

def test_nearer_never_drives_weaker():
    """Each reading gets a fresh mapper so hysteresis/pulse state can't confound."""
    last = -1
    for raw in range(0, 256, 8):
        out = HapticMapper(pulse=False).shape(zones(raw, 0, 0), 0.0)
        assert out["left"] >= last
        last = out["left"]


def test_approach_ramps_up_without_stepping_back():
    mapper = HapticMapper(pulse=False)
    seen = []
    for i, raw in enumerate(range(0, 256, 4)):
        seen.append(mapper.shape(zones(raw, raw // 2, 0), i / 30.0)["left"])
    assert seen == sorted(seen)
    assert seen[-1] == hp.PWM_MAX


# --- direction --------------------------------------------------------------

def test_asymmetric_scene_reads_asymmetrically():
    out = HapticMapper(pulse=False).shape(zones(220, 60, 30), 0.0)
    assert out["left"] > out["center"] >= out["right"]


def test_contrast_only_removes_the_common_component():
    values = {"left": 0.9, "center": 0.5, "right": 0.5}
    out = apply_lateral_contrast(values, 0.5)
    assert out["left"] > out["center"]
    assert out["left"] - out["center"] == pytest.approx(0.4)
    assert min(out.values()) >= 0.0


def test_contrast_never_goes_negative():
    out = apply_lateral_contrast({"left": 0.0, "center": 0.0, "right": 0.0}, 1.0)
    assert all(v == 0.0 for v in out.values())


# --- no chatter -------------------------------------------------------------

def test_value_on_a_boundary_does_not_rattle():
    """
    A reading dithering by a couple of counts across a level edge must not
    alternate the motor between two duties every frame — on the skin that is
    felt as a rattle, not as a reading.
    """
    mapper = HapticMapper(pulse=False)
    edge = int((hp.DEADBAND + (1.0 - hp.DEADBAND) / (hp.LEVELS - 1)) * 255) + 20
    outputs = set()
    for i in range(40):
        raw = edge + (2 if i % 2 else -2)
        outputs.add(mapper.shape(zones(raw, 0, 0), i / 30.0)["left"])
    assert len(outputs) == 1


def test_same_scene_twice_gives_the_same_answer():
    a = HapticMapper(pulse=False).shape(zones(180, 90, 20), 0.0)
    b = HapticMapper(pulse=False).shape(zones(180, 90, 20), 0.0)
    assert a == b


def test_a_real_drop_still_steps_down():
    """Hysteresis must resist noise, not resist the obstacle actually clearing."""
    mapper = HapticMapper(pulse=False)
    steady(mapper, zones(255, 0, 0), frames=10)
    assert mapper.shape(zones(0, 0, 0), 1.0)["left"] == 0


# --- pulse: cycle hold + rate coding ---------------------------------------

def test_pulse_is_on_by_default():
    """The rate code is the device's output; the amplitude path is the fallback."""
    assert hp.PULSE_ENABLED
    assert HapticMapper().pulse


def test_pulse_constants_are_consistent():
    assert len(hp.PULSE_PERIODS_S) == hp.LEVELS
    assert hp.PULSE_PERIODS_S[0] is None
    periods = [p for p in hp.PULSE_PERIODS_S[1:]]
    assert periods == sorted(periods, reverse=True), "nearer must tap faster"
    assert periods[-1] <= hp.PULSE_ON_S, "the top level must be continuous"
    assert all(p > hp.PULSE_ON_S for p in periods[:-1]), "every other level must have a gap"
    assert hp.PULSE_PWM >= hp.PWM_FLOOR
    assert hp.PULSE_RISE_FRAMES >= 2, "a single frame must never start a tap"


def test_mismatched_periods_are_refused():
    with pytest.raises(ValueError):
        HapticMapper(levels=3)


def test_a_single_frame_cannot_start_a_tap():
    """
    The model spikes. One frame of "something near" out of silence, gone the
    next frame, must produce nothing at all — not a click, not a tap.
    """
    mapper = HapticMapper()
    readings = hold(zones(0, 0, 0), 1.0) + [zones(255, 0, 0)] + hold(zones(0, 0, 0), 2.0)
    duties, _ = run(mapper, readings)
    assert duties == [0] * len(duties)


def test_a_confirmed_onset_starts_a_tap_within_a_few_frames():
    mapper = HapticMapper()
    duties, _ = run(mapper, hold(zones(200, 0, 0), 1.0))
    first_on = duties.index(hp.PULSE_PWM)
    assert hp.PULSE_RISE_FRAMES - 1 <= first_on <= hp.PULSE_RISE_FRAMES + 1


def test_a_tap_is_a_tap_then_a_gap():
    """Level 1: on for PULSE_ON_S, then nothing until the period ends."""
    mapper = HapticMapper()
    faint = zones(int(255 * (hp.DEADBAND + 0.05)), 0, 0)
    # Onset lands a few frames in, so three full periods hold exactly three taps.
    duties, _ = run(mapper, hold(faint, hp.PULSE_PERIODS_S[1] * 3))
    assert mapper.last_levels["left"] == 1
    on = sum(1 for d in duties if d)
    expected_on_per_tap = hp.PULSE_ON_S * FPS
    assert taps(duties) == 3, taps(duties)
    assert abs(on / taps(duties) - expected_on_per_tap) <= 1.5
    assert set(duties) <= {0, hp.PULSE_PWM}, "a tap has one amplitude"


def test_nearer_taps_faster():
    """The reading IS the rate: three readings, three strictly rising tap counts."""
    counts = []
    for raw in (int(255 * (hp.DEADBAND + 0.05)), 150, 200):
        duties, _ = run(HapticMapper(), hold(zones(raw, 0, 0), 6.0))
        counts.append(taps(duties))
    assert counts == sorted(counts) and len(set(counts)) == 3, counts


def test_point_blank_is_continuous():
    """The top level has no gap: a solid buzz is the point-blank vocabulary."""
    mapper = HapticMapper()
    duties, _ = run(mapper, hold(zones(255, 0, 0), 3.0))
    assert mapper.last_levels["left"] == hp.LEVELS - 1
    assert all(d == hp.PULSE_PWM for d in duties[hp.PULSE_RISE_FRAMES + 1:])


def test_the_driven_level_does_not_move_inside_a_cycle():
    """
    The integrator. Once a cycle is running, per-frame wobble — even a whole
    level's worth, even across the level boundary every frame — changes nothing
    until the boundary. This is the property the whole stage exists for.
    """
    mapper = HapticMapper()
    mid = 150
    run(mapper, hold(zones(mid, 0, 0), 1.0))
    driven = mapper.last_levels["left"]
    assert driven > 0
    # Now wobble hard around that reading for less than one period.
    period = hp.PULSE_PERIODS_S[driven]
    t0 = 1.0
    frames = int(period * FPS) - 2
    seen = set()
    for i in range(frames):
        raw = mid + (40 if i % 2 else -40)
        mapper.shape(zones(raw, 0, 0), t0 + i * DT)
        seen.add(mapper.last_levels["left"])
    assert seen == {driven}, seen


def test_a_short_spike_does_not_survive_the_boundary():
    """A spike shorter than half a cycle loses the median vote at the boundary."""
    mapper = HapticMapper()
    faint = zones(int(255 * (hp.DEADBAND + 0.05)), 0, 0)
    period = hp.PULSE_PERIODS_S[1]
    run(mapper, hold(faint, period + 0.1))
    assert mapper.last_levels["left"] == 1
    # One-level-up spike for a third of the next cycle, then back to faint.
    # (Two levels up for PULSE_RISE_FRAMES would be a legitimate rise.)
    readings = hold(zones(120, 0, 0), period / 3) + hold(faint, period)
    run(mapper, readings, start=period + 0.2)
    assert mapper.last_levels["left"] == 1


def test_a_confirmed_rise_interrupts_the_cycle():
    """
    The safety valve. Something arriving fast must not wait out a 1.2 s cycle:
    a jump of PULSE_RISE_LEVELS held for PULSE_RISE_FRAMES starts a new cycle
    now, at the new level.
    """
    mapper = HapticMapper()
    faint = zones(int(255 * (hp.DEADBAND + 0.05)), 0, 0)
    run(mapper, hold(faint, 0.5))
    assert mapper.last_levels["left"] == 1
    # Mid-cycle (0.5 s into a 1.2 s period): slam to point-blank.
    duties, _ = run(mapper, hold(zones(255, 0, 0), 0.5), start=0.5)
    assert mapper.last_levels["left"] == hp.LEVELS - 1
    assert duties[hp.PULSE_RISE_FRAMES] == hp.PULSE_PWM
    assert all(d == hp.PULSE_PWM for d in duties[hp.PULSE_RISE_FRAMES:])


def test_a_fall_waits_for_the_boundary_then_lands():
    """
    Falling never interrupts (a gap in the model's output must not silence a
    real obstacle). The cycle a clear falls in usually earns one more tap on
    the median vote; the cycle after it is silent. So: silent within about a
    period and a half of the clear, and never a tap beyond that.
    """
    mapper = HapticMapper()
    run(mapper, hold(zones(150, 0, 0), 2.0))
    driven = mapper.last_levels["left"]
    assert driven > 0
    period = hp.PULSE_PERIODS_S[driven]
    duties, _ = run(mapper, hold(zones(0, 0, 0), period * 1.5), start=2.0)
    # Silent by the time a full period has elapsed, and it stays silent.
    assert mapper.last_levels["left"] == 0
    assert duties[-1] == 0
    tail = duties[int(period * FPS) + 1:]
    assert tail == [0] * len(tail)


def test_silence_is_never_pulsed_into_noise():
    """The pulse stage must not be able to turn a zero into a non-zero."""
    duties, _ = run(HapticMapper(), hold(zones(0, 0, 0), 5.0))
    assert duties == [0] * len(duties)


def test_pulse_can_be_disabled():
    """pulse=False is the per-frame amplitude path: continuous, level_to_pwm."""
    mapper = HapticMapper(pulse=False)
    duties, _ = run(mapper, hold(zones(255, 0, 0), 2.0))
    assert all(d == hp.PWM_MAX for d in duties)


def test_last_levels_track_the_driven_level_through_the_gap():
    """
    The HUD and the log read last_levels, not the returned duty: a frame
    sampled in the gap between taps must not look like the detector lost the
    obstacle.
    """
    mapper = HapticMapper()
    faint = zones(int(255 * (hp.DEADBAND + 0.05)), 0, 0)
    duties, _ = run(mapper, hold(faint, 1.0))
    assert 0 in duties[hp.PULSE_RISE_FRAMES + 1:], "no gap to sample"
    # Every frame after the onset reports the driven level, gap or not.
    mapper2 = HapticMapper()
    for i, values in enumerate(hold(faint, 1.0)):
        mapper2.shape(values, i * DT)
        if i > hp.PULSE_RISE_FRAMES:
            assert mapper2.last_levels["left"] == 1


def test_zones_pulse_independently():
    """
    Each zone has its own clock; a left tap says nothing about the right.
    Winner-take-most is off here so the faint zone survives to be clocked.
    """
    mapper = HapticMapper(winner=0.0)
    faint = int(255 * (hp.DEADBAND + 0.05))
    left_taps = right_taps = 0
    prev = {"left": 0, "right": 0}
    for i, values in enumerate(hold(zones(faint, 0, 200), 6.0)):
        out = mapper.shape(values, i * DT)
        for zone in ("left", "right"):
            if prev[zone] == 0 and out[zone] > 0:
                if zone == "left":
                    left_taps += 1
                else:
                    right_taps += 1
            prev[zone] = out[zone]
    assert right_taps > left_taps > 0


# --- fail-safe --------------------------------------------------------------

def test_silence_zeroes_everything():
    mapper = HapticMapper()
    steady(mapper, zones(255, 255, 0), frames=5)
    assert mapper.silence(1.0) == {z: 0 for z in ZONE_NAMES}


def test_silence_clears_state_so_the_next_frame_is_not_judged_against_it():
    """
    After a failure the wearer is feeling nothing. If the mapper still believed
    it was at level 4, the next good frame would step DOWN from a level that was
    never delivered, and a genuinely weak reading could come back strong.
    """
    mapper = HapticMapper(pulse=False)
    steady(mapper, zones(255, 0, 0), frames=5)
    mapper.silence(1.0)
    after = mapper.shape(zones(0, 0, 0), 2.0)
    assert after == {z: 0 for z in ZONE_NAMES}
    assert mapper.last_levels["left"] == 0


def test_silence_clears_the_cycle_so_the_next_onset_is_confirmed_again():
    mapper = HapticMapper()
    run(mapper, hold(zones(255, 0, 0), 1.0))
    assert mapper.last_levels["left"] > 0
    mapper.silence(1.0)
    assert mapper.last_levels["left"] == 0
    # One frame is not enough to come back, exactly as from a cold start.
    assert mapper.shape(zones(255, 0, 0), 1.05)["left"] == 0


def test_reset_returns_a_fresh_mapper():
    mapper = HapticMapper(pulse=False)
    steady(mapper, zones(255, 255, 255), frames=10)
    mapper.reset()
    fresh = HapticMapper(pulse=False)
    assert mapper.shape(zones(140, 90, 60), 0.0) == fresh.shape(zones(140, 90, 60), 0.0)


# --- winner-take-most --------------------------------------------------------

def test_winner_take_most_leaves_the_loudest_zone_alone():
    out = hp.apply_winner_take_most(zones(1.0, 0.6, 0.2), 1.0)
    assert out["left"] == 1.0


def test_winner_take_most_passes_a_uniform_frame_through():
    """A wall across the whole field is three loud zones, not none."""
    out = hp.apply_winner_take_most(zones(0.7, 0.7, 0.7), 1.0)
    assert out == zones(0.7, 0.7, 0.7)


def test_winner_take_most_never_goes_negative():
    out = hp.apply_winner_take_most(zones(1.0, 0.1, 0.0), 2.0)
    assert all(v >= 0.0 for v in out.values())


def test_a_side_obstacle_is_felt_on_that_side_only():
    """
    The field report: a person on the right at 255 next to a centre zone at
    ~200 (something farther, dead ahead) came out at the SAME level on both
    motors — the wearer felt "centre". The right motor must clearly win.
    """
    out = steady(HapticMapper(pulse=False), zones(0, 200, 255), frames=30)
    assert out["right"] == hp.PWM_MAX
    assert out["center"] < out["right"]
    assert out["left"] == 0


def test_a_trailing_zone_that_is_close_behind_still_registers():
    """Winner-take-most amplifies the difference; it must not erase near-ties."""
    out = steady(HapticMapper(pulse=False), zones(0, 240, 255), frames=30)
    assert out["center"] > 0


def test_corridor_still_fires_both_temples():
    """Two tied walls are two winners: a corridor keeps warning on both sides."""
    mapper = HapticMapper(pulse=False)
    out = mapper.shape(zones(200, 40, 200), 0.0)
    assert out["left"] == out["right"] > 0
    assert out["center"] == 0


# --- centre priority ---------------------------------------------------------

def test_person_across_centre_and_right_is_felt_on_centre_only():
    """The request: centre + right -> centre alone. The centre is the path."""
    out = steady(HapticMapper(pulse=False), zones(0, 255, 240), frames=30)
    assert out["center"] > 0
    assert out["left"] == 0 and out["right"] == 0, out


def test_person_across_centre_and_left_is_felt_on_centre_only():
    out = steady(HapticMapper(pulse=False), zones(240, 255, 0), frames=30)
    assert out["center"] > 0
    assert out["left"] == 0 and out["right"] == 0, out


def test_wall_across_all_three_zones_is_felt_on_centre_only():
    """Three motors at once is not a stronger message, it is no message."""
    out = steady(HapticMapper(pulse=False), zones(255, 255, 255), frames=30)
    # (Not PWM_MAX: lateral contrast still takes CONTRAST_FRACTION of the common
    # component off a uniform frame first — a separate, documented trade-off.)
    assert out["center"] > 0
    assert out["left"] == 0 and out["right"] == 0, out


def test_a_clearly_nearer_side_is_not_silenced_by_a_far_centre():
    """
    The safety guard on the rule above: someone at your shoulder while the
    centre reads a wall metres ahead keeps the side motor. Silencing the nearer
    hazard would be the unsafe direction.
    """
    out = steady(HapticMapper(pulse=False), zones(0, 120, 255), frames=30)
    assert out["right"] > 0, out
    assert out["right"] > out["center"], out


def test_corridor_without_a_centre_reading_is_unaffected():
    out = steady(HapticMapper(pulse=False), zones(200, 0, 200), frames=30)
    assert out["left"] == out["right"] > 0
    assert out["center"] == 0


def test_center_priority_never_raises_a_zone():
    for vals in (zones(0.0, 0.0, 0.0), zones(1.0, 0.5, 0.2), zones(0.3, 0.9, 0.3)):
        out = hp.apply_center_priority(vals)
        assert all(out[z] <= vals[z] for z in ZONE_NAMES)


def test_center_priority_can_be_disabled():
    out = steady(HapticMapper(center_priority=False, pulse=False), zones(0, 255, 255), frames=30)
    assert out["right"] > 0


def test_center_priority_does_not_flicker_on_the_margin():
    """
    Live: a surface filling the field read L=255 C=203 R=252 for one frame and
    all three motors fired for a tick. Once the centre holds priority, a side
    has to beat it by margin + hysteresis to take it back.
    """
    mapper = HapticMapper(pulse=False)
    steady(mapper, zones(255, 244, 255), frames=30)          # centre holds
    out = mapper.shape(zones(255, 203, 252), 1.0)             # a hair past the margin
    assert out["left"] == 0 and out["right"] == 0, out
    assert out["center"] > 0


def test_center_priority_is_released_when_a_side_clearly_wins():
    mapper = HapticMapper(pulse=False)
    steady(mapper, zones(255, 244, 255), frames=30)
    out = steady(mapper, zones(0, 120, 255), frames=30, start=1.0)
    assert out["right"] > 0 and out["center"] == 0, out


def test_a_point_blank_wall_is_felt_at_full_strength():
    """Live: L=255 C=244 R=255 came out at C=145. The forehead must saturate."""
    out = steady(HapticMapper(pulse=False), zones(255, 244, 255), frames=30)
    assert out["center"] == hp.PWM_MAX, out
