# Task board

Implementation tasks derived from [REQUIREMENTS.md](REQUIREMENTS.md), worked through the
Lead / Developer / Tester workflow. Per-case status lives in [TRACEABILITY.md](TRACEABILITY.md);
reports live under [reports/](reports/). The team-level checklist in
`documentation/TASKS.md` mirrors the top-level rows here.

**Only the Lead edits this file.**

## Active task

| Active task | Attempt | State | Since |
|---|---|---|---|
| T-17 | 1 (verification of the demo-track build, slice 1 of 9) | IN-TEST (tester: sonnet; brief written 2026-10-19 12:50; shared baseline `.claude/baselines/T-03c1/`; plan: [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md)) | 2026-10-19 12:50 |

**Freeze (file `.claude/runs/DEMO-FREEZE`): written 2026-10-19 for the rehearsal, lifted 12:14 the same day.**
The file is gone and `DEMO-HANDOFF.md` records the user's answer ("lifted at once, so a Developer may change
the program while the user rehearses"). So a Developer may edit `src/` again, and the Testers may run device
checks, but only under the hardware rule: before any device work `systemctl is-active second-vision.service`
must be `inactive` and `pgrep -af "main.py|second_vision|demo_run"` must find no copy of the program. If the
user's rehearsal holds the device, device checks are BLOCKED (never wait, never stop it) and the work goes on
with what needs no device. The user's other answers of 2026-10-19: keep Sonnet; F5's rules 1 to 4 stand; no
status in the depth window.

There is **one active implementation task at a time**. A new task may start only when the
active slot is empty: the previous task is `ACCEPTED`, `AWAITING-HANDS-ON` (only the person's
real-device confirmation missing; the code stays in the tree), or parked as `BLOCKED` with the
reason written down.

## Lifecycle

| State | Meaning | Next |
|---|---|---|
| `BACKLOG` | Known, not yet broken down or not yet startable | `READY` |
| `READY` | Bounded, with acceptance criteria; dependencies met; can start | `IN-DEV` |
| `BUILT` | Built outside this loop (the demo track, DEMO-TRACK.md) and committed or in the tree; the Tester pass has not run yet. Added 2026-10-19 | `IN-TEST` |
| `IN-DEV` | Brief written, baseline taken, Developer working | `IN-TEST` |
| `IN-TEST` | Tester verifying independently | `IN-REVIEW` |
| `IN-REVIEW` | Lead reviewing diff, both reports, and criteria | `ACCEPTED`, `AWAITING-HANDS-ON`, `RETURNED` or `BLOCKED` |
| `RETURNED` | Tester or Lead found a reproducible failure; back to the Developer with evidence | `IN-DEV` (next attempt) |
| `AWAITING-HANDS-ON` | Every check that can run without a person passed. The code stays in the tree; only the person's one-time confirmation of the real device is missing (MANUAL-TESTS.md, "Waiting for a person") | `ACCEPTED` when confirmed; the next attempt if it fails |
| `ACCEPTED` | Lead accepted, on evidence; records updated | — |
| `BLOCKED` | Can't proceed: hardware, a dependency, or an open question. The reason is recorded | `READY` when cleared |
| `DEFERRED` | Set aside by decision | — |

## Rules for every task

- **Robustness and feedback only.** No task changes the vision algorithms: detection model,
  inference and post-processing math (`pipeline/callbacks.py` detection processing), the depth
  post-processing math (`core/depth_utils.py`), or how depth becomes motor strength
  (`core/haptics.py`). The decision is the team's, 2026-10-12.
- **No installed library is edited**; every change stays in this repo (AGENTS.md Rule 1).
- **Hailo docs first.** Anything touching the Hailo or GStreamer pipeline is checked against the
  hailo-apps docs and examples, never from memory (AGENTS.md Rule 6).
- **ESP32 firmware is not changed.** Vibration patterns are produced from the Pi.
- **Mock mode** is reachable only through `--mock`; nothing may switch into it automatically (D-13).

## Model routing

**Every role (Lead, Developer, Tester) runs on Sonnet at extra-high effort (`xhigh`) for
every task** (user, 2026-10-17: the flow used too many tokens). Opus is only for **very
high-risk** work. The Lead records the model on each task row, and for Opus, which part of
the bar was met:
- **Escalation:** a Sonnet attempt was RETURNED because the problem proved beyond it, and the
  task is in the very-high-risk area; or
- **Very high risk from the start:** a mistake could crash or hang the deployed device, or leave
  the motors running, without the wearer knowing; *and* the change is in the GStreamer/Hailo
  pipeline lifecycle or in timing shared between threads; *and* it can't be split into smaller
  Sonnet tasks.

Touching the AI pipeline, hardware or a P1 task is **not** by itself a reason for Opus. The
Tester goes on Opus only for a task that met the bar. The full rule is in the Lead's
instructions.

## Tasks

P1 = critical failsafes, done first (team, 2026-10-11). P2 = everything else.

| Task | Title | Pri | State | Requirements | UAT cases | Depends on | Developer | Blockers | Reports |
|---|---|---|---|---|---|---|---|---|---|
| T-00 | Alert output foundation. **Split (Lead, 2026-10-17; extended 2026-10-18 after D-15 to D-17):** T-00a repeating spoken system alerts (ACCEPTED), T-00b vibration patterns on all three motors, T-00c failsafe speech overrides the speech switch (D-15), T-00d an alert in progress finishes before an urgent object warning can cut it (D-16) | P1 | IN PROGRESS (parent): T-00a ACCEPTED; T-00b, T-00c and T-00d **BUILT** in the demo track (F1, F7), Tester pass queued | REQ-CON-04, 07, 10, 12 (patterns); REQ-CON-02, 05, 07 (repeating speech) | via T-01 to T-04 | — | — | None: D-15, D-16, D-17 answered by the user 2026-10-18 | —  |
| T-00a | Repeating spoken system alerts: one mechanism that speaks an alert at once, repeats it every N seconds or only once, stops it on demand, never cuts another alert off and never loses one (subtask of T-00; no alert is created here) | P1 | **ACCEPTED** (2026-10-18, attempt 1) | REQ-CON-03, 05, 07, 11 (cadences: every 10 s, 30 s, 5 min, or once; "once" includes switched off mid-run) | none directly (enabler for TC-022-002 to 006, TC-023-002/004, TC-024-002/003, TC-025-002 to 006) | — | developer-sonnet; tester: sonnet. Opus bar not met: pure Python on the speech path, no GStreamer/Hailo, no motors | Evidence: unit + mock-integration (real `tts_worker`, stub espeak), 160 real-worker and 240 virtual loss-path runs with 0 lost, doubled or cut-by-alert; the person hearing it is carried to the first task that raises an alert. **Known limit F-1 (D-16, user's question):** under a never-ending stream of urgent object warnings an alert is never heard to the end (today's preemption rule; 4 strict-xfail tests). F-2 (an interval of 0.01 s would starve detection speech) stays a recorded limit, no caller can reach it; F-3 (a docstring line that `repeat` callables must not block) goes into T-03c1 | [brief-1](reports/T-00a/brief-1.md), [dev-report-1](reports/T-00a/dev-report-1.md), [test-report-1](reports/T-00a/test-report-1.md), [decision-1](reports/T-00a/decision-1.md), [changes-1.patch](reports/T-00a/changes-1.patch) |
| T-00b | Vibration patterns on all three motors from the Pi: the reconnect *beep beep*, a distinct camera-alert *beep beep*, and *strong weak* x3 for missing earphones. They pre-empt the depth stream briefly and hand it back. **D-15 (user, 2026-10-18):** a pattern plays even when the vibration switch is off, at the strength knob's level but never below 30 % (0.30 of `motor_strength`), for that one pattern only; the depth vibration keeps obeying the switch and the knob (subtask of T-00) | P1 | **BUILT** (demo track F1, 2026-10-18); Tester pass queued | REQ-CON-04, 07, 10, 12 | none directly (enabler for TC-022-007, TC-024-002/003, TC-025-002 to 007) | — | demo-track author (Opus, chosen by the user in the fast lane; the Opus bar was not assessed); tester: sonnet | Device check outstanding: a person feels each pattern (DEMO-CHECKLIST Part A). **Open risk:** at the 30 % floor the strong pulse is duty 76, under the unmeasured motor start threshold of 90 (`core/haptics.py` PWM_FLOOR), so a floor-level pattern may not be felt; the user chose plain multiply (2026-10-18); the alternative is to map the knob onto the felt range | [F1](reports/DEMO/F1.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-00c | Failsafe speech overrides the speech switch: an alert that comes from the alert scheduler is spoken even when the wearer has switched speech off, at the volume knob's level but never below 30 % (0.30 of `tts_volume`), for that one utterance only; object and mode announcements keep obeying the switch and the knob (D-15; subtask of T-00) | P1 | **BUILT** (demo track F1, 2026-10-18); Tester pass queued with T-00b | REQ-CON-02, 05, 07 (every spoken failsafe alert) | none directly (enabler: TC-022-002 to 006, TC-023-002/004, TC-024-002/003/004) | T-00a (ACCEPTED), T-03c1 | demo-track author (Opus, F1); tester: sonnet | Device check outstanding: real espeak loudness at the 30 % floor (`-a 20`) not heard yet. Changes the speech worker's drop rule and volume for scheduler items only (named, Rule 3) | [F1](reports/DEMO/F1.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-00d | An alert in progress finishes: an urgent object warning no longer cuts a failsafe alert being spoken; it waits for the alert to end (2 to 3 s) (D-16; subtask of T-00) | P1 | **BUILT** (demo track F7, 2026-10-19); Tester pass queued | REQ-CON-02, 05, 07 | none directly (the four strict-`xfail` urgent-tier tests of `tests/uat/test_t00a_alert_scheduler.py` are its acceptance tests) | T-00a (ACCEPTED), T-00c | demo-track author (Opus, F7); tester: sonnet | Heard on the device only in the demo rehearsal (an alert falling due while objects are announced). D-16 is recorded as D42 in `documentation/DECISIONS.md`. Changes the speech worker's preemption check and the scheduler's offer rule (named, Rule 3) | [F7](reports/DEMO/F7.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-01 | Glasses ESP32 link-loss failsafe. **Split (Lead, 2026-10-18):** T-01a the spoken "Vibration is not working" alert and its cadence, T-01b the reconnect confirmation and *beep beep* | P1 | IN PROGRESS (parent): T-01a and T-01b **BUILT** (F2), Tester pass queued | REQ-CON-02, 03, 04 | TC-022-002 to 007 | T-00 | — | — | —  |
| T-01a | Glasses link lost: the device says "Vibration is not working" at once, every 30 s while depth is enabled and once while it is disabled (including switched off mid-run), whether the board was absent at start-up or lost while running; it stops when the link returns (subtask of T-01; no reconnect confirmation) | P1 | **BUILT** (demo track F2, 2026-10-18); Tester pass queued | REQ-CON-02, 03 | TC-022-002 to 006 (spoken part) | T-00a (ACCEPTED) | demo-track author (Opus, F2); tester: sonnet | Device check outstanding: pull the glasses board's cable (DEMO-CHECKLIST Part D). Known limit: a board port that cannot be opened at start-up is not retried (the glasses board is on `/dev/serial0`, which always exists). The panel never sends `depth_enabled`; depth on is read from `pipeline_mode` (F2.md) | [F2](reports/DEMO/F2.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-01b | Glasses link back: spoken confirmation and the reconnect *beep beep* (subtask of T-01). **D-17 (user, 2026-10-18):** "everything is connected" only when no other failsafe alert is raised, otherwise "Vibration is working again" | P1 | **BUILT** (demo track F2, 2026-10-18); Tester pass queued with T-01a | REQ-CON-04 | TC-022-007 | T-00b, T-01a | demo-track author (Opus, F2); tester: sonnet | Device check outstanding (reconnect *beep beep* felt, sentence heard; DEMO-CHECKLIST Part D). D-15 and D-17 applied | [F2](reports/DEMO/F2.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-02 | Control panel link-loss failsafe and last-session settings. **Split (Lead, 2026-10-18):** T-02a settings saved and restored, T-02b the spoken alert every 5 min, T-02c the reconnect confirmation | P1 | IN PROGRESS (parent): T-02b and T-02c **BUILT** (F6), Tester pass queued; **T-02a NOT BUILT** | REQ-CON-05, 06 | TC-023-002, 004, 007 | T-00 (speech part) | — | — | —  |
| T-02a | Last session's settings: the panel's settings are saved as they change and restored at start-up, so a panel that is absent at power-on gets the last session's settings, not the built-in defaults (subtask of T-02; REQ-CON-05 second half) | P1 | READY (**NOT BUILT**; FIX-RANKING rank 5). No longer waits for T-01b: that is built | REQ-CON-05 (settings in force) | TC-023-004 (settings half) | — | — | **Not built** (checked 2026-10-19: nothing in `core/config.py` or the panel worker saves settings). Where the file lives and which settings are saved are decided in the brief. Until it exists the absent-at-power-on half of the two panel-lost cases ("use the previously used settings") is not met: the spoken sentence says "current settings", which at power-on are the built-in defaults (everything on, full strength and volume). Low harm, as the defaults are the safest settings | —  |
| T-02b | Panel lost: the device says the controller board cannot be reached and the last session's settings are in use, at once and every 5 minutes, whether absent at start-up or lost while running; it stops when the panel returns (subtask of T-02) | P1 | **BUILT** (demo track F6, 2026-10-19); Tester pass queued. Alert half only: it says "current settings" because T-02a is not built | REQ-CON-05 (alert half) | TC-023-002, 004 (spoken part) | T-00a (ACCEPTED), T-02a | demo-track author (Sonnet, F6); tester: sonnet | Device check outstanding: unplug the panel, wait 20 to 31 s (DEMO-CHECKLIST Part H). Changes the panel worker's loop (named, Rule 3). A panel port that cannot be opened is not retried (the panel is on `/dev/ttyAMA3`, which always exists) | [F6](reports/DEMO/F6.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-02c | Panel back: spoken confirmation (subtask of T-02). **D-17 (user, 2026-10-18):** "everything is connected" only when no other failsafe alert is raised, otherwise "Controller board connected" | P1 | **BUILT** (demo track F6, 2026-10-19); Tester pass queued with T-02b | REQ-CON-06 | TC-023-007 | T-02b | demo-track author (Sonnet, F6); tester: sonnet | Device check outstanding (plug the panel back; the sentence follows its next heartbeat, up to 10 s). D-17 applied | [F6](reports/DEMO/F6.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-03 | Camera missing or removed never crashes the device. **Split (Lead, 2026-10-17):** T-03a removed while running (AWAITING-HANDS-ON), T-03b start-up without a camera and no automatic mock mode, T-03c the camera-missing alert (REQ-CON-07), T-03d the camera-reconnected confirmation (REQ-CON-09) | P1 | IN PROGRESS (parent): T-03a and T-03b AWAITING-HANDS-ON; T-03c1 (with T-03d) and T-03c2 **BUILT** (F0, F4), Tester pass queued | REQ-CON-07, 08, 09 | TC-024-002, 003, 004 | T-00 (alert parts only) | — | — | —  |
| T-03a | Camera removed while running: the program stays up and restarts vision when a camera returns (subtask of T-03; no alerts) | P1 | **AWAITING-HANDS-ON** (attempt 2, 2026-10-17 16:06; attempt 1 RETURNED) | REQ-CON-08 (removal half); REQ-DTA-08 (regression) | TC-024-003 (partial: not its REQ-CON-07 alert) | T-10a (ACCEPTED 2026-10-16) | attempt 1: developer-opus (old rubric); **attempt 2: developer-sonnet, tester: sonnet**. Opus bar not met: no Sonnet attempt was returned for being beyond it, and Sonnet got every repeat count to zero | Only the person's one real-camera pull and replug is missing (MANUAL-TESTS.md, "Waiting for a person"; the real panel's speech and vibration switches must be on). Device-integration passed with the simulated unplug: F1/F2/F3 gone (bounce 10 of 10, every mode-switch gap 4 passes, camera-that-cannot-open 30 of 30 clean, full device file 37 passed). Residuals accepted: a stop in a few-bytecode window of a start; the boot pipeline is not primed (both go to T-11); a Hailo element failing a start never reached. Questions for the user in decision-2 **Re-run needed after the freeze (Lead, 2026-10-19):** this device-integration evidence is from 2026-10-17, before the demo track changed `pipeline/app.py` (about 250 lines: F0, F4, F5, status). The device suites (`tests/uat/test_tc024_*`, `tests/test_camera_alerts_device.py`) must be run again before the person's confirmation counts. | [brief-1](reports/T-03a/brief-1.md), [dev-report-1](reports/T-03a/dev-report-1.md), [test-report-1](reports/T-03a/test-report-1.md), [decision-1](reports/T-03a/decision-1.md), [brief-2](reports/T-03a/brief-2.md), [dev-report-2](reports/T-03a/dev-report-2.md), [test-report-2](reports/T-03a/test-report-2.md), [decision-2](reports/T-03a/decision-2.md), [changes-2.patch](reports/T-03a/changes-2.patch)  |
| T-03b | Start-up without a camera: the program starts, stays up, keeps everything else running, looks for a camera and starts vision when one appears; nothing ever falls back to mock mode (subtask of T-03; no alerts) | P1 | **AWAITING-HANDS-ON** (attempt 1, 2026-10-17 19:35) | REQ-CON-08 (absent-at-start-up half; D-13) | TC-024-002 (partial: not its REQ-CON-07 alert) | T-03a (AWAITING-HANDS-ON; its changes stay in the tree) | developer-sonnet; tester: sonnet. Opus bar not met: reuses T-03a's staged, primed start | Only the person's one real start with the camera unplugged is missing (MANUAL-TESTS.md, "Waiting for a person", same session as T-03a). All 8 criteria PASS at device-integration/unit: 124 s with no camera, camera at 1/10/90 s in four modes 12 of 12, 25 of 25 random, 38 of 38 race sweep, 30 of 30 stops, T-03a subset 10 of 10. Residuals accepted: stops in the first ~0.5 s after launch (same on baseline, goes to T-11 with an `_shutting_down` ordering defect at `app.py:363`); camera present at launch is the library's unprimed start. Questions for the user in decision-1 **Re-run needed after the freeze (Lead, 2026-10-19):** this device-integration evidence is from 2026-10-17, before the demo track changed `pipeline/app.py` (about 250 lines: F0, F4, F5, status). The device suites (`tests/uat/test_tc024_*`, `tests/test_camera_alerts_device.py`) must be run again before the person's confirmation counts. | [brief-1](reports/T-03b/brief-1.md), [dev-report-1](reports/T-03b/dev-report-1.md), [test-report-1](reports/T-03b/test-report-1.md), [decision-1](reports/T-03b/decision-1.md), [changes-1.patch](reports/T-03b/changes-1.patch)  |
| T-03c | Camera-missing alert. **Split (Lead, 2026-10-18):** T-03c1 the spoken alert (and, as the same hook, the reconnect sentence of T-03d), T-03c2 the vibration pattern | P1 | BACKLOG (parent; done when its subtasks are) | REQ-CON-07 | TC-024-002, TC-024-003 (alert half) | T-00 (patterns, repeating speech), T-03a, T-03b | — | — | — |
| T-03c1 | Camera status in speech: "Critical Error: Camera cannot be reached" at once and every 10 s while a camera is missing (absent at start-up or lost while running), and, when a camera starts the vision pipeline again, the alert stops and the device says "Camera has been reconnected" once (this includes T-03d: one hook, two sentences) | P1 | **BUILT** (demo track F0, 2026-10-18/19; the Developer's run was interrupted, F0.md is the review); Tester pass queued | REQ-CON-07 (spoken half), REQ-CON-09 | TC-024-002, 003 (spoken half), TC-024-004 | T-00a (ACCEPTED), T-03a, T-03b | developer-sonnet (interrupted attempt 1), reviewed in F0 (Sonnet); tester: sonnet. Opus bar not met: non-blocking calls to the alert scheduler, no GStreamer/Hailo state change | Device check outstanding: 14 opt-in device tests (`tests/test_camera_alerts_device.py`) and the real pull (DEMO-CHECKLIST Part E). Includes one docstring line in `core/alerts.py` (T-00a finding F-3) | [brief-1](reports/T-03c1/brief-1.md), [F0](reports/DEMO/F0.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-03c2 | Camera-missing vibration: the distinct *beep beep* on all three motors every 10 s, with the alert (subtask of T-03c) | P1 | **BUILT** (demo track F4, 2026-10-19); Tester pass queued with T-03c1 | REQ-CON-07 (vibration half) | TC-024-002, 003 (vibration half) | T-00b, T-03c1 | demo-track author (Sonnet, F4); tester: sonnet | Device check outstanding: the camera *beep beep* is felt with each sentence and told apart from the reconnect one (DEMO-CHECKLIST Part E). Built as an `on_speak` hook on the alert scheduler | [F4](reports/DEMO/F4.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-03d | Camera-reconnected confirmation: the device says the camera has been reconnected and returns to normal (subtask of T-03) | P1 | **MERGED into T-03c1** (Lead, 2026-10-18): the same hook in `pipeline/app.py` (the camera state change) and the same test rig; fewer Tester runs | REQ-CON-09 | TC-024-004 | T-00a, T-03a | — | — | — |
| T-04 | Earphones missing or lost failsafe. **Split (Lead, 2026-10-18):** T-04a detection and the *strong weak* x3 alert with its cadence, T-04b the reconnect (D-17 wording and the *beep beep*) | P1 | IN PROGRESS (parent): T-04a and T-04b **BUILT** (F3), Tester pass queued | REQ-CON-10, 11, 12 | TC-025-002 to 007 | T-00b | — | Detection method decided: D-18 (USB sound card with a playback stream in `/proc/asound`) | —  |
| T-04a | Earphones missing: the Pi notices when no USB sound card can play (absent at start-up or lost while running) and plays the *strong weak* x3 pattern on all three motors at once, every 30 s while object detection is enabled and once while it is disabled (including switched off mid-run); stops when the earphones return (subtask of T-04; no reconnect confirmation) | P1 | **BUILT** (demo track F3, 2026-10-18); Tester pass queued | REQ-CON-10, 11 | TC-025-002 to 006 | T-00b, T-00a (cadence machinery) | demo-track author (Sonnet, F3); tester: sonnet | Device check outstanding (DEMO-CHECKLIST Part C). **Open:** that `usb_sim.py detach earphones` (the USB `authorized` flag) makes the card leave `/proc/asound` as a real unplug does; D-18 expects it, the checklist reads `/proc/asound/cards` during the detach. DEMO-TRACK said to use usb_sim's rule; the code follows D-18 (same answer on this Pi; F3.md) | [F3](reports/DEMO/F3.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-04b | Earphones back: "Earphones connected" (or "everything is connected" when no other alert is raised, D-17) spoken through the returned earphones, and the reconnect *beep beep* (subtask of T-04) | P1 | **BUILT** (demo track F3, 2026-10-18); Tester pass queued with T-04a | REQ-CON-12 | TC-025-007 | T-04a, T-00b | demo-track author (Sonnet, F3); tester: sonnet | Device check outstanding: the sentence is heard in the returned earphones, not cut off (F3.md, "the returned earphones may need a moment"). D-17 applied | [F3](reports/DEMO/F3.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-05 | Each model class gets its own spoken label | P2 | BACKLOG (**NOT BUILT**; FIX-RANKING rank 6) | REQ-DET-01, 02 | TC-001-001, 002 | — | — | — | —  |
| T-06 | Battery announcements and low-battery shutdown | P2 | BACKLOG (**NOT BUILT**; FIX-RANKING rank 1; brief being prepared, see "Next, after the freeze") | REQ-PWR-02, 04 | TC-028-*, TC-029-002 | T-00 (speech part) | — | Nothing in `src/` reads the battery (checked 2026-10-19). Now unblocked: the X1202 gauge reads over I2C 0x36 without root (4.18 V, 98.4 % on 2026-10-19; `docs/testing/tools/perf_sample.py` `battery()`). The gauge cannot say whether the charger is plugged in; GPIO6 (the HAT's power-loss line) is unconfigured | —  |
| T-07 | "Device shutting down." on clean shutdown | P2 | BACKLOG (**NOT BUILT**; FIX-RANKING rank 1, with T-06; brief being prepared) | REQ-PWR-03 | TC-029-001 | — | — | — | —  |
| T-08 | Depth pipeline status and recovery | — | DEFERRED | REQ-DHP-06, 07 | TC-018-001, 002 | — | — | Deferred (D-08) | — |
| T-10a | Test levels (`gst`, `device` markers with automatic skips), default timeout, GStreamer bus helpers, simulated-camera-unplug fixture, evidence-run command (subtask of T-10; test-side only, no `src/`) | P1 | **ACCEPTED** (2026-10-16, attempt 2) | — (enabler) | none directly | pytest in `my_hailo_env` (done by the user 2026-10-16) | developer-sonnet: test-side code only, clear criteria; tester: sonnet | None. Live unplug roundtrip passed (Tester, device-integration) | [brief-1](reports/T-10a/brief-1.md), [brief-2](reports/T-10a/brief-2.md), [decision-2](reports/T-10a/decision-2.md) |
| T-10 | Test harness: pytest for GStreamer and device tests, fake boards, captured speech *(proposed)* | P1 | BACKLOG (T-10a split out; T-10b remains) | — (enabler: automated checks for REQ-CON, REQ-PNL, REQ-PWR, REQ-DTA) | none directly; replaces most hands-on steps (MANUAL-TESTS.md, "How much of this can be automated") | the user installs pytest into my_hailo_env | — | Agents may not change environments: the user runs the install (task note) | — |
| T-11 | Stopping the program right after a pipeline start can segfault *(proposed; found by the T-03a Tester, pre-existing)* | P2 | BACKLOG | REQ-DTA-08 (clean stop, no zombie processes) | none directly | T-03a hands-on | — | **Mostly fixed by T-03a (2026-10-17):** after a mode-switch rebuild Ctrl+C within 0.15 s segfaulted 16 of 20 on the baseline and 0 of 20 on T-03a's code. Remaining, proposed scope (updated after T-03b, 2026-10-17): stops in the first ~0.5 s after launch (early signal handlers in `main.py`; class-level defaults for the attributes `shutdown()` reads, `_shutting_down` is set only after `super().__init__()` at `app.py:363`);  the program's first pipeline at boot (not primed, never seen to crash in 30 of 30 on either code) and the few-bytecode stop window in `_launch_on_camera` (decision-2, residual 1). Ask the user whether to keep, re-scope or close it (decision-2, question 3) | [test-report-1](reports/T-03a/test-report-1.md) O1, [test-report-2](reports/T-03a/test-report-2.md) check 23 |
| T-12 | Memory grows about 5 to 6 MB with every vision restart *(proposed; found by the T-03a Tester, pre-existing)* | P2 | BACKLOG (**NOT BUILT**; FIX-RANKING rank 4) | none directly (robustness of REQ-CON-08: a loose connector restarts vision many times) | none directly | — | — | Measured by the T-03a Tester over 15 restarts: 5.8 MB per camera restart (T-03a code), 5.2 (attempt 1), 5.0 per mode switch (T-03a) against 6.5 (untouched baseline). Where it stops, or whether the Pi runs out, is unmeasured. Proposed as a measurement first (a long run with repeated restarts). Ask the user whether to make it a task (decision-2, question 2) | [test-report-2](reports/T-03a/test-report-2.md) check 20  |
| T-13 | Camera blocked, too dark or too bright: once a second a tiny grey sample of the camera's own frame; three checks in a row raise it, three good ones clear it; spoken every 30 s; "Camera view is back." once (URGENT-TESTS.md section 1; the wording and thresholds were changed after real footage, F5.md) | P1 | **BUILT** (demo track F5, 2026-10-19); Tester pass queued | none in the workbook yet (TC-013-003's covered lens is reworded by URGENT-TESTS.md) | none directly | T-00a, T-03c1 | demo-track author (Opus, F5); tester: sonnet | Device check outstanding: lens covered, a torch, a dim and a normal room that must stay clear (DEMO-CHECKLIST Part I). The user confirmed on 2026-10-19 that the author's rules 1 to 4 stand as built (F5.md, "Decisions I made": no check with both rockers off; a blocked view is not a "missing part"; dark straight to bright says the new sentence; camera lost withdraws the view alerts). Rule 5, the thresholds, now comes from the user's two real frames (a finger, a phone flash; kept in `tests/data/`); a dim room and a normal room must still be shown to stay clear Performance measured on the device: no frame-rate cost ([T-15 measurement-2](reports/T-15/measurement-2.md)) | [F5](reports/DEMO/F5.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-14 | Blurry camera (URGENT-TESTS.md section 2): the same machinery as T-13 with a focus measure compared with the device's own recent median | P2 | BACKLOG (**NOT BUILT**) | none in the workbook | none | T-13 | — | Agreed in principle only (2026-10-17); thresholds need real footage; ships switched off if a plain wall alerts | — |
| T-15 | Performance and resource measurement: the 20-minute run (frame rate, delay, CPU, NPU, temperature) and the tools behind it (`docs/testing/tools/perf_*.py`) | P2 | **MEASURED** (two runs, 2026-10-18 and 2026-10-19). The tools' own tests go to the Tester in T-17 | REQ-PERF-01 (frame-rate part), REQ-DTA-09 | TC-015-003 | — | measured by the user and the demo-track sessions | The battery rundown (REQ-PERF-01) is still to do: charge to 100 %, unplug the charger, stop the boot service, `perf_run.py --duration 36000 --stop-at-battery 5` (DEMO-HANDOFF.md). Needs the device for hours | [measurement-1](reports/T-15/measurement-1.md), [measurement-2](reports/T-15/measurement-2.md) |
| T-16 | The failsafe checks on screen and in the terminal: a status strip and banners on the camera picture, a dark status screen with no live picture, `[CHECK]` lines in the terminal (the user's request, 2026-10-19, for testing and for the panel watching the TV) | P2 | **BUILT** (demo track, STATUS-DISPLAY, 2026-10-19); Tester pass queued | none in the workbook | none | T-13, T-01a, T-04a, T-02b, T-03c1 | demo-track author (Opus); tester: sonnet | Not seen on the TV yet (DEMO-CHECKLIST Part B). Touches `pipeline/callbacks.py`'s `_draw_detection_overlay` (display drawing, not the detection processing; the Tester confirms the hunk). The depth window is deliberately not touched | [STATUS-DISPLAY](reports/DEMO/STATUS-DISPLAY.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |
| T-17 | The demo run: `docs/testing/tools/demo_run.py` starts the program, walks the presenter through 7 steps, ticks what the program said and sent, and keeps an evidence folder (DEMO-TRACK F8), with the perf tools' battery columns | P2 | **IN-TEST** (demo track F8 built 2026-10-19; Tester pass started 12:50, first in the plan because the user runs this tool in the rehearsal) | none | none | T-03c1, T-01a, T-04a, T-02b, T-13 | demo-track author (Sonnet, F8); tester: sonnet | The rehearsal on the device has not been done; it is also the per-device confirmation for T-03a, T-03b and the F0 to F6 items (F8.md). The tool is under `docs/testing/tools/`, outside the baseline's `src/`, `tests/`, `scripts/` | [brief-1](reports/T-17/brief-1.md), [dev-report-1](reports/T-17/dev-report-1.md) (stub), [F8](reports/DEMO/F8.md); [VERIFICATION-PLAN](reports/DEMO/VERIFICATION-PLAN.md) |

Order of work (Lead, rewritten 2026-10-19 after the demo track built most of it):

1. **Done and accepted:** T-10a, T-00a. **Built, hands-on confirmation missing:** T-03a, T-03b.
2. **Built in the demo track, Tester pass queued** (the order is in
   [reports/DEMO/VERIFICATION-PLAN.md](reports/DEMO/VERIFICATION-PLAN.md)): T-17 the demo-run tool first
   (the user runs it in the rehearsal), then T-00b and T-00c (patterns, speech override), T-00d (an alert
   in progress finishes), T-03c1 and T-03c2 (camera alerts), T-01a and T-01b (glasses), T-04a and T-04b
   (earphones), T-02b and T-02c (panel), T-13 (camera view), T-16 (status display).
3. **Then the fixes still to build, in the user's ranking** ([FIX-RANKING.md](FIX-RANKING.md)):
   T-06 and T-07 (low-battery announcements, clean shutdown), then the depth floor re-run and the detection
   phantom re-run (no code until they are re-run on the device), T-12 (memory per vision restart), T-02a
   (save the panel's settings), T-05 (bicycle and tricycle), T-11 (stop right after a start), the NaN depth
   frame, the strength knob test.
4. **Not built (stated plainly, 2026-10-19):** T-02a, T-05, T-06, T-07, T-11, T-12, T-14, and the battery rundown
   measurement (REQ-PERF-01).

## Demo track → board (Lead, checked against the code 2026-10-19, AGENTS.md Rule 7)

Every claim below was checked in the working tree: the new function has a caller in the running program,
not only in tests.

| Demo item | Board tasks it built | Where the code is (callers confirmed) |
|---|---|---|
| F0 spoken camera alerts | T-03c1 (with T-03d) | `pipeline/app.py` `_raise_camera_missing`, `_announce_camera_back`, `_confirm_camera_back`; called from `_on_camera_lost`, `_wait_for_camera`, `_start_without_camera` |
| F1 patterns and the D-15 overrides | T-00b, T-00c | `core/haptic_patterns.py` (`PatternPlayer`, made in `main.py`, played by `workers/serial_worker.py`); `core/alerts.py` `failsafe_level`, `is_failsafe`; `workers/tts_worker.py` `_item_amplitude` |
| F2 glasses board lost and back | T-01a, T-01b | `core/connections.py` `glasses_lost`, `glasses_back`; called from `workers/serial_worker.py` `_report_glasses_link` |
| F3 earphones lost and back | T-04a, T-04b | `core/connections.py` `EarphonesWatcher`, `earphones_present`; `workers/earphones_worker.py`, started in `main.py` as `earphones-watch`; silent alerts through `alerts.raise_alert(speak=False)` |
| F4 camera alert vibration | T-03c2 | `core/alerts.py` `raise_alert(on_speak=)`, called by `AlertScheduler.take()` (the speech worker's side) outside the lock; `pipeline/app.py` `_raise_camera_missing` passes `patterns.play(PATTERN_CAMERA)` |
| F6 control panel lost and back | T-02b, T-02c (**not T-02a**) | `core/connections.py` `panel_lost`, `panel_back`; called from `workers/config_reader.py` `_report_panel_link` (the worker's loop changed, named, Rule 3) |
| F7 an alert in progress finishes | T-00d (D-16, recorded as D42) | `workers/tts_worker.py` `_cuts` (called in `_speak_interruptible`); `core/alerts.py` `_step_locked` |
| F5 camera blocked, dark, bright | T-13 | `core/camera_view.py` `CameraViewWatch` (made in `main.py`); `pipeline/app.py` `_camera_view_tick`, `_on_view_buffer`, started in `run()` |
| status on screen and in the terminal | T-16 (new row) | `core/status_board.py`, `core/status_view.py`, `workers/status_worker.py` (`status-board` thread in `main.py`); the drawing is called from `pipeline/callbacks.py` `_draw_detection_overlay` |
| F8 the demo run | T-17 (new row) | `docs/testing/tools/demo_run.py`; battery columns in `docs/testing/tools/perf_*.py` |

The tree before this work is `.claude/baselines/T-03c1/` (taken 2026-10-18 08:15, with T-00a, T-03a and T-03b
in it). The user has committed the demo work (git `51e9137` "snapshot: before fast track" to `9e36a81`), so
git is clean; the per-task diff is still tree against that baseline.

**Protected files in the demo-track diff** (`diff -ru` against the baseline): `pipeline/callbacks.py` has one
hunk, 24 lines, in `_draw_detection_overlay` plus an import guard and a throttled error log: the cv2 overlay
drawing of the status (T-16, the user's request 2026-10-19). It is not the detection processing. The Tester
confirms the hunk touches no detection logic. `core/depth_utils.py`, `core/haptics.py` and `firmware/`: no
change.

The P1 tasks are large. The Lead **splits each into subtasks** before starting it (e.g.
T-03a, T-03b). Each subtask is one behaviour, a few files, and independently testable, and
is added here as its own row.

## Task notes

These are facts gathered while writing the requirements, so the Lead doesn't rediscover them.

**T-00 Alert output foundation (proposed).** T-01, T-03 and T-04 need two things the Pi can't do
yet:
1. **Vibration patterns on all three motors.** These are *beep beep* for a reconnect, a
   different *beep beep* for the camera alert, and "strong weak ×3" for missing earphones. They
   must be made from the Pi with commands the glasses firmware already accepts.
2. **Spoken system alerts that repeat on a schedule** (every 10 s, 30 s, 5 min, or once). The
   speech mailbox already accepts `{"announce": str}` at top priority.

To settle in its brief:
- how patterns coexist with the continuous depth-to-motor stream;
- whether the strength knob scales alert patterns.

The second is a UX decision to ask the user.

**T-01.** Detecting the link going down and coming back already exists and is unit-tested in
`workers/serial_worker.py` (`test_link_goes_down_after_the_timeout` and the related tests).
Today nothing announces it. Cadence: every 30 s while depth is enabled; once while it is
disabled, including when switched off mid-run.

**T-02.** The panel sends `B:alive` every 10 s, and `workers/config_reader.py` already notices
30 s of silence (log only). Settings aren't saved between sessions today; the built-in
defaults are always used at start-up. "Last session's settings" therefore needs saving and
restoring.

**T-03.** Per the team, removing the camera produces a Hailo "No available devices" error that
crashes the whole program.
- **What's known:** hailo-apps' own USB-camera lookup ends the program with `exit(1)` when there
  is no camera (`hailo_apps/python/core/gstreamer/gstreamer_app.py`, read only, never edited).
  The boot launcher passes `/dev/video0` directly instead.
- **Seen in the system log (2026-10-15):** with the camera removed mid-run, the camera element
  reports "Could not read from resource … poll error 1: Invalid argument (22)"; the library
  turns it into a shutdown and the program exits with status 1. "No available devices" didn't
  appear. Those five drops happened without anyone unplugging the camera; the team treats
  them as a one-off. **If the camera drops out on its own again, flag it to the user.**
- **Decided for T-03b (start-up without a camera), 2026-10-15:** remove the launcher's
  automatic `--mock` fallback (`scripts/sv-main.sh:69-74`). Under the boot service it can
  still be reached if the camera drops between the launcher's two checks. Also in T-03b's
  scope under D-13: `main.py` enters mock mode by itself when `hailo_apps` fails to import
  (`if pre_args.mock or not HAILO_AVAILABLE`, `main.py:160`).
- **Constraints:**
  - The fix lives in this repo (`pipeline/app.py`, `main.py`).
  - Pipeline state changes go through `GLib.idle_add`, which is AGENTS.md Rule 4.
  - The Hailo device must be released and re-acquired as the docs describe.
- **Risk notes:** the GStreamer/Hailo lifecycle, threading, and hardware-only reproduction.

**T-10 (proposed 2026-10-15).** Fewer hands-on steps. The unit tests run in the Poetry
environment, which has no GStreamer or HailoRT bindings. `my_hailo_env` has both but no
pytest. So today nothing can test the pipeline automatically.
- **Prerequisite, done once by the user** (agents may not change environments):
  `/home/sv-rpi5/Projects/Second-Vision/second-vision-repo/my_hailo_env/bin/pip install pytest pytest-timeout`.
  A dry run on 2026-10-15 showed it only adds pytest 9.1.1, pluggy 1.7.0, iniconfig 2.3.1 and
  pytest-timeout 2.4.0, and upgrades nothing.
- **Split:**
  - **T-10a:** test levels. Markers `gst` (needs GStreamer, no devices) and `device` (needs
    the Hailo chip, camera or boards) skip automatically when the bindings are missing, the
    device is busy or `second-vision.service` is running. A default timeout makes a hung
    teardown fail instead of freezing the run. Add the commands for each level, and a helper
    that posts an error from a pipeline element and runs the GLib loop briefly. An
    **evidence run**: one command that runs every automated UAT test and saves a dated log
    listing each case ID with PASS / FAIL, for the thesis panel. Needed by T-03a.
  - The simulated camera unplug already exists: `.claude/tools/usb_sim.py` (local, root via
    the hook's exception; see the Lead's instructions). Device tests use it rather than
    their own sysfs writes.
  - **T-10b:** fake boards on pseudo-terminals, plus captured speech. A glasses ESP32 that
    answers or goes silent and records the motor packets. A control panel that sends
    `S:key:value` lines and can go quiet. The phrases and volume the speech worker would play.
    Needed by T-01, T-02, T-04 and the panel cases.
  - Later: a fake battery gauge (with T-06), and a recorded video clip for running the real
    pipeline without a camera (TC-013, TC-015).
- **Scope:** `tests/` and `pyproject.toml` (test settings, `pytest-timeout` as a dev
  dependency). No production code. Developer: Sonnet; it is test-side code only.

**T-04.** How the Pi detects the earphones isn't specified (see W-19); `aplay -l` lists output
devices.

**T-05.** Ownership confirmed (2026-10-15): the detection workstream owner asked for it. Changes
`DETECTION_CLASS_MAP` and the priority tables keyed by label. That is a label table, not the
detection algorithm. `tests/test_detection_classes.py` pins the current label set and must be
updated with it.

**T-07.** The power button: with the desktop running, one press opens a dialog and a second press
shuts down cleanly, stopping `second-vision.service` first (`scripts/install-service.sh`). The
announcement belongs on that clean-stop path.

**T-09 (withdrawn 2026-10-15).** It would have brought in the floor-preserving strength fix
(`3797182`, on `feat/depth-callback`). The team decided to keep this branch's knob behaviour
(D-14), so there is nothing to build. Don't reuse the ID.
