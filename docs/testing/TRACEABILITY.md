# UAT traceability

One row per UAT case:

**UAT case → requirement → affected subsystem → implementation task → test → status → evidence**

Requirements are in [REQUIREMENTS.md](REQUIREMENTS.md), tasks in
[TASK-BOARD.md](TASK-BOARD.md), decisions and workbook defects in
[DISAGREEMENTS.md](DISAGREEMENTS.md), the verbatim case text in
[uat-snapshot.yaml](uat-snapshot.yaml), and run evidence under [reports/](reports/). The
workbook stays authoritative: its *Workbook* column below copies what the Excel records, and
only people update the Excel.

Only the Lead edits this file, when it accepts or blocks a task.

## Keys

**Subsystem** (code the requirement touches; paths under `src/second_vision/` unless shown):

| Key | Code |
|---|---|
| `det` | `pipeline/callbacks.py`: `on_det_frame`, `DETECTION_CLASS_MAP`, `_get_detection_zone` |
| `model` | `models/hef_deploy/` (HEF, `labels.json`) |
| `prio` | `core/priority.py`: `PriorityMailbox`, scoring, tiers |
| `tts` | `workers/tts_worker.py` |
| `audio` | OS audio (ALSA) and the earphones |
| `depth` | `pipeline/callbacks.py` `on_depth_frame`, `core/depth_utils.py` |
| `view` | `core/depth_view.py`, debug banner in `pipeline/app.py` |
| `haptics` | `core/haptics.py` |
| `serial` | `workers/serial_worker.py` (glasses ESP32 link) |
| `panel` | `workers/config_reader.py`, `core/config.py` (control panel link and settings) |
| `app` | `pipeline/app.py`, `main.py` (pipeline lifecycle, video source, modes, shutdown) |
| `power` | X1202 UPS HAT, OS, `scripts/second-vision.service` |
| `fw-glasses` | `firmware/esp32_haptic_feedback/` (motors and BMI160; runs on the ESP32) |

**Test** (how the case is verified; "unit:" names existing tests that already cover the logic,
so they are not rewritten):

| Key | Meaning |
|---|---|
| `auto` | pytest under `tests/uat/` (planned), no hardware |
| `rpi-hw` | needs the Pi's devices (Hailo, camera, audio); an agent can run it unattended |
| `assisted` | a person sets up the scene or plugs and unplugs hardware; the agent runs it and reads the logs |
| `manual` | a person's judgement (listening, feeling) |
| `esp32-manual` | runs on the ESP32; outside the agentic workflow |

**Status** (one primary value; `· AMB` is added when a linked requirement is AMBIGUOUS):

| Status | Meaning |
|---|---|
| `READY` | Built; testable now; UAT test not written or run yet |
| `TO-FIX` | Built, but the code must change first (see task) |
| `NOT-BUILT` | The feature doesn't exist yet (see task) |
| `AMBIGUOUS` | Built, but pass/fail can't be judged until a person answers the requirement's question |
| `DEFERRED` | Set aside for now |
| `OUT-SCOPE` | Outside the project's scope |
| `OUT-ESP32` | Runs on the ESP32; outside the agentic workflow |
| `AWAITING-HANDS-ON` | Every automated check passes; waiting for the person's one-time confirmation of the real device |
| `VERIFIED` | The Lead accepted a task whose Tester report shows this case's checks `PASS`; the Evidence column says at what level |
| `FAILED` | A Tester report shows a reproducible `FAIL` that is still open |
| `BLOCKED` | Verification couldn't run (hardware unavailable, missing dependency, open question); the Evidence column says why |

**Workbook** is the Status the Excel records. *stale* means the code changed after that result,
so it needs a re-run (DISAGREEMENTS W-21, W-22, D-03).

**Evidence** links the report that supports the status, with its level:

| Level | What it proves |
|---|---|
| `unit` | Automated test, no hardware |
| `mock-integration` | Several components together, with hardware replaced by mocks or recorded fixtures. **Doesn't prove the device works.** |
| `device-integration` | Run on the Pi with the real camera, Hailo, ESP32 or audio connected |
| `physical-measurement` | A measured physical quantity (latency, battery life, FPS, temperature) |
| `user-observed` | A person on the team performed or judged the step (feel, hearing, plugging) and the Lead recorded what they reported |

A case is only `VERIFIED` at the level its requirement needs. A hardware case backed only by
`unit` or `mock-integration` evidence stays `READY` or `BLOCKED`, with the partial evidence
noted.

**Per-device confirmation (team, 2026-10-16).** The hardware cases of one scenario (for
example TC-022's seven glasses-link cases) are `VERIFIED` when both hold:
1. every case has an automated test that passes, at the highest level the harness can reach:
   fake boards, an injected error, the simulated camera unplug, the real devices;
2. a person has confirmed the real device once for that scenario: one real unplug and replug,
   one pass with the real panel. This is recorded as `user-observed`.

Cases whose point *is* a person's judgement (how speech sounds, how strong the vibration
feels) still need that judgement for each case. The aim is that failures are found and fixed
before the team's human verification, so it needs fewer revisions.

**Evidence the panel can see.** Every automated run that counts as evidence is saved as a
log in `reports/<task>/evidence/`: the command, date and time, the commit, and pytest's
per-test results, with the case ID in each test's name. Run it, then screenshot the
terminal or the log.

---

## Implementation tasks

The task list, its priorities, dependencies, chosen Developer model and blockers live in
[TASK-BOARD.md](TASK-BOARD.md). The *Task* column below only names which task, if any, a
case waits on.

---

## Matrix

### M01 Object detection

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-001-001 | REQ-DET-01 | `det` `model` | T-05 | rpi-hw · unit: `test_detection_classes.py::test_every_model_label_is_mapped` | TO-FIX | Passed *stale* | — |
| TC-001-002 | REQ-DET-02 | `det` `model` | T-05 | rpi-hw | TO-FIX | Passed *stale* | — |
| TC-001-003 | REQ-DET-03 | `det` `model` | — | rpi-hw | READY | Passed *stale* | — |
| TC-002-001 | REQ-DET-04 | `det` `prio` | — | rpi-hw · unit: `test_detection_classes.py::test_off_list_class_cannot_outrank_an_on_list_one` (partial) | READY | Passed *stale* | — |
| TC-002-002 | REQ-DET-05 | `det` `prio` | — | rpi-hw · unit: `test_priority.py::test_priority_monotonic_in_area` (partial) | READY | — | — |
| TC-002-003 | REQ-DET-06 | `det` | — | rpi-hw | READY | Passed *stale* | — |
| TC-002-004 | REQ-DET-07 | `det` | — | rpi-hw | READY | — | — |
| TC-002-005 | REQ-DET-08 | `det` `model` | — | rpi-hw | READY | Failed *stale* | — |
| TC-003-001 | REQ-DET-09 | `det` `model` | — | rpi-hw | READY | Passed *stale* | — |
| TC-003-002 | REQ-DET-10 | `det` `model` | — | rpi-hw | READY | Passed *stale* | — |
| TC-003-003 | REQ-DET-11 | `det` | — | rpi-hw | READY | — | — |
| TC-003-004 | REQ-DET-12 | `det` `model` | — | rpi-hw | READY | Passed *stale* | — |

### M02 Depth estimation

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-004-001 | REQ-DEP-01 | `depth` | — | assisted · unit: `test_depth_utils.py::test_proximity_is_zero_at_and_beyond_the_safe_cutoff`, `test_evaluate.py::test_replay_open_scene_is_silent_everywhere` | READY | Passed | — |
| TC-004-002 | REQ-DEP-02 | `depth` | — | assisted · unit: `test_depth_utils.py::test_proximity_is_monotonic_in_distance` | READY | Passed | — |
| TC-004-003 | REQ-DEP-03 | `depth` | — | assisted · unit: `test_depth_utils.py::test_near_object_raises_only_its_own_zone` | READY | Passed | — |
| TC-005-001 | REQ-DEP-04 | `depth` `view` | — | assisted · unit: `test_depth_utils.py::test_blank_wall_never_suppresses_a_reading` | READY | Passed | — |
| TC-005-002 | REQ-DEP-05 | `depth` | — | assisted · unit: `test_depth_utils.py::test_subgrid_needs_an_object_to_fill_a_tenth_of_one_cell` | READY | Passed | — |
| TC-005-003 | REQ-DEP-06 | `depth` | — | assisted · unit: `test_depth_utils.py::test_open_receding_floor_does_not_fire`, `::test_empty_floor_drives_nothing` | READY | Failed *stale* | — |
| TC-006-001 | REQ-DEP-07 | `depth` `view` | — | — | OUT-SCOPE (D-03) | Passed *stale* | — |
| TC-006-002 | REQ-DEP-08 | `depth` | — | — | OUT-SCOPE (D-03) | Passed *stale* | — |
| TC-006-003 | REQ-DEP-09 | `depth` | — | assisted · unit: `test_depth_utils.py::test_a_static_scene_does_not_flap` | READY | Passed | — |

### M03 Audio output

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-007-001 | REQ-AUD-01 | `audio` | — | rpi-hw | READY | — | — |
| TC-007-002 | REQ-AUD-02 | `audio` `tts` | — | rpi-hw | READY | — | — |
| TC-007-003 | REQ-AUD-03 | `audio` | — | manual | READY | — | — |
| TC-008-001 | REQ-AUD-04 | `tts` `audio` | — | manual (timed) | READY | — | — |
| TC-008-002 | REQ-AUD-05 | `tts` `prio` `audio` | — | manual | READY | — | — |
| TC-008-003 | REQ-AUD-06 | `tts` `audio` | — | manual | READY | — | — |
| TC-008-004 | REQ-AUD-07, REQ-AUD-04 | `tts` | — | auto | READY | — | — |
| TC-009-001 | REQ-AUD-08 | `tts` `prio` `audio` | — | manual · unit: `test_priority.py::test_worker_preempted_by_urgent` | READY | — | — |
| TC-009-002 | REQ-AUD-09 | `audio` | — | manual | READY | — | — |
| TC-009-003 | REQ-AUD-10 | `audio` `tts` | — | manual | READY | — | — |

### M04 ESP32 haptic firmware (outside the agentic workflow)

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-010-001 | REQ-HAP-01 | `fw-glasses` | — | esp32-manual | OUT-ESP32 (D-02) | — | — |
| TC-010-002 | REQ-HAP-02 | `fw-glasses` `serial` | — | esp32-manual · Pi side, unit: `test_serial_worker.py::test_ack_is_recognized` | OUT-ESP32 | — | — |
| TC-010-003 | REQ-HAP-03 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | — | — |
| TC-011-001 | REQ-HAP-04 | `fw-glasses` | — | — | OUT-ESP32 · superseded (D-01) | — | — |
| TC-011-002 | REQ-HAP-04 | `fw-glasses` | — | — | OUT-ESP32 · superseded (D-01) | — | — |
| TC-011-003 | REQ-HAP-05 | `fw-glasses` | — | esp32-manual · Pi side, unit: `test_depth_utils.py::test_to_motor_intensity_clamps_into_the_byte_range` | OUT-ESP32 | — | — |
| TC-012-001 | REQ-HAP-06 | `fw-glasses` | — | — | OUT-ESP32 (D-02) | — | — |
| TC-012-002 | REQ-HAP-07 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | — | — |
| TC-012-003 | REQ-HAP-08 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | — | — |

### M05 Detection to speech

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-013-001 | REQ-DTA-01 | `det` `prio` | — | assisted | READY | — | — |
| TC-013-002 | REQ-DTA-02 | `det` `prio` | — | assisted · unit: `test_priority.py::test_mailbox_keeps_higher_priority` | READY | — | — |
| TC-013-003 | REQ-DTA-03 | `det` `prio` | — | rpi-hw | READY | — | — |
| TC-014-001 | REQ-DTA-04 | `prio` `tts` | — | auto · unit: `test_priority.py::test_worker_speaks_once_when_uninterrupted` | READY | — | — |
| TC-014-002 | REQ-DTA-05 | `prio` `tts` | — | auto | READY | — | — |
| TC-014-003 | REQ-DTA-06 | `prio` `tts` | — | auto · unit: `test_priority.py::test_mailbox_never_grows_past_one_slot` | READY | — | — |
| TC-015-001 | REQ-DTA-07 | `det` `prio` `tts` `app` | — | assisted | READY | — | — |
| TC-015-002 | REQ-DTA-08 | `app` `prio` `tts` | — | rpi-hw | READY | — | — |
| TC-015-003 | REQ-DTA-09 | `app` | — | rpi-hw | READY | — | — |

### M06 Depth to haptics

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-016-001 | REQ-DHP-01 | `depth` `haptics` `serial` | — | assisted · unit: `test_haptics.py::test_approach_ramps_up_without_stepping_back` | READY | — | — |
| TC-016-002 | REQ-DHP-02 | `depth` `haptics` `serial` | — | assisted · unit: `test_haptics.py::test_a_side_obstacle_is_felt_on_that_side_only` | READY | — | — |
| TC-016-003 | REQ-DHP-03 | `depth` `haptics` `serial` | — | assisted | READY | — | — |
| TC-017-001 | REQ-DHP-04 | `depth` `haptics` | — | assisted · unit: `test_depth_utils.py::test_empty_floor_drives_nothing` | READY | — | — |
| TC-017-002 | REQ-DHP-05 | `depth` | — | — | OUT-SCOPE (D-03) | — | — |
| TC-017-003 | REQ-DHP-04 | `depth` `haptics` | — | assisted | READY | — | — |
| TC-018-001 | REQ-DHP-06 | `app` `depth` | T-08 | — | DEFERRED (D-08) | — | — |
| TC-018-002 | REQ-DHP-07 | `app` `depth` | T-08 | — | DEFERRED (D-08) | — | — |
| TC-018-003 | REQ-DHP-08 | `serial` | — | assisted · unit: `test_serial_worker.py::test_link_goes_down_after_the_timeout`, `::test_link_recovers_when_the_board_answers_again`, `::test_timeouts_are_set_so_the_worker_cannot_wedge` | READY (P1) | — | — |

### M07 BMI160 IMU (outside the agentic workflow)

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-019-001 | REQ-IMU-01 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |
| TC-019-002 | REQ-IMU-02 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |
| TC-019-003 | REQ-IMU-03 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |
| TC-020-001 | REQ-IMU-04 | `fw-glasses` | — | esp32-manual · Pi side, unit: `test_priority.py::test_turn_mute_*` | OUT-ESP32 | Passed | — |
| TC-020-002 | REQ-IMU-05 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |
| TC-020-003 | REQ-IMU-06 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Failed | — |
| TC-021-001 | REQ-IMU-07 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |
| TC-021-002 | REQ-IMU-08 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |
| TC-021-003 | REQ-IMU-09 | `fw-glasses` | — | esp32-manual | OUT-ESP32 | Passed | — |

### M08 Peripheral connectivity and failsafes (P1)

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-022-001 | REQ-CON-01 | `app` `serial` | — | assisted | READY | — | — |
| TC-022-002 | REQ-CON-02, 03 | `serial` `tts` `panel` | T-01a (speech), T-00c (speech switch off, D-15) | assisted | NOT-BUILT | — | — |
| TC-022-003 | REQ-CON-02, 03 | `serial` `tts` `panel` | T-01a, T-00c | assisted | NOT-BUILT | — | — |
| TC-022-004 | REQ-CON-02, 03 | `serial` `tts` `panel` | T-01a, T-00c | assisted | NOT-BUILT | — | — |
| TC-022-005 | REQ-CON-02, 03 | `serial` `tts` `panel` | T-01a, T-00c | assisted | NOT-BUILT | — | — |
| TC-022-006 | REQ-CON-03 | `serial` `tts` `panel` | T-01a, T-00c | assisted | NOT-BUILT | — | — |
| TC-022-007 | REQ-CON-04 | `serial` `tts` `haptics` | T-00b (pattern), T-01b (sentence, D-17) | assisted | NOT-BUILT | — | — |
| TC-023-001 | REQ-CON-01 | `panel` | — | assisted | READY | — | — |
| TC-023-002 | REQ-CON-05 | `panel` `tts` | T-02b, T-00c | assisted | NOT-BUILT | — | — |
| TC-023-004 | REQ-CON-05 | `panel` `tts` | T-02a (settings), T-02b, T-00c | assisted | NOT-BUILT | — | — |
| TC-023-007 | REQ-CON-06 | `panel` `tts` | T-02c (D-17) | assisted | NOT-BUILT | — | — |
| TC-024-001 | REQ-CON-01 | `app` | — | assisted | READY | — | — |
| TC-024-002 | REQ-CON-07, 08 | `app` `tts` `serial` `haptics` | T-03b (start-up half), T-03c1 (spoken alert, in work), T-03c2 (beep beep, needs T-00b), T-00c (speech switch off, D-15) | assisted · unit+device: `tests/uat/test_tc024_002_camera_absent_at_start.py` (90), gst: `tests/test_startup_without_camera.py` (16), unit: `tests/test_launcher.py` (41), `tests/test_main_startup.py` (5) | NOT-BUILT | — | **REQ-CON-08 half: AWAITING-HANDS-ON** (T-03b attempt 1, 2026-10-17): all automated checks pass at `device-integration` with the simulated unplug ([test-report-1](reports/T-03b/test-report-1.md), [decision-1](reports/T-03b/decision-1.md)); the person's one real start without a camera is missing (MANUAL-TESTS.md). REQ-CON-07 half (beep beep + spoken alert every 10 s): not built, T-03c. The workbook's "proceed in mock mode" is overruled by D-13 |
| TC-024-003 | REQ-CON-07, 08 | `app` `tts` `serial` `haptics` | T-03a (removal half), T-03c1 (spoken alert, in work), T-03c2 (beep beep, needs T-00b), T-00c (speech switch off, D-15) | assisted · device: `tests/uat/test_tc024_003_camera_removed.py` (51), gst: `tests/uat/test_tc024_003_start_paths_gst.py` (4), `tests/test_camera_loss.py` (31) | NOT-BUILT | — | **REQ-CON-08 half: AWAITING-HANDS-ON** (T-03a attempt 2, 2026-10-17): all automated checks pass at `device-integration` with the simulated unplug ([test-report-2](reports/T-03a/test-report-2.md), [decision-2](reports/T-03a/decision-2.md)); the person's one real pull and replug is missing (MANUAL-TESTS.md). REQ-CON-07 half (beep beep + spoken alert every 10 s): not built, T-03c |
| TC-024-004 | REQ-CON-09 | `app` `tts` | T-03c1 (merged T-03d), T-00c | assisted | NOT-BUILT | — | — |
| TC-025-001 | REQ-CON-01 | `audio` | — | assisted | READY | — | — |
| TC-025-002 | REQ-CON-10, 11 | `audio` `serial` `haptics` `panel` | T-04a (needs T-00b; D-18) | assisted | NOT-BUILT | — | — |
| TC-025-003 | REQ-CON-10, 11 | `audio` `serial` `haptics` `panel` | T-04a (needs T-00b; D-18) | assisted | NOT-BUILT | — | — |
| TC-025-004 | REQ-CON-10, 11 | `audio` `serial` `haptics` `panel` | T-04a (needs T-00b; D-18) | assisted | NOT-BUILT | — | — |
| TC-025-005 | REQ-CON-10, 11 | `audio` `serial` `haptics` `panel` | T-04a (needs T-00b; D-18) | assisted | NOT-BUILT | — | — |
| TC-025-006 | REQ-CON-10, 11 | `audio` `serial` `haptics` `panel` | T-04a (needs T-00b; D-18) | assisted | NOT-BUILT | — | — |
| TC-025-007 | REQ-CON-12 | `audio` `tts` `serial` `haptics` | T-04b (needs T-00b; D-17) | assisted | NOT-BUILT | — | — |
| TC-026-001 | REQ-UPS-01 | `power` | — | manual | READY | Passed | — |
| TC-026-002 | REQ-UPS-02 | `power` | — | manual | READY | Passed | — |
| TC-026-003 | REQ-UPS-03 | `power` | — | manual | READY | Passed | — |

### M09 Power

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-027-001 | REQ-PWR-01 | `power` `app` | — | assisted | READY | — | — |
| TC-028-001 | REQ-PWR-02 | `power` `tts` | T-06 | assisted | NOT-BUILT | — | — |
| TC-028-002 | REQ-PWR-02 | `power` `tts` | T-06 | assisted | NOT-BUILT | — | — |
| TC-028-003 | REQ-PWR-02 | `power` `tts` | T-06 | assisted | NOT-BUILT | — | — |
| TC-028-004 | REQ-PWR-02 | `power` `tts` | T-06 | assisted | NOT-BUILT | — | — |
| TC-028-001#2 (5 %, W-02) | REQ-PWR-02 | `power` `tts` | T-06 | assisted | NOT-BUILT | — | — |
| TC-029-001 | REQ-PWR-03 | `power` `tts` | T-07 | assisted | NOT-BUILT | — | — |
| TC-029-002 | REQ-PWR-04 | `power` `tts` `app` | T-06 | assisted | NOT-BUILT | — | — |

### M10 Control panel

| UAT | Requirement | Subsystem | Task | Test | Status | Workbook | Evidence |
|---|---|---|---|---|---|---|---|
| TC-030-001 | REQ-PNL-01 | `panel` `app` `tts` | — | assisted · unit: `test_pipeline_modes.py::test_depth_only_omits_the_detection_branch` (partial) | READY | — | — |
| TC-030-002 | REQ-PNL-02 | `panel` `app` `tts` | — | assisted | READY | — | — |
| TC-030-003 | REQ-PNL-03 | `panel` `app` `serial` | — | assisted · unit: `test_pipeline_modes.py::test_detection_only_omits_the_depth_branch` (partial) | READY | — | — |
| TC-030-004 | REQ-PNL-04 | `panel` `app` `serial` | — | assisted | READY | — | — |
| TC-031-001 | REQ-PNL-05 | `panel` `tts` | — | assisted · unit: `test_tts_volume.py::test_full_volume_is_espeaks_own_default` | READY | — | — |
| TC-031-002 | REQ-PNL-06 | `panel` `tts` | — | assisted · unit: `test_tts_volume.py::test_knob_fully_down_is_quiet_not_silent` | READY | — | — |
| TC-031-003 | REQ-PNL-07 | `panel` `tts` | — | manual · unit: `test_tts_volume.py::test_half_knob_is_half_way_in_decibels` | READY | — | — |
| TC-032-001 | REQ-PNL-08 | `panel` `serial` `haptics` | — | assisted (no unit test covers `motor_strength`) | READY | — | — |
| TC-032-002 | REQ-PNL-09 | `panel` `serial` `haptics` | — | assisted (no unit test covers `motor_strength`) | READY (D-14 decided) | — | — |
| TC-032-003 | REQ-PNL-10 | `panel` `serial` `haptics` | — | manual | READY (D-14 decided) | — | — |

---

## Requirements with no UAT case yet

| Requirement | Subsystem | Task | Test | Status |
|---|---|---|---|---|
| REQ-PERF-01: battery rundown under full load (FPS, temperature, battery life) | `power` `app` | — (measurement; battery parts via T-06) | manual | READY to run as a measurement; the workbook needs a case (W-23) |

## Totals (2026-10-15)

| Status | Cases | Of which P1 |
|---|---|---|
| READY | 60 | 5 (TC-018-003, TC-022-001, TC-023-001, TC-024-001, TC-025-001) |
| TO-FIX | 2 (T-05) | — |
| AMBIGUOUS | 0 (the strength-knob cases became READY when D-14 was decided) | — |
| NOT-BUILT | 25 | 18 (TC-022 to TC-025); the other 7 are battery and shutdown (TC-028, TC-029) |
| DEFERRED | 2 | — |
| OUT-SCOPE | 3 | — |
| OUT-ESP32 (2 of them superseded by D-01) | 18 | — |
| **Total** | **110** | **23** |

95 requirements. Every case traces to at least one; every requirement traces to at least one
case, except REQ-PERF-01, which waits for its workbook case. Recount with
`python3 docs/testing/tools/check_traceability.py`.
