# CLAUDE.md — AI Agent Instructions for Second Vision

> **Project**: Second Vision — IoT-Based Smart Glass for the Visually Impaired  
> **Hardware**: Raspberry Pi 5 + Hailo-8 AI Hat+ (26 TOPS) + ESP32 + vibration motors  
> **Stack**: Python 3.13, GStreamer, HailoRT, pyttsx3/espeak-ng, pyserial

---

## Project Context

Second Vision is an assistive device for visually impaired users. It runs real-time object detection (YOLOv8n) and depth estimation (SC-DepthV3) on a head-mounted camera, providing:
- **Audio feedback** via TTS (espeak-ng) — announces detected objects with spatial zones ("person left", "car center")
- **Haptic feedback** via 3 vibration motors (L/C/R on headband) — proportional to obstacle proximity from depth estimation
- **Physical control panel** (Arduino) — switches/knobs for runtime configuration

The system uses the `hailo_apps` framework as an **installed library** (`pip install hailo-apps`). We extend it by subclassing, never by modifying library code.

---

## Repository Structure

```
second-vision-repo/
├── my_hailo_env/                  ← Python venv (hailo_apps installed here)
├── src/
│   └── second_vision/
│       ├── __init__.py
│       ├── main.py                ← Entry point: python3 src/second_vision/main.py
│       ├── pipeline/
│       │   ├── app.py             ← SecondVisionApp (extends GStreamerParallelApp)
│       │   └── callbacks.py       ← on_det_frame, on_depth_frame
│       ├── workers/
│       │   ├── tts_worker.py      ← TTS consumer (espeak-ng + cooldown)
│       │   ├── serial_worker.py   ← ESP32 motor commands (binary protocol)
│       │   └── config_reader.py   ← Arduino settings reader
│       ├── core/
│       │   ├── config.py          ← SystemConfig (thread-safe shared state)
│       │   ├── protocol.py        ← Binary packet encode/decode
│       │   └── depth_utils.py     ← Zone splitting, proximity, hazard detection
│       └── mock/
│           └── data_generator.py  ← Fake data for --mock mode
├── scripts/
│   └── run.sh                     ← Activates venv + runs main.py
├── config/
│   └── defaults.yaml              ← Default thresholds, zones, baud rate
├── tests/
├── documentation/                 ← Project documentation 
│   ├── PROJECT.md                 ← Project overview, team, hardware specs
│   ├── ARCHITECTURE.md            ← System architecture, data flow, protocols
│   ├── DECISIONS.md               ← All architectural decisions with rationale
│   ├── PLAN.md                    ← 10-phase implementation plan
│   └── TASKS.md                   ← Task breakdown
└── pyproject.toml
```

---

## Critical Rules

### 1. Never modify the hailo_apps library

The `hailo_apps` package lives in `my_hailo_env/lib/python3.13/site-packages/hailo_apps/`. It is a third-party library. **Do not edit it.** Extend behavior by subclassing in `src/second_vision/`.

```python
# CORRECT: Subclass in your project
from hailo_apps.python.pipeline_apps.custom_depth_detection.sv_pipeline_v3 import GStreamerParallelApp

class SecondVisionApp(GStreamerParallelApp):
    def get_pipeline_string(self):
        # Your custom pipeline logic
        ...

# WRONG: Editing files in site-packages
```

### 2. Interface contracts are sacred

Queue dictionaries are the API between components. **Never change these keys without team agreement.**

```python
# Detection dict (tts_queue):
{"label": str, "zone": str, "confidence": float}

# Depth dict (serial_queue):
{"left": int, "center": int, "right": int, "hazard": bool, "hazard_severity": int}

# Mode announcement (tts_queue):
{"announce": str}
```

### 3. Stub pattern — replace internals, keep interface

Every module has a clearly marked `# STUBS BELOW` section. When implementing real functionality:
- **Replace only the stub functions** (prefixed with `_`)
- **Never change** the main worker loop, queue reading logic, or config checking
- The worker function signature is the contract: `def tts_worker(user_data, config):`

### 4. Threading rules

- All workers are `threading.Thread(daemon=True)` — they die when main exits
- All workers check `user_data.shutdown_event.is_set()` in their loop
- All workers use `queue.get(timeout=1.0)` — never blocking get
- **Never block the GStreamer callback** — use `put_nowait()`, catch `queue.Full`, drop the frame
- GStreamer state changes (pipeline rebuild) MUST go through `GLib.idle_add()`, never called directly from worker threads

### 5. Environment setup

Before running, the system needs:
1. Virtual environment activated: `source my_hailo_env/bin/activate`
2. Hailo environment loaded: `/usr/local/hailo/resources/.env`
3. Project source on PYTHONPATH: `export PYTHONPATH="$(pwd)/src:$PYTHONPATH"`

The `scripts/run.sh` script handles all of this.

---

## Coding Conventions

### Python
- Python 3.13 (RPi5 Trixie default)
- Type hints on function signatures
- Docstrings on all public functions
- f-strings for string formatting
- `get_logger(__name__)` for logging (from `hailo_apps.python.core.common.hailo_logger`)

### Naming
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private/stub functions: `_prefixed`

### Imports
- Standard library first, then third-party, then local
- Conditional imports with try/except for optional dependencies (mock mode)
- Always import from `hailo_apps.python.core...` (full path), not relative

### Error handling
- Workers: catch exceptions in the loop, log, continue — never let a worker thread crash
- Callbacks: catch exceptions, log, return — never block the pipeline
- Serial: handle `serial.SerialException`, `serial.SerialTimeoutError`

---

## Key Library Classes (hailo_apps)

| Class | Location | Purpose |
|---|---|---|
| `GStreamerApp` | `hailo_apps.python.core.gstreamer.gstreamer_app` | Base GStreamer application |
| `GStreamerParallelApp` | `hailo_apps.python.pipeline_apps.custom_depth_detection.sv_pipeline_v3` | Dual-branch pipeline (depth + detection) |
| `app_callback_class` | `hailo_apps.python.core.gstreamer.gstreamer_app` | Base user data class for callbacks |
| `DISPLAY_PIPELINE` | `hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines` | Display sink builder |
| `INFERENCE_PIPELINE` | `hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines` | Hailo inference element builder |
| `TRACKER_PIPELINE` | `hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines` | Object tracker pipeline element |

### Key methods to override in SecondVisionApp

| Method | Purpose | Notes |
|---|---|---|
| `get_pipeline_string()` | Returns GStreamer pipeline string | Mode-aware: detection/depth/both |
| `_connect_callback()` | Wires up callback functions to identity elements | Mode-aware callback connection |
| `trigger_rebuild()` | Schedules pipeline rebuild via `GLib.idle_add` | Called by config reader on mode switch |

---

## Running the Project

```bash
# Development (with display)
./scripts/run.sh --input usb

# With ESP32 connected
./scripts/run.sh --input usb --serial-port /dev/ttyUSB0

# Full system (production, headless)
./scripts/run.sh --input usb --serial-port /dev/ttyUSB0 --config-port /dev/ttyACM0 --headless

# Mock mode (no hardware needed — for development on any machine)
./scripts/run.sh --mock
```

---

## Testing

- `--mock` flag runs the full system with fake data — no hardware needed
- Each worker prints `[STUB]` prefixed messages when using stub implementations
- Run `python3 -m pytest tests/` for unit tests
- Protocol encoding/decoding can be tested without any hardware
- `SystemConfig` can be tested without any hardware

---

## Reference Documentation

| Document | Contents |
|---|---|
| `documentation/ARCHITECTURE.md` | System architecture, data flow, diagrams |
| `documentation/DECISIONS.md` | All architectural decisions with rationale |
| `documentation/PLAN.md` | Implementation phases and milestones |
| `documentation/TASKS.md` | Task breakdown and assignments |
| `documentation/PROJECT.md` | Project overview, team, hardware |
