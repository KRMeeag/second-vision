# PLAN.md — Implementation Plan

> Phased development plan for Second Vision.  
> Each phase is independently testable. The system runs end-to-end from Phase 1.

---

## Phase Overview

| Phase | Name | Deliverables | Test Command | Dependencies |
|---|---|---|---|---|
| **1** | Scaffolding | Project structure, `main.py`, `run.sh`, `--mock` mode | `./scripts/run.sh --mock` | None |
| **2** | Pipeline Subclass | `SecondVisionApp` extends `GStreamerParallelApp` | `./scripts/run.sh --input usb` | Phase 1, RPi5+Hailo |
| **3** | Callbacks | `on_det_frame`, `on_depth_frame` with queue output | Pipeline runs, prints to console | Phase 2 |
| **4** | TTS Worker | espeak-ng with cooldown logic | Hear announcements from detections | Phase 3 |
| **5** | Depth Utilities | Zone splitting, proximity curves, hazard detection | Print L/C/R values to console | Phase 3 |
| **6** | Serial Protocol | Binary packet encoding/decoding + serial writer | ESP32 receives motor commands | Phase 5 |
| **7** | Config Reader | Arduino text protocol + SystemConfig updates | Knob changes motor strength live | Phase 1 |
| **8** | Pipeline Switching | Mode-aware `get_pipeline_string()` + `_connect_callback()` | Toggle switch changes pipeline | Phases 2, 7 |
| **9** | Hazard Detection | Ground plane departure analysis | Console warnings on stairs/ledges | Phase 5 |
| **10** | Headless + Debug | `--headless` flag, OpenCV debug composite window | Runs without display | Phase 2 |

---

## Phase 1: Scaffolding (Foundation)

**Goal**: Full project structure with `--mock` mode working end-to-end.

### Files to create

| File | Status | Notes |
|---|---|---|
| `src/second_vision/__init__.py` | New | Empty |
| `src/second_vision/main.py` | New | Orchestrator with mock/real mode switch |
| `src/second_vision/core/__init__.py` | New | Empty |
| `src/second_vision/core/config.py` | New | `SystemConfig` (full implementation, not stub) |
| `src/second_vision/workers/__init__.py` | New | Empty |
| `src/second_vision/workers/tts_worker.py` | New | Stub: prints `[TTS STUB]` messages |
| `src/second_vision/workers/serial_worker.py` | New | Stub: prints `[SERIAL STUB]` messages |
| `src/second_vision/workers/config_reader.py` | New | Stub: no-op loop |
| `src/second_vision/pipeline/__init__.py` | New | Empty |
| `src/second_vision/pipeline/callbacks.py` | New | Stub: pass-through |
| `src/second_vision/mock/__init__.py` | New | Empty |
| `src/second_vision/mock/data_generator.py` | New | Full implementation of mock generators |
| `scripts/run.sh` | New | Venv activation + env setup + python launch |

### Validation

```bash
./scripts/run.sh --mock
# Expected output:
# [MAIN] TTS worker started
# [MAIN] Serial worker started
# [MOCK] Detection generator started (2s interval)
# [MOCK] Depth generator started (100ms interval)
# [SERIAL STUB] → aa 01 80 40 c0 01
# [TTS STUB] 🔊 'person center'
# ^C
# [MAIN] Done.
```

---

## Phase 2: Pipeline Subclass

**Goal**: `SecondVisionApp` launches the existing dual pipeline without breaking anything.

### Files to create/modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/pipeline/app.py` | New | Subclass `GStreamerParallelApp`, add custom CLI args |

### Validation

```bash
./scripts/run.sh --input rawusb:///dev/video0 --width 640 --height 360 --frame-rate 25
# Expected: Two display windows (depth + detection), same as current behavior
```

---

## Phase 3: Callbacks with Queue Output

**Goal**: Detection and depth callbacks extract data and push into queues.

### Files to modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/pipeline/callbacks.py` | Implement | Real hailo buffer parsing, zone detection |

### Validation

```bash
./scripts/run.sh --input usb
# Expected: [TTS STUB] messages appear when objects are detected
# Expected: [SERIAL STUB] messages appear with hex packets
```

---

## Phase 4: TTS Worker

**Goal**: Real audio announcements with cooldown logic.

### Files to modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/workers/tts_worker.py` | Replace `_speak()` | Call espeak-ng via subprocess |

### Validation

```bash
./scripts/run.sh --input usb
# Expected: Hear "person center" from speaker when person detected
# Expected: 3-second silence before same label-zone repeats
```

### Can be developed with `--mock` (no hardware needed)

---

## Phase 5: Depth Utilities

**Goal**: Zone splitting, proximity → intensity mapping.

### Files to create

| File | Action | Notes |
|---|---|---|
| `src/second_vision/core/depth_utils.py` | New | `compute_zone_intensities()`, `compute_proximity()` |

### Validation

```bash
./scripts/run.sh --input usb
# Expected: [SERIAL STUB] shows varying L/C/R values based on scene
```

---

## Phase 6: Serial Protocol + Writer

**Goal**: ESP32 receives real binary motor commands.

### Files to create/modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/core/protocol.py` | New | `pack_motor_update()`, `pack_hazard_alert()`, `parse_ack()` |
| `src/second_vision/workers/serial_worker.py` | Replace stubs | Real `pyserial` open/send/check |

### Validation

```bash
./scripts/run.sh --input usb --serial-port /dev/ttyUSB0
# Expected: ESP32 drives motors proportionally to scene depth
```

### Protocol can be tested with `--mock` (no camera needed)

---

## Phase 7: Config Reader

**Goal**: Arduino knob/switch changes affect system behavior in real-time.

### Files to modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/workers/config_reader.py` | Replace stubs | Real `pyserial` for Arduino |

### Validation

```bash
./scripts/run.sh --input usb --config-port /dev/ttyACM0
# Expected: Turning potentiometer changes motor vibration strength
```

---

## Phase 8: Dynamic Pipeline Switching

**Goal**: Physical toggle switch changes which AI models are running.

### Files to modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/pipeline/app.py` | Implement mode builders | `_build_detection_only()`, `_build_depth_only()`, `_build_dual()` |

### Validation

```bash
./scripts/run.sh --input usb --config-port /dev/ttyACM0
# Expected: Toggle switch → TTS says "detection mode" → pipeline rebuilds → only detection runs
```

---

## Phase 9: Hazard Detection

**Goal**: Ground plane departure analysis warns about stairs/ledges.

### Files to modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/core/depth_utils.py` | Add `detect_ground_hazard()` | Gradient spike analysis on bottom 25% of depth map |
| `src/second_vision/pipeline/callbacks.py` | Call hazard detection in depth callback | Set `hazard=True` in serial_queue dict |

### Validation

```bash
# Point camera at staircase going down
# Expected: [HAZARD] warning in console, distinct motor vibration pattern
```

---

## Phase 10: Headless + Debug Display

**Goal**: Production headless mode and development debug overlay.

### Files to create/modify

| File | Action | Notes |
|---|---|---|
| `src/second_vision/pipeline/app.py` | Add `--headless` sink switching | Replace `DISPLAY_PIPELINE` with `fakesink` |
| `src/second_vision/debug/display.py` | New | OpenCV composite window showing all system state |

### Validation

```bash
./scripts/run.sh --input usb --headless
# Expected: No display windows, but TTS and serial still work

./scripts/run.sh --input usb --debug-display
# Expected: Single OpenCV window with depth map, detections, motor values, TTS log
```

---

## Parallel Development Tracks

Some phases can be developed simultaneously by different team members:

```
            Phase 1 (scaffolding)
                    │
           ┌────────┼────────┐
           ▼        ▼        ▼
        Phase 2   Phase 4   Phase 7
       (pipeline) (TTS)    (config)
           │        │        │
           ▼        │        │
        Phase 3     │        │
       (callbacks)  │        │
           │        │        │
     ┌─────┼────────┘        │
     ▼     ▼                 │
  Phase 5  Phase 4           │
  (depth)  (TTS done)        │
     │                       │
     ▼                       │
  Phase 6                    │
  (serial)                   │
     │                       │
     ├───────────────────────┘
     ▼
  Phase 8 (pipeline switching)
     │
     ▼
  Phase 9 (hazard detection)
     │
     ▼
  Phase 10 (headless + debug)
```

After Phase 1, three independent tracks can proceed:
- **Track A** (needs hardware): Phase 2 → 3 → 5 → 6 → 8 → 9 → 10
- **Track B** (no hardware needed): Phase 4 (TTS) — use `--mock`
- **Track C** (no hardware needed): Phase 7 (Config reader) — use `--mock`
