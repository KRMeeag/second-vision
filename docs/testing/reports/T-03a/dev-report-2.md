# T-03a · Developer report, attempt 2

- Date / model: 2026-10-17 / developer-sonnet (extra-high effort). This continues a partial attempt 2 that
  a developer-opus session left in the tree when the usage limit cut it off (brief-2, Amendment A). There was
  no earlier dev-report-2; this is the first.
- Summary: I checked the partial work instead of trusting it, on the device. Its design holds, and I kept
  all of it. Two things were wrong or unproven and are fixed here: the one failing unit test was wrong
  (the code was right), and the root-cause claim in the code comments rested on nothing a reader could
  check. The claim is now sourced and measured (below). The three failures of attempt 1 are gone on the
  device: F1 (0 of 10 fresh-process runs crash, was 8 of 10), F2 and F3 (every mode-switch gap, 3 passes
  each), and the bounce (6 passes of 6). Counts and caveats are listed. The person's real-camera
  confirmation is still outstanding.

## Read first: the answers to brief-2, "What you must do first"

**1. Where was `gsthailonet.cpp` read, and what does the claim rest on?**
It rests on **the HailoRT source and on measurements on this Pi**. Not on memory, and not on the
installed binary alone.
- Source: `hailort/libhailort/bindings/gstreamer/gst-hailo/gsthailonet.cpp` and `.hpp`, tag **v4.23.0**,
  <https://github.com/hailo-ai/hailort> (fetched with curl into scratch, read as data only). Installed:
  `hailortcli --version` gives 4.23.0, `dpkg -l hailort` gives 4.23.0, and `libgsthailo.so` is linked against
  `libhailort.so.4.23.0`. I did **not** disassemble the installed binary (it is stripped); the match is by
  version and by the strings `:hailo_allocator`, `Could not release buffer pool`, `dma buff is not
  supported on this OS`, all present in both.
- What the source says (lines as of the tag):
  - `gst_hailonet_change_state`, case `READY_TO_NULL` (499-507), calls `gst_hailonet_free`.
  - `gst_hailonet_free` (105-170) ends with `else if (nullptr != self->impl->allocator)
    gst_object_unref(self->impl->allocator)` (164-165).
  - `allocator` is set in only one place, `gst_hailonet_init_allocator` (359-379), called from
    `gst_hailonet_allocate_infer_resources` (line 420), called from `gst_hailonet_sink_probe` (433-470).
    That probe is a pad probe on the sink pad that runs on the **first buffer** and removes itself.
  - `HailoNetImpl::HailoNetImpl()` (1497-1501) lists many members in its initialiser list and **not**
    `allocator`, and the header (`gsthailonet.hpp`, struct `HailoNetImpl`) gives it no default value. So a
    hailonet that goes READY to NULL before any buffer calls `gst_object_unref` on whatever the heap
    held. That is the "segfault in `gst_object_unref`" and the "`ref_count > 0` CRITICAL".
  - The vdevice is not taken at a state change. It is created lazily, the first time the sink pad is asked
    for its caps (`gst_hailonet_handle_sink_query` 1448 -> `gst_hailonet_get_caps` 1393 ->
    `gst_hailonet_init_infer_model`, 1404-1410). The attempt-1 report found no document that says so; the
    source does.
  - The native stack of the Tester's gdb run
    (`evidence/2026-10-17-tester-bounce-segfault-gdb-backtrace-2.log`) fits: `gst_object_unref` called
    directly from `libgsthailo.so` inside the pipeline's `gst_element_change_state`.
- Measured (device, section "Numbers" below): with no buffer, a hailonet torn down crashes in 7 of 10 runs
  after an earlier pipeline ran in the process and in 10 of 10 fresh-process runs; with a buffer, or after
  the app's priming, 0 of 15. A fresh process crashed 10 of 10 here, against the attempt-1 Tester's 1 of 15
  in a leaner probe; a plausible reason is that my probes construct the whole app first, so the heap is
  no longer fresh zeroed memory, but I did not test that. This is **consistent with** the uninitialised
  pointer (the crash depends on what the heap held). Which freed object the bytes came from is not proven,
  and the code comment no longer says it.
- Workbench `/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`: grepped `.hailo/` and `SV-Docu/` for
  "first buffer", "allocator", "READY ... NULL", "state change"; no hit. No hailo-apps document describes
  this, so the source and the measurements are what the claim has.

**2. Priming (`_prime_hailonets`), reproduced without it, then with it.** Numbers in "Numbers" below. In
short: it is needed, and it is safe on the evidence I could get.
- Without priming (and without the staged start) a failed start crashes 8 of 10 times; the staged start
  alone fixes that (0 of 10). A teardown in the first ~0.6 s after PLAYING still crashes without priming
  (3 of 6 at 0 ms, 6 of 6 at 300 ms) and not with it (0 of 12). So priming earns its place for F2 and the
  bounce; the staged order earns its place for F1.
- No callback runs on the priming buffer, and the first real frame is processed normally in detection and
  in depth (device run, `firstframe.py`, all three modes plus an unprimed control): each hailonet's sink pad
  sees exactly one empty buffer then camera frames; its src pad and both callbacks see camera frames only;
  zero empty buffers reached a callback in 3 runs (about 110 frames each); the first depth buffer carried its
  depth mask (`depth_masks=1`); `[DEPTH]` lines read the same as without priming; the real caps, stream-start
  and segment events reached each hailonet's sink pad after the start. Protected processing was only
  observed (the callbacks were wrapped to read, then call the original).
- If priming fails: the three branches (no input format, an exception, a flow that is not OK) each log a
  warning and the start goes on. That element is then exposed to a teardown before its first frame, as in
  the unprimed rows of the table. Device: I could not make a real hailonet refuse; the unprimed rows are
  the device measurement of that exposure. Gst level: a new test covers the "no input format" branch.
  The other two branches log the same warning and are not unit-tested.
- Oddity, not chased: in `both` mode the depth hailonet's **stored** sink-pad caps still show the caps the
  priming stored, not the real ones, although the real CAPS event reached the pad (probe) and the element's
  src pad shows the real caps. Outputs were identical to the unprimed run.

**3. Scope.** `_drop_buffer` was a module-level function. It is now a `@staticmethod` of `SecondVisionApp`
(called as `self._drop_buffer`). The module-level functions in `app.py` are again only those the baseline had.
The two module-level imports (`re`, `get_usb_video_devices`) and the constants are the other additions
outside the class, as in attempt 1.

**4. Walk through the staged order, for every path that leaves a half-started pipeline.** Read, and where
the path is reachable, run (device) or test (gst). Findings, none needing a change:
- Stop (SIGINT, SIGTERM) during step 1 or 2: Python runs the handler between two bytecodes, so the stop
  lands after the blocking C call returns. `self.pipeline` is the empty parked pipeline, so `shutdown()`
  never touches the half-started one. Step 1 checks `_shutting_down` before and after the camera READY and
  tears the new pipeline down (`_abandon_start`, nothing Hailo had left NULL). Between step 2 (PAUSED) and
  step 3 (priming) there is deliberately no check.
- Stop during step 3: the same; the new pipeline only becomes `self.pipeline` in step 4, after every
  hailonet is primed; `_launch_on_camera` then returns `START_STOPPED` before PLAYING, and `run()`'s cleanup
  takes the primed pipeline to NULL. Tests: `stop_during_a_camera_restart_never_tears_down_an_unfed_hailo_element`
  (both cases) and the Tester's `stop_mid_transition` (4 passes in the full run).
- Exception in `_on_pipeline_rebuilt()` (or `_connect_callback()`, `disable_qos`): step 4 has already made the
  pipeline `self.pipeline`, so the `except` branch calls `_release_old_pipeline()` (primed, so safe), returns
  `START_NO_CAMERA`, and the caller goes back to waiting. That is the decision-1 Q4 behaviour (keep looking).
  The message then reads "did not start ... still looking", whatever the cause. Not run on the device (no way
  to make it raise); read only.
- `_failure_note`: only formats the ERROR text or returns `''`. A start with `failure is None` after the
  camera opened goes to `START_FAILED` and the shutdown path (read; not reached on the device).
- `_start_on_camera` outcomes: RUNNING (state OK, watchdog resumed, mode re-checked), STOPPED (nothing),
  FAILED (shutdown, today's path for a non-camera element), anything else back to WAITING with the node
  marked if it still exists. `_rebuild_on_camera`: RUNNING resumes the watchdog, FAILED shuts down, NO_CAMERA
  waits and zeroes the motors once; STOPPED does nothing. Device: RUNNING, NO_CAMERA from step 1, and (bounce)
  a failure after PLAYING that arrives as an ordinary camera loss were all seen in the logs. FAILED was not
  reached on the device: it needs a Hailo element to fail.
- Residual windows I did not close, small: (a) a stop that lands between `if self._shutting_down` and
  `set_state(PLAYING)` in step 4 runs `shutdown()` on the primed pipeline and then the code sets it PLAYING
  once more before the loop quits; microseconds wide, not reproduced, and a hailonet that has been through
  `gst_hailonet_free` must not go through it twice (source), so I would not call it safe. (b) If a Hailo
  element itself fails the start (device refused), the teardown of the unfed element can still crash instead
  of exiting 1; by the source the baseline's shutdown after a Hailo error has the same exposure (reasoned,
  not run: it needs a Hailo failure I cannot make on demand).

**5. The one failing test was wrong, not the code.**
`…camera_lost_before_its_first_frame_never_tears_down_an_unfed_hailo_element`, line 889, asserted the pipeline
state is PLAYING right after the restart. A pipeline whose live source has sent nothing stays PAUSED with
PLAYING pending: checked in isolation with the same elements, `get_state(0)` gives `async paused playing`.
The real pipeline answers `async` for the same reason (my probe logs, `pipeline #1 PLAYING -> async`). The
assertion now accepts PLAYING current or pending. The rest of the test, which carries its point (the element
had its buffer, then the teardown), was already right. 31 of 31 pass in `my_hailo_env`.

## Inspected before changing
- `src/second_vision/pipeline/app.py` in full (1734 lines at the start), against the baseline and against
  attempt 1 (`changes-1.patch` applied to a copy of the baseline, scratch, to get attempt 1's `app.py`; 1466
  lines, as dev-report-1 says). `_release_old_pipeline()` has **no** diff against the baseline: its order is
  unchanged.
- `tests/test_camera_loss.py` in full (1153 lines at the start), `tests/gst_helpers.py`, `tests/conftest.py`
  (device fixtures), `tests/uat/test_tc024_003_camera_removed.py` (all 857 lines; not edited).
- Installed library `gstreamer_app.py`: `bus_call`, `on_eos`, `_rebuild_pipeline`, `shutdown`, `run`
  (unchanged since attempt 1; read again today, read only).
- Callers confirmed: `_launch_on_camera` is called by `_start_on_camera` and `_rebuild_on_camera`;
  `_rebuild_on_camera` by `_rebuild_pipeline`; `_prime_hailonets` and `_abandon_start` by `_launch_on_camera`;
  `_pop_error`, `_failure_note`, `_back_to_waiting`, `_shut_down_after_failed_start` by those two. Nothing
  unused. `trigger_rebuild` is still called by `workers/config_reader.py`.
- Device before every job: `systemctl is-active second-vision.service` = `inactive`; `pgrep -af
  "main.py|second_vision|Parallel-Depth"` found only the Lead's agent process (its command line contains
  the words; it is not a vision process); `usb_sim.py status` = attached; no `--from-timer` usb_sim timer
  pending; one device job at a time.

## Hailo docs consulted (Rule 6)
- HailoRT sources, tag v4.23.0, `gsthailonet.cpp` / `.hpp` (see answer 1 for functions and lines).
- Installed versions: `hailortcli --version`, `dpkg -l`, `gst-inspect-1.0 hailo`, `strings` on `libgsthailo.so`.
- Workbench: `.hailo/memory/`, `.hailo/instructions/`, `.hailo/toolsets/`, `SV-Docu/`: nothing on hailonet
  teardown or first-buffer behaviour (grep). `toolsets/gstreamer-elements.md:7-17` lists hailonet properties only.
- The GStreamer calls priming uses (`Pad.query_caps`, `Pad.store_sticky_event`, `Pad.add_probe`, `Pad.chain`)
  are GStreamer's, not hailo-apps'. They are not used in any hailo-apps example; that is why I measured them.
- Installed `gstreamer_app.py` as above.

## Files changed
Against `.claude/baselines/T-03a/` (whole task): `src/second_vision/pipeline/app.py` (+751 -3),
`tests/test_camera_loss.py` (new), `tests/uat/test_tc024_003_camera_removed.py` (new, the Tester's). Nothing
in `scripts/`, the workers, `callbacks.py`, `core/`, `firmware/`. `check_outside_writes.py` against the
baseline stamp: installed libraries and environments nothing changed; other checkouts and the workbench
nothing changed.

| File | What changed in this attempt | Why |
|---|---|---|
| `src/second_vision/pipeline/app.py` | `_drop_buffer` moved from module level into the class as a `@staticmethod`; `_prime_hailonets` calls `self._drop_buffer`. Docstrings of `_launch_on_camera` and `_prime_hailonets` rewritten: the root cause now cites the HailoRT source and the measured rates, the unverified statements (a `gstbin.c` rollback detail, `gstv4l2src.c`) are removed or replaced by what I measured, and "if priming cannot be done" is written down. | Brief-2: scope, and "a claim that rests on neither is unproven, not written as fact in a docstring" |
| same, kept unchanged from the partial work | `_launch_on_camera` (camera alone to READY, whole pipeline PAUSED, `_prime_hailonets`, only then `self.pipeline`, callbacks, `_on_pipeline_rebuilt`, `disable_qos`, PLAYING); `_prime_hailonets`; `_abandon_start`; `_pop_error`; `_failure_note`; `_back_to_waiting`; `_shut_down_after_failed_start`; `_park_empty_pipeline`; `_rebuild_on_camera` (and `_rebuild_pipeline` using it for a USB camera); the START_* outcomes; `CAMERA_STILL_LOOKING_LOG_SECONDS` and the INFO line; `_camera_missing_since`; `_is_camera_element(element, pipeline=None)` | F1, F2, F3, decision-1 Q5. Each is exercised by the device numbers below |
| same, kept from attempt 1 | Camera-loss handling (bus override, teardown, watcher thread, `MOTORS_OFF`, `trigger_rebuild` guard, node pick) | The plain case; the full device file passes |
| `tests/test_camera_loss.py` | One assertion fixed (answer 5). One test added (below). | See below |

I removed no behaviour from the partial work. `_release_old_pipeline()` is unchanged (the brief allowed adding
safe steps; none was needed once hailonets are primed).

## Tests added or updated
All `gst` level (they skip in the Poetry environment). Each docstring says it covers the removal half of
TC-024-003 only and states what it cannot show: that the real plugin does not crash needs the Hailo chip.
Red-before-green was run with the **current** test file against (a) the baseline `src/` and (b) attempt 1's
`src/` (the last accepted-for-testing shape, rebuilt from `changes-1.patch`), each in a scratch tree with the
current `conftest.py`, `gst_helpers.py`, `pyproject.toml`.

| Test (`test_tc024_003_con08_…`) | What it checks | Fails on baseline? | Fails on attempt 1? |
|---|---|---|---|
| `camera_that_cannot_open_never_moves_a_hailo_element` | F1 at gst level: a camera that cannot open leaves the Hailo stand-in in NULL; the next start feeds it before the camera streams | yes | **yes** |
| `camera_lost_before_its_first_frame_never_tears_down_an_unfed_hailo_element` | F2 / bounce: the stand-in had its buffer before the teardown; **assertion fixed** | yes | **yes** |
| `hailo_element_that_cannot_be_primed_is_warned_about_and_the_start_goes_on` (**new**) | The "no input format" branch of priming: warning logged, no buffer given, start still runs and frames flow | yes | **yes** |
| `mode_switch_onto_a_camera_that_cannot_open_waits` | F3: a mode-switch rebuild onto a camera that cannot open waits, no `loop.quit`, mode recorded, one zero, Hailo element never moved | yes | **yes** |
| `mode_switch_rebuild_feeds_hailo_first_and_keeps_loss_handling` | A normal mode switch through `_rebuild_on_camera`: new mode, counted, watchdog resumed, frames flow, first buffer never processed, camera error afterwards is still a loss | yes | **yes** |
| `stop_during_a_camera_restart_never_tears_down_an_unfed_hailo_element[after-hailo-fed, while-building]` | A real `shutdown()` in the middle of a restart: no PLAYING, loop asked to quit, no unfed teardown | yes, yes | **yes** (after-hailo-fed), no (while-building: guard, held before) |
| `still_looking_for_a_camera_is_logged_about_once_a_minute` | Decision-1 Q5, with the period shortened | yes | **yes** |
| the 24 other tests (attempt 1's, and the Opus session's guards) | unchanged | 22 of them fail on the baseline; 2 are guards that pass | pass |

Results (my_hailo_env, `tests/test_camera_loss.py`): baseline `29 failed, 2 passed in 103.67s` (the two passes
are the guards `error_from_another_element_still_shuts_down` and `file_source_eos_keeps_library_looping`);
attempt 1 `7 failed, 24 passed in 33.50s`; current `31 passed`.

Not tested at gst level: the two other "not primed" branches (the chain call raising, or not returning OK).

## Commands run and their actual results
Worktree `/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth`.

```
# Final tree
my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -p no:cacheprovider      (three times)
  31 passed in 32.74s / 31 passed in 32.70s / 31 passed in 32.68s
my_hailo_env/bin/python -m pytest tests/ -q -p no:cacheprovider -rs
  SKIPPED [23] tests/conftest.py:415: device test skipped: the device level is opt-in ...
  445 passed, 23 skipped in 95.99s (0:01:35)
~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q -p no:cacheprovider
  388 passed, 80 skipped in 36.82s
```
(The same two full runs, taken before the device work, gave 445 passed, 23 skipped in 96.04s and 388 passed,
80 skipped in 36.95s. Attempt 1 ended at 437 passed (and 388 passed in Poetry); the difference is the Opus
session's seven tests plus mine.)

The 23 skips in `my_hailo_env` are 23 `device`-marked tests (the Tester's 22 and one in
`tests/test_harness.py`), skipped without `--run-device`. The 80 skips in Poetry are the `gst` tests (no `gi`
there), the same device tests and the harness tests that need pytest-timeout.

Red-before-green (scratch trees under the session scratchpad, `syncred.sh` copies the current tests):
```
cd scratchpad/red_base && my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -p no:cacheprovider -rf
  29 failed, 2 passed in 103.67s (0:01:43)
cd scratchpad/red_a1   && my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -p no:cacheprovider -rf
  7 failed, 24 passed in 33.50s
```

Device (one pytest per job; boot service stopped; camera attached; `SV_UAT_APP_LOG_DIR=<scratch>/applogs`):
```
my_hailo_env/bin/python -m pytest tests/uat/test_tc024_003_camera_removed.py --run-device -v -rA -p no:cacheprovider
  22 passed in 589.67s (0:09:49)
... -k camera_drops_again_while_restarting   (5 separate runs)    1 passed in 81.31s / 86.25s / 86.28s / 85.50s / 86.22s
... -k unplug_while_mode_switch_pending      (2 more runs)        7 passed in 152.28s / 148.92s
scratch copy, gaps 1.0-1.6 (2 runs)                               7 passed in 151.21s / 152.42s
scratch O1 tests                                                  20 passed in 136.28s; baseline 7 failed, 1 passed in 67.40s; final reps 6-10: 5 passed in 41.99s
```

## Numbers (device runs; all on the Hailo chip, the real camera, the service stopped)
Probes are in `/tmp/claude-1000/-home-sv-rpi5-Projects-Second-Vision-worktrees-kenth/56aa9637-501c-424e-8a8c-5732e695d84a/scratchpad/probes/`
(`probe.py`, `nobuf.py`, `firstframe.py`, `step1.py`, `runner.sh`, `runner2.sh`, logs in `logs/`). Each run is
a fresh process through the same environment `run.sh` sets up, built on the app's **own** class
(`SecondVisionApp(None, StandaloneUserData(), config)`), its own pipeline string and its own
`_release_old_pipeline()`. Exit 139 is SIGSEGV. **Batches 1 to 4 and the first-frame runs ran against the tree
as the Opus session left it; since then the only code changes are `_drop_buffer` moving into the class and
docstrings. I repeated the F1 probe on the final code (10 of 10 clean) and every device-test run below is on
the final code.**

**A. F1: a start on `/dev/video99` (cannot open) after a first pipeline ran 3 s in the process and was torn
down as `_release_old_pipeline()` does, 5 s earlier.** (`probe.py f1 <variant>`, 10 runs each)

| Variant | Crashes | Survivors |
|---|---|---|
| raw `set_state(PLAYING)` (attempt 1's shape) | **8 of 10** (exit 139) | 1 clean, 1 with `CRITICAL: gst_object_unref ... ref_count > 0` |
| staged order, priming switched off | 0 of 10 | 10 clean, 0 CRITICAL, outcome "camera" |
| `_launch_on_camera` as written | 0 of 10 | 10 clean, 0 CRITICAL |
| `_launch_on_camera`, final code | 0 of 10 | 10 clean, 0 CRITICAL |

Step 1 on its own (`step1.py`, 3 runs): `camera.set_state(READY)` on the missing node returns FAILURE in
0.000 s with "Cannot identify device", and both hailonets are still NULL.

**B. The plugin defect with no camera and no USB timing.** The app's own pipeline with the camera replaced by a
live `videotestsrc`; pipeline held at PAUSED (a live source sends nothing there), then torn down with
`_release_old_pipeline()`. (`nobuf.py`)

| Variant | Crashes |
|---|---|
| earlier pipeline ran 3 s first, no priming | **7 of 10** (1 of the 3 survivors printed the CRITICAL) |
| earlier pipeline ran first, `_prime_hailonets` after PAUSED | 0 of 10 (0 CRITICAL) |
| fresh process, no priming | **10 of 10** |
| earlier pipeline ran first, then PLAYING 2 s with real frames before the teardown | 0 of 5 |

**C. A teardown soon after a start on the live camera** (earlier pipeline first; start at t=0, teardown at the
offset; 6 runs each; `probe.py early <variant> <ms>`)

| Teardown after | raw start | staged, no priming | as written |
|---|---|---|---|
| 0 ms | 3 of 6 crash | 3 of 6 crash | **0 of 6** |
| 300 ms | 4 of 6 crash | **6 of 6** crash | **0 of 6** |
| 1500 ms (control: first frames have passed) | 0 of 6 | 0 of 6 | 0 of 6 |

**D. First frames** (`firstframe.py`, fresh process, `_launch_on_camera('/dev/video0')`, one run per mode
plus an unprimed `both` control): see answer 2. The start (`_launch_on_camera`) took 0.33 s in `both`, 0.18 s in
`depth`, 0.16 to 0.18 s in `detection`, against 0.30 to 0.31 s for the unprimed `both` control (two runs each). From the 27 restarts in the full
device run: "Camera found on" to "Camera back" 0.013 to 0.35 s, median 0.21 s (mode dependent: `none` is the
shortest). Attach to "Camera back": 23 samples, 0.55 to 2.71 s, median 1.78 s (attempt 1's Tester: median
1.71 s, 30 samples; the 2 s look interval dominates, so I see no slowdown). A panel mode switch with the
camera present: first frame after 1.28 to 1.87 s (7 samples, median 1.46 s) against 1.47 to 2.12 s in the
attempt-1 logs.

**E. The device tests (the Tester's file, not edited; one pytest per job; `SV_UAT_APP_LOG_DIR` in scratch;
`PYTHONFAULTHANDLER=1`).**

| Brief-2's "fixed means repeated" | Result |
|---|---|
| F1 isolation, ≥10 fresh-process runs, no crash | **10 of 10 clean** on the final code (and 10 of 10 on the code before the move), table A |
| Bounce test `…camera_drops_again_while_restarting` (0.0, 0.3, 1.0 s), ≥5 consecutive passes | **5 consecutive passes** (81 to 86 s each), plus one in the full run: 6 of 6. What the five dedicated runs show: at the 0.0 s bounce always "start failed, back to waiting" (the camera could not be opened, nothing Hailo moved). At the 0.3 s bounce the pipeline had started and the loss came after it (2 runs), or the bounce landed while `_launch_on_camera` was still running (3 runs; the Tester's test labels it "other"): the start finished, "Camera back" was logged, and 0.2 s later the camera's error arrived on the bus and was torn down normally ("released in 1.1 s"). Both are the F2 window (a Hailo pipeline lost within 0.6 s of its start) |
| Each `…unplug_while_mode_switch_pending[gap]`, ≥3 passes | **3 passes each** for at-once, 0.8, 1.1, 1.2, 1.3, 1.4, 1.5 s (full file + two more runs of the group; 7 of 7 each time) |
| The other device tests, one pass each | The full file: **`22 passed in 589.67s`** (all four modes, clean stops, three unplugs in a row, real glasses board and panel, another node, mode switch during the loss, bounce, four mid-transition stops) |
| Extra, mine: gaps 1.0 to 1.6 s in 0.1 s steps (brief 1c) | A scratch copy of the Tester's mode-switch test with only that list changed (`scratchpad/sweep/`, in `/tmp`, symlinks `.claude`): **2 passes each, 14 of 14**. The runs show the switch-over: at 1.0 to 1.2 s the rebuild failed to start or the node had gone ("waiting for a camera instead" / "did not start"), from 1.2 to 1.3 s on the rebuilt pipeline's camera error arrived as a normal loss and was torn down |
| Crash markers in all 75 app logs of the final code (22 + 5 + 14 + 14 + 20) | **0** with `CRITICAL`, `Traceback`, `Fatal Python`, `Segmentation fault`, `MOCK`, `HAILO_OUT_OF_PHYSICAL_DEVICES`, `not enough free devices`, `forcing exit`, `stopped acknowledging`, `Failed to start new pipeline`, `Exiting with error` |

**F. O1 (Ctrl+C right after a pipeline start), measured because the same mechanism may cover it; not widened
into a fix.** Scratch tests in the same copy: a panel switch to `detection`, then SIGINT after "Pipeline
rebuilt".
- Baseline code (`scratchpad/base_run`, the baseline `src/` through the same `run.sh`), 0.15 s: **7 of 8 exit
  -11**.
- Final code: 0.15 s **0 of 10** (all exit 0, 0.57 to 0.67 s); 0.05 s 0 of 5; 0.3 s 0 of 5.
- So O1 after a mode-switch rebuild is covered by the same mechanism, with evidence. **Not covered:** the
  program's first pipeline at boot is started by the library's `run()`, not by this code, so its hailonets are
  not primed. A stop 0.15 s after "[MAIN] Starting pipeline..." exited 0 in 5 of 5 here, but that is no
  proof of safety: the first hailonets a process allocates may sit in memory that is still zero, which would
  hide the defect (a guess; table B's fresh-process row crashed because its pipeline was not the first one
  allocated). The brief said not to chase it; I did not. For T-11: re-test it with these numbers, and decide whether the boot
  pipeline needs the same treatment.

## Acceptance criteria: self-check, not approval
| # | How it is addressed | Evidence level reached |
|---|---|---|
| 1 No crash (1a, 1b, 1c) | 1a: the camera bounces at 0.0, 0.3, 1.0 s after "Camera found on": 6 of 6 passes of the bounce test, no segfault, no CRITICAL. 1b: a camera found but not openable goes back to waiting with the log line; F1 probe 10 of 10 clean. 1c: every gap from at-once to 1.6 s, 3 passes (Tester's gaps) or 2 passes (my 1.0 to 1.6 sweep), no exit | device-integration (simulated unplug) for all three; gst-level tests for the logic. Person's real-camera confirmation outstanding |
| 2 Everything else keeps running | Unchanged from attempt 1; the full file (fake board, fake panel, real speech worker) and `real_ports` (real board, real panel) pass | device-integration |
| 3 Motors not left on a stale reading | One zero dict per loss; asserted on the wire in the device tests (bounce: the last motor packet is zero) | unit + device-integration (fake board) |
| 4 Keeps checking; restarts on a camera | Median attach to "Camera back" 1.78 s, any node (another-node test passes); the camera-first start and priming add no measurable time (start 0.2 to 0.35 s) | device-integration |
| 5 Never mock mode | `grep MOCK` over the 75 final-code logs: 0; no `--mock` in the code | unit + device log |
| 6 Clean stop | Ctrl+C and SIGTERM while waiting, during teardown and during restart: the full-file tests pass (≤ 1.0 s); and the O1 numbers above | device-integration |
| REQ-DTA-08 regression | The stop tests above, plus the unit suite | device + unit |

## Not done, limitations, deviations from the brief
- **No real unplug, no person.** The unplug is `usb_sim.py` (USB `authorized`=0) through the Tester's
  `camera_unplug` fixture. The one real-camera confirmation (MANUAL-TESTS.md, T-03a) stays outstanding.
  Under the boot service (`systemctl stop`) was not run (service control is not mine).
- **The plugin finding is not confirmed in the binary**, only by source version, strings and measurement.
- Residual windows (answer 4 (a), (b)); the boot-time first pipeline is unprimed (answer F).
- The sink-pad caps oddity (answer 2).
- Two Bash commands of mine were refused by the role hook (a `sudo usb_sim.py status` inside a longer command
  line with a pipe or a redirect, or next to a here-document); I re-ran them in the allowed form. No other sudo: `usb_sim.py status` and `sudo fuser -v` by
  hand; detach and attach only through the Tester's fixture.
- libhailort writes `hailort.log` and `hailort.1.log` into the folder a pipeline runs from (the repo root; git
  ignores `*.log`) on every run, mine and the Tester's. Not a change of mine.
- `tests/uat/test_tc024_003_camera_removed.py`: not edited. No test in it expects the wrong thing (R11). Its
  `NEVER` list does not include `CRITICAL` (brief 1a says a CRITICAL is a finding); I grepped the logs for it
  myself. Its gap list stops at 1.5 s; the brief's 1c asks for 1.0 to 1.6. The Tester may want to add 1.0
  and 1.6 and `CRITICAL`.

## Untested or simulated behaviour
- Everything about a real cable pull, and the wearer feeling the motors stop, hearing the speech.
- `rawusb://` and file sources (kept on the library's path; the file-source guard is a gst-level test).
- Real failure of a Hailo element during a start (`START_FAILED`, shutdown path): gst-level stand-in only.
- The `--use-frame` display process, the boot service and its 10 s stop bound.
- Priming failure on the real plugin (no way to force it); the exception and flow-not-OK branches.

## Risks and questions for the Lead
1. **Rule 8, to put to the user:** the decision "every USB-camera pipeline is started camera-first and with
   its hailonets primed, because HailoRT 4.23.0's hailonet crashes when taken down before its first
   buffer" is a settled design choice other workstreams will meet. It would go in `DECISIONS.md`, and the
   start order in `ARCHITECTURE.md`. Not written by me (out of my scope).
2. **Upstream:** the defect is in HailoRT's `gsthailonet.cpp` (allocator pointer not initialised). The user
   may want to report it to hailo-ai/hailort. I did not.
3. **T-11 / O1:** after a rebuild it is now 0 of 20 (baseline 7 of 8). Suggest T-11 is re-scoped to the boot
   pipeline only, or closed once the person has run the boot path a few times.
4. **Cost:** priming runs each network's load on the main loop during the start (that work used to happen at
   PAUSED to PLAYING and the first frame). Measured, the start as a whole is not slower: `_launch_on_camera`
   0.33 s in `both` primed against 0.30 to 0.31 s unprimed. If the Tester sees a plain restart slower than
   1.71 s (attempt 1's median), compare against that figure.
5. **Stability:** my scratch probes and the O1 comparison tree live under the session scratchpad (paths above)
   and are not in the repository. If the Tester wants them for the evidence folder they need copying there.
6. Camera state at the end: attached (`usb_sim.py status`: authorized=1, video0 to video3); no usb_sim timer
   pending; nothing of mine running; `sudo fuser -v /dev/hailo0` printed nothing (exit 1), so neither a stray
   process nor `hailort_service` held the device node.
