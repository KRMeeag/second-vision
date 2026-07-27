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
| 16 | Target user | Visually impaired | White cane as fallback for ground-level hazards |
| 17 | Downward hazards | Software ground-plane departure detection | Depth gradient spike analysis; catches stairs/ledges |
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

**Limitations**: Works for staircases (3+ steps) and ledges. Small curbs and potholes may be below the noise floor. Not a replacement for the white cane.

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
