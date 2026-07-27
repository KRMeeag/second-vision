# TASKS.md — Status Board

> **Last synced: 2026-07-27.** This file is the **source of truth for status**.
> Tick boxes here as work lands. `.agents/*.md` handoffs remain the narrative record —
> the why, the decisions, the instructive failures — but they are local to one machine and
> are not the status board.
>
> Organised by the five workstreams in [PLAN.md](PLAN.md), which run concurrently.

## Status legend

- `[ ]` Not started
- `[/]` In progress
- `[x]` Complete
- `[~]` Blocked / waiting on a dependency

---

## Foundation (done)

- [x] Package layout under `src/second_vision/`
- [x] `core/config.py` — thread-safe `SystemConfig`
- [x] `mock/data_generator.py` — fake detections + depth for `--mock`
- [x] `main.py` — orchestrator, worker startup, mock/pipeline split
- [x] `pipeline/app.py` — `SecondVisionApp`, dual-branch pipeline, `_connect_callback`
- [x] `scripts/run.sh` — venv + Hailo env + `PYTHONPATH`, passes args through
- [x] `--mock` runs end-to-end with no hardware

> Note: `SecondVisionApp` extends `GStreamerApp`, **not** `GStreamerParallelApp` — that
> class exists only in the prototyping repo and is not importable from the installed
> library. The dual-branch pipeline is built inline here.

---

## 1. Object detection callbacks — *detection owner*

- [x] Extract detections, compute zone from bbox centre
- [x] Zone hysteresis `0.22 / 0.25 / 0.75 / 0.78`, holding previous zone in the bands
- [x] Per-track history keyed by tracker ID
- [x] Stale-track cleanup (`STALE_TRACK_FRAMES = 15`), including on empty frames
- [x] Head-turn suppression, gated by `HEAD_TURN_MIN_TRACKED = 3`
- [x] "leaving to X" / "still X" event phrasing
- [x] False-positive confirmation gate (`MIN_CONFIRMATION_SECONDS = 0.3`, `first_seen`)
- [x] "multiple X" wording, tallied from `track_history` after stale cleanup
- [x] "multiple X" composes with all phrase types, not just first sightings
- [x] cv2 debug overlay: zone tints, dividers, boxes, track IDs, FPS
- [x] Per-detection priority/tier, one winner per frame → TTS mailbox
- [ ] **Validate on real hardware**: confirmation gate, "multiple X", flicker-tolerant tally
      (all three verified only against mocked-hailo harnesses)
- [ ] Decide whether to tune `CONFIDENCE_THRESHOLD` / NMS thresholds at the source
- [ ] Settle the final detection class list
- [ ] Pluralization (`person` → `people`) — deferred until the class list is settled
- [ ] Remove or repurpose `zone_since` (computed and stored every frame, read by nothing)

---

## 2. Depth estimation callbacks — *depth owner*

> The approach is **under active design in the prototyping repo** and changing rapidly.
> Tasks here cover the port into this repo, not the algorithm design — see
> [PLAN.md](PLAN.md#2-depth-estimation-callbacks).

- [x] Placeholder `_process_real_depth` keeping the `serial_queue` interface alive
- [x] Depth-branch FPS scaffolding on `user_app_callback_class`
- [/] Design and validate the post-processing in the prototyping repo
- [ ] Port into this repo — verify imports resolve here
- [ ] Ensure it coexists cleanly with the haptic/serial path
- [ ] Apply this repo's coding standards before it lands
- [ ] Replace the placeholder `_process_real_depth`
- [ ] Wire real hazard output (currently `hazard=False` unconditionally)
- [ ] Port/author unit tests for the depth math in this repo
- [ ] Tune parameters against real captures
- [ ] Decide where depth post-processing state lives across a pipeline rebuild
- [ ] **After the port lands and is accepted: update the documentation immediately**
      (PLAN.md, ARCHITECTURE.md depth section, DECISIONS.md, this file)

---

## 3. TTS — *TTS owner*

- [x] `PriorityMailbox` — `offer` / `take` / `peek`, keep-higher, ties to newer
- [x] Composite priority score (confidence, area, zone, class, recency penalty)
- [x] Urgency tier model (`normal` / `urgent`)
- [x] `preempts()` — tier-OR-margin, with `PREEMPT_USE_TIER` flag for on-device A/B
- [x] Serialized interruptible speech (~50 ms preempt poll while speaking)
- [x] Minimum inter-utterance gap, skipped by an urgent pending item
- [x] Hard repeat floor (`MIN_REPEAT_INTERVAL = 10.0`) with urgent bypass
- [x] Removed the old `CooldownManager`; suppression now lives in one layer
- [x] espeak-ng primary, pyttsx3 fallback for dev machines
- [x] All tunables consolidated as named constants in `core/priority.py`
- [x] `tests/test_priority.py` — **19 tests passing**
- [ ] Wire the `W_APPROACH` approach-velocity hook (reserved, currently `0.0`)
- [ ] Confirm on-device that the layering fix holds under real load
- [~] Tune weights / margins / gap by ear — **scheduled last**, see the field-testing gate

---

## 4. ESP32 vibration motors — *firmware owner*

### ESP32 side — complete

- [x] Parse the binary protocol (start byte, type, payload, checksum)
- [x] Validate the XOR checksum
- [x] `MOTOR_UPDATE` → 3× PWM
- [x] `HAZARD_ALERT` → pulsing pattern override
- [x] `HEARTBEAT` → watchdog reset
- [x] Send ACK on a valid packet
- [x] 3-second watchdog → all motors to 0

### RPi side — **UART comms still being built**

- [x] `_pack_motor_update()` and `_pack_hazard_alert()` implemented and reached
- [ ] Register a `--serial-port` CLI argument (**does not exist** — currently only in a
      `main.py` docstring)
- [ ] Plumb the port path through to the worker (`SystemConfig` has no `serial_port` field,
      so `_open_serial_port(config)` has nothing to open)
- [ ] Extract packing into `core/protocol.py` (testable without hardware)
- [ ] `tests/test_protocol.py` — pack/unpack/checksum for every message type
- [ ] Real `pyserial` open / write / read
- [ ] Wire the orphaned `_pack_heartbeat()` — **it currently has zero callers**, so no
      heartbeat is ever sent and the ESP32 watchdog would fire on every quiet period
- [ ] Real ACK reading (`_check_ack` returns `True` unconditionally today)
- [ ] Decide and implement the ACK-failure policy (the handler is a `TODO` print)
- [ ] Restore observable send logging (`_send_packet`'s print is commented out)
- [ ] Joint UART bring-up: Pi ↔ ESP32 end to end, motors responding to real depth

---

## 5. Control panel + mode switching — *control owner*

### Config reader

- [x] Text protocol parsing (`S:key:value`, `M:mode`, `B:event`) and type casting
- [ ] **Fix the `config.udpate(...)` typo** (should be `update`) — would `AttributeError`
      on the first real `S:` line
- [ ] Register a `--config-port` CLI argument — **does not exist**, so
      `getattr(app.options_menu, "config_port", None)` is always `None` and the config
      reader thread never starts
- [ ] Real `pyserial` reads (`_open_config_port` / `_read_line` / `_close_config_port`)
- [~] Verify a potentiometer changes `motor_strength` live — blocked on panel hardware
- [~] Verify a toggle changes `tts_enabled` live — blocked on panel hardware

### Mode switching

- [x] `trigger_rebuild()` defers correctly through `GLib.idle_add`
- [ ] Make `get_pipeline_string()` branch on `config.pipeline_mode`
- [ ] Make `_connect_callback()` branch on `config.pipeline_mode`
- [ ] Detection-only / depth-only / both pipeline builders
- [ ] Verify the mode announcement is heard before the rebuild blackout
- [ ] Decide where depth post-processing state is reset across a rebuild (with depth owner)

---

## Cross-cutting / not owned by one stream

- [ ] `--headless` flag → `fakesink` (documented in older plans, never implemented)
- [ ] `--debug-display` composite OpenCV window (the current overlay is in-callback cv2)
- [ ] `tests/test_config.py` — `SystemConfig` thread safety
- [ ] `tests/test_depth_utils.py` — lands with the depth port
- [ ] Decide whether to delete `main2.py` (temporary smoke-test script)
- [ ] Decide whether to trim the `TBR-*` draft files
- [ ] **Open interface question**: hazard *direction*. The prototype's ground-hazard
      detection distinguishes `"down"` (drop-off) from `"up"` (curb), but the `serial_queue`
      contract has no field for it and `HAZARD_ALERT`'s payload is severity + pattern.
      Needs a joint decision between the depth and firmware owners; note the return-arity
      mismatch is a port-time `ValueError` risk.

---

## Field testing — **last**

- [~] Run the scenarios in [FIELD-TESTING.md](FIELD-TESTING.md) and tune by ear.
      Blocked by design: this is the final activity, after the four build streams converge.
