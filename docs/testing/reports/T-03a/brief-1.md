# T-03a · Camera removed while running: stay up, restart vision when a camera returns · Brief, attempt 1

> **Written during the 2026-10-15 dry run; the user's answers to the pre-start questions are
> recorded at the end (2026-10-15). Amended by the Lead on 2026-10-16 before attempt 1:**
> T-10a is accepted (pytest, the `gst` and `device` levels, the bus helpers and the
> `camera_unplug` fixture exist), the Lead re-read `app.py` and the library's `bus_call`, and the
> sections "Design notes from the Lead's reading" and "Testing notes (2026-10-16)" were added.
> Nothing in the acceptance criteria changed.

- Date: 2026-10-15 (amended 2026-10-16)
- Requirements: REQ-CON-08, **only its "removed while running" half**. Quoted from REQUIREMENTS.md:
  > when the device runs as deployed (started at boot) and the camera is absent at start-up or
  > removed while running:
  > - the program does not crash;
  > - every other part keeps running: the glasses and panel links, speech, and the alerts above;
  > - the device keeps checking the USB ports for a camera;
  > - when a camera appears, the vision pipeline starts on it.
  >
  > It must never fall back to the testing mock mode.
- Regression requirement: REQ-DTA-08, "Ctrl+C while detecting and speaking stops both threads after
  their current loop, with no zombie processes."
- UAT cases: TC-024-003, partly (see `uat-snapshot.yaml`, `Test Cases!A263:G263`). Its REQ-CON-08
  part is this task. Its REQ-CON-07 part (the vibration pulses and the "Critical Error: Camera
  cannot be reached" speech every 10 s) is **not** in this task: it waits for T-00 and becomes
  T-03c. The case therefore stays NOT-BUILT after this task, with partial evidence noted.
- Priority / depends on: P1 / none. The alert part, which needs T-00, is excluded.
- Developer: **developer-opus**. Why: the routing rubric names "surviving camera removal (T-03)"
  as Opus work. This is the GStreamer/Hailo pipeline lifecycle: tearing down a pipeline whose
  source has failed, releasing and re-acquiring the Hailo device, and rebuilding. It is also
  timing on the GLib main loop, shared with a panel thread that can request rebuilds at any
  moment (`config_reader.py:103-104`).
- Tester model: **opus**, because the Developer is Opus (the Lead's routing rule).
- Test environment: the tests need GStreamer, which only `my_hailo_env` has. T-10a (accepted
  2026-10-16) provides pytest there, the `gst` marker, `tests/gst_helpers.py` and the default
  60 s timeout. Run them with `my_hailo_env/bin/python -m pytest tests/ -q`; the hardware-free
  suite still runs in the Poetry environment (the `gst` tests skip there). See "Testing notes".
- Baseline: .claude/baselines/T-03a/ (taken 2026-10-16, before attempt 1).

## What happens today (checked by the Lead against the code, 2026-10-15)

When the camera is unplugged while the pipeline runs:

1. The installed library's bus handler turns **any** GStreamer error into a shutdown. It sets
   `error_occurred = True` and calls `self.shutdown()`
   (`hailo_apps/python/core/gstreamer/gstreamer_app.py:481-485`). An end-of-stream from a
   non-file source also calls `shutdown()` (`:497-505`).
2. `SecondVisionApp.shutdown()` sets `shutdown_event`, which stops every worker, and starts a
   5 s force-exit timer (`src/second_vision/pipeline/app.py:854-863`). Its docstring names this
   exact case: "Bus errors land here too … an unplugged camera" (`app.py:850-852`).
3. `run()` ends in `sys.exit(1)` because `error_occurred` is set (`gstreamer_app.py:751-753`).
4. `main()`'s `finally` joins the workers and the process exits (`src/second_vision/main.py:171-177`).
   On its way out the serial worker sends a zero motor command (`workers/serial_worker.py:267-270`).
5. The boot service restarts the program after 5 s (`scripts/second-vision.service:57-58`). The
   launcher then waits up to 60 s for `/dev/video0` and exits again if it never appears
   (`scripts/sv-main.sh:39-57`). This loop repeats until the camera returns.

So today, "removing the camera crashes the program" means the whole program ends:
- speech stops;
- the glasses link stops, and its heartbeat with it;
- the panel is ignored.

It comes back only through systemd's restart loop. Nothing inside the program watches for the
camera.

Two more facts any fix must respect:
- **Stale motors.** While the program runs and no depth readings arrive, the serial worker sends
  only heartbeats (`serial_worker.py:220-224`). The glasses board **holds its last motor command**
  (`serial_worker.py:148-150`). A program that merely stays up would therefore leave the wearer
  feeling the last obstacle reading indefinitely.
- **The library's rebuild ends the program when the new pipeline fails to start.** It calls
  `self.loop.quit()` on a PLAYING failure or an exception (`gstreamer_app.py:599-603`,
  `:610-613`). A rebuild attempted while the camera is missing would end the program. That
  includes a panel mode switch, which calls `app.trigger_rebuild()` (`config_reader.py:94-105`).

## Goal

When the camera is removed while the device runs, the program stays up:
- speech, the glasses link and the panel keep working;
- the dead pipeline is stopped and the Hailo device released;
- the motors are not left on a stale reading;
- the program keeps checking for a USB camera.

When a camera appears, the vision pipeline starts on it again, in the mode currently selected,
inside the same process, without mock mode at any point.

## In scope

- `src/second_vision/pipeline/app.py`, class `SecondVisionApp` only:
  - recognise a failure or end-of-stream coming from the **camera source** and handle it
    without calling `shutdown()`;
  - keep a camera-lost state;
  - tear down through the existing `_release_old_pipeline()`;
  - check for a camera on the GLib main loop;
  - rebuild through the existing `_rebuild_pipeline()` / `trigger_rebuild()` path;
  - make `trigger_rebuild()` safe while the camera is missing;
  - send one zero motor command through `serial_queue` (criterion 3).
- New tests: `tests/test_camera_loss.py` (unit and mock-integration; no camera, no Hailo).
- Anything else (for example `main.py`): stop and ask the Lead first, with the reason.

## Out of scope: must not change

- **Protected files:**
  - the vision algorithms: `core/depth_utils.py`, `core/haptics.py`, and the detection and depth
    processing in `pipeline/callbacks.py`;
  - `firmware/`;
  - every installed library (`my_hailo_env`, including `hailo_apps`; AGENTS.md Rule 1). Override
    in the subclass; never patch.
- **Contracts (Rule 2):**
  - the `serial_queue` dict keeps exactly `left, center, right, hazard, hazard_severity`;
  - `tts_queue` items are unchanged;
  - worker entry signatures are unchanged (Rule 3).
- **Workers:** `workers/tts_worker.py`, `workers/serial_worker.py` and `workers/config_reader.py`
  are unchanged.
- **Scripts:** `scripts/` is unchanged (`sv-main.sh`, `run.sh`, the service). The start-up case is
  T-03b.
- **No new user-facing output.** No spoken alert, no vibration pattern, no "camera has been
  reconnected" phrase. Those are REQ-CON-07 (T-03c) and REQ-CON-09 (T-03d).
- **No mock mode on any path (D-13).** No code may start the mock generators or pass `--mock`.
- **Errors from any element other than the camera source** keep today's behaviour: shutdown, then
  the boot service restarts the program.
- **Mode switching with the camera present is unchanged:**
  - `MIN_REBUILD_INTERVAL_SECONDS`;
  - request coalescing;
  - `_release_old_pipeline()`'s ordering;
  - `_on_pipeline_rebuilt()`'s resets.
- **The clean-shutdown path is unchanged in behaviour:** `shutdown()` and
  `_force_exit_if_stuck()`.

## Acceptance criteria

Each criterion restates a requirement; the quoted words are the requirement's.

1. **No crash.** REQ-CON-08: "the program does not crash". When the camera is removed while the
   pipeline is running, the program keeps running: the same process, no exit, no traceback. This
   holds in every pipeline mode, including "none", whose idle pipeline also holds the camera.
2. **Everything else keeps running.** REQ-CON-08: "every other part keeps running: the glasses
   and panel links, speech". While the camera is missing:
   - the speech, glasses-link and panel threads stay alive;
   - the glasses link keeps its heartbeat, so the camera loss causes no
     "ESP32 stopped acknowledging";
   - a panel mode switch is still announced ("<mode> mode") and recorded;
   - that mode switch neither ends the program nor tries to start a pipeline on the missing
     camera.
3. **Motors not left on a stale reading.** This keeps today's end state, confirmed by the user
   (Q-B). After the camera is lost, one zero motor command reaches the glasses.
   It goes through the existing `serial_queue` contract dict, with all five keys and every value
   0 / False. It is not sent through a new channel.
4. **Keeps checking; restarts on a camera.** REQ-CON-08: "the device keeps checking the USB ports
   for a camera; when a camera appears, the vision pipeline starts on it". While the camera is
   missing, the program keeps checking for a USB camera. When one appears, the vision pipeline
   starts on it:
   - in the mode currently selected;
   - inside the same process;
   - with the Hailo device re-acquired, so no `HAILO_OUT_OF_PHYSICAL_DEVICES` and no
     "not enough free devices".

   A camera that comes back under a different `/dev/video*` node counts as "a camera appears".
5. **Never mock mode.** REQ-CON-08: "It must never fall back to the testing mock mode" (D-13).
   Nothing the camera loss sets off enters mock mode: no mock generator thread, no
   `[MAIN] Running in MOCK mode`, no generated detections or depth.
6. **Clean stop still works.** REQ-DTA-08 regression. With the camera missing, Ctrl+C, and
   SIGTERM as a service stop sends it, still end the program cleanly:
   - within the existing bounds (`SHUTDOWN_FORCE_EXIT_SECONDS`, the unit's `TimeoutStopSec=10`);
   - motors at zero;
   - no leftover process.

   The full unit suite still passes.

## Verification expectations

| Criterion | Minimum evidence level for VERIFIED | Hardware needed |
|---|---|---|
| 1 | device-integration + user-observed (a person unplugs the camera); mock-integration first | camera, Hailo |
| 2 | device-integration + user-observed (the person hears the mode announcement during the loss) | glasses ESP32, panel, earphones |
| 3 | unit + device-integration + user-observed (the person feels the vibration stop after unplugging) | glasses ESP32 |
| 4 | device-integration + user-observed (the person replugs, including into another port; vibration responds again) | camera, Hailo, glasses ESP32 |
| 5 | unit + device-integration (log) | camera |
| 6 | mock-integration + device-integration; unit suite | camera |

Mock evidence alone leaves criteria 1 to 4 at READY with partial evidence, never VERIFIED.

**Updated 2026-10-16 (team decisions):**
- On the device, the Tester repeats the unplug and replug with the simulated camera unplug
  (`sudo .claude/tools/usb_sim.py detach camera --for <s>` / `attach camera`, service
  stopped). That counts as device-integration evidence.
- The person then confirms the real camera **once** (MANUAL-TESTS.md, T-03a). Under the
  per-device rule in TRACEABILITY.md that is enough user-observed evidence for criteria 1 to 4.
- The Tester saves each run's log under `reports/T-03a/evidence/`.

### For the Developer: testing without a person

- Use real GStreamer with no camera and no Hailo:
  - build the app without `__init__`, as `tests/test_pipeline_modes.py` does, or build a minimal
    pipeline whose source element is named `source`;
  - post an error from that element to the bus (`Gst.Message.new_error`) and run the GLib loop
    briefly;
  - assert that `shutdown()` is not called, that a zero dict arrives in `serial_queue`, and that
    the check for a camera is scheduled;
  - monkeypatch the camera lookup to report a camera, and assert that a rebuild follows on the
    main loop;
  - also cover: an error from a *non-source* element still shuts down; `trigger_rebuild()` during
    the loss does not build; a shutdown during the loss exits.
- Show that each new test **fails on the baseline**. Copy `.claude/baselines/T-03a/src` into a
  scratch folder and point the test at it, or explain the method you used.
- Don't run the real pipeline, unplug anything, or stop services. The device runs belong to the
  Tester and to the Lead with the user. On 2026-10-15 `second-vision.service` was active.

## Design notes from the Lead's reading (2026-10-16; hints, not instructions)

The Lead re-read `pipeline/app.py` and the library's `gstreamer_app.py` (byte-identical to the
workbench copy as of 2026-10-15). The Developer decides the design and owns the Hailo-docs check.
Facts to start from, so nothing is rediscovered:

1. **Where the errors go.** `bus.connect("message", self.bus_call, self.loop)` is made in the
   library's `run()` and again in its `_rebuild_pipeline()`. Both look `bus_call` up on `self`, so
   an override of `bus_call` in `SecondVisionApp` is used on every pipeline, the first and every
   rebuilt one. Any ERROR in the library's `bus_call` sets `error_occurred` and calls
   `shutdown()`. An override must send **only camera-source** messages to the new handling and
   everything else to `super().bus_call(...)`.
2. **Naming the camera source.** `SOURCE_PIPELINE` names the v4l2 element `source` (library
   `gstreamer_helper_pipelines.py`, `SOURCE_PIPELINE(name="source", …)`); the decode helpers are
   `source_queue_decode` and `source_decodebin`. An ERROR's `message.src` is the element that
   failed. Classify by that element, as the brief's R3 says; do not match on message text.
3. **End-of-stream has no useful `message.src`.** A bus EOS message is posted by the pipeline
   itself, so it cannot be traced to an element. In these pipelines nothing but the live camera
   can end a stream (`source_type` is not `"file"`), so an EOS on a non-file source is a camera
   loss. Say in your report how you justified treating it so, and test that a *file* source
   (`source_type == "file"`) keeps the library's looping behaviour.
4. **`video_source` is fixed at construction.** The library resolves `/dev/videoN` once and
   `get_source_pipeline()` reads `self.video_source`. A camera that returns under another node
   needs `self.video_source` updated before the rebuild (criterion 4's last paragraph).
5. **The library's `_rebuild_pipeline()` quits the loop on any failure** (R4). When a rebuild
   onto a returned camera fails (the node exists but cannot be opened yet, or the device has not
   settled), the program must go back to "camera lost" and try later, not exit. The subclass may
   implement its own rebuild for this path; it must still go through `_release_old_pipeline()`'s
   ordering and run on the main loop.
6. **Checking for a camera costs processes.** `get_usb_video_devices()` runs `udevadm` once per
   `/dev/video*` node (about 25 nodes on this Pi: the camera's four plus the SoC's decoders). A
   check on the GLib thread blocks the loop for that long. A small daemon thread that polls and
   then calls `GLib.idle_add` with the result keeps the loop free, honours `shutdown_event` and
   needs no extra locking, because the pipeline is still only touched on the main loop. Choose
   the interval and justify it (R6).
7. **After a loss, `self.pipeline` is `None`.** `shutdown()` already handles that (the `Shutdown
   during pipeline rebuild — quitting main loop directly` path), but the library's `run()` then
   hits `None.set_state(...)` in its cleanup, which it catches and logs. Check that the exit
   code stays 0 after a loss followed by a clean stop (R5) and that the log is not misleading.
8. **The panel thread calls `trigger_rebuild()`** and reads `app.pipeline`-related state only
   through it. Keep any new "camera lost" flag readable from a thread without a lock problem
   (a plain attribute set on the main loop is enough) and never touch GStreamer from the thread.

## Testing notes (2026-10-16)

- **Developer tests**: `tests/test_camera_loss.py`, marked `gst` (T-10a: they skip in the Poetry
  environment and run in `my_hailo_env`). Use the helpers in `tests/gst_helpers.py`
  (`make_test_pipeline`, `post_error`, `post_eos`, `bus_watch`, `run_main_loop`) and the
  real `GStreamerApp` base the harness pre-imports. Put the UAT case and requirement in the
  names (`test_tc024_003_con08_<what>`) so the evidence log lists them, and say in each
  docstring that this is the **removal half** of TC-024-003 only (the alert half is T-03c), so
  the evidence log's case line is read in that light.
- **Developer: do not use `--run-device`**, do not run `usb_sim.py`, do not start the real app.
  The Tester does.
- **Tester, device level** (`tests/uat/`, marker `device`, `--run-device`, boot service stopped,
  checked first): launch the program as the boot service does (`scripts/run.sh --input
  /dev/video0 --width 640 --height 480 --serial-port /dev/serial0 --config-port /dev/ttyAMA3`,
  without `SV_SERVICE=1`) as a subprocess, capture its output, and use the `camera_unplug`
  fixture. Stop the subprocess by its PID. Cover at least: unplug in `both`, `depth`, `detection`
  and `none` modes; replug; repeated unplugs (three in a row); an unplug while a mode switch is
  pending; a clean stop (SIGINT, and SIGTERM) while the camera is missing.
- **Tool defect to design around (found by the T-10a Tester, 2026-10-16).** Each
  `usb_sim.py detach --for N` leaves a timer that, after N s, re-attaches *whichever* detach is
  current. A 60 s detach was re-attached after 7 s. So: use `--for 20` or less, call `attach`
  explicitly when a test is done, wait at least the previous `--for` before the next detach, and
  assert the camera is still absent before relying on it. The tool is outside the workflow's
  write access; the Lead reports the defect to the user.
- The fixture's "detach for 10 s" is really about 0.1 s in the harness's own test, because the
  test attaches as soon as the nodes vanish. These tests must hold the camera away themselves
  (sleep, then attach).
- Save every run with `my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a --device
  --tests <paths> --label <name>`, or as a pytest log, under `reports/T-03a/evidence/`.

## References

- **AGENTS.md:**
  - Rules 1 and 2;
  - Rule 3 (worker contracts);
  - Rule 4: pipeline state changes only on the GLib main loop. The bus watch and
    `GLib.timeout_add`/`idle_add` callbacks run there; never `set_state` from a worker thread;
  - Rule 6: say where you checked each library call;
  - Rule 7.
- **Installed library, read only:**
  `my_hailo_env/lib/python3.13/site-packages/hailo_apps/python/core/gstreamer/gstreamer_app.py`.
  The Lead found it byte-identical to the workbench copy (`diff -q`, 2026-10-15). Relevant parts:
  - `bus_call` 475-495;
  - `on_eos` 497-505;
  - `_rebuild_pipeline` 541-616;
  - `shutdown` 618-639;
  - `run` 694-756.
- **Camera lookup:** `hailo_apps/python/core/common/camera_utils.py` `get_usb_video_devices()`
  391-418, Linux branch 421-448.
  - On Linux it returns **paths** (`/dev/videoN`), keeping only USB capture devices.
  - It runs one `udevadm` subprocess per `/dev/video*` node.
  - Note: `.hailo/memory/camera_and_display.md:19` in the workbench says it returns integer
    indices. That is wrong for Linux; trust the source.
- **hailo-apps docs** (`/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`):
  - `.hailo/memory/camera_and_display.md`: "Never assume `/dev/video0`";
  - `.hailo/toolsets/core-framework-api.md`: `get_usb_video_devices`;
  - `.hailo/instructions/gstreamer-pipelines.md`: watchdog and rebuild;
  - `.hailo/memory/common_pitfalls.md`: `HAILO_OUT_OF_PHYSICAL_DEVICES`.

  No hailo-apps document describes recovering from a camera unplug. The established pattern is
  the library's own `_rebuild_pipeline()`, used for file looping and by the watchdog, together
  with this repo's `_release_old_pipeline()`.
- **This repo:**
  - `pipeline/app.py`:
    - 121-127 and 705-706: tearing down a v4l2 source mid-teardown has segfaulted;
    - 665-725: `trigger_rebuild` / `_rebuild_pipeline`;
    - 756-831: `_release_old_pipeline`;
    - 833-887: shutdown and force exit;
  - `workers/serial_worker.py` 148-150, 220-224, 267-270;
  - `workers/config_reader.py` 94-105;
  - `main.py` 159-177;
  - `tests/test_pipeline_modes.py`.

## Open risks: stop and report instead of guessing

- **R1, teardown hang.** The pipeline's PAUSED→READY transition deadlocks about 1 stop in 4
  (`app.py:843-847`). If tearing down after a camera loss can block the GLib main loop,
  recovery silently never happens. Add no unbounded wait on the main loop. If you can't bound
  the teardown, stop and report. Don't invent a fallback such as exiting the program.
- **R2, native crash.** Tearing down a v4l2 source has segfaulted inside `gst_object_unref`
  (`app.py:121-127`), and Python can't catch that. Report any segfault you see, even once.
- **R3, the real unplug message is unknown.** It may be:
  - an ERROR from `v4l2src name=source`;
  - an end-of-stream;
  - a Hailo error. The team reported "No available devices", which has not been traced.

  Classify by the element that sent the message, not by its text alone. Leave every other
  element's error on today's path.

  **Observed (system log, 2026-10-15, boot service on `deploy/v2`):** five exits between
  19:35 and 19:49, each the same message from the camera element:
  `GStreamer Error: gst-resource-error-quark: Could not read from resource. (9), debug:
  ../sys/v4l2/gstv4l2object.c(6253): gst_v4l2_object_poll (): /GstPipeline:pipeline1/GstV4l2Src:so…
  poll error 1: Invalid argument (22)`, then "Shutdown initiated", "Exiting with error", exit
  status 1, and a systemd restart 5 s later. "No available devices" did not appear. Once
  (19:35:56) the error came 2 s after a fresh start with the camera still attached, so R4's
  "camera present but failing again" is a real path, not a hypothetical one.
- **R4, rebuild onto a missing camera.** The library quits the main loop when the new pipeline
  fails (`gstreamer_app.py:599-603`, `:610-613`).
  - Only rebuild once a camera is present.
  - Report what happens if the camera vanishes again mid-rebuild, and how you handled it.
- **R5, `error_occurred`.** The library sets it on any ERROR, and then a later clean stop exits
  with code 1 (`gstreamer_app.py:751-753`). Don't let a recovered camera loss turn a later clean
  stop into an error exit, or report why it must.
- **R6, cost of checking.** Each check runs `udevadm` once per video node, on the main loop if
  done there. Keep checks cheap and their interval sensible. The requirement gives no interval;
  choose one and justify it in your report.
- **R7, threading.** `trigger_rebuild()` is called from the panel thread. It must stay
  schedule-only (`GLib.timeout_add`) and never touch the pipeline directly.
- **R8, motors.** Settled: zero after a loss (Q-B).

## Pre-start questions, with the user's answers (2026-10-15)

- **Q-A. The device is in mock mode right now.**
  - On 2026-10-15 the boot service's process (PID 9718, active since 12:22:35) was
    `main.py --mock --serial-port /dev/serial0 --config-port /dev/ttyAMA3`.
  - Agents may not stop services.
  - Device verification of this task needs the service stopped either way.
  - **Answer:** no action now. By 19:49 the service was running the real pipeline (process
    title `Parallel-Depth-Detection-V4`); the mock run was earlier, before a reboot. The user
    stops the service before the hands-on steps (MANUAL-TESTS.md, "Before a hands-on
    session").
- **Q-B. Motors after camera loss.** Proposed: zero, which is what happens today because the
  program exits. Criterion 3 depends on the answer.
  - **Answer:** zero.
- **Q-C. Reproduce before the Developer starts?** A person unplugs the camera once, with this
  branch run by hand, and the Lead records the exact log for R3.
  - **Answer:** not needed; the system log already has the error (R3). Nobody unplugged the
    camera at 19:35 to 19:49: the teammate holding it confirmed it. The user takes it as a
    one-off and asks to treat it as an unplug for this task. **If the camera drops out again
    without being unplugged, flag it to the user.**
- **Q-D. The desk launcher's mock fallback** (`sv-main.sh:69-74`). This doesn't block this task;
  it affects T-03b.
  - **Answer:** remove it (T-03b). Mock mode only through an explicit `--mock`
    (DISAGREEMENTS.md D-13).

## For attempt > 1: failures to fix

Not applicable (attempt 1).
