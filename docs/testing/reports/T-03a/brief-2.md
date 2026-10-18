# T-03a · Camera removed while running: stay up, restart vision when a camera returns · Brief, attempt 2

> **Read brief-1.md first.** It applies in full (requirement, goal, scope, protected files,
> contracts, acceptance criteria 1 to 6, design notes, references), except where this brief
> amends it. This brief adds the failures to fix, what the Developer may now do on the device,
> and the repeat counts that count as "fixed". The acceptance criteria are still brief-1's;
> nothing about what the requirement asks has changed.

- Date: 2026-10-17
- Requirements: REQ-CON-08, **its "removed while running" half**, quoted in brief-1:
  > the program does not crash; every other part keeps running; the device keeps checking the
  > USB ports for a camera; when a camera appears, the vision pipeline starts on it. It must
  > never fall back to the testing mock mode.

  Regression: REQ-DTA-08 (Ctrl+C stops cleanly, no zombie processes).
- UAT cases: TC-024-003, partly (its REQ-CON-07 alert part is T-03c). Unchanged from brief-1.
- Priority / depends on: P1 / T-10a (accepted). Unchanged.
- Developer: **developer-sonnet** (changed 2026-10-17 11:35, see "Amendment A" below; this
  brief was first written for developer-opus, under the old routing rubric).
- Tester model: **sonnet** (the Opus bar is not met, Amendment A).
- Baseline: `.claude/baselines/T-03a/` (taken 2026-10-16 10:05, before attempt 1; reused).
  The task's whole change stays `for d in src tests scripts; do diff -ruN -x __pycache__
  .claude/baselines/T-03a/$d $d; done`.

## Amendment A (2026-10-17 11:35): new model, and the partial work already in the tree

**Read this section first. Where it conflicts with the text below it, this section wins.**

### Model, under the routing rule of 2026-10-17

Every role runs on Sonnet at extra-high effort; Opus only when the very-high-risk bar is met.
Checked here, and recorded on the board:
- *Escalation* (a Sonnet attempt RETURNED because the problem proved beyond it): **not met**. No
  Sonnet attempt at T-03a exists. Attempt 1 ran on Opus and was returned for the three failures;
  attempt 2 (Opus) was cut off by the usage limit at about 06:00, not returned.
- *Very high risk from the start* (all three together): the first two hold (a segfault ends vision
  on a wearer's head without telling them; the change is in the Hailo pipeline start/stop
  lifecycle). The **third does not hold any more**: the part that needed the deep debugging (why
  a Hailo element crashes when it is taken down or fails to start) was done by the Opus session and
  is written in the tree. What remains is checking that finding independently, finishing the
  change, repeating it on the device and reporting, steps a Sonnet Developer can do one at a time.
- So: **developer-sonnet** continues attempt 2. If this attempt shows the problem is beyond Sonnet
  (it cannot get the repeat counts to zero and cannot explain what is left), the Lead returns it
  and the next attempt may go to Opus under the escalation rule, recorded as such.

### What the Opus session left (the Lead's reading of the tree against the baseline; not trusted)

There is **no dev-report-2.md**, and the Opus session's scratch probes (`/tmp/t03a_probes/`) no
longer exist. Only two files changed after attempt 1: `src/second_vision/pipeline/app.py`
(05:38) and `tests/test_camera_loss.py` (05:55). The state of the tree at the start of this
attempt: `for d in src tests scripts; do diff -ruN -x __pycache__ .claude/baselines/T-03a/$d $d; done`
(the attempt-1 diff is `changes-1.patch`; the Lead's copy of the attempt-1 tree is not kept).

What the partial change does (read it yourself, this is only a map):
1. **`_launch_on_camera(node)`** is one start path for a returning camera (`_start_on_camera`) and
   for a USB-camera mode-switch/watchdog rebuild (`_rebuild_on_camera`, called from
   `_rebuild_pipeline()`). That is the F3 fix: the library's `_rebuild_pipeline()` is no longer
   used for the USB camera, so a failed start there can no longer quit the loop. File sources keep
   the library's path.
2. Its start order: `parse_launch`; the **camera element alone to READY** (the only step the camera
   can fail synchronously; on failure, tear the new pipeline down, back to waiting); the whole
   pipeline to PAUSED; **`_prime_hailonets()`**; only then does the pipeline become
   `self.pipeline`; callbacks; `_on_pipeline_rebuilt()`; `disable_qos`; PLAYING.
   `_park_empty_pipeline()` puts an empty `Gst.Pipeline` in `self.pipeline` while a start is
   running, so a stop (SIGINT/SIGTERM) in that window sees nothing to tear down.
3. **The claimed root cause** (docstrings in `_launch_on_camera` and `_prime_hailonets`):
   HailoRT 4.23.0's `hailonet` unrefs a buffer allocator on READY->NULL that is only set when its
   **first buffer** arrives, so a hailonet taken down before any buffer unrefs an uninitialised
   pointer (segfault in `gst_object_unref`, or the "ref_count > 0" CRITICAL). It says a failed
   start does exactly that (GstBin rolls its started children back), and so does any teardown in
   the first ~0.6 s. `_prime_hailonets()` feeds every hailonet one empty buffer through its sink
   pad while the pipeline is PAUSED, with a pad probe that drops it after the element's own probe
   has run, so the allocator exists before anything can tear the element down.
4. Also: an INFO line "Still no USB camera after N s" about every 60 s (`CAMERA_STILL_LOOKING_LOG_SECONDS`,
   decision-1 Q5); `_camera_missing_since`; `_is_camera_element(element, pipeline=None)`.
5. Tests: `tests/test_camera_loss.py` gained a `HailoStandIn` and seven tests. In the GStreamer
   environment, **one of them fails**: `…camera_lost_before_its_first_frame_never_tears_down_an_unfed_hailo_element`
   (`tests/test_camera_loss.py:889`: `app.pipeline` is PAUSED, expected PLAYING). Run it with
   `my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -p no:cacheprovider`
   (1 failed, 29 passed at 11:30). In the Poetry environment the whole suite passes
   (388 passed, 79 skipped), because it skips the `gst` tests.

### What you must do first, in this order

1. **Inspect it against the baseline and against attempt 1. Do not trust any claim above or in the
   code's comments**; the Lead has verified none of the following.
   - **The root cause is a claim about a binary plugin.** Where was `gsthailonet.cpp` read? The
     installed plugin is a binary and AGENTS.md Rule 6 asks for a source you can cite (the
     workbench `/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`, the hailo-apps or HailoRT
     sources online, or a measurement). Find the source, or measure the behaviour on the device,
     and **say in the report which one the claim now rests on**. A claim that rests on neither is
     reported as unproven, not written as fact in a docstring.
   - **Priming is a use of the plugin that no example shows** (a synthetic buffer through a Hailo
     element's sink pad). It is the riskiest piece. Before keeping it: reproduce the crash **without**
     priming (so the cause is shown independently of the fix: a start that fails, a teardown in the
     first 0.6 s), then with priming, with counts; show that no callback runs on the priming
     buffer and that the first real frame is processed normally in both detection and depth
     (protected processing: observe it, do not change it); and say what happens if priming fails.
     If the evidence does not support it, **stop and report** with your numbers; do not add more
     workarounds on top of it.
   - Scope: `_drop_buffer` is a **module-level function**, outside "class `SecondVisionApp` and
     the module constants only". Move it into the class (a `staticmethod`) or say why it must stay.
   - Walk through the staged order for any path that leaves a half-started pipeline: a stop during
     step 1/2/3, an exception in `_on_pipeline_rebuilt()`, `_failure_note`, the START_* outcomes in
     `_start_on_camera` and `_rebuild_on_camera`.
2. Fix the one failing test (or show with evidence that the test, not the code, is wrong) and make
   `my_hailo_env/bin/python -m pytest tests/ -q` and the Poetry-environment run both pass.
3. Run the repeat counts of "Fixed means repeated" below on the device and record the numbers. Your
   own isolation probe for F1 (the app's own `both` pipeline running ~3 s, torn down as
   `_release_old_pipeline()` does, then the same pipeline on `/dev/video99`) has to be rebuilt in
   your scratch; the Tester's probe text is in `reports/T-03a/evidence/` and `test-report-1.md`.
4. Write **`dev-report-2.md`**: what you kept, changed or removed from the partial work and why;
   every claim marked "device run" or "mock"; the numbers; anything unproven.

Everything else in this brief, including "What the Developer may now do on the device", the
acceptance criteria and the repeat counts, stands for the Sonnet Developer unchanged.

## Where attempt 1 stands

The plain case is done and verified on the device (unplug while running in each of the four modes,
three in a row, another node, Ctrl+C or SIGTERM while waiting, real glasses board and panel
once). Keep all of that working. Read `test-report-1.md` (the Tester's "Failures" and "Read first"),
`decision-1.md`, and `dev-report-1.md`. The Tester's device tests are in
`tests/uat/test_tc024_003_camera_removed.py` (22 tests); its logs are in `reports/T-03a/evidence/`.

## Failures to fix (quoted from test-report-1.md)

Each is a reproducible failure of acceptance criterion 1 ("the program does not crash … no exit,
no traceback"), and F1 also of criterion 4.

**F1. Segfault when vision is started on a camera that cannot be opened (Hailo pipeline).**
- "Within about 0.1 s of 'Camera found on /dev/video0 — starting the vision pipeline', unplug it
  again. Expected: 'The vision pipeline did not start on /dev/video0 — still looking for a camera';
  the program stays up. Actual: `Fatal Python error: Segmentation fault`; exit -11 (7 of 8 tries).
  Python stack: `app.py` line 1299 `_start_on_camera` ← 1224 `_on_camera_found` ← GLib main loop.
  Native stack (gdb): `#0 gst_object_unref` ← `#1 libgsthailo.so` ← `#2 gst_element_change_state`
  … ← `#9 gst_element_change_state` (the pipeline) … ← `g_main_loop_run`."
- Isolation, no USB timing involved (Tester probe; the Lead repeated it on 2026-10-17 and got 3
  segfaults in 4 runs): in one process the app's own `both` pipeline runs 3 s on the camera and is
  torn down the way `_release_old_pipeline()` does; 5 s later the same pipeline on `/dev/video99`
  (a node that does not exist) is set to PLAYING. Segfault (exit 139) in most runs. In a fresh
  process, 1 of 15. With no Hailo element in the pipeline (mode `none`), 0 of 3. In one gdb run
  the same bounce took the intended failure path, so it is a race inside the plugin, not a fixed
  outcome. The probe script is `/tmp/t03a_probes/probe_failed_start_after_run.py`
  (usage: `my_hailo_env/bin/python <it> <an app log that holds a "Generated Pipeline string
  (mode=both)" block>`, for example the log
  `docs/testing/reports/T-03a/evidence/app-logs/2026-10-17-043800-app-test_tc024_003_con08_device_camera_drops_again_while_restarting.log`).
  Copy it to your own scratch first. The other probes (`probe_failed_start.py`,
  `bounce_under_gdb.py`, `stop_after_mode_switch.py`) are in the same folder.
- Meaning: the designed path "camera found but cannot start, go back to waiting"
  (`_start_on_camera`, brief-1 design note 5 and R4) usually crashes on the real pipeline. The
  Developer's gst-level test of it uses a pipeline with no Hailo element, so it could not see this.
- The crash is inside an installed library. It must be **avoided from the app side**, never
  patched (Rule 1).

**F2. Segfault in the camera-loss teardown.**
- Test `…unplug_while_mode_switch_pending[after-1.5s]`: panel `M:depth`; the rebuilt pipeline
  started 0.3 s before the camera was detached. "Camera lost (error from source …)", then about
  1.8 s later `Fatal Python error: Segmentation fault` at `app.py` line 889,
  `element.set_state(Gst.State.NULL)` on a hailonet, in `_release_old_pipeline` ←
  `_release_old_pipeline_with_alarm` ← `_release_after_camera_loss`; exit -11. Related: in a bounce
  run (0.33 s) a similar teardown printed `CRITICAL: gst_object_unref: assertion
  '((GObject *) object)->ref_count > 0' failed` and survived.
- Common thread with F1 and with O1 below: **a Hailo pipeline is torn down, or fails, very soon
  after it started**.

**F3. The program ends when the camera goes during a mode-switch rebuild** (the "R4 residual").
- Tests `…unplug_while_mode_switch_pending[after-1.2s]` and `[after-1.4s]`: panel `M:depth`, camera
  detached 1.2 to 1.4 s later, between `_rebuild_pipeline`'s node check and the library rebuild
  opening the camera (about 0.3 s: the library's 0.2 s sleep plus `parse_launch`). "ERROR |
  gstreamer.gstreamer_app | Failed to start new pipeline", then "Exiting successfully", exit 0:
  speech, the glasses link and the panel stop with the program. At 1.3 s the same window
  segfaulted (library `gstreamer_app.py:599` `_rebuild_pipeline` ← `app.py:802`).
- This path is the **library's** `_rebuild_pipeline()`, which calls `self.loop.quit()` when the new
  pipeline cannot start (`gstreamer_app.py:599-603`, `:610-613`). The dev-report called it "R4
  residual" and asked whether to accept it. **Not accepted** (decision-1).

## Amended scope

- `src/second_vision/pipeline/app.py`, class `SecondVisionApp` and the module constants only.
- **Amended:** brief-1 kept "mode switching with the camera present unchanged". That still holds
  for `MIN_REBUILD_INTERVAL_SECONDS`, request coalescing, `_release_old_pipeline()`'s **order**
  (hailonets to NULL first, one by one; then the pipeline; then the bus watch; references
  dropped; `gc.collect()`; the settle), and `_on_pipeline_rebuilt()`'s resets. What **may** now
  change is what happens when the new pipeline cannot start (F3): it must not end the program.
  Replacing the library's `_rebuild_pipeline()` for the USB-camera case, or routing the mode-switch
  rebuild through `_start_on_camera`, is allowed. A failure of a **non-camera** element keeps
  today's path (shutdown), as confirmed in decision-1.
- `_release_old_pipeline()`: you may add steps that make the teardown safe (for example waiting,
  bounded, for a pending state change to finish before the NULL transitions) but not reorder the
  existing ones. Say exactly what you changed and why.
- `tests/test_camera_loss.py`: update and extend. It is gst-level, with no Hailo element: say in
  each new test what it cannot show (the plugin crash only reproduces with the real chip).
- **Not yours:** `tests/uat/test_tc024_003_camera_removed.py` is the Tester's file. Run it; do not
  edit it. If a device test needs to change, say so in your report.
- Everything else in brief-1's "Out of scope: must not change" stands: protected files, contracts,
  workers, `scripts/`, the clean-shutdown path, no mock mode, no new user-facing output.
- **O1** (Ctrl+C within about 0.15 s of any pipeline start segfaults, 3 of 5, baseline too) is **not
  in this task** (board: T-11, proposed). If the mechanism you find for F1/F2 also covers it, say
  so with evidence; do not widen the work to chase it.
- **Answers from decision-1:** keep "a restart that a non-camera element fails shuts down" (Q3);
  keep looking after an exception while building the restart pipeline (Q4); add one INFO line
  about every 60 s while waiting for a camera (Q5): for example "Still no USB camera after N s".

## What the Developer may now do on the device (this attempt only)

Attempt 1 forbade device use; the crashes only reproduce on the real chip, so this brief allows it.
Rules:
- **Before every device run:** `systemctl is-active second-vision.service` must say `inactive`
  and `pgrep -af "main.py|second_vision|Parallel-Depth"` must find nothing but your own shell
  (at 05:12 on 2026-10-17 both were true). If either fails, stop and report; stop nothing yourself.
  `sudo .claude/tools/usb_sim.py status` must show the camera attached.
- **One device job at a time.** Never two processes on the Hailo chip or the camera.
- **You may run:**
  - your own scratch scripts under `/tmp` using the camera and the Hailo chip (copies of the
    Tester's probes are a good start);
  - `sudo .claude/tools/usb_sim.py status|detach|attach camera` (nothing else with sudo, except
    `sudo fuser -v /dev/hailo0` and, only with the service stopped, `sudo fuser -k /dev/hailo0` for
    a stale test process of yours; put both outputs in the report);
  - the Tester's tests one at a time:
    `my_hailo_env/bin/python -m pytest '<node id>' --run-device -v -p no:cacheprovider`.
    Do not run `scripts/uat_evidence.py` or write into `reports/T-03a/evidence/`; those are the
    Tester's. Set `SV_UAT_APP_LOG_DIR` to a scratch folder so your runs don't add to the evidence.
  - `gdb` on a process you started.
- **usb_sim defect (brief-1, "Testing notes"):** an older `--for N` timer can re-attach a newer
  detach early. Use `--for 20` or less, call `attach` when done, wait the previous `--for` before
  the next detach, and check the camera is still absent before relying on it.
- **At the end:** camera attached, nothing of yours running, `sudo fuser -v /dev/hailo0` listed in
  the report. Never leave the camera detached.
- The device runs are the Developer's investigation and smoke test. They do **not** replace the
  Tester's, and they are not evidence for the decision.

## Design notes from the Lead (hints, not instructions; you own the design)

1. **Where the crash seems to be.** Setting a Hailo pipeline to PLAYING while its `v4l2src` fails
   to open crashes inside `libgsthailo.so` (the state-change rollback after the failure, from the
   native stack), most often after an earlier pipeline ran in the same process. Pipelines with no
   Hailo element never crash. Before designing, find out what the plugin does when a state change
   fails (gdb with the plugin's symbols if there are any; the hailo-apps docs and sources in the
   workbench for `hailonet`) and at which state change `hailonet` takes the vdevice (the dev-report
   found no document that says; look for it, or measure it).
2. **Keep Hailo elements out of a failing start.** The fix has to keep a Hailo element from being
   part of a state change that fails on the camera, or make that failure safe. Candidates to try
   and measure, in any order; you may find better:
   - **Pre-flight check:** before building or starting a Hailo pipeline on a camera (a returning
     camera and a mode-switch rebuild), open the camera with a small pipeline that has no Hailo
     element (`v4l2src device=<node> ! video/x-raw,width=640,height=480 ! fakesink`-like, state
     PLAYING, a bounded wait, require a buffer or a clean PLAYING), tear it down, and only then
     build the real one. Measure: does the camera reopen cleanly right after? What time does it
     add? And **the residual window** between the check and the real open (about 0.3 s): what
     still happens if the camera vanishes in it? If the answer is "it can still crash", say so with
     numbers; do not describe a smaller window as a fix.
   - **Staged state changes:** READY, then PAUSED, then PLAYING, checking the result and the bus
     between steps, so a source that cannot open fails before a Hailo element is mid-change. Find
     out whether the source opens before any hailonet changes state; measure.
   - **Settle before teardown (F2, and perhaps O1):** a teardown that starts while the pipeline is
     still inside its first state change (async PAUSED to PLAYING) may be what crashes. A bounded
     `get_state()` before the NULL transitions is one thing to test. It must stay bounded (brief-1
     R1: no unbounded wait on the main loop).
3. **F3 is a decision about who owns the rebuild**, not a Hailo problem: the library's rebuild
   calls `loop.quit()` on any failure. For the USB-camera case the app can start the new pipeline
   itself (as `_start_on_camera` does, with its "back to waiting" outcome), after the same
   pre-flight check as a returning camera. Keep file sources and the watchdog's rebuild on the
   library's path, or say why not.
4. **Do not "fix" it by exiting.** No `sys.exit`, `os._exit`, `loop.quit()` or restart of the
   process when a start fails, and no process-per-pipeline redesign: the requirement is that the
   program stays up. If staying up cannot be reached from the app side (the plugin crashes
   whatever the app does), **stop and report** with your measurements: what you tried, what rate
   each attempt reached, what the options are. The Lead will ask the user. Do not hide a residual
   crash behind a longer wait.
5. **Hailo docs first (Rule 6).** For anything that starts, stops or stages the pipeline, cite what
   you checked, in the workbench (`/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`:
   `.hailo/memory/`, `.hailo/instructions/gstreamer-pipelines.md`, `.hailo/toolsets/`,
   `SV-Docu/`) or at <https://github.com/hailo-ai/hailo-apps>. Attempt 1 found no document on
   camera-unplug recovery, and none that says when `hailonet` takes the device.

## Acceptance criteria

Brief-1's criteria 1 to 6 stand, word for word. Criterion 1 is made explicit; each bullet is
something the Tester will run on the device:
- 1a. The camera goes while a pipeline is **starting or has just started** (a loose connector: the
  camera returns and drops again 0.0, 0.3 and 1.0 s after "Camera found on …"): the program
  ends up running (vision restarted) or waiting for a camera. Same process, no exit, no
  segfault, no traceback. A GStreamer `CRITICAL` in the log is a finding unless you show it is
  harmless and expected.
- 1b. A camera is found but **cannot be opened** (any reason): back to "waiting", with the clear
  log line, no crash, no exit.
- 1c. The camera goes during a **panel mode-switch rebuild**, at any moment from the panel line to
  well after the rebuild (every gap the Tester's test covers, 1.0 to 1.6 s in 0.1 s steps as well
  as at-once): the program ends up running or waiting; the switch stays announced and recorded; no
  exit.
- 1d. The plain cases of attempt 1 still pass (all four modes, three in a row, another node, stops
  while waiting).

**Fixed means repeated, not once** (the failures are races). Before you hand over, show on the
device, with the counts in the report:
- the F1 isolation probe (the app's real pipeline on a node that cannot open, after an earlier
  pipeline ran in the process): **at least 10 fresh-process runs, no crash**;
- the bounce test (`…camera_drops_again_while_restarting`, all three offsets): **at least 5
  consecutive passes**;
- each `…unplug_while_mode_switch_pending[gap]` gap, **at least 3 passes each**;
- the other device tests of the file: one pass each, to show no regression.

A rate that is not zero is reported as such, with the cause if known. The Tester repeats these
independently with larger counts.

## Verification expectations

As in brief-1. The new Developer tests are unit or mock-integration (`gst` level, no Hailo
element). The device evidence for 1a to 1c is the Tester's, and the person's one real-camera
confirmation (MANUAL-TESTS.md, T-03a) comes after the Tester passes. A mocked test never proves the
plugin does not crash; say which of your claims rest on a device run and which on a mock.

Tester (attempt 2), coverage the brief's criteria require (Lead, 2026-10-17 12:55): criterion 1a counts a GStreamer `CRITICAL` in an app log as a finding, and criterion 1c names every gap from 1.0 to 1.6 s in 0.1 s steps. If your test file does not check for `CRITICAL`, or does not cover those gaps, extend it (it is your file) before you judge.

Tester (attempt 2): rerun the failed checks 7, 8, 9 (with the counts above, or more), the full
device file once, the unit suites in both environments, the diff review, the red-before-green of
any new Developer test against the baseline, and the outside-writes check. Check specifically that
the pre-flight (or whatever replaces it) did not slow the plain restart noticeably (attempt 1:
median 1.71 s from replug to "Camera back", 30 samples) and report the new figure.

## Open risks: stop and report instead of guessing

- **R1 to R8 of brief-1 stand.** R2 (native crash): report any segfault you see, even once, with
  the rate.
- **R9. The window cannot be closed from the app side.** See design note 4.
- **R10. A pre-flight opens the camera too.** Opening and closing the camera quickly may itself
  upset the UVC driver or the Arducam (two capture nodes). Measure it (a loop of 20 open/close
  cycles with frames) before relying on it.
- **R11. Do not weaken a test to make it pass.** If you think a Tester test expects the wrong
  thing (as the first `real_ports` test did, which expected depth frames in mode `none`), say so
  in the report with the evidence; the Tester fixes its own tests.

## For attempt > 1: failures to fix

See "Failures to fix" above (F1, F2, F3), quoted from `test-report-1.md`.
