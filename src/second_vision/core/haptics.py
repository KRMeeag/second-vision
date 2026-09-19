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
    6. cycle hold        — the level actually driven is decided once per pulse
                           cycle, from the levels seen during the cycle before
    7. rate coding       — a fixed strong tap, repeated faster the nearer the
                           obstacle; a solid buzz at point-blank

With PULSE_ENABLED = False, stages 6-7 are replaced by a per-frame amplitude
(level_to_pwm, floored at PWM_FLOOR) — the pre-September-2026 behaviour, kept
for tests of stages 1-5 and as the fallback if the rate code fails the wearer.

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
    minute. A rate code re-triggers the skin by construction — every tap is an
    onset — so this no longer needs a separate modulation stage; only the
    continuous top level can still fade, and at point-blank the wearer is
    acting on the warning, not standing in it.
  - The depth model is noisy frame to frame, and at 20 FPS a per-frame motor
    command puts that noise straight onto the skin. The cycle hold (stage 6) is
    the integrator: nothing is sampled at frame rate, so nothing is felt at
    frame rate.

Every threshold below is a starting value chosen from ERM datasheet behaviour
and the reasoning above, NOT from a measurement on our motors. PWM_FLOOR
especially is a property of the specific motors — measure it (ramp duty until
the motor starts, on the assembled headset) and set it. Unlike the depth
thresholds, none of this is blocked on depth calibration: the floor is about the
hardware, not about distance.

Pure functions plus HapticMapper, which holds the hysteresis and pulse state.
No hardware, no serial port — all unit-testable.
"""

from typing import Dict, List, Optional, Sequence, Tuple

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

# --- PWM floor (amplitude path, PULSE_ENABLED = False) ------------------------
# ERMs do not turn at all across the bottom of the duty range, so a "weak"
# warning sent as a small number is indistinguishable from no warning — the
# device looks broken while behaving correctly. Every non-zero level is mapped
# into [PWM_FLOOR, PWM_MAX] instead, so the quietest thing the wearer can be
# told is still something they can feel.
# PLACEHOLDER — roughly a third of range is typical for coin ERMs. MEASURE IT.
PWM_FLOOR = 90
PWM_MAX = 255

# --- 6. cycle hold + 7. rate coding -----------------------------------------
# The motor is NOT re-commanded every frame. Each zone runs a pulse clock, and
# the level actually driven is decided once per cycle, from the levels the
# quantizer produced during the cycle just ended — the upper median, i.e. the
# level met or exceeded at least half the time, ties toward nearer. Between
# boundaries the command does not change. This is where the depth model's
# frame-to-frame wobble stops: the EMA, deadband and hysteresis upstream all
# thin it, but at 20 FPS whatever got past them reached the skin within 50 ms,
# and the wearer felt the model's noise directly ("overwhelmed with the
# changes", Sept 2026). A cycle is the unit of decision because it is also the
# unit of perception — nobody feels a change inside a tap.
#
# WHAT A PULSE MEANS. Rate-coded, like a parking sensor: a fixed strong tap
# whose repetition rate rises with proximity, and a solid buzz at point-blank.
# Chosen over amplitude levels (user decision, 2026-09-15) because rhythm is
# discriminated on skin far more reliably than amplitude: the four duties of
# level_to_pwm have never been shown to be tellable apart on these motors, and
# PWM_FLOOR is still unmeasured. A rate code depends on neither. It also
# retires the anti-habituation dip that lived here — a tap re-triggers the
# skin by construction.
#
# Two earlier pulse designs were rejected by testers as "255 one second, 0 the
# next". Both MODULATED a held amplitude on top of the reading; here the pulse
# IS the reading, and its rate is the information. Same skin, though — this
# must be wearer-tested before it is called good, and PULSE_ENABLED = False
# is the way back.
PULSE_ENABLED = True
# Cycle length per level, seconds; index = level, entry 0 unused (silent). The
# ON time is fixed (below), so a shorter period is a faster tap. A period no
# longer than PULSE_ON_S is continuous — that is the top level, point-blank.
# The far levels, where the model is noisiest, are also re-evaluated least
# often, which is the right way round. PLACEHOLDER — reasoned from ERM spin-up
# and parking-sensor practice, not measured. len() must equal LEVELS.
PULSE_PERIODS_S: Tuple[Optional[float], ...] = (None, 1.2, 0.7, 0.4, 0.18)
# How long each tap drives the motor. Coin ERMs need ~50 ms to spin up and
# about as long to stop, so anything under ~120 ms is a click rather than a
# buzz; 180 ms is a clear tap and 3-4 frames at 20 FPS. PLACEHOLDER.
PULSE_ON_S = 0.18
# Duty of every tap. One value: with a rate code the amplitude carries no
# information, so it is simply "clearly felt". Must be >= PWM_FLOOR.
PULSE_PWM = PWM_MAX
# The one thing that cuts a cycle short: something arriving FAST. A quantized
# level at least PULSE_RISE_LEVELS above the driven level — or any level at
# all while the zone is silent — seen on PULSE_RISE_FRAMES consecutive frames
# starts a new cycle at once instead of waiting for the boundary. Three frames
# is 150 ms at 20 FPS: under the motor's own spin-up plus the wearer's reaction,
# and enough that a single-frame spike from the model cannot fire a tap. A
# reading that flickers at the deadband for three frames still buys one tap;
# the next boundary then judges it by its median and drops it. That single
# tap is the accepted cost — raise PULSE_RISE_FRAMES if it is felt too often.
# Falling never interrupts. An obstacle that clears is judged at the next
# boundary by the median of a cycle it was mostly present in, so it usually
# earns ONE more tap; simulated at every phase, the last drive lands within
# 0.75 s of the clear at level 1 and within 0.25 s at level 4. A stale tap is
# the conservative direction; a dropped real obstacle is not.
PULSE_RISE_LEVELS = 2
PULSE_RISE_FRAMES = 3

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


def upper_median(levels: Sequence[int]) -> int:
    """
    The level met or exceeded at least half the time — the cycle's verdict.
    Ties go nearer (for four samples 0,0,4,4 the answer is 4), because the
    unsafe direction is under-reporting. Empty input is silence.
    """
    if not levels:
        return 0
    ordered = sorted(levels)
    return ordered[len(ordered) // 2]


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
                 center_priority: bool = CENTER_PRIORITY,
                 periods: Sequence[Optional[float]] = PULSE_PERIODS_S,
                 on_s: float = PULSE_ON_S, pulse_pwm: int = PULSE_PWM,
                 rise_levels: int = PULSE_RISE_LEVELS,
                 rise_frames: int = PULSE_RISE_FRAMES):
        if pulse and len(periods) != levels:
            raise ValueError(f"need one pulse period per level: {len(periods)} != {levels}")
        self.deadband = deadband
        self.levels = levels
        self.contrast = contrast
        self.winner = winner
        self.center_priority = center_priority
        self._center_holding = False
        self.pwm_floor = pwm_floor
        self.pulse = pulse
        self.periods = tuple(periods)
        self.on_s = on_s
        self.pulse_pwm = pulse_pwm
        self.rise_levels = rise_levels
        self.rise_frames = rise_frames
        # Stage 5: the quantizer's per-frame level, with its hysteresis.
        self._level: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}
        # Stages 6-7: the level a zone is being DRIVEN at this cycle, when the
        # cycle began, the per-frame levels seen since, and how many frames in
        # a row have qualified as a rise interrupt.
        self._driven: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}
        self._cycle_start: Dict[str, float] = {zone: 0.0 for zone in ZONE_NAMES}
        self._cycle_levels: Dict[str, List[int]] = {zone: [] for zone in ZONE_NAMES}
        self._rise_run: Dict[str, int] = {zone: 0 for zone in ZONE_NAMES}
        # What the motors were asked for, kept for the HUD and the log: the
        # driven level (pulse on) or the quantized level (pulse off). A frame
        # sampled in the gap between taps must not read as "the detector lost
        # the obstacle" — the returned duty is 0 there, this is not.
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

    def _start_cycle(self, zone: str, level: int, now: float) -> None:
        self._driven[zone] = level
        self._cycle_start[zone] = now
        self._cycle_levels[zone] = []
        self._rise_run[zone] = 0

    def _cycle_len(self, level: int) -> float:
        """
        How long a cycle at `level` runs before its verdict. A level whose
        period is no longer than the tap is continuous: it is still
        re-judged, every `on_s`, but the motor never stops between verdicts.
        """
        period = self.periods[level]
        return self.on_s if period is None else max(period, self.on_s)

    def _tap(self, zone: str, now: float) -> int:
        """
        Stage 7: the duty this instant for the zone's driven level — the tap's
        duty for the first `on_s` of the cycle, 0 for the rest. A continuous
        level's cycle IS `on_s` long, so it never reaches the gap. Never
        non-zero for a silent zone.
        """
        driven = self._driven[zone]
        if driven <= 0:
            return 0
        return self.pulse_pwm if now - self._cycle_start[zone] < self.on_s else 0

    def _pulse(self, zone: str, level: int, now: float) -> int:
        """
        Stage 6: fold this frame's quantized `level` into the zone's cycle and
        decide whether the cycle ends here — at its boundary, on the verdict of
        the frames it contained, or early, on a confirmed rise. Then stage 7.

        A silent zone has no cycle: it accumulates nothing and waits for
        PULSE_RISE_FRAMES consecutive non-zero frames, so silence is left by a
        confirmed onset, never by one frame.
        """
        driven = self._driven[zone]
        if driven == 0:
            self._rise_run[zone] = self._rise_run[zone] + 1 if level >= 1 else 0
            if self._rise_run[zone] >= self.rise_frames:
                self._start_cycle(zone, level, now)
            return self._tap(zone, now)

        self._cycle_levels[zone].append(level)
        if level >= driven + self.rise_levels:
            self._rise_run[zone] += 1
        else:
            self._rise_run[zone] = 0

        if self._rise_run[zone] >= self.rise_frames:
            self._start_cycle(zone, level, now)
        elif now - self._cycle_start[zone] >= self._cycle_len(driven):
            self._start_cycle(zone, upper_median(self._cycle_levels[zone]), now)
        return self._tap(zone, now)

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
            self._level[zone] = level

            if self.pulse:
                out[zone] = self._pulse(zone, level, now)
                self.last_levels[zone] = self._driven[zone]
            else:
                out[zone] = level_to_pwm(level, self.levels, self.pwm_floor)
                self.last_levels[zone] = level
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
            self._level[zone] = 0
            self._start_cycle(zone, 0, now)
            self.last_levels[zone] = 0
        return dict(SILENT)

    def reset(self) -> None:
        """Drop all state — call on pipeline rebuild or mode switch."""
        self._center_holding = False
        self._level = {zone: 0 for zone in ZONE_NAMES}
        self._driven = {zone: 0 for zone in ZONE_NAMES}
        self._cycle_start = {zone: 0.0 for zone in ZONE_NAMES}
        self._cycle_levels = {zone: [] for zone in ZONE_NAMES}
        self._rise_run = {zone: 0 for zone in ZONE_NAMES}
        self.last_levels = {zone: 0 for zone in ZONE_NAMES}
