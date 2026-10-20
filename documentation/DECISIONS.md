# DECISIONS.md — Architectural Decision Log

> **Last synced: 2026-07-27.**
> D1–D29 come from the original architecture planning phase (v1–v8). D30+ were made during
> implementation and are promoted here from the `.agents/` handoffs.
>
> **Decisions are never silently rewritten.** When one is overtaken, it is marked
> *superseded* or *amended* and left in place, so the reasoning trail survives.

---

## Decision Index

| # | Decision | Choice | Rationale Summary |
|---|---|---|---|
| 1 | Consumer threading model | `threading.Thread` + `queue.Queue` | I/O-bound consumers; GIL irrelevant for serial/TTS |
| 2 | TTS engine | pyttsx3 / espeak-ng | < 1% CPU, < 50ms latency; utterances too short for neural TTS quality to matter |
| 3 | Serial protocol (ESP32) | Binary: `0xAA` + type + payload + XOR | Deterministic parsing on MCU, no string allocation |
| 4 | Baud rate | 115,200 | < 1% wire capacity used; universally reliable |
| 5 | ACK strategy | Non-blocking, 10ms timeout | Stale data worse than missing data in obstacle avoidance |
| 6 | Display mode | `--headless` flag → `fakesink` | Callbacks still fire regardless of sink; saves CPU/power |
| 7 | Depth zone split | 25% / 50% / 25% (L/C/R) | Center zone wider — forward obstacles matter most |
| 8 | Motor controller | ESP32 via wired USB serial | Reliable, no BLE latency, supports high baud rates |
| 9 | Motor wiring | Parallel + individual MOSFETs | Series wiring prevents independent motor control |
| 10 | ~~Cooldown strategy~~ | ~~Per `"{label}-{zone}"` key, 3s default~~ | **SUPERSEDED by D30–D34** — replaced by layered suppression |
| 11 | TTS scope | Detection only — no depth in speech | Prevents info overload; motors handle depth |
| 12 | Motor mode | Proportional (all motors active) | Single-motor mode has dangerous corridor failure mode |
| 13 | TTS phrasing | Short: `"person left"` | 0.6s per utterance; leaves silence between cooldowns |
| 14 | ESP32 firmware | Teammate-owned; protocol spec provided | Independent development against the binary protocol spec |
| 15 | Form factor | Head-mounted | Camera, motors, earpiece all on headband |
| 16 | Target user | Visually impaired, **no white cane** | Earlier drafts assumed a cane fallback; corrected Sept 2026 — see D36 |
| 17 | ~~Downward hazards~~ | ~~Software ground-plane departure detection~~ | **SUPERSEDED by D36** — disabled; not reliable enough to trust, and there is no cane fallback |
| 18 | Control panel protocol | Text: `"S:key:value\n"` | Human-speed input; debuggable with serial monitor |
| 19 | Config sharing | `SystemConfig` with `threading.Lock` | Workers poll on each loop iteration (< 1s latency) |
| 20 | Pipeline switching | Dynamic rebuild via `_rebuild_pipeline()` | **Reaffirmed 2026-07-27** — being built now alongside the control panel |
| 21 | Rebuild blackout | ~0.8-1.2s, masked by TTS announcement | ESP32 watchdog holds last motor values during gap |
| 22 | Thread count | 6 core + 2 optional, all daemon | Single `shutdown_event` for clean exit |
| 23 | Project structure | `src/second_vision/` imports `hailo_apps` as library | Never modify library; extend by subclassing |
| 24 | Entry point | `main.py` starts workers, then `app.run()` (blocking) | Workers must start BEFORE the blocking GLib loop |
| 25 | Dev approach | Walking skeleton with stubs | Full system runs end-to-end from day 1 |
| 26 | Mock mode | `--mock` flag with fake data generators | Enables development without RPi5/Hailo/camera |
| 27 | Stub pattern | Private `_functions` under `# STUBS BELOW` | Replace internals, never change the interface |
| 28 | Interface contract | Queue dict keys are the API | **AMENDED by D30** — keys and queue type both changed |
| 29 | Directly implemented | `SystemConfig`, `protocol.py`, mock generators | Simple enough to implement fully from start |
| 30 | TTS queue type | `PriorityMailbox`, keep-highest | A 1-slot `Queue` dropped the *newer* item — the opposite of the intent |
| 31 | Announcement selection | Composite priority score | Confidence/area alone can't tell "approaching car" from "routine reminder" |
| 32 | Urgency model | Two tiers: `normal` / `urgent` | Some events must be able to bypass every gate |
| 33 | Preemption | Urgent-tier OR relative margin | Barge-in for what matters; margin avoids twitchiness on near-ties |
| 34 | Suppression layering | Hard floor + soft penalty + pacing gap | One authority instead of two competing cooldowns |
| 35 | False-positive gate | `MIN_CONFIRMATION_SECONDS = 0.3` (seconds, not frames) | Gates the TTS label only; one-time cost per track |
| 36 | "multiple X" composition | Compose with **all** phrase types | Restricting it to first sightings made it fire at most once ever |
| 37 | Zone boundaries | `0.22 / 0.25 / 0.75 / 0.78`, hold-previous | Hysteresis holds the previous zone rather than dropping the detection |
| 38 | Depth development location | Prototyping repo first, then port | Depth needs rapid iteration against live captures |
| 39 | UAT work process | Requirements layer in `docs/testing/`; failsafes first; vision algorithms untouched | The working detection and depth chain is too risky to change; a crash or a silent failure matters more than a missing feature |
| 40 | Strength knob at minimum | Knob multiplies motor commands; minimum = motors off | Team kept the current code (UAT D-14); the floor-preserving variant on `feat/depth-callback` must not come in by merge |
| 41 | Failsafe alerts and the control panel switches | A failsafe alert is spoken or vibrated even when its switch is off, at the knob's level but never below 30 %, one time only | A safety alert nobody can perceive is not an alert; the switches go back as the wearer left them (UAT D-15) |

---

## Detailed Rationale

### D1: Threads over Processes

**Alternatives considered**: `multiprocessing.Process`, `concurrent.futures.ThreadPoolExecutor`, `asyncio`

**Why threads win**:
- Consumers (TTS, serial) are **I/O-bound** — the GIL is released during espeak-ng subprocess calls and `serial.write()` system calls
- GStreamer callbacks already run in GLib-managed threads — adding worker threads fits naturally
- `multiprocessing` adds serialization overhead, sentinel patterns, potential deadlocks on `multiprocessing.Queue`
- `ThreadPoolExecutor` is designed for discrete tasks with `Future` results, not long-running consumer loops
- `asyncio` conflicts with GLib's own main loop

### D2: pyttsx3/espeak-ng over Piper TTS

**Alternatives considered**: Piper TTS (neural, ONNX-based), Google TTS (cloud)

**Why espeak wins**:
- Utterances are 3-5 words ("person left") — neural quality is imperceptible at this length
- espeak-ng: < 1% CPU spike, < 50ms latency
- Piper: 15-30% CPU spike, 200-500ms latency, 50-150MB model in RAM
- The device is already running two CV models on the Hailo NPU — CPU budget is tight
- Piper's `TextToSpeechProcessor` in the hailo_apps repo is designed for GenAI voice assistants (Hailo-10H), not pipeline detection announcements

### D9: Parallel Motor Wiring (Critical)

**The series mistake**: If motors are wired in series, opening any transistor breaks the entire circuit — all motors stop. You physically cannot control them independently. PWM on one motor affects current through all three.

**The fix**: Each motor has its own N-channel MOSFET. All motors share the 18650 power rail in parallel. Each MOSFET gate is driven by a separate ESP32 PWM pin. Flyback diodes (1N4148) across each motor protect against back-EMF spikes.

### D12: Proportional Motor Mode over Single-Motor

**The corridor problem**: If only the highest-proximity motor vibrates, the user walking between two walls feels vibration on one side only. They drift toward the "silent" side and hit the other wall.

**Proportional mode**: All three motors vibrate based on their zone's proximity. The user's brain naturally performs relative comparison — "both sides are buzzing, center is quiet" means "stay centered."

### D17: Software Downward Hazard Detection

**The limitation**: Monocular depth estimation (SC-DepthV3) reads stairs going down as "far" — the floor drops away, and depth increases. This looks like open space, not danger.

**The workaround**: Ground plane departure detection. The bottom 20-30% of the depth map shows ground 1-3m ahead. On flat ground, the depth gradient is smooth. A staircase creates a sudden gradient spike (depth jumps from "close ground" to "far nothing"). Detecting this spike ratio (> 5x median gradient) flags potential drop-offs.

**Limitations**: Works for staircases (3+ steps) and ledges. Small curbs and potholes may be below the noise floor.

**Superseded by D36.**

### D36: Ground-Hazard Detection Disabled (Sept 2026)

**Decision**: `GROUND_HAZARD_ENABLED = False` in `depth_utils.py`. The detector, its debouncer and its tests stay in the tree; the callback publishes `hazard=False, hazard_severity=0` on every frame, so the serial contract and the ESP32 firmware are untouched.

**Why**: the detector was the least trustworthy stage in the chain — D17's own limitation (a drop-off reads as "far") was never solved, the gate divides by the ground strip's noise and flapped 131 times in 400 frames on a static scene, and every threshold is an uncalibrated placeholder. A ground warning that is wrong that often teaches the wearer to ignore the device, which costs them the warnings that do work — and a device that *claims* to see stairs while missing most of them is more dangerous than one that is known not to.

**What it costs — read this**: the wearer uses **no white cane** (D16, corrected). With this off, **breaks in the ground are invisible to the device**: stairs, drop-offs, step-ups, curbs. Scoped precisely (team, 2026-09-15): it is the *hazard* detector that is out — the ground plane is still modelled every frame (`floor_profile`, DECISIONS on floor suppression) and obstacles standing on the floor are still detected. This was first recorded on the false premise that a cane covered them; it was re-confirmed by the team on 2026-09-12 knowing there is no fallback. **What it must not cost**: honesty — every user, demo, doc and script must state plainly that the device does not warn about stairs.

**To reverse**: calibrate `HAZARD_MIN_STEP` / `HAZARD_MAX_JUMP` against real stairs (`SV_CALIBRATE=1`, `core/calibration.py`), settle the direction question (D17 / TASKS), then flip the flag.

### D20: Dynamic Pipeline Rebuild over "Ignore Output"

**Alternative**: Keep both pipelines always running, just `if not enabled: return` in callbacks.

**Why rebuild wins for this device**:
- Head-mounted = user wears the heat source — thermal matters
- Battery-powered = power draw matters
- Mode switches are infrequent (physical toggle, not per-frame)
- ~1s blackout during rebuild is acceptable and masked by TTS announcement
- The framework's `_rebuild_pipeline()` already handles the full teardown/rebuild cycle

**Status 2026-07-27**: reaffirmed. `trigger_rebuild()` already defers correctly through
`GLib.idle_add`; the mode-aware pipeline builders are being written now alongside the
control panel.

---

# Implementation-phase decisions (D30+)

> Promoted from `.agents/handoff_v2.md` and `.agents/handoff_v3.md` so they are visible to
> the whole team rather than living only in local handoff notes.

## D30: `PriorityMailbox` replaces `queue.Queue(maxsize=1)`

**The bug in the old design**: a 1-slot queue with `put_nowait` + drop-on-`Full` drops the
**newer** event and keeps the **older** one — exactly backwards from the "avoid stale
phrases" intent that motivated `maxsize=1` in the first place.

**The replacement**: one slot, but `offer()` keeps the higher-priority of {stored, incoming},
ties going to the newer item. Never blocks, never raises, backlog cannot accumulate.

**Contract change (amends D28)**: `tts_queue` exposes `offer` / `take` / `peek` — not
`put_nowait` / `get`. The detection dict gained `priority` (float) and `tier`
(`"normal"` | `"urgent"`), plus an optional `phrase` override. Items without those fields are
defaulted, so mock and announcement paths keep working; announcements get `+inf` priority and
urgent tier so a physical mode switch is always heard.

## D31: Composite priority score

`confidence + area + zone_weight + class_weight − recency_penalty`, all terms normalized to
roughly 0–1 with equal starting weights so they stay comparable.

Previously the frame's winner was `max(confidence, area)`, which cannot distinguish a
fast-approaching car from a routine "still there" reminder — only which was detected more
confidently or looked bigger.

`W_APPROACH` is a **reserved hook at 0.0** for approach velocity. Wiring it means storing
`prev_area` on the existing `track_history` entry and computing `(area − prev_area)/dt` — no
restructuring needed. Deliberately not computed yet.

Class and zone weights are **placeholders**, not committed policy: the final detection class
set isn't settled, so they ship as an easily-edited table.

## D32–D33: Tiers and preemption

An item is urgent if it is a vehicle-type class in the centre zone, or if its absolute
priority crosses `URGENT_ABS_THRESHOLD`. `preempts(new, current)` returns true if the new
item is urgent-tier **or** beats the current one by more than `PREEMPT_MARGIN`.

`PREEMPT_USE_TIER` flips this to pure relative-margin. **Both paths are kept alive
deliberately** — the intent is to A/B them on-device, so don't hardcode one.

*Amended by D42 (2026-10-19): a failsafe alert is the exception. No object warning cuts one being
spoken, and one waiting cuts nothing. `preempts()` itself is unchanged.*

## D34: Layered suppression, one authority

Two independent cooldowns used to coexist: a per-track one in `callbacks.py` and a separate
`CooldownManager` keyed by `(label, zone)` in `tts_worker.py`. Both had to independently
agree before anything was spoken.

Resolution — the worker's `CooldownManager` was **deleted**, and suppression became three
cooperating layers with distinct jobs:

- **Hard floor** (`MIN_REPEAT_INTERVAL`) — an *eligibility gate*
- **Soft recency penalty** — a *ranking term* among the eligible
- **Pacing gap** (`MIN_UTTERANCE_GAP`) — *calm cadence* between utterances

**Urgent bypasses the hard floor and the pacing gap.** This is intended, not an oversight: an
approaching car in the centre must be allowed to re-announce and to cut in. A gate muting an
urgent escalation is the bug.

## D35: Confirmation gate in seconds, not frames

A track must exist for `MIN_CONFIRMATION_SECONDS = 0.3` before its first announcement,
filtering 1–2 frame tracker/NMS glitches.

**Why seconds**: it gates only the TTS *label*, not the haptic/hazard channel (separate per
D11/D12); it is a one-time cost per track, not recurring; and 0.3 s is roughly 3× the
tracker's own `keep_new_frames` grace period at the assumed 30 fps operating point.

Requires a `first_seen` field that — unlike `zone_since` — **does not reset on a zone
change**, so an object walking centre → left isn't re-treated as new.

## D36: "multiple X" composes with all phrase types

**The rule**: multiplicity is checked *before* the event phrase, and composes with it —
`"multiple person center"`, `"multiple person still center"`, `"multiple person leaving to
left"`. Originally the opposite ("say multiple only when there is no event phrase"), then
**explicitly reversed** after live testing.

**Why the original failed** — not because an event phrase is rare, but because of which case
dominates at runtime. `phrase` is `None` for a brand-new track, for a track that hasn't
announced yet, for a zone change that isn't centre→side, and when head-turn suppression
fires. But for the case that actually matters — a group standing together in one zone — the
first announcement has no phrase and **every repeat after it is a `"still X"` phrase**. So
the old rule said "multiple" once and reverted to singular for the rest of the episode. A
technically-correct rule producing a near-useless result.

**Lesson worth keeping**: when a rule says "only in case X, never in case Y", work out what
fraction of real runtime falls into X before treating the design as settled.

**Separate fix, same feature**: the group tally is built from `track_history` *after* stale
cleanup and counts only tracks past `MIN_CONFIRMATION_SECONDS` — not the current frame's raw
detections. A single frame where the detector blinks therefore cannot undercount a group that
is genuinely still present.

Wording stays literal (`"multiple person center"`). Pluralization is **deferred, not
rejected** — it waits on the final class list.

## D38: Depth is developed in the prototyping repo, then ported

Depth post-processing needs fast iteration against live captures and off-device replay
tooling, which the prototyping repo already has. This repo receives it as a **port**:
integration, imports, coexistence with the haptic path, and coding standards — not algorithm
development.

**Consequence to be aware of**: two `depth_utils.py` exist, and their constants look
interchangeable but are not (relative model units vs metric). See ARCHITECTURE.md.

**Open, not decided here**: the prototype's depth debug view uses `multiprocessing`, which
sits in tension with **D1** (threads over processes). The production math is cheap
vectorized NumPy and is safe in-thread; the tension is only about the debug visualization
path. The depth owner should make this call explicitly during the port rather than defaulting
either way.

## D39: UAT work runs through `docs/testing/`, failsafes first, vision algorithms untouched

*Decided by the team, 2026-10-15.*

**What**: work driven by the UAT workbook (`tests/Group2-SV_Test-Tracker.xlsx`) goes through
the requirements layer in `docs/testing/`. The workbook stays authoritative; `REQUIREMENTS.md`,
`TRACEABILITY.md` and `TASK-BOARD.md` are derived from it, and `DISAGREEMENTS.md` records
every place where the workbook and the code disagree and which side changes.

**Three rules for that work:**

1. **The computer vision algorithms are not modified.** That covers the detection processing
   in `pipeline/callbacks.py`, the depth math in `core/depth_utils.py` and the depth-to-motor
   mapping in `core/haptics.py`. The current version works, and depth in particular was tested
   by hand and the team is satisfied with it. Tables around them (spoken labels, priorities)
   and the code that keeps the device alive around them (error handling, reconnects, alerts)
   may change.
2. **Critical failsafes come first** (P1): losing the glasses link, the control panel link,
   the camera or the earphones must never crash the device or fail silently. Everything else
   is P2.
3. **Mock mode is for testing only**, reached only through `--mock`. A deployed device never
   switches into it by itself; without a camera it keeps running and keeps checking for one
   (`docs/testing/DISAGREEMENTS.md` D-13).

**How the work is done (optional tooling)**: one checkout (`worktrees/kenth`) has a local,
gitignored Lead / Developer / Tester agent workflow in `.claude/`. A Lead agent plans one task
at a time, a Developer agent implements it, a Tester agent verifies it independently, and the
Lead accepts or returns it. Nothing in the repo depends on it: anyone without `.claude/`
follows `AGENTS.md` alone, and the rules above bind them the same way.

**Why**: ESP32-only cases stay manual and outside the agents' scope, because the agents can't
reach those boards reliably and must never flash firmware. Evidence is graded (unit, mocked,
on the device, physically measured, observed by a person), so a mocked test is never reported
as proof that hardware works.

## D40: The strength knob multiplies motor commands; at minimum the motors are off

*Decided by the team, 2026-10-15. Recorded in `docs/testing/DISAGREEMENTS.md` as D-14.*

**What**: the control panel's strength knob (0.00 to 1.00) multiplies every motor command in
`workers/serial_worker.py`. At minimum the motors are off; at the middle every command is
halved. The UAT workbook's "minimal" vibration at minimum is corrected to "does not vibrate"
(W-24).

**Why**: the team chose to keep the code as it is on `feat/test-and-robustness`.

**Watch at merge time**: commit `3797182` on `feat/depth-callback` does the opposite. It
scales only the part of each command above the lightest felt tap, so the knob's minimum is
still a felt tap. Merging that branch would silently reverse this decision. Whoever merges
keeps this branch's scaling, or reopens the decision with the team.

## D41: Failsafe alerts override the speech and vibration switches, at 30 % or more, one time only

*Decided by the team, 2026-10-18 (the user, for the team). Recorded in `docs/testing/DISAGREEMENTS.md`
as D-15. The UAT workbook said nothing about it.*

**What**: a failsafe alert is a message or vibration pattern the device gives about itself because a
part has stopped working (glasses link lost, control panel lost, camera missing, earphones missing,
and their reconnections). It is **not** silenced by the wearer's switches:
- **Speech.** Spoken even when the panel's speech switch is off. It uses the volume knob's current
  level, but never below 30 %: with the knob under 30 %, that alert is spoken at 30 %.
- **Vibration.** Played even when the panel's vibration switch is off. It uses the strength knob's
  current level, but never below 30 %: with the knob under 30 %, that pattern plays at 30 %.
- **One time only.** The override covers that one alert or pattern, and each repeat of it. Afterwards
  the wearer's settings are exactly as left: a switch that was off is off, a knob that was low is low.
- **Nothing else changes.** Object announcements, mode announcements and the normal depth vibration
  still obey the switches and the knobs (D40 and the UAT record's D-04 for the volume knob, unchanged).

**Why**: the alerts are the only way a wearer who cannot see learns that part of the device has
stopped working. If the speech switch is off, "Critical Error: Camera cannot be reached" would never be
heard; with the strength knob at the bottom, the vibration pattern would never be felt. The 30 % floor
keeps the wearer's choice of a quieter setting while still making sure the alert is perceived.

**See also** (`docs/testing/DISAGREEMENTS.md`): D-16, an alert in progress finishes (2 to 3 s) before an
urgent object warning can cut it (recorded and built as D42 below); D-17, "everything is connected" is said only when no other failsafe
alert is still raised.

**Built (2026-10-18, demo item F1; `docs/testing/reports/DEMO/F1.md`)**: the override is a property of
the message, never a change to the switches.
- **Speech.** Every alert raised on the alert scheduler (`core/alerts.py`) is a failsafe alert.
  `workers/tts_worker.py` speaks one even with `tts_enabled` off, at `failsafe_level(tts_volume)`.
  An item that would not be spoken cannot cut in on it.
- **Vibration.** A pattern from `core/haptic_patterns.py` is played by `workers/serial_worker.py` even
  with `vibration_enabled` off, at duty × `failsafe_level(motor_strength)`.
- **Strength scaling.** *Amended 2026-10-19.* It was a plain multiply, the same as depth (D40), chosen by the user
  on 2026-10-18; at the 30 % floor a pattern's strong pulse was duty 76, under the unmeasured start threshold of
  90 in `core/haptics.py`. The user then found the earphones pattern weak at a low knob, and chose the alternative:
  the knob maps onto the motors' felt range, `felt_duty()` in `core/haptic_patterns.py`:
  `PWM_FLOOR + (duty − PWM_FLOOR) × level` for an on step, level still the knob never under 30 %, an off step
  stays off, and nothing changes at full strength. The earphones pattern's weak step is 190 (was 150). Depth
  vibration is unchanged (D40). The device check (DEMO-CHECKLIST Part A) still sets the numbers.
- **A pattern holds the motors** for at most 2.25 s; a ground hazard is still sent during it.

## D42: A failsafe alert in progress finishes; an object warning waits for it

*Decided by the team, 2026-10-18 (the user, for the team). Recorded in `docs/testing/DISAGREEMENTS.md`
as D-16, option (b). The UAT workbook says how often each alert repeats, not what happens when the
device has something else urgent to say at the same moment. Amends D32–D33.*

**What**:
- **An alert being spoken is not cut by an object warning**, however urgent. It finishes (2 to 3 s),
  then the warning is said.
- **An alert that falls due while an object warning is being said goes next**, as soon as that phrase
  ends, ahead of later warnings. It does not cut the phrase in progress.
- **Unchanged:** a mode announcement (the wearer's own switch) may still cut an alert, which is then
  said again; behind a mode announcement an alert waits for its end.

**Why**: a failsafe alert is the wearer's only notice that part of the device has stopped working, and
a vehicle ahead is also when an object warning matters most. Before this, an urgent object that stayed
in view was announced again on every frame. The announcements cut each other, so the speech worker was
never idle, and the alert scheduler, which waited for an idle worker, never offered the alert: the
wearer was never told (the Tester's finding F-1 on T-00a, 2026-10-18).

**Cost**: when an alert falls due during an urgent warning, that warning's next repeat waits for the
phrase in progress (about 1.2 s) and the alert (2 to 3 s). Alerts repeat every 10 s (the camera, when
there are no detections anyway), 30 s (the glasses board) or 5 minutes (the control panel), so at most
once per repeat.

**Built (2026-10-19, demo item F7; `docs/testing/reports/DEMO/F7.md`)**:
- `workers/tts_worker.py` `_cuts()`: `priority.preempts()` with the two exceptions above. Without the
  alert scheduler it is `preempts()` exactly.
- `core/alerts.py`: the scheduler offers a due alert while the worker speaks an object warning, not only
  when it is idle. Behind a mode announcement it still waits, so a burst of switch flips cannot keep
  replacing it in the mailbox slot.
- Measured with a stub speaker (`tests/uat/test_t00a_alert_scheduler.py`): under urgent warnings arriving
  every 0.8, 0.3 or 0.1 s, the alert went from never heard to heard within 1.3 s.
