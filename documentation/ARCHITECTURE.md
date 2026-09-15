# ARCHITECTURE.md — System Architecture

> **Last synced: 2026-07-27.** Verified against source.
> This is the single source of truth for how the system is built.
>
> The depth post-processing section is deliberately kept high-level: that design is still
> moving. See [PLAN.md](PLAN.md#2-depth-estimation-callbacks).

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     HEAD-MOUNTED UNIT                           │
│  [Camera]  [Motor L]  [Motor C]  [Motor R]  [Earpiece]         │
└──────┬────────┬──────────┬──────────┬───────────┬───────────────┘
       │        │          │          │           │
  USB  │   ┌────┴──────────┴──────────┴────┐     │ audio
       │   │          ESP32                 │     │
       │   │  3× MOSFET PWM + 18650 power  │     │
       │   └──────────────┬────────────────┘     │
       │             USB Serial                   │
       │                  │                       │
┌──────┴──────────────────┴───────────────────────┴───────────────┐
│                    RASPBERRY PI 5                                │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              GStreamer Pipeline (Hailo-8 NPU)             │   │
│  │                                                          │   │
│  │  Camera → tee ──→ SCDepthV3 ──→ depth_callback           │   │
│  │               └──→ YOLOv8n ──→ tracker ──→ det_callback  │   │
│  └──────────────────────────────────────────────────────────┘   │
│           │                              │                      │
│     serial_queue                   tts_queue                    │
│     (queue.Queue)                  (queue.Queue)                │
│           │                              │                      │
│  ┌────────▼────────┐          ┌──────────▼──────────┐          │
│  │  Serial Worker   │          │    TTS Worker        │          │
│  │  (daemon thread) │          │   (daemon thread)    │          │
│  │  binary protocol │          │   espeak-ng + cooldown│          │
│  └────────┬─────────┘          └──────────┬──────────┘          │
│           │                               │                     │
│      USB Serial                      espeak-ng                  │
│      to ESP32                        subprocess                 │
│                                                                 │
│  ┌──────────────────┐     ┌─────────────────┐                  │
│  │  Config Reader    │     │  SystemConfig    │                  │
│  │  (daemon thread)  │────→│  (thread-safe)   │                  │
│  │  Arduino serial   │     │  shared state    │                  │
│  └────────┬──────────┘     └────────┬────────┘                  │
│           │                         │ read by all workers       │
│      USB Serial                     │                           │
└───────────┼─────────────────────────┼───────────────────────────┘
            │                         
┌───────────┴──────────┐              
│   Arduino Control     │              
│   Panel (switches,    │              
│   pots, buttons)      │              
└──────────────────────┘              
```

---

## Threading Model

| # | Thread | Type | Input | Output | Lifetime |
|---|---|---|---|---|---|
| 1 | GLib MainLoop | Main thread | GStreamer bus events | Pipeline state mgmt | App lifetime |
| 2 | Depth streaming | GStreamer-managed | Camera frames | SCDepthV3 inference → depth_callback | While depth pipeline active |
| 3 | Det streaming | GStreamer-managed | Camera frames | YOLOv8 inference → det_callback | While det pipeline active |
| 4 | TTS Worker | `threading.Thread` daemon | `tts_queue` | espeak-ng audio | App lifetime |
| 5 | Serial Writer | `threading.Thread` daemon | `serial_queue` | Binary packets to ESP32 | App lifetime |
| 6 | Config Reader | `threading.Thread` daemon | Arduino serial text | `SystemConfig` updates | App lifetime |
| 7 | *(opt)* Watchdog | `threading.Thread` daemon | Frame count | Restart on stall | If `--enable-watchdog` |

The debug overlay is **not** a separate thread. It is drawn with cv2 inline inside the
detection callback, gated on `user_data.use_frame`. A separate composite debug window
(`--debug-display`) is a planned task, not current behaviour.

### Shutdown sequence

1. `SIGINT` (Ctrl+C) or pipeline error
2. `user_data.shutdown_event.set()`
3. All workers check `shutdown_event` in their `queue.get(timeout=1.0)` loop
4. Workers exit within ~1 second
5. `main()` joins threads with `timeout=2.0`
6. Process exits

---

## Data Flow Contracts

### Detection → TTS

`tts_queue` is a **`PriorityMailbox`**, not a `queue.Queue`. It exposes
`offer()` / `take()` / `peek()` — there is no `put_nowait` / `get`. It holds one slot and
keeps the **higher-priority** of {stored, incoming}, ties going to the newer item, so a
busy TTS worker receives the most urgent item rather than a stale one. `offer()` never
blocks and never raises.

```
GStreamer det_callback → user_data.tts_queue.offer({
    "label": "person",          # str: YOLO class label
    "zone": "left",             # str: "left" | "center" | "right"
    "confidence": 0.87,         # float: 0.0-1.0
    "priority": 2.45,           # float: composite score, higher = surface first
    "tier": "normal",           # str: "normal" | "urgent"
    "phrase": "person still left",   # str, OPTIONAL: overrides "{label} {zone}"
})

# Mode announcement (special):
user_data.tts_queue.offer({
    "announce": "detection mode"  # str: spoken as-is
})
```

Items arriving without `priority` / `tier` (mock data, mode announcements) are defaulted by
`item_priority()` / `item_tier()` in `core/priority.py`. Announcements get `+inf` priority
and urgent tier, so a physical mode switch is always heard.

### Depth → Serial

```
GStreamer depth_callback → user_data.serial_queue.put_nowait({
    "left": 200,                # int: 0-255 motor PWM intensity
    "center": 50,               # int: 0-255
    "right": 180,               # int: 0-255
    "hazard": False,            # bool: ground hazard detected?
    "hazard_severity": 0,       # int: 0-255
})
```

### Arduino → Config

```
Text protocol over serial (9600 baud):
  "S:motor_strength:0.75\n"    → config.update(motor_strength=0.75)
  "S:tts_enabled:0\n"          → config.update(tts_enabled=False)
  "M:detection\n"              → config.update(pipeline_mode="detection") + rebuild
  "B:announce_status\n"        → button event handler
```

---

## Binary Serial Protocol (RPi5 → ESP32)

### Packet format

```
| START (0xAA) | MSG_TYPE (1B) | PAYLOAD (N bytes) | CHECKSUM (1B) |
Checksum = XOR of all bytes from MSG_TYPE through end of PAYLOAD
```

### Message types

| Direction | Type | Code | Payload | Total bytes |
|---|---|---|---|---|
| RPi → ESP32 | MOTOR_UPDATE | `0x01` | `left(1B) center(1B) right(1B)` | 6 |
| RPi → ESP32 | HAZARD_ALERT | `0x04` | `severity(1B) pattern(1B)` | 5 |
| RPi → ESP32 | HEARTBEAT | `0xFE` | none | 3 |
| ESP32 → RPi | ACK | `0xFF` | `acked_type(1B)` | 4 |

### Wire config

```
Baud:     115200
Data:     8 bits
Stop:     1 bit
Parity:   None
Flow:     None
```

---

## Pipeline Modes

The system supports four pipeline configurations, switchable at runtime via the ESP32 control panel. The mode is not a setting the panel sends independently — it is derived from the positions of the two latching rockers, so the panel can never display a state that is not running.

| Mode | GStreamer Pipeline | Active Callbacks | Use Case |
|---|---|---|---|
| `"both"` | Source → tee → (SCDepthV3 + YOLOv8) | Both | Default — full system |
| `"detection"` | Source → YOLOv8 → tracker | det_callback only | Familiar routes, TTS only |
| `"depth"` | Source → SCDepthV3 | depth_callback only | Open areas, haptics only |
| `"none"` | Source → fakesink (no inference) | frame counter only | Both rockers off — run nothing |

`"none"` keeps the camera running on purpose. Tearing the source down would save a little idle power, but returning would cost a camera cold-start on top of the rebuild blackout in D21, and a rocker is exactly the control a user flips straight back. A callback identity stays wired with its handler disabled so the frame counter keeps moving — otherwise `--enable-watchdog` reads idle as a stall.

Switching is done via `GLib.idle_add(app._rebuild_pipeline)`. The ~0.8-1.2s blackout during rebuild is masked by a TTS announcement ("detection mode").

---

## Depth Processing

### Zone splitting (25/50/25)

```
Frame width:
  ├── 0-25% ──┤── 25-75% ──────────────┤── 75-100% ──┤
     LEFT           CENTER                   RIGHT
   (Motor L)       (Motor C)               (Motor R)
```

Stable: the frame is split L/C/R with a wider centre, each zone producing one 0–255 intensity for the `serial_queue` contract. Closer must map to stronger along a non-linear curve — linear feels unnatural to users.

### Units — relative, not metres

The SC-DepthV3 postprocess `.so` emits **relative model units**, not metres. Any constant carrying an `_M` suffix in prototype code is still in those relative units despite the name. Porting metric-looking thresholds without preserving that semantics produces plausible but wrong motor output — a silent failure on an assistive device, which is worse than a loud one.

### Edge cases the design must handle

These come from the requirements, so they outlive any particular algorithm:

| Edge case | Why raw depth is not enough |
|---|---|
| Thin objects (poles, cables, chair legs) | Networks smooth them away; a whole-zone aggregate averages them out |
| Blank / textureless walls | No texture → scale ambiguity → washed-out, uncertain output |
| Near walls the model renders as *far* | Same ambiguity, but failing dangerously rather than noisily |
| Drop-offs and descending stairs | A drop reads as "far", indistinguishable from open space — *stair / drop-off / step-up detection is OUT OF SCOPE (D36). The ground plane itself is still modelled (floor suppression) and obstacles standing on it are still detected* |
| The floor itself | A forehead camera angled down sees the wearer's own feet in every frame — the *nearest* surface the model reports, and every "how near is this?" detector fires on it |

### The floor is not an obstacle

Measured on the real chain (`depth_utils.py`, Sept 2026) with a synthetic open floor: an **empty** scene drove all three zones to ~213/255 and the motors to 145/145/145; a person at collision range fully inside the right zone changed the motors by nothing — the floor set the level in every zone, and the contrast + quantize stages collapsed the remaining gap. That is the "person on the right, centre rings" field report.

`depth_utils.floor_profile` fits `1/depth = a + b·row` (a plane under a pinhole camera) to the row medians of the bottom `FLOOR_FIT_FRACTION` of the grid, extrapolates it upward, and `suppress_floor` erases every pixel at or beyond that plane before the near-cluster and blank-wall detectors run. The fit is **refused** unless the plane recedes by `FLOOR_MIN_RECESSION` across the window, so a wall filling the bottom of the frame is never mistaken for a floor; rows above the horizon keep everything. `detect_floor_to_wall` and `detect_ground_hazard` still read the raw grid — they need the floor as their ruler. `FLOOR_SUPPRESSION = False` restores the previous behaviour in one line. The HUD darkens erased pixels.

### Direction has to be amplified, not preserved

`haptics.py` runs **winner-take-most** after lateral contrast: each zone is pushed down by `WINNER_SUPPRESSION` × its shortfall from the loudest zone. Tied zones (a wall across the field, a corridor's two walls) pass through unchanged; a zone that is merely trailing is silenced. With three motors the only direction the device can express is *which zone wins*, and a 255-vs-200 pair that lands on the same quantizer level says nothing.

### Current state in this repo

`_process_real_depth` in `pipeline/callbacks.py` runs the full chain above (`DepthPostProcessor` → `HapticMapper` → `serial_queue`).

**Ground-hazard detection is disabled** (`GROUND_HAZARD_ENABLED = False`, DECISIONS D36): the callback publishes `hazard=False, hazard_severity=0` on every frame *on purpose*: the detector was not trustworthy enough to ship, and the wearer must be told the device does not see stairs (there is no cane fallback — D16). `detect_ground_hazard` / `HazardDebouncer` remain in `depth_utils.py`, tested, and the serial contract keeps both fields, so the firmware's `HAZARD_ALERT` path is unchanged and simply never triggered.

**Open interface question (parked with the feature):** the detector distinguishes a drop-off (`"down"`) from a step-up/curb (`"up"`), but the `serial_queue` contract has no field for direction and `HAZARD_ALERT`'s payload is severity + pattern. Settle it only if the feature is re-enabled.

---

## TTS Prioritization and Suppression

> The per-`"{label}-{zone}"` `CooldownManager` this section used to describe **no longer
> exists** — it was deleted in the priority revamp. Suppression now lives in one layer
> (callback + `core/priority.py`), not two competing ones.

### Choosing what to say

Every qualifying detection gets a composite **priority** score — confidence + bbox area +
zone weight + class weight, minus a decaying recency penalty — and an urgency **tier**
(`normal` / `urgent`). One winner per frame is chosen by priority and offered to the
mailbox. Nothing is lost by reducing to one: persistent objects re-emit every frame from
`track_history`, so a runner-up simply wins on a later frame.

### Three layers of suppression

| Layer | Mechanism | Urgent bypasses? |
|---|---|---|
| Hard repeat floor | `MIN_REPEAT_INTERVAL` (10 s) between repeat announcements for a track | **Yes** |
| Soft recency penalty | Just-spoken items lose priority, decaying back over `RECENCY_DECAY_SECONDS` | No — it is a ranking term, not a gate |
| Inter-utterance gap | `MIN_UTTERANCE_GAP` (0.5 s) of silence after each utterance | **Yes** |

**Urgent deliberately bypasses both the floor and the gap.** An approaching car in the
centre must be able to re-announce and cut in even if that object spoke moments ago. If you
find a gate muting an urgent escalation, the gate is the bug — the bypass is intended.

### Preemption

While speaking, the worker polls the mailbox every ~50 ms. A pending item preempts if it is
urgent-tier **or** beats the current item's priority by more than `PREEMPT_MARGIN`. The
`PREEMPT_USE_TIER` flag flips this to pure relative-margin; both paths are kept live for
on-device A/B comparison.

All tunables live in `core/priority.py`, plus the detection-side constants in
`pipeline/callbacks.py`. See [FIELD-TESTING.md](FIELD-TESTING.md) for the symptom → parameter
tuning map.

### Zone boundary hysteresis

To prevent jitter when objects sit at zone boundaries:

```
Enter left:    center_x < 0.22
Enter center:  center_x > 0.25 AND center_x < 0.75
Enter right:   center_x > 0.78
Hysteresis:    0.22-0.25 and 0.75-0.78 = keep previous zone
```

The hysteresis bands **hold the previous zone** rather than dropping the detection, and the
state is tracked per tracker ID, not per label.

---

## ESP32 Safety

| Feature | Behavior |
|---|---|
| Watchdog | If no packet received for 3 seconds, set all motors to 0 |
| ACK | Send immediately upon parsing valid packet |
| Hazard pattern | Override normal motor values with pulsing pattern |
| Flyback diodes | 1N4148 across each motor (cathode to positive) |
| Power | 18650 → parallel motor wiring → individual MOSFETs per motor |

---

## File Map

```
src/second_vision/
├── main.py                 ← Orchestrator: creates config, starts workers, runs pipeline
├── main2.py                ← Temporary smoke-test script (deletion pending — see TASKS.md)
├── pipeline/
│   ├── app.py              ← SecondVisionApp: extends GStreamerApp (NOT GStreamerParallelApp)
│   ├── callbacks.py        ← on_det_frame (built), on_depth_frame (placeholder)
│   └── TBR-*.py            ← Draft/staging files, unwired. Not imported by anything
├── workers/
│   ├── tts_worker.py       ← espeak-ng, serialized + interruptible
│   ├── serial_worker.py    ← packet packing built; serial I/O still stubbed
│   └── config_reader.py    ← text protocol parsing built; serial I/O still stubbed
├── core/
│   ├── config.py           ← SystemConfig: thread-safe shared state
│   ├── priority.py         ← PriorityMailbox + all TTS scoring/preemption tunables
│   ├── protocol.py         ← PLANNED — packing currently lives in serial_worker.py
│   └── depth_utils.py      ← Placeholder; real approach still in the prototyping repo
└── mock/
    └── data_generator.py   ← fake detections + depth for --mock mode

tests/
└── test_priority.py        ← 19 tests: mailbox, scoring, tiers, preemption, worker
```

`SecondVisionApp` extends **`GStreamerApp`**. `GStreamerParallelApp` exists only in the
prototyping repo and is **not importable** from the installed `hailo_apps` library, so the
dual-branch pipeline is built inline here.

---

## Shared files — expect concurrent edits

Five workstreams run in parallel and legitimately touch the same files. This map exists so
you know *who else is in here*, so you can coordinate before and after a change.

**This is not an ownership gate.** Any member's agent may edit any of these files; the team
coordinates directly on what lands and how it affects the other side.

| File | Workstreams that touch it |
|---|---|
| `pipeline/callbacks.py` | depth (`_process_real_depth`), detection (`_process_real_detections`), TTS (tuning constants) |
| `pipeline/app.py` | control (`get_pipeline_string`, `_connect_callback`), depth (branch wiring), detection (tracker params) |
| `core/config.py`, `workers/config_reader.py` | control |
| `workers/serial_worker.py`, `core/protocol.py` | firmware, depth (hazard fields) |
| `core/priority.py` | TTS |
| `main.py` | all — worker startup and `user_data` construction |
