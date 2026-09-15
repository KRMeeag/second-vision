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
  * habituation            — a held strong reading must not stay continuously on
  * fail-safe              — failure paths produce zeros, not stale values
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
    out = steady(HapticMapper(), zones(120, 120, 120), frames=30)
    assert out["left"] == out["right"] <= hp.PWM_FLOOR


def test_uniform_corridor_does_not_buzz_continuously():
    """
    And it must not hold that level continuously: a device that hums for the
    length of every corridor in a mall is one the wearer switches off, and skin
    habituates to a gentle hum exactly as readily as to a strong one.
    """
    mapper = HapticMapper(pulse=True)
    corridor = zones(120, 0, 120)
    steady(mapper, corridor, frames=30)
    samples = [mapper.shape(corridor, hp.PULSE_AFTER_S + 0.5 + i * 0.05)["left"]
               for i in range(int(hp.PULSE_PERIOD_S / 0.05) + 1)]
    assert min(samples) < max(samples), "hums flat down the whole corridor"
    assert any(s > 0 for s in samples), "goes silent instead of pulsing"


def test_head_on_obstacle_survives_contrast():
    """
    The guard on the test above: contrast must not be so aggressive that a wall
    filling the whole field reads the same as open space. A uniformly CLOSE
    frame still has to fire.
    """
    out = HapticMapper().shape(zones(255, 255, 255), 0.0)
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
    out = HapticMapper().shape(zones(220, 60, 30), 0.0)
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


# --- habituation ------------------------------------------------------------

def test_held_strong_reading_starts_pulsing():
    mapper = HapticMapper(pulse=True)
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    # Well past PULSE_AFTER_S, sample a full pulse period.
    samples = [mapper.shape(strong, hp.PULSE_AFTER_S + 0.5 + i * 0.05)["left"]
               for i in range(int(hp.PULSE_PERIOD_S / 0.05) + 1)]
    assert min(samples) < hp.PWM_MAX, "never dips — the skin will stop feeling it"
    assert max(samples) == hp.PWM_MAX, "never comes back to full"


def test_a_held_strong_reading_dips_but_never_stops():
    """
    The field report: "255 one second, 0 the next" overwhelmed the wearer. A
    held obstacle is modulated, not chopped — above the floor level the motor
    is never driven to zero, and the dip is a small part of each period.
    """
    mapper = HapticMapper(pulse=True)
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    samples = [mapper.shape(strong, hp.PULSE_AFTER_S + i * 0.02)["left"]
               for i in range(int(hp.PULSE_PERIOD_S * 3 / 0.02))]
    assert min(samples) >= hp.PWM_FLOOR, "a held strong reading went fully silent"
    dipped = sum(1 for s in samples if s < hp.PWM_MAX) / len(samples)
    assert dipped <= 1.0 - hp.PULSE_DUTY + 0.02, f"dip occupies {dipped:.0%} of the period"


def test_modulation_only_starts_after_a_genuinely_static_hold():
    """Long enough that a person walking past never sees it start."""
    assert hp.PULSE_AFTER_S >= 5.0


def test_a_floor_level_reading_dips_to_silence_briefly():
    """Level 1 has nothing lower to dip to, so it ticks instead."""
    mapper = HapticMapper(pulse=True)
    faint = zones(int(255 * (hp.DEADBAND + 0.05)), 0, 0)
    mapper.shape(faint, 0.0)
    samples = [mapper.shape(faint, hp.PULSE_AFTER_S + i * 0.02)["left"]
               for i in range(int(hp.PULSE_PERIOD_S / 0.02))]
    assert 0 in samples and hp.PWM_FLOOR in samples


def test_pulse_holds_off_while_the_reading_is_still_changing():
    """A changing reading already re-triggers the skin; chopping it adds nothing."""
    mapper = HapticMapper(pulse=True)
    out = None
    for i in range(int((hp.PULSE_AFTER_S + 2.0) * 30)):
        raw = 200 + (i % 2) * 55           # strong, but never settling
        out = mapper.shape(zones(raw, 0, 0), i / 30.0)
    assert out["left"] > 0


def test_a_held_reading_is_continuous_until_the_pulse_delay():
    """
    Pulsing must not start immediately. The first seconds of a new reading are
    when it carries the most information, and chopping it there would read as a
    flickering detector rather than as an obstacle.
    """
    mapper = HapticMapper(pulse=True)
    strong = zones(255, 0, 0)
    first = mapper.shape(strong, 0.0)["left"]
    assert first > 0
    for i in range(int(hp.PULSE_AFTER_S * 30)):
        assert mapper.shape(strong, i / 30.0)["left"] == first


def test_silence_is_never_pulsed_into_noise():
    """The pulse gate must not be able to turn a zero into a non-zero."""
    mapper = HapticMapper(pulse=True)
    for i in range(int((hp.PULSE_AFTER_S + hp.PULSE_PERIOD_S * 2) * 30)):
        assert mapper.shape(zones(0, 0, 0), i / 30.0) == {z: 0 for z in ZONE_NAMES}


def test_pulse_can_be_disabled():
    mapper = HapticMapper(pulse=False)
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    for i in range(60):
        assert mapper.shape(strong, hp.PULSE_AFTER_S + i * 0.05)["left"] == hp.PWM_MAX


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


def test_reset_returns_a_fresh_mapper():
    mapper = HapticMapper(pulse=False)
    steady(mapper, zones(255, 255, 255), frames=10)
    mapper.reset()
    fresh = HapticMapper(pulse=False)
    assert mapper.shape(zones(140, 90, 60), 0.0) == fresh.shape(zones(140, 90, 60), 0.0)


def test_last_levels_track_the_unpulsed_intent():
    """
    The HUD reads last_levels, not the returned duty: a frame sampled during a
    pulse's off phase must not look like the detector lost the obstacle.
    """
    mapper = HapticMapper(pulse=True)
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    for i in range(int(hp.PULSE_PERIOD_S / 0.05) + 1):
        mapper.shape(strong, hp.PULSE_AFTER_S + 0.5 + i * 0.05)
        assert mapper.last_levels["left"] == hp.LEVELS - 1


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
    out = steady(HapticMapper(), zones(0, 200, 255), frames=30)
    assert out["right"] == hp.PWM_MAX
    assert out["center"] < out["right"]
    assert out["left"] == 0


def test_a_trailing_zone_that_is_close_behind_still_registers():
    """Winner-take-most amplifies the difference; it must not erase near-ties."""
    out = steady(HapticMapper(), zones(0, 240, 255), frames=30)
    assert out["center"] > 0


def test_corridor_still_fires_both_temples():
    """Two tied walls are two winners: a corridor keeps warning on both sides."""
    mapper = HapticMapper()
    out = mapper.shape(zones(200, 40, 200), 0.0)
    assert out["left"] == out["right"] > 0
    assert out["center"] == 0


# --- centre priority ---------------------------------------------------------

def test_person_across_centre_and_right_is_felt_on_centre_only():
    """The request: centre + right -> centre alone. The centre is the path."""
    out = steady(HapticMapper(), zones(0, 255, 240), frames=30)
    assert out["center"] > 0
    assert out["left"] == 0 and out["right"] == 0, out


def test_person_across_centre_and_left_is_felt_on_centre_only():
    out = steady(HapticMapper(), zones(240, 255, 0), frames=30)
    assert out["center"] > 0
    assert out["left"] == 0 and out["right"] == 0, out


def test_wall_across_all_three_zones_is_felt_on_centre_only():
    """Three motors at once is not a stronger message, it is no message."""
    out = steady(HapticMapper(), zones(255, 255, 255), frames=30)
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
    out = steady(HapticMapper(), zones(0, 120, 255), frames=30)
    assert out["right"] > 0, out
    assert out["right"] > out["center"], out


def test_corridor_without_a_centre_reading_is_unaffected():
    out = steady(HapticMapper(), zones(200, 0, 200), frames=30)
    assert out["left"] == out["right"] > 0
    assert out["center"] == 0


def test_center_priority_never_raises_a_zone():
    for vals in (zones(0.0, 0.0, 0.0), zones(1.0, 0.5, 0.2), zones(0.3, 0.9, 0.3)):
        out = hp.apply_center_priority(vals)
        assert all(out[z] <= vals[z] for z in ZONE_NAMES)


def test_center_priority_can_be_disabled():
    out = steady(HapticMapper(center_priority=False), zones(0, 255, 255), frames=30)
    assert out["right"] > 0


def test_center_priority_does_not_flicker_on_the_margin():
    """
    Live: a surface filling the field read L=255 C=203 R=252 for one frame and
    all three motors fired for a tick. Once the centre holds priority, a side
    has to beat it by margin + hysteresis to take it back.
    """
    mapper = HapticMapper()
    steady(mapper, zones(255, 244, 255), frames=30)          # centre holds
    out = mapper.shape(zones(255, 203, 252), 1.0)             # a hair past the margin
    assert out["left"] == 0 and out["right"] == 0, out
    assert out["center"] > 0


def test_center_priority_is_released_when_a_side_clearly_wins():
    mapper = HapticMapper()
    steady(mapper, zones(255, 244, 255), frames=30)
    out = steady(mapper, zones(0, 120, 255), frames=30, start=1.0)
    assert out["right"] > 0 and out["center"] == 0, out


def test_a_point_blank_wall_is_felt_at_full_strength():
    """Live: L=255 C=244 R=255 came out at C=145. The forehead must saturate."""
    out = steady(HapticMapper(), zones(255, 244, 255), frames=30)
    assert out["center"] == hp.PWM_MAX, out


def test_pulse_is_off_by_default():
    """Field evidence: testers read the modulation as a fault, twice."""
    assert not hp.PULSE_ENABLED
    mapper = HapticMapper()
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    for i in range(int(hp.PULSE_PERIOD_S * 3 / 0.05)):
        assert mapper.shape(strong, hp.PULSE_AFTER_S + i * 0.05)["left"] == hp.PWM_MAX
