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
    2. centre priority   — a centre reading as near as the sides silences them
    3. winner-take-most  — zones trailing the loudest one are pushed down
    4. deadband          — anything below "worth noticing" becomes exactly 0
    5. quantize          — ~5 levels, with hysteresis so they don't chatter
    6. PWM floor         — a non-zero level must actually spin the motor
    7. pulse when static — an unchanging reading dips briefly now and then

Contrast runs BEFORE the deadband on purpose. Walking a normal corridor lights
both temples at a similar level all the way down it; contrast collapses that
common component and the deadband then takes it to silence. Run in the other
order, the corridor survives the deadband and the wearer feels a two-minute
buzz on both temples — the failure mode FIELD-TESTING.md's environments
(schools, malls, streets) would hit within seconds of walking in.

Winner-take-most exists because contrast alone answers the wrong question. It
removes what all three zones share, but says nothing about the zones that are
merely LOWER than the loudest: a person on the right at 255 next to a centre
zone reading 200 from something farther away came out, after contrast and the
quantizer, at the same level on both motors — the wearer felt "centre" (field
report). The direction a three-motor headband can convey is which zone WINS;
that difference has to be amplified, not preserved.

Use-case assumptions baked in here (PROJECT.md):
  - 3 ERM motors: left temple, forehead, right temple. Coin ERMs do not spin at
    low duty and have a ~50 ms spin-up, so anything they are asked to do must be
    slow and coarse compared to the 30 FPS depth stream.
  - This channel is the wearer's obstacle sense at body and head height; there
    is NO white cane (PROJECT.md, corrected Sept 2026) and no ground-hazard
    detection (DECISIONS D36). Silence is still a valid and common output —
    nothing near means nothing felt — but it must never be silence by
    accident.
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
# Now 0.0 — retired, on live evidence. Contrast can only act when ALL three
# zones are non-zero (a clear centre makes the minimum 0, so the corridor case
# it was written for was never touched by it). The one case it did act on is a
# surface filling the whole field, and there it took a point-blank object down
# to 145/255 on the forehead (live: L=255 C=244 R=255 -> motor C=145). With
# centre priority and winner-take-most doing the directional work, all this
# stage could do was weaken the winner. The function stays for the tests and in
# case a measured reason for it appears.
CONTRAST_FRACTION = 0.0

# --- 2. centre priority ------------------------------------------------------
# When the centre zone is active AND at least as near as the sides, only the
# centre motor runs. The centre is the collision path (DECISIONS D7; the TTS
# priority weights it 1.0 against 0.5 for the sides for the same reason). A
# person straddling centre-and-right, or a wall filling all three zones, lights
# two or three motors at once — and three motors on a head buzzing together do
# not say "obstacle ahead" more strongly, they say nothing at all, which is the
# "overwhelmed" report from the field. One motor, the one on the path, is the
# whole message.
#
# The nearness condition is the safety guard: a side that is CLEARLY nearer than
# the centre — someone at your shoulder while the centre reads a wall metres
# ahead — keeps its motor, because silencing the nearer hazard is the unsafe
# direction. "Clearly" means more than CENTER_PRIORITY_MARGIN (0-1 scale,
# roughly one quantizer level) above the centre. Set CENTER_PRIORITY False to
# disable.
CENTER_PRIORITY = True
CENTER_PRIORITY_MARGIN = 0.2
# Once the centre holds priority, a side must exceed the centre by MARGIN plus
# this before it wins it back. Live: a surface filling the field read
# L=255 C=203 R=252 for one frame — the centre a hair past the margin — and all
# three motors fired for a tick before it snapped back to centre-only. A
# decision worth two motors switching on must not sit on a knife edge.
CENTER_PRIORITY_HYSTERESIS = 0.15

# --- 3. winner-take-most -----------------------------------------------------
# Each zone is pushed down by this multiple of its distance below the LOUDEST
# zone. At 1.0 a zone reading 60% of the winner is silenced entirely
# (0.6 - 1.0 * 0.4 = 0.2, then the deadband), one at 80% keeps a faint level,
# and the winner itself is untouched — so a wall closing in across the whole
# field (all zones equal) still fires all three at full, and a corridor (both
# temples equal, centre quiet) still fires both temples. Only the zones that
# are BOTH quieter AND not tied lose. PLACEHOLDER — 1.0 is reasoned, not
# measured; the honest test is whether a person on one side is felt on that
# side alone. Set to 0.0 to disable.
WINNER_SUPPRESSION = 1.0

# --- 4. deadband -----------------------------------------------------------
# Below this (0-1 scale, after contrast) the output is exactly 0. This is the
# single most important rule in the file: it is what makes "nothing worth
# reporting" the device's resting state instead of a permanent low hum.
DEADBAND = 0.18

# --- 5. quantize -----------------------------------------------------------
# Nobody discriminates 256 vibration levels on a temple; a handful is the real
# resolution of the channel. Quantizing also kills micro-flicker for free —
# a value wobbling by a couple of counts stops moving the motor at all.
LEVELS = 5                   # level 0 = silent, 1..4 = felt intensities
# A level must lose this much (0-1 scale) before it steps back DOWN. Without it
# a value sitting exactly on a boundary alternates between two levels every
# frame, which is felt as a rattle rather than as a steady reading.
LEVEL_HYSTERESIS = 0.06

# --- 6. PWM floor ----------------------------------------------------------
# ERMs do not turn at all across the bottom of the duty range, so a "weak"
# warning sent as a small number is indistinguishable from no warning — the
# device looks broken while behaving correctly. Every non-zero level is mapped
# into [PWM_FLOOR, PWM_MAX] instead, so the quietest thing the wearer can be
# told is still something they can feel.
# PLACEHOLDER — roughly a third of range is typical for coin ERMs. MEASURE IT.
PWM_FLOOR = 90
PWM_MAX = 255

# --- 7. pulse when static --------------------------------------------------
# A zone held at the same level for this long starts being modulated instead of
# running flat. Standing at a bus stop facing a wall should not fade to nothing;
# re-triggering the mechanoreceptors is what keeps it perceptible.
#
# This used to be a hard on/off square wave — 0.54 s at full, 0.36 s at ZERO,
# from 3 s onward, at every level. Field report: "255 one second, 0 the next",
# and it overwhelmed the wearer. A ~1 Hz full-off chop is the haptic vocabulary
# for an ALARM, and it was being applied to every obstacle that simply stayed
# where it was — a person standing in front of you, a wall you are walking
# along. It is now a short DIP every couple of seconds, to a still-felt duty
# rather than to nothing, and only after the reading has been genuinely static.
# The re-triggering that defeats habituation comes from the CHANGE in amplitude,
# not from silence — a dip does that job without reading as an emergency.
# PLACEHOLDER — reasoned, not measured; the honest test is that a wall stood in
# front of for a minute is still felt at the end of it, without being alarming.
# OFF by default, on field evidence. Two rounds of testers described the
# modulation as a fault ("255 one second, 0 the next"; then, after it was
# softened to a dip, "255 then 90 all of a sudden"). The habituation concern
# it addresses is real in the literature but has not been observed on this
# device, while the modulation's cost has been — every time. Re-enable
# (PULSE_ENABLED = True) only if a tester facing a wall for a minute reports
# the vibration fading from awareness.
PULSE_ENABLED = False
PULSE_AFTER_S = 8.0
PULSE_PERIOD_S = 2.0         # one full-duty + dip cycle
PULSE_DUTY = 0.85            # fraction of the period at the level's own duty
# Duty during the dip. PWM_FLOOR (the weakest thing the motor can express) means
# the vibration never actually STOPS at levels above the floor; a level already
# sitting on the floor has nowhere lower to go and dips to 0 — a 0.3 s gap every
# 2 s, which reads as a soft tick, not a chop. Set to 0 to restore the full
# on/off pulse.
PULSE_DIP_PWM = PWM_FLOOR
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


def center_has_priority(values: Dict[str, float], holding: bool,
                        margin: float = CENTER_PRIORITY_MARGIN,
                        hysteresis: float = CENTER_PRIORITY_HYSTERESIS) -> bool:
    """
    Should the centre zone own the output this frame? Yes when it is active and
    no side is more than `margin` above it — or, if it already held priority
    (`holding`), more than `margin + hysteresis` above it.
    """
    center = values["center"]
    if center <= 0.0:
        return False
    allowance = margin + (hysteresis if holding else 0.0)
    return max(values["left"], values["right"]) <= center + allowance


def apply_center_priority(values: Dict[str, float], holding: bool = False,
                          margin: float = CENTER_PRIORITY_MARGIN) -> Dict[str, float]:
    """
    If the centre has priority (see center_has_priority), silence both sides
    and return the centre alone. Otherwise return the values unchanged. Never
    raises any zone.
    """
    if not center_has_priority(values, holding, margin):
        return dict(values)
    return {"left": 0.0, "center": values["center"], "right": 0.0}


def apply_winner_take_most(values: Dict[str, float],
                           suppression: float = WINNER_SUPPRESSION) -> Dict[str, float]:
    """
    Push every zone down by `suppression` x its shortfall from the loudest
    zone. The loudest zone (and any zone tied with it) is returned unchanged;
    a uniformly loud frame passes through intact. Never returns a negative
    value.
    """
    peak = max(values[zone] for zone in ZONE_NAMES)
    return {zone: max(0.0, values[zone] - suppression * (peak - values[zone]))
            for zone in ZONE_NAMES}


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
                 pulse: bool = PULSE_ENABLED, winner: float = WINNER_SUPPRESSION,
                 center_priority: bool = CENTER_PRIORITY):
        self.deadband = deadband
        self.levels = levels
        self.contrast = contrast
        self.winner = winner
        self.center_priority = center_priority
        self._center_holding = False
        self.pwm_floor = pwm_floor
        self.pulse = pulse
        self._level: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}
        self._level_since: Dict[str, float] = {zone: 0.0 for zone in ZONE_NAMES}
        # Pre-shaping levels, kept for the HUD: what the motors were asked for
        # before the pulse modulation dipped it, so a frame sampled in a dip
        # doesn't read as "the detector lost the obstacle".
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

    def _pulsed_duty(self, zone: str, level: int, pwm: int, now: float) -> int:
        """
        The duty to drive this instant, given the level's own duty `pwm`. A zone
        that has held the same level longer than PULSE_AFTER_S is modulated: for
        most of each PULSE_PERIOD_S it runs at `pwm`, then dips briefly to
        PULSE_DIP_PWM (or to 0 if it is already at the floor). Anything still
        changing is left alone, because a changing reading is already
        re-triggering the skin on its own. Never raises a duty, and never turns
        a 0 into anything else.
        """
        if pwm <= 0 or not self.pulse or level < PULSE_MIN_LEVEL:
            return pwm
        held = now - self._level_since[zone]
        if held < PULSE_AFTER_S:
            return pwm
        phase = (held - PULSE_AFTER_S) % PULSE_PERIOD_S
        if phase < PULSE_PERIOD_S * PULSE_DUTY:
            return pwm
        return PULSE_DIP_PWM if pwm > PULSE_DIP_PWM else 0

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
        if self.center_priority:
            self._center_holding = center_has_priority(contrasted, self._center_holding)
            if self._center_holding:
                contrasted = {"left": 0.0, "center": contrasted["center"], "right": 0.0}
        contrasted = apply_winner_take_most(contrasted, self.winner)

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
            out[zone] = self._pulsed_duty(
                zone, level, level_to_pwm(level, self.levels, self.pwm_floor), now)
        return out

    def silence(self, now: float) -> Dict[str, int]:
        """
        All-zero output, with the state wound back to match.

        For the callback's failure paths. Returning zeros WITHOUT clearing the
        state would leave the hysteresis holding a level the wearer is no longer
        being driven at, so the next good frame could step down from a level
        that was never actually felt.
        """
        self._center_holding = False
        for zone in ZONE_NAMES:
            if self._level[zone] != 0:
                self._level[zone] = 0
                self._level_since[zone] = now
            self.last_levels[zone] = 0
        return dict(SILENT)

    def reset(self) -> None:
        """Drop all state — call on pipeline rebuild or mode switch."""
        self._center_holding = False
        self._level = {zone: 0 for zone in ZONE_NAMES}
        self._level_since = {zone: 0.0 for zone in ZONE_NAMES}
        self.last_levels = {zone: 0 for zone in ZONE_NAMES}
