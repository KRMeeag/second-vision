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
│   ├── second_vision/
│   │   ├── main.py                ← Entry point: python3 src/second_vision/main.py
│   │   ├── main2.py               ← Temporary smoke-test script (deletion pending)
│   │   ├── pipeline/
│   │   │   ├── app.py             ← SecondVisionApp (extends GStreamerApp)
│   │   │   ├── callbacks.py       ← on_det_frame (built), on_depth_frame (placeholder)
│   │   │   └── TBR-*.py           ← Draft/staging files, unwired — nothing imports them
│   │   ├── workers/
│   │   │   ├── tts_worker.py      ← Serialized, interruptible espeak-ng
│   │   │   ├── serial_worker.py   ← Packing built; serial I/O still stubbed
│   │   │   └── config_reader.py   ← Parsing built; serial I/O still stubbed
│   │   ├── core/
│   │   │   ├── config.py          ← SystemConfig (thread-safe shared state)
│   │   │   ├── priority.py        ← PriorityMailbox + all TTS tunables
│   │   │   └── depth_utils.py     ← Placeholder (real work in the prototyping repo)
│   │   └── mock/
│   │       └── data_generator.py  ← Fake data for --mock mode
│   └── custom_depth_detection/    ← Older prototype drafts, not part of the app
├── scripts/
│   ├── run.sh                     ← Venv + Hailo env + PYTHONPATH, args passed through
│   ├── env.sh                     ← Venv activation only
│   └── sv-main.sh                 ← Fixed-argument launcher
├── tests/
│   └── test_priority.py           ← 19 tests (mailbox, scoring, tiers, preemption)
├── documentation/                 ← Project documentation (team source of truth)
│   ├── PROJECT.md                 ← Project overview, team, hardware specs
│   ├── ARCHITECTURE.md            ← System architecture, data flow, protocols
│   ├── DECISIONS.md               ← All architectural decisions with rationale
│   ├── PLAN.md                    ← Workstream plan (NOT phases — see below)
│   ├── TASKS.md                   ← Status board — source of truth for what's done
│   └── FIELD-TESTING.md           ← Real-world tuning scenarios (final activity)
├── .agents/                       ← Session handoffs. LOCAL to this machine only
└── pyproject.toml
```

Notes that matter:

- **`core/protocol.py` does not exist yet.** Packet packing currently lives inline in
  `workers/serial_worker.py`. Extracting it is a planned task.
- **`config/` is empty.** There is no `defaults.yaml`; `SystemConfig` defaults are hardcoded.
- **`.agents/` is local to this machine** and is not shared with the team. It is the
  narrative record (why, decisions, instructive failures). `documentation/` is what the team
  and their agents actually read.
- **`.agents/temp-folder-do-not-read/` is exactly what it says.** Do not read or import from
  it; it holds scratch/staging material that is not part of the application.

---

## Workstreams

Five workstreams run **concurrently**, each driven by a different team member whose agent
reads these docs cold. Read [documentation/PLAN.md](documentation/PLAN.md) for the full
picture and [documentation/TASKS.md](documentation/TASKS.md) for status.

| Workstream | State |
|---|---|
| Object detection callbacks | Substantially built; recent fixes unvalidated on hardware |
| Depth estimation callbacks | Placeholder here; approach still being designed in the prototyping repo |
| TTS | Built, including priority/preemption. Tuning scheduled last |
| ESP32 vibration motors | ESP32 side done; **RPi-side serial layer unimplemented** |
| Control panel + mode switching | Stubbed; hardware in progress |

`documentation/ARCHITECTURE.md` carries a **shared-files map** showing which workstreams
touch which files. It flags collision paths so you can coordinate — it is **not** an
ownership gate, and it does not forbid editing anything.

---

## Critical Rules

### 1. Never modify the hailo_apps library

The `hailo_apps` package lives in `my_hailo_env/lib/python3.13/site-packages/hailo_apps/`. It is a third-party library. **Do not edit it.** Extend behavior by subclassing in `src/second_vision/`.

```python
# CORRECT: Subclass GStreamerApp from the installed library
from hailo_apps.python.core.gstreamer.gstreamer_app import GStreamerApp

class SecondVisionApp(GStreamerApp):
    def get_pipeline_string(self):
        # Dual-branch (depth + detection) pipeline, built inline
        ...

# WRONG: Editing files in site-packages
```

> **`GStreamerParallelApp` is NOT importable here.** It exists only in the prototyping repo;
> the installed `hailo_apps` ships no `custom_depth_detection` package. That is why
> `SecondVisionApp` extends `GStreamerApp` and builds the dual-branch pipeline itself.

### 2. Interface contracts are sacred

Queue dictionaries are the API between components. **Never change these keys without team agreement.**

```python
# Detection (tts_queue) — priority/tier required, phrase optional:
{"label": str, "zone": str, "confidence": float,
 "priority": float, "tier": "normal" | "urgent", "phrase": str}

# Depth dict (serial_queue):
{"left": int, "center": int, "right": int, "hazard": bool, "hazard_severity": int}

# Mode announcement (tts_queue) — treated as +inf priority, urgent tier:
{"announce": str}
```

**`tts_queue` is a `PriorityMailbox`, not a `queue.Queue`.** Use `offer()` / `take()` /
`peek()` — `put_nowait()` and `get()` do not exist on it. It keeps the higher-priority of
{stored, incoming}. `serial_queue` remains a plain FIFO `queue.Queue`.

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

### 6. Cross-reference the hailo-apps prototyping repo for vision tasks

The hailo-apps repo at `/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps` is the **prototyping workbench** where the vision pipeline was developed and validated. This is only visible if this session is in the Raspberry Pi. If not, then this repo cannot be found. It contains the original `hailo_apps` library code alongside custom Second Vision work.

**When any task involves object detection, depth estimation, GStreamer pipeline composition, HailoRT APIs, or inference post-processing**, you MUST consult this repo before writing or architecting code. Follow this lookup order:

1. **Documentation first** — read the relevant docs in the hailo-apps repo:
   - `SV-Docu/` — Second Vision-specific design docs (depth post-processing spec, architecture, decisions)
   - `.hailo/instructions/` — GStreamer pipeline patterns, coding standards
   - `.hailo/toolsets/` — GStreamer element catalog, HailoRT API reference, COCO class list
   - `.hailo/memory/` — Known pitfalls, camera/display patterns, pipeline optimization
   - `.hailo/skills/` — Step-by-step build guides (especially `hl-build-pipeline-app.md`)

2. **Custom prototypes second** — the user's own implemented code in:
   - `hailo_apps/python/pipeline_apps/custom_depth_detection/` — all `sv_pipeline_v*.py`, `depth_utils.py`, `callbacks.py`, and the calibration/demo tools
   - These represent **the current progress of the user** that will soon be ported or adapted for this repository.

3. **Library source last** — if behavior of a library class is unclear, read the source in:
   - `hailo_apps/python/core/` — `GStreamerApp`, `app_callback_class`, helper pipelines
   - `hailo_apps/python/pipeline_apps/` — other upstream pipeline examples for reference patterns

**Never invent GStreamer pipeline strings or HailoRT API usage** when the prototyping repo already has established working implementations to reference. However, if the required functionality is not found in the prototyping repo, you may invent GStreamer pipeline strings or HailoRT API usage, but you MUST consult the hailo-apps documentation first. Never modify code in `hailo_apps` directly.

### 7. Verify before you trust — including this document

Documentation records **what the developers intended**. Code records what is true. These
drift apart, and this project has already been bitten by it repeatedly.

- **Check the current state of a file before acting on any description of it.** Read the
  function bodies. **Confirm the callers exist** — something implemented but never called is
  not a working feature. (Real example: `_pack_heartbeat()` is fully written and has zero
  callers, so no heartbeat is ever sent.)
- **When two sources disagree, never silently pick one.** Surface the disagreement to the
  user and say which one the code actually supports. Quietly resolving a conflict between a
  draft and production code has already shipped a real bug here.
- **Do not privilege an inherited claim over what you can verify right now.** Not from these
  docs, not from `.agents/` handoffs, not from a previous session's conclusion — and not from
  your own earlier statement in the same session. A claim is not verified merely because it
  is written down.
- **Report the gap.** If a doc and the code disagree, fix or flag the doc as part of the
  work; don't leave the next reader to rediscover it.

### 8. Documentation checkpoints — keep the team in sync

Five workstreams run concurrently and each teammate's agent reads only these files. An
undocumented decision is **invisible to the other four**, and that invisibility is exactly
how the current drift formed.

- **After any major feature implementation or major decision, ask the user whether it should
  be documented** before moving on. Make it a question, not an assumption — the user decides
  what is significant enough to record.
- **Ask concretely.** Name the specific decision and the file it would land in
  (`DECISIONS.md` for a settled choice, `ARCHITECTURE.md` for a contract or data flow,
  `TASKS.md` for status, `PLAN.md` for scope) — not a generic "should I update the docs?".
- **Post-port documentation gate for depth**: once the depth estimation callbacks have been
  ported successfully, with proper test cases, and the user has confirmed satisfaction,
  **updating the documentation is your immediate next task.** Do not wait to be asked, and do
  not treat the port as finished until it is written down. The depth section of
  ARCHITECTURE.md is deliberately left unpinned until that moment.

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

| Class                  | Location                                                                | Purpose                                  |
| ---------------------- | ----------------------------------------------------------------------- | ---------------------------------------- |
| `GStreamerApp`         | `hailo_apps.python.core.gstreamer.gstreamer_app`                        | Base GStreamer application — **this is what `SecondVisionApp` extends** |
| `app_callback_class`   | `hailo_apps.python.core.gstreamer.gstreamer_app`                        | Base user data class for callbacks       |
| `_internal_callback_wrapper` | `hailo_apps.python.core.gstreamer.gstreamer_app`                  | Wraps a callback for frame counting/watchdog |
| `DISPLAY_PIPELINE`     | `hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines`           | Display sink builder                     |
| `INFERENCE_PIPELINE`   | `hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines`           | Hailo inference element builder          |
| `TRACKER_PIPELINE`     | `hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines`           | Object tracker pipeline element          |

### Key methods to override in SecondVisionApp

| Method                  | Purpose                                          | Notes                                  |
| ----------------------- | ------------------------------------------------ | -------------------------------------- |
| `get_pipeline_string()` | Returns GStreamer pipeline string                | Mode-aware: detection/depth/both       |
| `_connect_callback()`   | Wires up callback functions to identity elements | Mode-aware callback connection         |
| `trigger_rebuild()`     | Schedules pipeline rebuild via `GLib.idle_add`   | Called by config reader on mode switch |

---

## Running the Project

```bash
# Mock mode (no hardware needed — for development on any machine)
./scripts/run.sh --mock

# Development (with display)
./scripts/run.sh --input usb
./scripts/run.sh --input /dev/video0 --width 640 --height 480 --use-frame
```

`run.sh` activates the venv, sources the Hailo env, sets `PYTHONPATH`, and passes every
argument through to `main.py`.

> **Flags that do NOT exist yet**: `--serial-port`, `--config-port`, `--headless`,
> `--debug-display`. They appear in older documentation but were never registered, so
> passing them fails argparse. Registering them is tracked in
> [documentation/TASKS.md](documentation/TASKS.md).

---

## Testing

- `--mock` flag runs the full system with fake data — no hardware needed
- Each worker prints `[STUB]` prefixed messages when using stub implementations
- **Run tests with `poetry run pytest tests/ -v`** — the system python and `my_hailo_env`
  both lack pytest; only the Poetry env has it
- `tests/test_priority.py` (19 tests) covers the mailbox, scoring, tiers, preemption, and
  worker serialization against a fake espeak — no hardware needed
- Protocol encoding/decoding and `SystemConfig` can be tested without any hardware
- For logic that runs inside a hailo callback, a mocked-hailo harness (duck-typed
  bbox/detection/ROI/track-id objects) has repeatedly caught real bugs that eyeballing
  missed. When fixing a bug, reconstruct the pre-fix state and confirm your test actually
  fails against it before trusting that it passes against the fix.

---

## Reference Documentation

### This repo

| Document                        | Contents                                                         |
| ------------------------------- | ---------------------------------------------------------------- |
| `documentation/PLAN.md`         | The five workstreams: state, next steps, blockers                |
| `documentation/TASKS.md`        | **Status source of truth** — tick boxes here as work lands       |
| `documentation/ARCHITECTURE.md` | Architecture, data flow, contracts, **shared-files map**         |
| `documentation/DECISIONS.md`    | Architectural decisions with rationale; superseded ones retained |
| `documentation/PROJECT.md`      | Project overview, team, hardware                                 |
| `documentation/FIELD-TESTING.md`| Real-world tuning scenarios — the final activity                 |

`.agents/*.md` are session handoffs: rich narrative context, but **local to this machine**
and not the status board. Where a handoff and `TASKS.md` disagree, verify against the code.

### hailo-apps prototyping repo (`/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`)

Consult for all vision pipeline, object detection, and depth estimation tasks (see Rule 6).

| Path                                                      | Contents                                                                         |
| --------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `SV-Docu/depth_estimation_handoff.md`                     | Depth post-processing design spec (edge cases, perf constraints)                 |
| `SV-Docu/depth_backlog.md`                                | Current depth priorities — what's done, blocking, and queued                     |
| `SV-Docu/ARCHITECTURE.md`                                 | Prototype system architecture                                                    |
| `SV-Docu/DECISIONS.md`                                    | Architectural decisions from prototyping phase                                   |
| `.hailo/README.md`                                        | Master index of all hailo-apps knowledge                                         |
| `.hailo/instructions/gstreamer-pipelines.md`              | GStreamer pipeline composition patterns                                          |
| `.hailo/instructions/coding-standards.md`                 | Import rules, logging, HEF resolution, parsers, error handling                   |
| `.hailo/toolsets/gstreamer-elements.md`                   | Hailo GStreamer element catalog                                                  |
| `.hailo/toolsets/core-framework-api.md`                   | `GStreamerApp`, parsers, `HailoInfer` API                                        |
| `.hailo/toolsets/yolo-coco-classes.md`                    | COCO 80-class label set — relevant to the class-list decision                    |
| `.hailo/memory/common_pitfalls.md`                        | Known import errors, signal handling, multiprocessing gotchas                    |
| `hailo_apps/python/pipeline_apps/custom_depth_detection/` | All prototype pipeline versions + depth math + tooling                           |

Within `custom_depth_detection/`, the files most worth knowing:

| File                            | Purpose                                                             |
| ------------------------------- | ------------------------------------------------------------------- |
| `depth_utils.py`                | The depth math — **actively changing**                              |
| `test_depth_utils.py`           | Unit tests for the detectors; runs with plain `python3`, no pytest  |
| `sv_dual_callback_withdepth.py` | Live integration reference (depth + detection wired into a pipeline) |
| `capture.py`                    | `SV_CAPTURE=1` — dumps labelled raw depth frames to `.npy`          |
| `score_corpus.py`               | Offline batch scoring of a captured corpus; no hardware needed      |
| `verify_scene.py`               | Replays one frame (captured or synthetic) through the real chain    |
| `calibration.py`                | `SV_CALIBRATE=1` — logs per-zone stats to CSV for threshold tuning  |
| `depth_view.py`                 | Rendering, deliberately separated so it can run off-device          |
