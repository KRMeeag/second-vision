"""
Haptics — turns per-zone depth warnings into what the three ERM motors actually do.

This is the LAST stage before the wire. depth_utils.py answers "how near is
something in this zone"; this file answers "what should the wearer feel". Those
are different questions, and the gap between them is where a technically-correct
device becomes an unwearable one.

    depth callback -> DepthPostProcessor (perception, 0-255)
                   -> HapticMapper       (this file: perception -> PWM duty)
                   -> serial_queue -> serial_worker (transport only)

Shaping order, and why it is this order:

    1. lateral contrast  — subtract part of the quietest zone from all three
    2. deadband          — anything below "worth noticing" becomes exactly 0
    3. quantize          — ~5 levels, with hysteresis so they don't chatter
    4. PWM floor         — a non-zero level must actually spin the motor
    5. pulse when static — an unchanging strong reading goes intermittent

Contrast runs BEFORE the deadband on purpose. Walking a normal corridor lights
both temples at a similar level all the way down it; contrast collapses that
common component and the deadband then takes it to silence. Run in the other
order, the corridor survives the deadband and the wearer feels a two-minute
buzz on both temples — the failure mode FIELD-TESTING.md's environments
(schools, malls, streets) would hit within seconds of walking in.

Use-case assumptions baked in here (PROJECT.md):
  - 3 ERM motors: left temple, forehead, right temple. Coin ERMs do not spin at
    low duty and have a ~50 ms spin-up, so anything they are asked to do must be
    slow and coarse compared to the 30 FPS depth stream.
  - The wearer KEEPS their white cane. This channel covers body and head height,
    which the cane cannot reach. It is not the primary ground sense, so it can
    afford to stay quiet; silence is a valid and common output.
  - Skin habituates. A continuous vibration stops being felt within about a
    minute, so a constant-level warning must be modulated or it decays to
    nothing exactly when the wearer has been near an obstacle the longest.

Every threshold below is a starting value chosen from ERM datasheet behaviour
and the reasoning above, NOT from a measurement on our motors. PWM_FLOOR
especially is a property of the specific motors — measure it (ramp duty until
the motor starts, on the assembled headset) and set it. Unlike the depth
thresholds, none of this is blocked on depth calibration: the floor is about the
hardware, not about distance.

Pure functions plus HapticMapper, which holds the hysteresis and pulse state.
No hardware, no serial port — all unit-testable.
"""

from typing import Dict

ZONE_NAMES = ("left", "center", "right")

# --- 1. lateral contrast ---------------------------------------------------
# Fraction of the QUIETEST zone subtracted from all three. With only three
# motors on a head, the question the wearer is actually asking is "which way is
# clear", not "how far is the left wall" — and a reading common to all three
# zones carries no directional information at all.
# Deliberately not 1.0: at 1.0 a wall closing in dead ahead across the whole
# field reads identically to open space, because subtracting the minimum zeroes
# a uniform frame. Keeping half means absolute urgency still gets through while
# the directional contrast is what dominates. PLACEHOLDER — 0.5 is reasoned,
# not measured; the honest test is whether a corridor goes quiet while a
# head-on wall does not.
CONTRAST_FRACTION = 0.5

# --- 2. deadband -----------------------------------------------------------
# Below this (0-1 scale, after contrast) the output is exactly 0. This is the
# single most important rule in the file: it is what makes "nothing worth
# reporting" the device's resting state instead of a permanent low hum.
DEADBAND = 0.18

# --- 3. quantize -----------------------------------------------------------
# Nobody discriminates 256 vibration levels on a temple; a handful is the real
# resolution of the channel. Quantizing also kills micro-flicker for free —
# a value wobbling by a couple of counts stops moving the motor at all.
LEVELS = 5                   # level 0 = silent, 1..4 = felt intensities
# A level must lose this much (0-1 scale) before it steps back DOWN. Without it
# a value sitting exactly on a boundary alternates between two levels every
# frame, which is felt as a rattle rather than as a steady reading.
LEVEL_HYSTERESIS = 0.06

# --- 4. PWM floor ----------------------------------------------------------
# ERMs do not turn at all across the bottom of the duty range, so a "weak"
# warning sent as a small number is indistinguishable from no warning — the
# device looks broken while behaving correctly. Every non-zero level is mapped
# into [PWM_FLOOR, PWM_MAX] instead, so the quietest thing the wearer can be
# told is still something they can feel.
# PLACEHOLDER — roughly a third of range is typical for coin ERMs. MEASURE IT.
PWM_FLOOR = 90
PWM_MAX = 255

# --- 5. pulse when static --------------------------------------------------
# A zone held at the same level for this long starts pulsing instead of running
# continuously. Standing at a bus stop facing a wall should not fade to nothing;
# re-triggering the mechanoreceptors is what keeps it perceptible.
PULSE_AFTER_S = 3.0
PULSE_PERIOD_S = 0.9         # one on+off cycle
PULSE_DUTY = 0.6             # fraction of the period the motor is ON
# Lowest level that pulses once held. This is 1 — i.e. everything — because
# habituation is not a function of amplitude: a gentle hum held against the
# temples fades from awareness just as completely as a strong one, and the
# low-level case is the COMMON one (a corridor puts a moderate reading on both
# temples for its entire length). An intermittent gentle tap for a corridor is
# information; two minutes of continuous hum is what gets a device switched off.
# Raise this only if field testing shows the pulsing itself is the irritant.
PULSE_MIN_LEVEL = 1

SILENT: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}


def apply_lateral_contrast(values: Dict[str, float],
                           fraction: float = CONTRAST_FRACTION) -> Dict[str, float]:
    """
    Subtract `fraction` of the quietest zone from all three, so what survives is
    mostly the DIFFERENCE between zones. Never returns a negative value.
    """
    common = min(values[zone] for zone in ZONE_NAMES) * fraction
    return {zone: max(0.0, values[zone] - common) for zone in ZONE_NAMES}


def level_to_pwm(level: int, levels: int = LEVELS,
                 floor: int = PWM_FLOOR, ceiling: int = PWM_MAX) -> int:
    """
    Map a quantized level onto the duty range the motors can actually express.
    Level 0 is silence (a true 0, not a small duty); 1 lands exactly on `floor`
    and the top level on `ceiling`.
    """
    if level <= 0:
        return 0
    top = levels - 1
    if top <= 1:
        return ceiling
    span = ceiling - floor
    return int(round(floor + span * (level - 1) / (top - 1)))


class HapticMapper:
    """
    Stateful per-zone shaping: perception in (0-255), motor duty out (0-255).

    Holds the hysteresis level and the pulse clock for each zone, so it must be
    a single long-lived instance per pipeline — and must be reset() whenever the
    stream restarts, or the first frame after a rebuild is judged against levels
    from a scene that is no longer in front of the wearer.

    `now` is injected rather than read from time.time() inside, so the pulse
    behaviour is testable without sleeping.
    """

    def __init__(self, deadband: float = DEADBAND, levels: int = LEVELS,
                 contrast: float = CONTRAST_FRACTION, pwm_floor: int = PWM_FLOOR,
                 pulse: bool = True):
        self.deadband = deadband
        self.levels = levels
        self.contrast = contrast
        self.pwm_floor = pwm_floor
        self.pulse = pulse
        self._level: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}
        self._level_since: Dict[str, float] = {zone: 0.0 for zone in ZONE_NAMES}
        # Pre-shaping levels, kept for the HUD: what the motors were asked for
        # before the pulse gate chopped it, so a dark frame in a pulse's off
        # phase doesn't read as "the detector lost the obstacle".
        self.last_levels: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}

    def _quantize(self, zone: str, value: float) -> int:
        """
        Value (0-1, post-deadband) -> level, with downward hysteresis.

        Rising is immediate: a real approach must not be delayed by smoothing
        that has already happened upstream. Falling has to clear an extra
        margin, which is what stops a value parked on a boundary from rattling.
        """
        if value <= 0.0:
            return 0
        top = self.levels - 1
        # Map the usable band (deadband..1.0) across levels 1..top, so the very
        # first thing above the deadband is a felt level 1 rather than a 0.
        span = max(1e-6, 1.0 - self.deadband)
        raw = 1 + int((value - self.deadband) / span * top)
        raw = max(1, min(top, raw))

        current = self._level[zone]
        if raw >= current:
            return raw
        # Stepping down: only if it has fallen clear of the boundary it came
        # from by LEVEL_HYSTERESIS, otherwise hold the level we are on.
        boundary = self.deadband + span * (current - 1) / top
        if value < boundary - LEVEL_HYSTERESIS:
            return raw
        return current

    def _pulse_gate(self, zone: str, level: int, now: float) -> bool:
        """
        True if the motor should be ON this instant. A zone that has held the
        same strong level longer than PULSE_AFTER_S switches to an intermittent
        pattern; anything still changing is left alone, because a changing
        reading is already re-triggering the skin on its own.
        """
        if not self.pulse or level < PULSE_MIN_LEVEL:
            return True
        held = now - self._level_since[zone]
        if held < PULSE_AFTER_S:
            return True
        phase = (held - PULSE_AFTER_S) % PULSE_PERIOD_S
        return phase < PULSE_PERIOD_S * PULSE_DUTY

    def shape(self, intensities: Dict[str, int], now: float) -> Dict[str, int]:
        """
        One frame of perception -> one frame of motor duty.

        Input is the 0-255 per-zone dict DepthPostProcessor.process() returns.
        Output is the same shape, but the numbers now mean PWM duty the ESP32
        applies directly: 0 means "hold still", and any non-zero value is
        guaranteed to be above the motors' start threshold.
        """
        norm = {zone: max(0.0, min(1.0, intensities[zone] / 255.0)) for zone in ZONE_NAMES}
        contrasted = apply_lateral_contrast(norm, self.contrast)

        out: Dict[str, int] = {}
        for zone in ZONE_NAMES:
            value = contrasted[zone]
            if value < self.deadband:
                value = 0.0

            level = self._quantize(zone, value)
            if level != self._level[zone]:
                self._level[zone] = level
                self._level_since[zone] = now

            self.last_levels[zone] = level
            out[zone] = level_to_pwm(level, self.levels, self.pwm_floor) \
                if self._pulse_gate(zone, level, now) else 0
        return out

    def silence(self, now: float) -> Dict[str, int]:
        """
        All-zero output, with the state wound back to match.

        For the callback's failure paths. Returning zeros WITHOUT clearing the
        state would leave the hysteresis holding a level the wearer is no longer
        being driven at, so the next good frame could step down from a level
        that was never actually felt.
        """
        for zone in ZONE_NAMES:
            if self._level[zone] != 0:
                self._level[zone] = 0
                self._level_since[zone] = now
            self.last_levels[zone] = 0
        return dict(SILENT)

    def reset(self) -> None:
        """Drop all state — call on pipeline rebuild or mode switch."""
        self._level = {zone: 0 for zone in ZONE_NAMES}
        self._level_since = {zone: 0.0 for zone in ZONE_NAMES}
        self.last_levels = {zone: 0 for zone in ZONE_NAMES}
