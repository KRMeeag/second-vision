# T-03b · Developer report, attempt 1

- Date / model: 2026-10-17 / developer-sonnet
- Summary: With no camera node at launch the app now builds **no pipeline at all**: the constructor
  parks the empty pipeline and goes to T-03a's WAITING state, and the library's `run()` (otherwise
  untouched) starts the camera watcher from the loop's first iteration; when a camera appears the
  watcher starts vision through T-03a's staged, primed `_launch_on_camera`, in the mode current then.
  `sv-main.sh` no longer waits for the camera, no longer exits, and never passes `--mock`; `main.py`
  no longer enters mock mode by itself (hailo_apps missing without `--mock` is a message and exit 1,
  before any worker). Device: 20 of 20 fresh starts with the camera appearing 0.5 to 5 s later ended
  with vision running in the right mode; 17 of 20 starts stopped 0.1 to 3 s after launch exited 0,
  and the 3 that did not were all stopped before the program had installed any signal handler
  (under 0.45 s), a window that exists with a camera attached and in the baseline too (see
  "Not done", item 1: the one question I need answered).

## Read first: what happens today (measured before designing; brief, design note 1)

The brief's reading of the three points was right. Differences and measurements:

1. **Launcher**: read, and confirmed by the new launcher tests run against a scratch copy of the
   baseline launcher with a stub `run.sh` (21 fail, below): with no camera and `SV_SERVICE=1` the
   stub is never reached (wait, then `exit 1`); without it the stub is called with
   `--mock --serial-port .. --config-port ..`.
2. **`main.py`**: confirmed by the new `main()` test run on the baseline `src/`: hailo_apps missing,
   no `--mock` -> `_run_mock_mode` runs, workers started.
3. **First pipeline, measured (the brief said unknown)**. Baseline code (`scripts/run.sh --input
   /dev/video0 ...`, camera detached by `usb_sim.py`, fake glasses board and panel on pseudo-terminals,
   no `DISPLAY`), **25 fresh-process starts: 4 were killed by SIGSEGV (exit -11) about 0.2 to 0.5 s
   after "[MAIN] Starting pipeline..."; the other 21 survived**, but only because the library's
   `run()` ignores the failed PAUSED, the v4l2 error then arrives on the bus ("Cannot identify device
   '/dev/video0'") and T-03a's camera-loss path tears the never-fed Hailo pipeline down, which is the
   crash T-03a measured (7 to 10 of 10), here at 16 %. So the program does not reliably stay up today.
   Logs: `scratchpad/t03b/baseline/` (5 starts) and `scratchpad/t03b/baseline20/` (20 starts); example
   crash: `baseline/161329-baseline.log` ends "exit code: -11" with nothing after `hailo_display not
   found in pipeline`.

## Design (brief, design notes 2 and 3)

Take the safest shape in note 2 and do not build the Hailo pipeline at all when the node is missing:

- `SecondVisionApp.create_pipeline()` (called once, by the constructor): if `_camera_node_missing()`
  (USB source and `os.path.exists(video_source)` false) -> `_start_without_camera()`, else the
  library's `create_pipeline()` untouched. `_start_without_camera()` does what the library leaves
  behind (`Gst.init`, `self.loop`) plus T-03a's waiting state: `_park_empty_pipeline()`, state
  WAITING, `_camera_missing_since` (so "Still no USB camera after N s" counts from the start), one
  `MOTORS_OFF` through `_stop_motors()` (the board holds its last command), and one WARNING that says
  what is going on. `get_pipeline_string()` is never called, so no Hailo element exists.
- `SecondVisionApp.run()`: only in state WAITING it queues `_begin_waiting` on the loop, then calls
  the library's `run()`. The library sets the parked pipeline PAUSED/PLAYING (empty: no effect), runs
  the loop, and its own cleanup and exit code apply to a stop. `_begin_waiting` (first loop
  iteration, after the library started the optional display process) sets the placeholder back to
  NULL, removes the bus watch the library gave it, and starts T-03a's watcher.
  - *Why NULL*: without it GStreamer printed `CRITICAL: Trying to dispose element no_camera, but it
    is in PLAYING instead of the NULL state` when the camera's pipeline replaced the placeholder (seen
    on the first device smoke run; T-03a's parked placeholder is never started, so it never had this).
    A gst test now catches it (red without the NULL line, shown).
  - *Why the idle quit when `_shutting_down`*: a SIGINT after `GStreamerApp.__init__` installed
    `shutdown` and before the constructor finished finds no pipeline and no loop, only records itself,
    and the loop would never be told to quit: the program ran until the 5 s force-exit timer
    ("forcing exit", exit 1 in the baseline: 1 of 8 baseline early stops). Now `run()` quits the loop
    as soon as it runs.
- `_connect_callback()`: returns at once in state WAITING (the library's `run()` calls it on the empty
  placeholder; two "identity not found" warnings would read as a fault).
- Mode at start-up: `SystemConfig.pipeline_mode` defaults to `"both"` (`core/config.py:10`);
  `trigger_rebuild()` and `_rebuild_pipeline()` already return while the state is not OK (T-03a), so a
  panel switch while waiting is only recorded and the pipeline that starts is built for it. Confirmed,
  not re-implemented (gst test per mode, device runs per mode).
- **R1 answered (note 3)**: the brief asked whether simply dropping the references to a pre-built,
  never-started pipeline is safe. Measured: baseline `src/`, `parse_launch` of the app's real pipeline
  string, references dropped, `gc.collect()`, fresh process each: modes both, depth, detection 10
  each, and "both" with every hailonet and the pipeline explicitly set to NULL 10 more: **40 of 40
  survived** (scratch `r1_drop.py`; the camera and Hailo device are not touched, no state is set). It
  matches the source: `gst_hailonet_dispose` is `delete self->impl` and nothing else; the allocator
  is unref'd only in `gst_hailonet_free`, called from READY_TO_NULL (copy of
  `gsthailonet.cpp` v4.23.0 from T-03a's fetch, read as data: lines 66-73, 105-170, 499-507). So
  building and dropping would also have been safe; not building is simply less work at boot, and it
  makes the question moot. I did not test a built-then-PAUSED-then-failed pipeline here: that is the
  baseline crash above.

## Inspected before changing

- Read: the brief; REQ-CON-08 (REQUIREMENTS.md:637) and TC-024-002 (`uat-snapshot.yaml:1658`) as
  written; `reports/T-03a/dev-report-2.md` answer 1 and the docstrings of `_launch_on_camera` and
  `_prime_hailonets` (only those); `app.py` (`__init__`, `create_pipeline` users, `shutdown`,
  `_rebuild_pipeline`, `trigger_rebuild`, the whole camera-loss section); `main.py`; `sv-main.sh`,
  `run.sh`, `second-vision.service`; the library's `GStreamerApp.__init__`, `create_pipeline`,
  `bus_call`, `shutdown`, `run`, `get_source_type`; `serial_worker.py` main loop; `config_reader.py`
  mode path; `tests/test_camera_loss.py` fixtures and the Tester's `test_tc024_003_camera_removed.py`
  harness (`FakeGlasses`, `FakePanel`, `AppRun`).
- Callers confirmed: `create_pipeline` has one caller in `app.py` (`__init__`, line 431; the drafts
  `pipeline/TBR-*.py` and `custom_depth_detection/` have their own and are unwired); `run()` is called by `main.py:_run_pipeline_mode`; `_begin_waiting` by `run()`;
  `_start_without_camera` by `create_pipeline`; `trigger_rebuild` by `config_reader.py:103`.
- Existing tests read: `test_camera_loss.py` (31 tests), `test_pipeline_modes.py` (it stubs hailo
  modules into `sys.modules`; this bit my first `main()` test, see "Tests").

## Hailo docs consulted (Rule 6)

- Workbench `/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`: grepped `.hailo/memory/`,
  `.hailo/instructions/gstreamer-pipelines.md`, `.hailo/toolsets/` and `SV-Docu/` for camera
  absent/disconnected/missing, "no camera", first buffer, READY to NULL, `set_state`, shutdown,
  `exit(1)`, `loop.quit`. **No hailo-apps document describes starting or tearing down a pipeline with
  a camera missing or a Hailo element before its first buffer** (same finding as T-03a). The only
  relevant lines are `camera_and_display.md:12-28` (USB camera discovery).
  - **Doc vs code (Rule 7)**: `.hailo/memory/camera_and_display.md:19` and
    `toolsets/core-framework-api.md:98-102` say `get_usb_video_devices()` returns device *indices*
    (int); the installed library's docstring and the logs say it returns `/dev/videoN` paths on Linux
    (`camera_utils.py:391-405`, and "USB camera detected: /dev/video2" in every device log). The app
    (T-03a) uses paths; nothing to change here, but the workbench note is stale.
- Library source, read only (`my_hailo_env/.../gstreamer_app.py`): `__init__` (`--input usb` with no
  camera does `exit(1)` before our code runs, see "Not done" item 2), `create_pipeline`, `run`
  (`set_state(PAUSED)`/`PLAYING` on `self.pipeline`, `loop.run()`, cleanup, `sys.exit`), `shutdown`
  (`self.pipeline.set_state`, `GLib.idle_add(self.loop.quit)`), `bus_call`.
- HailoRT source for `hailonet`: see R1 above (tag v4.23.0, <https://github.com/hailo-ai/hailort>).

## Files changed

| File | What changed | Why |
|---|---|---|
| `src/second_vision/pipeline/app.py` | New: `create_pipeline`, `_start_without_camera`, `run`, `_begin_waiting`, class default `_placeholder_watched`, a comment block; `_connect_callback` returns first in state WAITING. Nothing of T-03a's changed (`_release_old_pipeline`, `_launch_on_camera`, `_prime_hailonets`, `_start_on_camera`, `_camera_watch`, `MOTORS_OFF`, the "Still no USB camera" line are untouched; the diff has only additions plus the 3-line guard) | Criteria 1 to 4, 6 |
| `src/second_vision/main.py` | `HAILO_IMPORT_ERROR` recorded; `main()` refuses (stderr message, `sys.exit(1)`) before any worker when hailo_apps cannot be imported and `--mock` was not given; the selection is `if pre_args.mock:` (was `or not HAILO_AVAILABLE`) | Criterion 5 (D-13) |
| `scripts/sv-main.sh` | Removed the `SV_SERVICE=1` camera wait (up to 60 s, then `exit 1`), removed the `SV_SERVICE=1` hailo_apps import check, removed the `--mock` fallback; one informational message when the node is missing; `RUN="${SV_RUN_SH:-$REPO_ROOT/scripts/run.sh}"` (the one test variable the brief allowed, default unchanged); header comments rewritten | Criteria 1, 5, 7 |
| `scripts/second-vision.service` | Two comment sentences only (the stale "Wait for the camera and hard-fail instead of mock mode", and "Retry forever: a camera plugged in late must still come up") | Brief |
| `tests/test_startup_without_camera.py` (new), `tests/test_main_startup.py` (new), `tests/test_launcher.py` (new) | See next section | |

Final launcher, read line by line (R2): header comments; `REPO_ROOT`, `cd`; `RUN` (as above);
`SERIAL_PORT`, `CONFIG_PORT`, `INPUT` defaults unchanged; `FRAME_FLAG` rule unchanged; if `$INPUT` is
missing, three `echo` lines and carry on; if `$CONFIG_PORT` is missing,
`exec "$RUN" --input "$INPUT" --width 640 --height 480 $FRAME_FLAG --serial-port "$SERIAL_PORT"`;
otherwise the same with `--config-port "$CONFIG_PORT"`. Both `exec` lines are byte-for-byte the
baseline's. There is no other `exec`, no `sleep`, no `exit`.

**Removed on purpose, say if you disagree**: the `SV_SERVICE=1` hailo_apps import check. It existed
only because `main.py` would run mock mode unasked; `main.py` now refuses itself (message and exit 1,
systemd retries). It also cost a second interpreter start (about 0.3 s) before the program could start
its speech and link, which T-03c wants early.

## Tests added or updated

Unit tests run in the Poetry environment; the `gst` file runs in `my_hailo_env` and skips with a reason
in the Poetry one. Red-before-green with scratch copies under `scratchpad/t03b/` (`basetree/` =
baseline `src/` `tests/` `scripts/` with the new test files copied in; `baselauncher/` = baseline
`sv-main.sh` and `second-vision.service` next to a stub `run.sh`); no git.

| Test file | What it checks | Fails on the baseline? |
|---|---|---|
| `tests/test_main_startup.py` (5) | `main()`: hailo missing + no `--mock` -> exit non-zero, message names hailo_apps, the import error and `--mock`, **no worker thread, no mock runner, no pipeline runner**; `--mock` still runs mock mode (hailo missing, and present); hailo present + no flag runs the pipeline; the import error is kept. The module is loaded fresh with the two hailo imports blocked, so other test files' stubs in `sys.modules` cannot change the result | 2 of 5 fail (the refusal; the import-error attribute). The 3 that pass are the unchanged behaviours, as intended |
| `tests/test_launcher.py` (41) | The real `sv-main.sh` run with `SV_RUN_SH` = a stub that records its arguments. 18 camera-attached cases (panel port or not x headless/DISPLAY/WAYLAND_DISPLAY x no/0/1 `SV_SERVICE`) -> the same command line as before (including `--use-frame`); 18 camera-absent cases of the same matrix -> started anyway, same command line, no `--mock`, in under 5 s; the log says no camera; source check: no non-comment line mentions mock and every `exec` is `$RUN`; `bash -n`; default run target unchanged; the unit file no longer says wait/hard-fail and its settings are unchanged. It refuses to run a launcher it cannot redirect to a stub, so a test run can never start the real program | **21 of 41 fail** on the baseline launcher (18 camera-absent, the log, the source check, the unit comment); the 20 that pass are the 18 camera-present cases, `bash -n` and the default target (nothing changed there, as intended) |
| `tests/test_startup_without_camera.py` (16, `gst`) | Real `create_pipeline()` and the library's real `run()` on stand-in pipelines. Absent node: no pipeline string built, empty placeholder, WAITING, loop exists, one `MOTORS_OFF`, watcher not started yet (x4 modes). Present node: library path unchanged. Camera appears later: panel switch to the mode recorded and builds nothing, then exactly one build, in that mode, on the new node, no "identity not found" warning, no GStreamer CRITICAL on stderr, exit 0 on Ctrl+C (x4 modes). Stop while waiting: SIGINT and SIGTERM -> exit 0, watcher gone, no mock. Stop that landed during construction is not lost. Real panel worker: switch while waiting is announced "depth mode" and recorded. Camera that appears but cannot open (real `v4l2src` on a missing node) -> back to waiting, then starts on the next one. Stop the moment the camera's pipeline runs: exit 0 and the Hailo stand-in was fed its buffer first. "Still no USB camera" counted from the start | **15 of 16 fail** on the baseline tree (7 more teardown errors; the run took 7 min because stuck loops end at the 60 s guard). Honest breakdown: 3 fail only because the baseline has no `_begin_waiting` (AttributeError, they call it to stand in for `run()`'s first idle); the other 12 fail on behaviour (a pipeline was built, no WAITING state, the stop was lost and the loop ran to the guard). The one that passes is "present node keeps the library path". The CRITICAL assertion: with only the `set_state(NULL)` line of `_begin_waiting` removed, the 4 per-mode "camera appearing later" tests fail (scratch `mid/`), with it all 16 pass |

Not done in tests: nothing in `tests/uat/` was edited. None of the Tester's tests there needs to
change for this task (the camera-removed ones start with a camera, and were run, below).

## Commands run and their actual results

Whole unit suite after the last edit:

```
$ ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q -p no:cacheprovider
434 passed, 129 skipped in 37.43s
$ my_hailo_env/bin/python -m pytest tests/ -q -p no:cacheprovider
511 passed, 52 skipped in 112.22s (0:01:52)
$ my_hailo_env/bin/python -m pytest tests/test_startup_without_camera.py tests/test_main_startup.py tests/test_launcher.py tests/test_camera_loss.py -q
93 passed in 45.27s
```

(The skips are the `gst` tests in the Poetry run and the opt-in `device` tests.)

Device runs, all by my scratch harness (`scratchpad/t03b/devrun.py`, `devrun2.py`: they launch the
program through `scripts/sv-main.sh` with `SV_SERVICE=1`, no `DISPLAY`, the glasses board and panel as
pseudo-terminals using the Tester's `FakeGlasses`/`FakePanel` read-only, camera detached and attached
with `usb_sim.py`). They are my investigation, not the evidence for the decision. Before every job
`systemctl is-active second-vision.service` said `inactive` and `pgrep` found nothing of ours.

| Run | Result |
|---|---|
| Baseline, no camera, 25 fresh starts (above) | 4 SIGSEGV, 21 survive via the camera-loss path |
| **A. 130 s with no camera** (`hold`) | alive at 130.0 s, same PID; 129 glasses heartbeats seen; 0 vibration packets and 1 zero packet (the `MOTORS_OFF` at start); "Still no USB camera after 61 s" at 61.3 s and "after 122 s" at 122.1 s; CPU 0.5 % over 20 s; a panel `M:depth` at 10 s: "[CONFIG] Mode: depth" and "[TTS] 'depth mode'", `Mode depth recorded while the camera is missing`, no pipeline string ever generated; SIGTERM -> exit 0 in 0.4 s; no forbidden line (Traceback, MOCK, CRITICAL, Segmentation fault, forcing exit ...); nothing left in the session |
| **B. camera appears 0.5 to 5 s after launch, 20 fresh starts**, modes cycled (5 each of both, depth, detection, none), the mode switch sent by the fake panel before the camera appears, stop alternately SIGINT/SIGTERM | **20 of 20**: "Camera found" once, one pipeline start, `Camera back` 0.7 to 2.5 s after the attach command, the start was in the mode set while waiting (`mode=` in the log), `[DEPTH]` frames flowing (both, depth), switch announced, exit 0 in 0.5 to 1.0 s, no forbidden line, nothing left behind |
| **C. camera at 10 s (4) and at 90 s (4)**, one per mode | **8 of 8**, same checks; at 90 s one "Still no USB camera" line each |
| **D. camera drops again just after it appears**, 6 fresh starts: attached at 2 s, detached again 0.1 to 0.6 s after "Camera found" (`--for 6`), then back | **6 of 6** ended in "running" (frames again), via "Camera lost" (5) or "did not start on ... still looking" (1); never a crash or exit; exit 0 on stop |
| **E. stop 0.13 to 1.5 s after "Camera found on"**, 12 fresh starts, SIGINT/SIGTERM alternating | **12 of 12** exit 0 in 0.41 to 1.01 s, no forbidden line |
| **F. 20 fresh starts with no camera, stopped 0.1 to 3 s after launch**, alternating SIGINT/SIGTERM | **17 of 20** exit 0 (0.3 to 0.96 s). **3 did not**: stops at 0.14 s and 0.18 s (SIGINT: `KeyboardInterrupt` in the numpy/gi imports, exit -2, a Traceback) and at 0.43 s (SIGTERM: exit -15). All three landed before the program had installed any signal handler. All 17 stops at 0.73 s or later were clean. Analysis in "Not done", item 1 |
| **G. camera present at start, plain start through the launcher**, 3 starts | 3 of 3: frames flow, no "No camera at start-up" or "Camera found" line, one pipeline string, panel switches through detection, depth, none, both each rebuilt, exit 0 |
| **H. real boards once**: `/dev/serial0` and `/dev/ttyAMA3` (the launcher defaults), no camera, camera at 3 s | connected to both, the real board ACKed ("UART Connection to ESP32 is WORKING"), no "stopped acknowledging", camera back, frames, exit 0 in 0.7 s. Whether the motors vibrated correctly was not observed (needs a person) |
| **I. T-03a's device cases with the Tester's file**, `--run-device -k 'unplug_replug_in_each_mode or mode_switch_pending or bounce_sweep'` (`SV_UAT_APP_LOG_DIR` in scratch) | **14 passed in 635.78 s** (4 modes plain removal, 9 mode-switch gaps including the short ones and 1.2 s, and the bounce sweep) |
| R1: build the pipeline and drop it, 40 fresh processes | 40 of 40 survived (above) |

Camera attached again after every job; the harness `attach`es in a `finally`. End state:
`systemctl is-active second-vision.service` = `inactive`; `pgrep -af "main.py|second_vision|Parallel-Depth"`
finds nothing of mine; `sudo .claude/tools/usb_sim.py status` = camera attached on
`/dev/video0..3`; `sudo fuser -v /dev/hailo0` printed nothing (exit 1: no process has the device open,
so no `hailort_service` holder either at this moment). `check_outside_writes.py
.claude/baselines/T-03b/.stamp`: nothing changed in libraries or other checkouts.

## Acceptance criteria: self-check, not approval

| # | How it is addressed | Evidence level reached |
|---|---|---|
| 1 No crash, stays up >= 120 s, "Still no USB camera" about once a minute | Run A (130 s, lines at 61 s and 122 s, same PID, no forbidden line); the 4/25 baseline crash is gone because nothing Hailo is built or started | device-integration (my run, fake boards) |
| 2 Everything else keeps running; mode switch recorded and announced; no vibration | Run A (heartbeats, announce, 0 vibration packets), run H (real boards), gst test with the real panel worker | device-integration, unit (gst) |
| 3 Camera at 1, 10, 90 s, same or other node, four modes, three in a row | B (20 in a row, 0.5 to 5 s, all modes), C (10 s and 90 s, all modes). "Same or another node": the camera came back on `/dev/video0` every time on the device; another node is covered only by the gst tests (`/dev/video5`) and by T-03a's device test of another node, not re-run | device-integration (B, C), unit (nodes) |
| 4 Cannot open / drops again | D (6/6), gst test with a real failing `v4l2src` | device-integration, unit (gst) |
| 5 Never mock | Launcher: 36 stub cases + source check; `main()`: 5 tests; no "MOCK" in any device log (it is in my forbidden list) | unit, device-integration |
| 6 Clean stop, exit 0 within ~1 s, while waiting and right after the camera appears | A (SIGTERM 0.4 s), E (12/12 up to 1.01 s), B/C (stop after vision ran), gst tests. **Stops in the first ~0.45 s after launch: not met** (F, item 1 below) | device-integration, unit (gst) |
| 7 Camera present: nothing changes | 18 launcher cases give the baseline command line; both `exec` lines unchanged; G (3 plain starts); `create_pipeline` on a present node is the library's (gst test) | unit, device-integration |
| 8 T-03a holds | `test_camera_loss.py` unchanged and passing; run I (14 device cases) | unit (gst), device-integration |

## Not done, limitations, deviations from the brief

1. **Stops in the first ~0.45 s after launch are not exit 0, with or without a camera. Needs your
   decision.** The brief's "Fixed means repeated" asks for every stop 0.1 to 3 s after launch to
   exit 0. In run F 3 of 20 did not, and the cause is not this task's code: from launch until
   `GStreamerApp.__init__` (SIGINT, about 0.33 s after launch) and `main()`'s `signal.signal(SIGTERM)`
   (about 0.57 s) nothing handles the signals, so Python's defaults apply: SIGINT in the imports is a
   `KeyboardInterrupt` traceback and exit -2 (killed by SIGINT), SIGTERM kills outright (-15). Evidence it is
   old and camera-independent: (a) baseline `src/`, 8 stops at 0.10 to 0.46 s, no camera: **8 of 8
   non-zero** (-2, -15, and one 5 s "forcing exit" exit 1, the window I did fix); the new tree, same
   8 times: 1 clean, 7 not (one SIGINT at 0.29 s was *lost*, the program went on and started vision when the
   timer brought the camera back); (b) 25 SIGINTs at 0.2 to 0.4 s with the camera attached (the unchanged
   path): baseline 21 killed by SIGINT, 3 lost, 1 exit 1; new tree 18, 5 lost, 2 exit 1; (c) a bare
   interpreter that only imports `cv2`, `gi`, `numpy`, SIGINT at 0.02 to 0.2 s: 38 of 40 killed, 1 lost,
   1 exit 1, so a lost early SIGINT is not even from this program. The part I did fix (a SIGINT after
   `GStreamerApp.__init__` and before the constructor finished) is in the app: `run()` quits the loop.
   The rest needs handlers earlier than anything in `app.py`, i.e. in `main.py`: install SIGINT and
   SIGTERM handlers before the heavy imports (under `if __name__ == "__main__"`, so importing
   `main` in a test changes nothing), raising `SystemExit(0)`, and after the app exists replay a SIGTERM
   that arrived meanwhile. About 15 lines and a test. I did **not** do it: the brief limits `main.py` to
   the mock decision and "where the app is told the camera is missing", and a stop is REQ-DTA-08
   territory. The bash part of the launcher (about 0.09 s before Python starts) cannot be covered by any
   of it. In service use this window cannot matter: systemd's `KillSignal=SIGINT` of a process killed by
   SIGINT counts as a clean stop. If you want the literal "every stop from 0.1 s exits 0", say so in
   attempt 2 and I will do it with that scope. The Tester will see the same 0.14 to 0.45 s failures if
   it starts its stop clock at launch.
2. `--input usb` with no camera still ends in the library's `exit(1)` inside `GStreamerApp.__init__`,
   before any of our code can run ("No USB camera found for '--input usb'"). The launcher never passes
   `usb` (it passes `$INPUT`, default `/dev/video0`; `INPUT=usb` was already unsupported there: it
   fell to mock). It is a development flag. Not changed (it would need a library change or a parser
   hack).
3. A camera node that exists at launch but cannot be opened yet is still the library's `run()` start
   (R3; T-11's boot pipeline question). Not touched, as the brief says. It was not exercised: the
   attach in runs B to E always came 0.5 s or more after launch, and the node reappears about 1.5 s
   after the `attach` command, so the camera never raced `create_pipeline()`.
4. `--use-frame` (a `DISPLAY`) was not run with no camera: the Pi session is headless. The library's
   display process starts in `run()` before our first idle, as after a camera loss.
5. The long detaches (`--for 145` for run A, `--for 100` for the 90 s runs) are above the brief's
   "`--for 20` or less". Each was the only detach in flight, the camera was re-attached explicitly,
   and the next detach waited for the timer (as the Tester's own 135 s long-absence test does); a
   120 s absence cannot be made with 20 s.
6. In detection and none mode I could not see vision *running* in the log (there is no per-frame
   line); the evidence is "Camera back ... (mode=detection)", the process alive for 4 to 25 s after,
   no "Camera lost", exit 0. Depth frames were seen in both and depth.
7. T-03c: nothing was added to speak or vibrate (none). The hook for it is `_start_without_camera()`:
   the waiting state begins about 0.57 s after launch, before the first look at 2.6 s, so an alert
   started there needs no wait.

## Untested or simulated behaviour

- All device runs used pseudo-terminal stand-ins for the glasses board and the panel, except run H
  (real ports, once). Whether the real motors stay still while there is no camera, and that nothing
  odd is heard, needs a person (MANUAL-TESTS.md, with T-03a's).
- A real cable pull and a real boot with the camera unplugged: only the simulated `usb_sim.py` unplug
  was run. The service was never started or stopped.
- `DISPLAY` set: not run.
- The gst tests use stand-ins for the camera and the Hailo element; they show the order, not that the
  real plugin survives (the device runs do, within their counts).
- The unit tests of the launcher use a stub for `run.sh`; the real chain `sv-main.sh -> run.sh ->
  main.py` was run in every device run above.

## Risks and questions for the Lead

- **Q1 (item 1 above):** do you want the early signal handlers in `main.py` (extends the brief's
  `main.py` scope), or is "stops from about 0.5 s after launch exit 0; earlier stops behave as before"
  the accepted bar? The two answers change what the Tester should count.
- **Q2 (Rule 8, for the user, concrete):** record in `documentation/DECISIONS.md` that D-13 is now
  enforced in three places (launcher never passes `--mock`; `main()` refuses without hailo_apps; the
  app starts without a pipeline when the camera node is missing), and in `ARCHITECTURE.md` the
  start-up path (constructor parks an empty pipeline, `run()` starts the watcher, the camera's
  pipeline is the staged one)? Also `TASKS.md` for the T-03b line. I did not touch `documentation/`.
- **Q3:** the installed unit under `/etc/systemd` still carries the old comment sentences (comments
  only) until the user re-runs `install-service.sh`; behaviour does not depend on it, but the
  *launcher* it runs is the repo's, so the next boot already behaves as described.
- **Risk:** the launcher is the boot path. Besides the stub matrix, every device run went through the
  real `sv-main.sh`, and the baseline-shaped command line is asserted for all 18 camera-present
  combinations, but the first real boot after this lands is the one test of the service itself, which I
  must not run.
- **Risk, residual and not hidden:** the camera-present start is still the library's unprimed `run()`
  (T-11). A camera that is there but fails during that first start is T-03a's camera-loss path with the
  same Hailo exposure as before; this task does not change its rate.
