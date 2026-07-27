# PLAN.md — Development Plan

> **Last synced: 2026-07-27.** Verified against source, not against prior documents.
> This is the shared plan for the whole team. Each workstream below runs **concurrently**,
> driven by a different member. Read your own section; read the others before touching a
> file they own.

Superseded model: this document used to describe ten sequential phases. The team never
worked that way — five workstreams progress in parallel — so the phase numbering is gone.
If you find a "Phase N" reference in an old document, map it here by name.

---

## Workstreams at a glance

| Workstream | Role label | State |
|---|---|---|
| [Object detection callbacks](#1-object-detection-callbacks) | detection owner | Substantially built; recent fixes not yet validated on hardware |
| [Depth estimation callbacks](#2-depth-estimation-callbacks) | depth owner | Placeholder here; **approach under active design** in the prototyping repo |
| [TTS](#3-tts) | TTS owner | Built, including the priority/preemption revamp. Tuning deferred to last |
| [ESP32 vibration motors](#4-esp32-vibration-motors) | firmware owner | ESP32 side done; **RPi side unimplemented**; UART comms in progress |
| [Control panel + mode switching](#5-control-panel--mode-switching) | control owner | Reader stubbed, mode-awareness unimplemented; hardware in progress |

**Field testing and TTS tuning happen last**, after the four build streams converge — see
the [Field testing gate](#field-testing-gate).

---

## 1. Object detection callbacks

**Role label:** detection owner
**Purpose:** Turn YOLO detections into a single, well-chosen spoken event per frame.

### Built and working

Implemented in `_process_real_detections` ([`pipeline/callbacks.py`](../src/second_vision/pipeline/callbacks.py)):

- **Zone assignment with hysteresis** — boundaries `0.22 / 0.25 / 0.75 / 0.78`. A detection
  sitting in a boundary band **keeps its previous zone** rather than being dropped, so an
  object loitering on a zone line doesn't flip-flop.
- **Confirmation gate** — `MIN_CONFIRMATION_SECONDS = 0.3`. A track must exist this long
  before its first announcement, filtering 1–2 frame tracker/NMS glitches. Backed by a
  `first_seen` field that, unlike `zone_since`, survives zone changes.
- **Stale-track cleanup** — `STALE_TRACK_FRAMES = 15`. Runs on empty frames too, so an
  object leaving abruptly doesn't leave its state behind.
- **Head-turn suppression** — when most tracked objects slide sideways together it reads as
  a camera pan, suppressing "leaving to X" for `HEAD_TURN_SUPPRESS_SECONDS`. Requires
  `HEAD_TURN_MIN_TRACKED = 3` so a lone object can't suppress its own transition.
- **"multiple X" wording** — group size is tallied from `track_history` *after* stale
  cleanup, not from the current frame's raw detections, so one flickered frame can't
  undercount. Composes with all phrase types (`still X`, `leaving to X`), not just first
  sightings.
- **Priority + tier per detection**, one winner per frame, offered to the TTS mailbox.
- **cv2 debug overlay** — zone tints, divider lines, per-object boxes and IDs, FPS. Gated
  on `user_data.use_frame`.

### Next

- Validate the confirmation gate, "multiple X" composition, and the flicker-tolerant tally
  **on real hardware**. All three were verified only against mocked-hailo harnesses.
- Decide whether `CONFIDENCE_THRESHOLD` (0.70) and the NMS thresholds in `app.py` need
  tuning to cut false positives at the source. Additive to the confirmation gate, not a
  replacement.
- Settle the final detection class list. Pluralization (`person` → `people`) is deferred
  until then and does not exist anywhere yet.

### Key files

`pipeline/callbacks.py`, `pipeline/app.py` (tracker parameters).

---

## 2. Depth estimation callbacks

**Role label:** depth owner
**Purpose:** Turn the raw depth map into left/center/right warning intensities for the
haptic channel.

### Status: in flux — deliberately not pinned down here

The depth post-processing approach is **being actively designed right now** in the
prototyping repo, and it is changing rapidly. An early mean-per-zone approach was already
abandoned as far too simplistic. **This document does not describe the current algorithm on
purpose** — anything written at that level of detail would be stale within days.

**Go to the prototyping repo for current detail:**
`/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps/hailo_apps/python/pipeline_apps/custom_depth_detection/`
(plus `SV-Docu/depth_backlog.md` and `SV-Docu/depth_estimation_handoff.md`).

### What is stable enough to write down

**The edge cases the design must handle** — these come from the requirements, not the
implementation, so they outlive any given algorithm:

| Edge case | Why depth alone fails |
|---|---|
| Thin objects (poles, cables, chair legs) | Networks smooth them away; a whole-zone aggregate averages them out |
| Blank / textureless walls | No texture → scale ambiguity → washed-out, uncertain output |
| Near walls the model renders as *far* | Same ambiguity, but failing dangerously rather than noisily |
| Drop-offs and descending stairs | A drop reads as "far", i.e. indistinguishable from open space |

**In this repo today:** `_process_real_depth` in
[`pipeline/callbacks.py`](../src/second_vision/pipeline/callbacks.py) is a **placeholder
awaiting replacement**, not the intended design. It emits `hazard=False` unconditionally.
Nothing else in `src/` implements depth.

**Units:** the SC-DepthV3 postprocess emits **relative** model units, not metres. Any
constant carrying an `_M` suffix in prototype code is still in those relative units.

### Next

1. Continue the design/validation loop in the prototyping repo.
2. Port into this repo. The port is **integration work, not algorithm development**: verify
   how to import it here, make it coexist with the haptic path, and apply this repo's coding
   standards before it lands.
3. Tune parameters against real captures.

> **Documentation gate.** Once the depth callbacks are ported with proper test cases and the
> user is satisfied with them, **updating these docs is the immediate next task** — this
> section, ARCHITECTURE.md's depth section, TASKS.md status, and any decision the port
> settled. The moment the approach stops moving is the moment it becomes worth writing down.

### Key files

`pipeline/callbacks.py` (`_process_real_depth`), `core/depth_utils.py` (placeholder).

---

## 3. TTS

**Role label:** TTS owner
**Purpose:** Speak the most important thing, one utterance at a time, without overwhelming
the user.

### Built and working

- **Serialized, interruptible speech** ([`workers/tts_worker.py`](../src/second_vision/workers/tts_worker.py)) —
  one espeak-ng subprocess at a time, watched to completion. A ~50 ms poll while speaking
  lets an urgent or decisively higher-priority item terminate the current utterance and take
  over. This fixed the layering bug where announcements spoke over each other.
- **`PriorityMailbox`** ([`core/priority.py`](../src/second_vision/core/priority.py)) —
  single slot that keeps the **higher-priority** of {stored, incoming}, ties to the newer
  item. Replaced `queue.Queue(maxsize=1)`, whose drop-on-full policy kept the *stale* item.
- **Composite priority score** — confidence + bbox area + zone weight + class weight, minus
  a decaying recency penalty. An urgency **tier** (`normal` / `urgent`) rides alongside.
- **Layered suppression** — a hard repeat floor (`MIN_REPEAT_INTERVAL = 10.0 s`), a soft
  recency penalty folded into the score, and a minimum inter-utterance gap
  (`MIN_UTTERANCE_GAP = 0.5 s`). **Urgent bypasses both the floor and the gap** — this is
  deliberate, not a bug.
- **Every threshold is a named constant in one file** so it can be tuned by ear on-device.
- **19 unit tests** in `tests/test_priority.py`, including worker serialization and
  preemption against a fake espeak — no hardware needed.

### Next — but scheduled last

Field testing and by-ear tuning is the **final** activity for the project, after the other
workstreams converge. See the [Field testing gate](#field-testing-gate) and
[FIELD-TESTING.md](FIELD-TESTING.md).

Also open: `W_APPROACH` is a reserved hook sitting at `0.0`. Wiring approach velocity
(bbox-area growth per second, using the `prev_area` that `track_history` can already carry)
is the most likely next scoring improvement — field testing scenario U1 is designed to
produce the evidence for whether it's needed.

### Key files

`core/priority.py` (all tunables), `workers/tts_worker.py`, `tests/test_priority.py`.

---

## 4. ESP32 vibration motors

**Role label:** firmware owner
**Purpose:** Drive three vibration motors from depth intensities over USB serial.

### Built and working — ESP32 side

The board is built and its firmware logic is **done**: binary protocol parsing, checksum
validation, `MOTOR_UPDATE` → 3× PWM, `HAZARD_ALERT` → pulsing override, `HEARTBEAT` →
watchdog reset, ACK responses, and the 3-second watchdog that zeroes the motors.

### Not built — RPi side

**UART communication between the Pi and the ESP32 is still being built.** In
[`workers/serial_worker.py`](../src/second_vision/workers/serial_worker.py) the loop
structure exists, but almost nothing underneath it does real work:

| Piece | Actual state |
|---|---|
| `_open_serial_port(config)` | Returns `None`. **There is no port path to open** — `SystemConfig` has no `serial_port` field, and `--serial-port` appears only in a `main.py` docstring, never registered as a CLI argument |
| `_send_packet()` | No-op; its debug print is commented out, so nothing is observable |
| `_check_ack()` | Returns `True` unconditionally, so the consecutive-failure counter can never trip |
| `_send_heartbeat()` | Bare `pass`. **`_pack_heartbeat()` has zero callers** — no heartbeat is ever sent, so the ESP32 watchdog would fire on every quiet period |
| `_pack_motor_update()`, `_pack_hazard_alert()` | Implemented and reached |

Treat this workstream as **early**, not nearly-done.

### Next

1. Register a `--serial-port` CLI argument and plumb the path through to the worker.
2. Extract the packing functions into `core/protocol.py` so they are unit-testable without
   hardware, and write those tests.
3. Real `pyserial` open / write / read with the documented wire settings.
4. Wire the orphaned `_pack_heartbeat()` into the idle branch.
5. Real ACK handling and a decided failure policy (the current handler is a `TODO` print).
6. Joint UART bring-up against the finished firmware.

### Key files

`workers/serial_worker.py`, `core/protocol.py` (planned), `main.py` (worker startup).

---

## 5. Control panel + mode switching

**Role label:** control owner
**Purpose:** Physical switches and knobs that change system behaviour live, including
switching which models run.

### Status

Hardware is **in progress**. Both halves of the software are unimplemented:

- **Config reader** ([`workers/config_reader.py`](../src/second_vision/workers/config_reader.py)) —
  the text-protocol parsing (`S:key:value`, `M:mode`, `B:event`) and type casting are
  written, but `_open_config_port` / `_read_line` / `_close_config_port` are all stubs.
  **There is a live bug**: line 27 calls `config.udpate(...)` (typo for `update`), which
  would `AttributeError` the first time a real `S:` line arrives.
  `--config-port` is never registered as a CLI argument, so `main.py`'s
  `getattr(app.options_menu, "config_port", None)` is always `None` and the config reader
  thread never starts.
- **Mode switching** ([`pipeline/app.py`](../src/second_vision/pipeline/app.py)) —
  `trigger_rebuild()` exists and correctly defers through `GLib.idle_add`, which is the part
  that is easy to get wrong. But `get_pipeline_string()` and `_connect_callback()` do **not**
  branch on `config.pipeline_mode`, so a rebuild today just recreates the same fixed
  dual-branch pipeline.

### Next

1. Fix the `config.udpate` typo.
2. Register `--config-port`, plumb it, and implement real `pyserial` reads.
3. Make `get_pipeline_string()` / `_connect_callback()` mode-aware
   (detection-only / depth-only / both).
4. Verify the mode-switch announcement is heard before the rebuild blackout.

### Key files

`workers/config_reader.py`, `core/config.py`, `pipeline/app.py`.

---

## Field testing gate

**This happens last.** Once detection, depth, haptics and the control panel are converged,
the team runs the scenarios in [FIELD-TESTING.md](FIELD-TESTING.md) — schools, malls,
streets; best case, worst case, edge cases — and tunes the priority constants by ear.

Two standing caveats while testing:

- The device is **under test**. It is not to be relied on for real obstacle avoidance, and a
  visually-impaired tester keeps their white cane and has a dedicated spotter.
- Until depth lands, unclassifiable obstacles (poles, glass doors, curbs) are **silent** —
  that is expected, and noting which ones felt dangerous to miss is itself a deliverable for
  the depth workstream.

---

## Running the system

```bash
./scripts/run.sh --mock                     # no hardware needed
./scripts/run.sh --input usb                # real pipeline
./scripts/run.sh --input /dev/video0 --width 640 --height 480 --use-frame
```

`--headless`, `--debug-display`, `--serial-port` and `--config-port` appear in older docs
but are **not registered yet** — they will fail argparse. They are listed as tasks in the
workstreams above.
