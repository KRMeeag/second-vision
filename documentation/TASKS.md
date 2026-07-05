# TASKS.md — Task Breakdown & Assignments

> Granular task list derived from the implementation plan.  
> Update status as work progresses.

---

## Status Legend

- `[ ]` Not started
- `[/]` In progress
- `[x]` Complete
- `[~]` Blocked / waiting on dependency

---

## Phase 1: Scaffolding

- [ ] Create `src/second_vision/__init__.py`
- [ ] Create `src/second_vision/core/__init__.py`
- [ ] Create `src/second_vision/workers/__init__.py`
- [ ] Create `src/second_vision/pipeline/__init__.py`
- [ ] Create `src/second_vision/mock/__init__.py`
- [ ] Implement `src/second_vision/core/config.py` — `SystemConfig` class
- [ ] Implement `src/second_vision/mock/data_generator.py` — mock detection + depth generators
- [ ] Create `src/second_vision/workers/tts_worker.py` — stub version (prints to console)
- [ ] Create `src/second_vision/workers/serial_worker.py` — stub version (prints hex packets)
- [ ] Create `src/second_vision/workers/config_reader.py` — stub version (no-op loop)
- [ ] Create `src/second_vision/pipeline/callbacks.py` — stub pass-through
- [ ] Create `src/second_vision/main.py` — orchestrator with `--mock` support
- [ ] Create `scripts/run.sh` — venv activation + env setup + launch
- [ ] Test: `./scripts/run.sh --mock` runs end-to-end with stub output

---

## Phase 2: Pipeline Subclass

- [ ] Create `src/second_vision/pipeline/app.py` — `SecondVisionApp` class
  - [ ] Extend `GStreamerParallelApp`
  - [ ] Add custom CLI arguments (`--serial-port`, `--config-port`, `--headless`, `--debug-display`)
  - [ ] Override `get_pipeline_string()` (initially just call super)
  - [ ] Override `_connect_callback()` (initially just call super)
- [ ] Update `main.py` to use `SecondVisionApp` in pipeline mode
- [ ] Test: `./scripts/run.sh --input usb` shows two display windows (matches current behavior)

---

## Phase 3: Callbacks

- [ ] Implement `on_det_frame()` in `callbacks.py`
  - [ ] Extract hailo detections from buffer
  - [ ] Compute zone from bbox center (left/center/right)
  - [ ] `put_nowait()` detection dict into `tts_queue`
- [ ] Implement `on_depth_frame()` in `callbacks.py`
  - [ ] Extract depth mask from buffer
  - [ ] Compute basic zone averages (stub — later replaced by `depth_utils`)
  - [ ] `put_nowait()` depth dict into `serial_queue`
- [ ] Wire callbacks in `SecondVisionApp._connect_callback()`
- [ ] Test: `./scripts/run.sh --input usb` prints `[TTS STUB]` and `[SERIAL STUB]` messages

---

## Phase 4: TTS Worker (Can develop with `--mock`)

- [ ] Replace `_speak()` stub with real espeak-ng call
  - [ ] Option A: `subprocess.Popen(["espeak-ng", "-s", "160", text])`
  - [ ] Option B: `pyttsx3.init()` + `engine.say()` + `engine.runAndWait()`
- [ ] Add zone boundary hysteresis (prevent jitter at 33%/66% boundaries)
- [ ] Add detection priority ordering (closer/higher confidence first)
- [ ] Test: `./scripts/run.sh --mock` produces audio from speaker
- [ ] Test: `./scripts/run.sh --input usb` announces real detections
- [ ] Tune cooldown duration (start at 3s, adjust based on testing)

---

## Phase 5: Depth Utilities

- [ ] Create `src/second_vision/core/depth_utils.py`
  - [ ] `compute_zone_intensities(depth_data, width)` — 25/50/25 split
  - [ ] `compute_proximity(zone_slice)` — inverse/exponential curve → 0-255
  - [ ] Apply outlier filtering (percentile-based)
- [ ] Update `on_depth_frame()` to use `depth_utils` instead of inline stub
- [ ] Test: Serial stub shows varying L/C/R values that respond to scene changes
- [ ] Tune proximity curve (exponential vs inverse-square)

---

## Phase 6: Serial Protocol + Writer (Protocol testable without hardware)

- [ ] Create `src/second_vision/core/protocol.py`
  - [ ] `pack_motor_update(left, center, right) → bytes`
  - [ ] `pack_hazard_alert(severity, pattern) → bytes`
  - [ ] `pack_heartbeat() → bytes`
  - [ ] `parse_ack(data) → bool`
  - [ ] `compute_checksum(data) → int`
- [ ] Write unit tests for protocol encoding/decoding (`tests/test_protocol.py`)
- [ ] Replace serial stubs in `serial_worker.py`
  - [ ] `_open_serial_port()` — real `serial.Serial()`
  - [ ] `_send_packet()` — real `port.write()`
  - [ ] `_check_ack()` — real `port.read()` with 10ms timeout
  - [ ] `_close_port()` — real `port.close()`
- [ ] Add heartbeat sending during idle periods
- [ ] Add consecutive ACK failure tracking (warn at 5 failures)
- [ ] Test with ESP32: motors respond to depth data

---

## Phase 7: Config Reader (Can develop with `--mock`)

- [ ] Replace serial stubs in `config_reader.py`
  - [ ] `_open_config_port()` — real `serial.Serial(port, 9600)`
  - [ ] `_read_line()` — real `port.readline()`
  - [ ] `_close_config_port()` — real `port.close()`
- [ ] Test text protocol parsing with Arduino serial monitor
- [ ] Test: potentiometer changes `motor_strength` → serial worker applies multiplier
- [ ] Test: toggle switch changes `tts_enabled` → TTS stops/starts
- [ ] Test: mode switch sends `M:detection\n` → pipeline rebuild triggered

---

## Phase 8: Dynamic Pipeline Switching

- [ ] Implement `_build_detection_only()` in `app.py`
- [ ] Implement `_build_depth_only()` in `app.py`
- [ ] Implement `_build_dual()` in `app.py` (refactor from current `get_pipeline_string()`)
- [ ] Make `get_pipeline_string()` mode-aware (reads `config.pipeline_mode`)
- [ ] Make `_connect_callback()` mode-aware (only connects active callbacks)
- [ ] Implement `trigger_rebuild()` — `GLib.idle_add(self._rebuild_pipeline)`
- [ ] Wire config reader mode change to `app.trigger_rebuild()`
- [ ] Test: TTS announces mode, pipeline rebuilds within ~1.2s, new mode active

---

## Phase 9: Hazard Detection

- [ ] Add `detect_ground_hazard(depth_map, frame_height)` to `depth_utils.py`
  - [ ] Extract ground strip (bottom 25% of frame)
  - [ ] Compute row-wise average depth
  - [ ] Compute depth gradient (row-to-row change)
  - [ ] Detect gradient spike > threshold (default 5x median)
  - [ ] Return `(hazard_detected: bool, severity: float)`
- [ ] Call `detect_ground_hazard()` in `on_depth_frame()` callback
- [ ] Set `hazard=True` and `hazard_severity` in serial queue dict
- [ ] Serial worker sends `HAZARD_ALERT` packet when hazard detected
- [ ] Test: point camera at staircase → hazard detected → distinct motor pattern
- [ ] Tune gradient threshold

---

## Phase 10: Headless + Debug Display

- [ ] Add `--headless` logic to `get_pipeline_string()` — replace `DISPLAY_PIPELINE` with `fakesink`
- [ ] Create `src/second_vision/debug/__init__.py`
- [ ] Create `src/second_vision/debug/display.py` — OpenCV composite debug window
  - [ ] Side-by-side depth map (colorized) + detection view (bbox overlay)
  - [ ] Text overlay: motor values, TTS log, FPS, ACK status
- [ ] Add `--debug-display` flag to CLI
- [ ] Test: `--headless` runs without any display windows
- [ ] Test: `--debug-display` shows single composite window

---

## ESP32 Firmware (Teammate)

- [ ] Parse binary protocol (start byte, msg type, payload, checksum)
- [ ] Validate checksum (XOR)
- [ ] Handle MOTOR_UPDATE: set 3× PWM via `ledcWrite()`
- [ ] Handle HAZARD_ALERT: override motors with pulsing pattern
- [ ] Handle HEARTBEAT: reset watchdog timer
- [ ] Send ACK: `0xAA 0xFF acked_type checksum`
- [ ] Implement 3-second watchdog: if no packets → all motors to 0
- [ ] Test with RPi5: motors respond correctly to binary commands

---

## Arduino Control Panel (Teammate)

- [ ] Wire toggle switches (pipeline mode, TTS on/off, hazard on/off)
- [ ] Wire potentiometers (motor strength, cooldown duration)
- [ ] Wire momentary button (status announce)
- [ ] Implement text serial protocol: `"S:key:value\n"`, `"M:mode\n"`, `"B:event\n"`
- [ ] Add debouncing for switches/buttons
- [ ] Add smoothing for potentiometer readings (prevent jitter)
- [ ] Test with serial monitor: verify correct messages sent

---

## Tests

- [ ] `tests/test_config.py` — SystemConfig thread safety, update/get/snapshot
- [ ] `tests/test_protocol.py` — pack/unpack/checksum for all message types
- [ ] `tests/test_depth_utils.py` — zone splitting, proximity curves, hazard detection
- [ ] `tests/test_cooldown.py` — CooldownManager per-label-zone logic
