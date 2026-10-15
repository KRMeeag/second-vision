# Task board

Implementation tasks derived from [REQUIREMENTS.md](REQUIREMENTS.md), worked through the
Lead / Developer / Tester workflow. Per-case status lives in [TRACEABILITY.md](TRACEABILITY.md);
reports live under [reports/](reports/). The team-level checklist in
`documentation/TASKS.md` mirrors the top-level rows here.

**Only the Lead edits this file.**

## Active task

| Active task | Attempt | State | Since |
|---|---|---|---|
| *none* | — | — | — |

There is **one active implementation task at a time**. A new task may start only when the
active slot is empty: the previous task is `ACCEPTED`, or parked as `BLOCKED` after the user
decided whether to keep or revert its changes.

## Lifecycle

| State | Meaning | Next |
|---|---|---|
| `BACKLOG` | Known, not yet broken down or not yet startable | `READY` |
| `READY` | Bounded, with acceptance criteria; dependencies met; can start | `IN-DEV` |
| `IN-DEV` | Brief written, baseline taken, Developer working | `IN-TEST` |
| `IN-TEST` | Tester verifying independently | `IN-REVIEW` |
| `IN-REVIEW` | Lead reviewing diff, both reports, and criteria | `ACCEPTED`, `RETURNED` or `BLOCKED` |
| `RETURNED` | Tester or Lead found a reproducible failure; back to the Developer with evidence | `IN-DEV` (next attempt) |
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

The Lead picks one model per task before it starts, and records it here with the reason:
- **`developer-sonnet`**: bounded, local, low-risk work. Most tasks.
- **`developer-opus`**: complex debugging, cross-module or threading/timing work, the
  GStreamer/Hailo lifecycle, and high-risk changes.

The **Tester** runs on Sonnet, and on Opus whenever the task's Developer is Opus. The **Lead**
runs on Sonnet; for an Opus task the user may start it on Opus
(`.claude/tools/lead.sh --opus`). Every role runs at extra-high effort (`xhigh`).

A task touching the AI pipeline is **not** automatically Opus. The rubric is in the Lead's
instructions.

## Tasks

P1 = critical failsafes, done first (team, 2026-10-11). P2 = everything else.

| Task | Title | Pri | State | Requirements | UAT cases | Depends on | Developer | Blockers | Reports |
|---|---|---|---|---|---|---|---|---|---|
| T-00 | Alert output foundation *(proposed)* | P1 | BACKLOG | REQ-CON-04, 07, 10, 12 (patterns); REQ-CON-02, 05, 07 (repeating speech) | via T-01 to T-04 | — | — | Lead to confirm the split | — |
| T-01 | Glasses ESP32 link-loss failsafe | P1 | BACKLOG | REQ-CON-02, 03, 04 | TC-022-002 to 007 | T-00 | — | — | — |
| T-02 | Control panel link-loss failsafe and last-session settings | P1 | BACKLOG | REQ-CON-05, 06 | TC-023-002, 004, 007 | T-00 (speech part) | — | — | — |
| T-03 | Camera missing or removed never crashes the device | P1 | BACKLOG | REQ-CON-07, 08, 09 | TC-024-002, 003, 004 | T-00 (alert part only) | — | — | — |
| T-03a | Camera removed while running: the program stays up and restarts vision when a camera returns (subtask of T-03; no alerts) | P1 | READY | REQ-CON-08 (removal half); REQ-DTA-08 (regression) | TC-024-003 (partial: not its REQ-CON-07 alert) | pytest in my_hailo_env (T-10a): its tests need GStreamer | developer-opus: GStreamer/Hailo lifecycle (teardown, device release, rebuild) and main-loop timing shared with the panel thread | None for development (pre-start questions answered 2026-10-15, see brief-1). Device steps need the boot service stopped by the user first | [brief-1](reports/T-03a/brief-1.md) |
| T-04 | Earphones missing or lost failsafe | P1 | BACKLOG | REQ-CON-10, 11, 12 | TC-025-002 to 007 | T-00 | — | — | — |
| T-05 | Each model class gets its own spoken label | P2 | BACKLOG | REQ-DET-01, 02 | TC-001-001, 002 | — | — | — | — |
| T-06 | Battery announcements and low-battery shutdown | P2 | BACKLOG | REQ-PWR-02, 04 | TC-028-*, TC-029-002 | T-00 (speech part) | — | — | — |
| T-07 | "Device shutting down." on clean shutdown | P2 | BACKLOG | REQ-PWR-03 | TC-029-001 | — | — | — | — |
| T-08 | Depth pipeline status and recovery | — | DEFERRED | REQ-DHP-06, 07 | TC-018-001, 002 | — | — | Deferred (D-08) | — |
| T-10 | Test harness: pytest for GStreamer and device tests, fake boards, captured speech *(proposed)* | P1 | BACKLOG | — (enabler: automated checks for REQ-CON, REQ-PNL, REQ-PWR, REQ-DTA) | none directly; replaces most hands-on steps (MANUAL-TESTS.md, "How much of this can be automated") | the user installs pytest into my_hailo_env | — | Agents may not change environments: the user runs the install (task note) | — |

Suggested order, for the Lead to confirm:
0. **T-10a first** (small): T-03a's tests need GStreamer under pytest, which no environment
   has yet.
1. **T-03's no-crash core first.** It is the most serious failure, a crash, and it needs no alert
   mechanism.
2. **Then T-00**, which unblocks the alert parts of T-01 to T-04.
3. **Then T-01, T-04, T-02**, then the P2 tasks.

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
