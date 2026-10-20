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
| 4 | `tts-worker` | `threading.Thread` daemon | `tts_queue`, taken through `user_data.alerts` | espeak-ng audio | App lifetime |
| 5 | `alert-scheduler` | `threading.Thread` daemon | the raised failsafe alerts (`user_data.alerts`), every 0.05 s | one alert at a time into `tts_queue` | App lifetime |
| 6 | `serial-writer` | `threading.Thread` daemon | `serial_queue`, `user_data.patterns` | Binary packets to the glasses ESP32; its ACKs: glasses lost / back | App lifetime |
| 7 | `config-reader` | `threading.Thread` daemon | Control panel serial text | `SystemConfig` updates; panel lost / back | Only with `--config-port` |
| 8 | `earphones-watch` | `threading.Thread` daemon | `/proc/asound`, every 1 s | earphones lost / back | App lifetime |
| 9 | `status-board` | `threading.Thread` daemon | the alerts and the camera view check, every 0.5 s | `user_data.status`, `[CHECK]` lines | App lifetime |
| 10 | `camera-watch` | `threading.Thread` daemon (`pipeline/app.py`) | looks for a USB camera every 2 s while there is none | starts vision when one appears | While no camera |
| 11 | *(opt)* Watchdog | the library's (`GStreamerApp`) | Frame count | Restart on stall | If `--enable-watchdog` |
| 12 | *(opt)* `mode-cycler` | `threading.Thread` daemon | a timer | mode changes (a debug stand-in for the panel) | If `--cycle-modes` |

Mock mode adds `mock-det` and `mock-depth`. The first stop request starts `shutdown-timer`
(`pipeline/app.py`), which forces the exit if the stop hangs.

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

**Failsafe alerts** (D41, 2026-10-18) go through the same mailbox as `{"announce": str}`, but
only via `user_data.alerts` (`core/alerts.py`, the alert scheduler), never offered directly.
The speech worker asks `alerts.is_failsafe(item)`: an alert is spoken even with `tts_enabled`
off, at `max(tts_volume, 0.30)`; every other item still obeys the switch and the knob. The
queue keys are unchanged.

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

**Failsafe vibration patterns** (D41, 2026-10-18): `user_data.patterns.play(name)`
(`core/haptic_patterns.py`; reconnect, camera, earphones) from any thread. The serial worker
plays a pattern by sending one ordinary motor packet per step, so the firmware is unchanged.
While it plays, the pattern owns the motors, for at most 2.25 s:
- depth duties are not sent, and the depth frames that arrive meanwhile are discarded;
- a ground hazard among them is still sent;
- the vibration switch is ignored, and the strength is `max(motor_strength, 0.30)` × duty
  (plain multiply, as for depth).

If the board stops answering, patterns are dropped. After a pattern, depth resumes on its next
frame.

### Arduino → Config

```
Text protocol over serial (9600 baud):
  "S:motor_strength:0.75\n"    → config.update(motor_strength=0.75)
  "S:tts_enabled:0\n"          → config.update(tts_enabled=False)
  "M:detection\n"              → config.update(pipeline_mode="detection") + rebuild
  "B:announce_status\n"        → button event handler
```

**"Is depth on?" and "is detection on?" come only from `pipeline_mode`.** The DEPTH and DETECT
rockers reach the Pi only as the `M:` line. The panel never sends `depth_enabled` or
`detection_enabled`, so those two `SystemConfig` fields stay True for the whole run, whatever the
rockers say. Depth is on in mode `depth` or `both`, and detection in `detection` or `both`
(`core/connections.py` `depth_on()`).

### Failsafe alerts → speech, vibration and the screen

*Built by the demo track, 2026-10-18 and 19 (`docs/testing/DEMO-TRACK.md`, F0 to F7). Decisions: D41 in
[DECISIONS.md](DECISIONS.md), and D-15 to D-18 in `docs/testing/DISAGREEMENTS.md`.*

A failsafe alert is something the device says or plays about itself because a part stopped working.
Every part that can be lost reports through one object, **`user_data.alerts`**, the `AlertScheduler`
in `core/alerts.py`. It is the only code that decides *when* an alert is spoken. Callers say what,
and how often, and clear it when the condition ends:

```python
alerts.raise_alert(name, text, interval=None, repeat=True, speak=True, on_speak=None)
#   interval: seconds between repeats, None = once;  repeat: bool, or a callable read every tick
#   speak=False: a SILENT alert, raised and counted but never spoken (the earphones: nothing to speak through)
#   on_speak: called each time the speech worker STARTS the sentence (the camera's vibration rides on it)
alerts.clear(name)            # stops it; a sentence under way is not cut
alerts.is_raised(name); alerts.raised(); alerts.last_spoken()   # -> (text, time) of the last one started
```

The speech worker takes from the mailbox through the scheduler (`take()`, `drain()`, `finish(item,
SPOKEN|CUT|DROPPED)`, `is_failsafe(item)`), so an alert is never lost or doubled, and one cut by a
mode announcement is said again. An alert reaches `tts_queue` as `{"announce": text}`, the existing
mode-announcement contract; no queue key changed.

| Part | Lost when | Said / played | Back |
|---|---|---|---|
| Camera | the source fails, or none at start (`pipeline/app.py`) | "Critical Error: Camera cannot be reached" at once and every 10 s, each time with the camera pattern | "Camera has been reconnected" once, 1 s after vision runs |
| Camera view | 3 checks in a row (one a second, `core/camera_view.py`) see it dark/blocked or at bright light | "Camera is blocked or it is too dark..." / "Camera is facing very bright light..." every 30 s; none in mode `none` | "Camera view is back." once |
| Glasses board | no ACK for `ACK_TIMEOUT_SECONDS` (2.5 s, `workers/serial_worker.py`) | "Vibration is not working" every 30 s while depth is on, once while off | the reconnect pattern, then the "back" sentence |
| Control panel | 30 s without its heartbeat (`B:alive`, sent every 10 s; `ALIVE_TIMEOUT_SECONDS` in `workers/config_reader.py`), or its port cannot be opened | "Controller board cannot be reached. Using the current settings." every 5 minutes | the "back" sentence; settings stay as they were (none are saved) |
| Earphones | no USB sound card that can play, two reads in a row (`/proc/asound`, D-18; `workers/earphones_worker.py`, every 1 s) | silent alert; the earphones pattern every 30 s while detection is on, once while off | the reconnect pattern, then the "back" sentence |

- **The "back" sentence (D-17):** "Everything is connected" only when no other part is still missing
  (`core/connections.py` `MISSING_ALERTS`); otherwise the returning part's own sentence ("Vibration is
  working again", "Earphones connected", "Controller board connected"). The camera's is always its own.
- **Overrides (D41):** every alert is spoken with the speech switch off, and every pattern played with
  the vibration switch off, at the knob's level but never under 30 %, for that one alert. Settings are
  never changed. A pattern's strength is mapped onto the motors' felt range (`felt_duty()`, never under
  `PWM_FLOOR` while a step is on), not multiplied, so it is felt at any knob position.
- **Patterns** (`core/haptic_patterns.py`, `user_data.patterns.play(name)`): `reconnect` (two short
  pulses), `camera` (two long), `earphones` (strong, weak, three times), on all three motors. The serial
  worker plays them in place of depth commands for at most 2.25 s, then hands the motors back.
- **An alert in progress finishes (D-16, F7):** see "Preemption" below.
- **On screen and in the terminal:** `workers/status_worker.py` reads the five checks every 0.5 s
  (`core/status_board.py` `read_status()`, read-only), keeps them on `user_data.status`, and prints
  each change as a `[CHECK]` line. The detection overlay draws them on the picture (`core/status_view.py`:
  a status strip, red banners, the last sentence said for 8 s, the camera view's brightness top right).
  With no live picture the app shows a dark status screen. The depth window is not touched.
- **The camera view check reads frames without slowing the camera branch:** once a second a one-shot
  buffer probe on the source's last element (`source_fps_caps`, RGB) hands one frame over and removes
  itself (`pipeline/app.py` `_request_view_frame`); a check costs about 1 ms. Measured on the device:
  no frame-rate cost (`docs/testing/reports/T-15/measurement-2.md`).

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
on-device A/B comparison. A pending item that would not be spoken does not preempt: with the
speech switch off, a detection cannot cut a failsafe alert. A preempting item is spoken at its own
volume.

**A failsafe alert in progress finishes (D-16, built 2026-10-19 as F7; `workers/tts_worker.py` `_cuts`).**
Two exceptions to the rule above, both for the alert scheduler's own item:
- an alert being spoken is not cut by an object warning, however urgent: it finishes (2 to 3 s), then
  the warning is said;
- an alert waiting in the slot cuts nothing. The scheduler offers it while the worker speaks an object
  warning (not only when the worker is idle), so it is said as soon as that phrase ends, ahead of later
  warnings: an announcement outranks every detection in the mailbox. Behind a mode announcement it waits
  for the end, as before.

A mode announcement still cuts an alert (the wearer's own switch), and the alert is said again. Before
F7, urgent warnings for an object staying in view cut each other every frame, the worker was never idle,
and an alert was never heard; now within 1.3 s.

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
│   ├── callbacks.py        ← on_det_frame and on_depth_frame (both built; depth through DepthPostProcessor)
│   └── TBR-*.py            ← Draft/staging files, unwired. Not imported by anything
├── workers/
│   ├── tts_worker.py       ← espeak-ng, serialized + interruptible; failsafe alerts finish (D-16)
│   ├── alert_worker.py     ← runs the alert scheduler's step every 0.05 s
│   ├── serial_worker.py    ← glasses ESP32 link: real port, packets, heartbeat, ACK / link state, patterns
│   ├── config_reader.py    ← control panel link: real port, text protocol, lost / back
│   ├── earphones_worker.py ← looks for the earphones every 1 s
│   └── status_worker.py    ← the failsafe status every 0.5 s: user_data.status, [CHECK] lines
├── core/
│   ├── config.py           ← SystemConfig: thread-safe shared state
│   ├── priority.py         ← PriorityMailbox + all TTS scoring/preemption tunables
│   ├── alerts.py           ← AlertScheduler: when each failsafe alert is spoken (see "Failsafe alerts")
│   ├── connections.py      ← the parts that can be lost: wording, cadence, D-17's "back" sentence
│   ├── haptic_patterns.py  ← the failsafe vibration patterns (PatternPlayer)
│   ├── camera_view.py      ← blocked / too dark / too bright, from one frame a second
│   ├── status_board.py, status_view.py ← the status: read it (no cv2), draw it (cv2)
│   ├── protocol.py         ← PLANNED — packing currently lives in serial_worker.py
│   └── depth_utils.py      ← the depth post-processing (ported from the prototyping repo)
└── mock/
    └── data_generator.py   ← fake detections + depth for --mock mode

tests/                      ← the unit, GStreamer and device tests (test_*.py); tests/uat/ for UAT cases
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
| `pipeline/app.py` | control (`get_pipeline_string`, `_connect_callback`), depth (branch wiring), detection (tracker params), failsafe alerts (camera lost / back, the camera view probe; demo track) |
| `core/config.py`, `workers/config_reader.py` | control |
| `workers/serial_worker.py`, `core/protocol.py` | firmware, depth (hazard fields), failsafe patterns (demo track) |
| `workers/tts_worker.py`, `core/alerts.py`, `core/haptic_patterns.py` | TTS, failsafe alerts and patterns (demo track) |
| `core/connections.py`, `workers/earphones_worker.py` | failsafe alerts (glasses, panel, earphones; demo track), control (`depth_on`, `detection_on`) |
| `core/camera_view.py`, `core/status_board.py`, `core/status_view.py`, `workers/status_worker.py` | failsafe checks on screen (demo track); detection (`_draw_detection_overlay` draws the status) |
| `core/priority.py` | TTS |
| `main.py` | all — worker startup and `user_data` construction |
