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
    mapper = HapticMapper()
    corridor = zones(120, 120, 120)
    steady(mapper, corridor, frames=30)
    samples = [mapper.shape(corridor, hp.PULSE_AFTER_S + 0.5 + i * 0.05)["left"]
               for i in range(int(hp.PULSE_PERIOD_S / 0.05) + 1)]
    assert any(s == 0 for s in samples), "hums nonstop down the whole corridor"
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
    mapper = HapticMapper()
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    # Well past PULSE_AFTER_S, sample a full pulse period.
    samples = [mapper.shape(strong, hp.PULSE_AFTER_S + 0.5 + i * 0.05)["left"]
               for i in range(int(hp.PULSE_PERIOD_S / 0.05) + 1)]
    assert any(s == 0 for s in samples), "never goes off — the skin will stop feeling it"
    assert any(s > 0 for s in samples), "never comes back on"


def test_pulse_holds_off_while_the_reading_is_still_changing():
    """A changing reading already re-triggers the skin; chopping it adds nothing."""
    mapper = HapticMapper()
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
    mapper = HapticMapper()
    strong = zones(255, 0, 0)
    first = mapper.shape(strong, 0.0)["left"]
    assert first > 0
    for i in range(int(hp.PULSE_AFTER_S * 30)):
        assert mapper.shape(strong, i / 30.0)["left"] == first


def test_silence_is_never_pulsed_into_noise():
    """The pulse gate must not be able to turn a zero into a non-zero."""
    mapper = HapticMapper()
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
    mapper = HapticMapper()
    strong = zones(255, 0, 0)
    mapper.shape(strong, 0.0)
    for i in range(int(hp.PULSE_PERIOD_S / 0.05) + 1):
        mapper.shape(strong, hp.PULSE_AFTER_S + 0.5 + i * 0.05)
        assert mapper.last_levels["left"] == hp.LEVELS - 1
