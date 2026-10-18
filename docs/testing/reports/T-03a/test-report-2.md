# T-03a · Verification report, attempt 2

- Date: 2026-10-17. A resumed run: the first attempt-2 Tester (Sonnet) was cut off by the usage limit at about 14:37, before
  it wrote this report. This is the second Tester session (Sonnet), 15:15 to 16:05. It relied on the earlier session's logs where
  they were complete and ran only what was not logged (lists below).
- Overall: **BLOCKED**. No check has FAILED and every automated check passes; the only blocked checks are the person's one
  real-camera confirmation (brief-1, "Verification expectations": criteria 1 to 4 need "device-integration + user-observed").
- Inputs: brief-2.md (Amendment A, "Verification expectations"), brief-1.md, dev-report-2.md, baseline `.claude/baselines/T-03a/`
- Commit: `cb0b20e` with uncommitted task changes. `second-vision.service`: inactive at every device job.
- Counts: 25 checks: **23 PASS, 0 FAIL, 2 BLOCKED** (the two BLOCKED are the person's: checks 24 and 25).

## Read first

1. **The three attempt-1 failures (F1, F2, F3) are gone on the device, with larger counts than the brief asked for.** In the 149
   program logs of this attempt that ran the task's code there is no segfault, no GStreamer `CRITICAL`, no traceback, no
   exit, no `forcing exit`, no mock mode, and every log ends `# exit code: 0` (counts under check 8).
2. **The root cause is backed by a source I read myself.** HailoRT v4.23.0 `gsthailonet.cpp` (fetched by me from
   github.com/hailo-ai/hailort, tag v4.23.0, read as data): `GstHailoAllocator *allocator;` has no initialiser
   (`gsthailonet.hpp:133`); the user-provided constructor `HailoNetImpl::HailoNetImpl()` (`.cpp:1497-1499`) does not set it, so the
   member is indeterminate; it is set only in `gst_hailonet_init_allocator` (`:359-377`), reached only from the sink-pad
   probe that runs on the first buffer (`:433-470`, `:420`); and `gst_hailonet_free` (`:105`, called on READY to NULL, `:499-507`)
   does `else if (nullptr != self->impl->allocator) gst_object_unref(self->impl->allocator)` (`:164-165`). The installed plugin is that
   version (`hailortcli --version` 4.23.0; `dpkg -l hailort` 4.23.0; `libgsthailo.so` links `libhailort.so.4.23.0`; the strings
   `:hailo_allocator`, `Could not release buffer pool`, `dma buff is not supported on this OS` are in the binary). I did not disassemble
   the stripped binary, so "the binary has exactly this code" rests on the version match. The device numbers agree with the source
   (checks 5 to 7).
3. **Priming (`_prime_hailonets`) is justified by numbers that are not the Developer's.** Fresh process per run, teardown N ms after a
   start on the real camera: with priming **40 of 40 clean** (0, 100, 300, 600, 1000 ms); without it **6 of 6 segfault at 0 ms and
   5 of 6 at 300 ms**; attempt 1's start shape 5 of 6 at both. No callback ever sees the priming buffer (check 7). A side result: the
   same mechanism fixes the older "Ctrl+C right after a mode switch" defect (O1, board T-11): **baseline 16 of 20 segfault, this code
   0 of 20** (check 23).
4. **Cost.** The restart is not slower (check 14). RSS grows 5 to 6 MB per vision restart (check 20); attempt 1 grew 5.2, the baseline's
   own mode switch 6.5. Not caused by this task; reported for the Lead.
5. **Two residual risks stay, both disclosed by the Developer, neither failed in any run** ("Residual risks"): a stop landing in a
   few-bytecode window of the start, and a Hailo element that itself fails a start (never reached on the device).

## What was relied on, what was run

Relied on from the earlier session (all under `docs/testing/reports/T-03a/evidence/`; only summary lines read):

| Log | What it holds | Summary line |
|---|---|---|
| `2026-10-17-tester2-red-before-green-baseline.log` | current `tests/test_camera_loss.py` vs baseline `src/` | `29 failed, 2 passed` |
| `2026-10-17-tester2-red-before-green-attempt1.log` | same vs attempt 1's `src/` | `7 failed, 24 passed` |
| `2026-10-17-tester2-gst-start-paths.log` | my four gst tests: worktree, baseline, attempt 1, "priming off" mutant | `4 passed` / `4 failed` / `3 failed, 1 passed` / `3 failed, 1 passed` |
| `2026-10-17-tester2-device-full-file.log` | the whole device file once, 13:57 | `37 passed in 1287.06s (0:21:27)` |
| `2026-10-17-tester2-device-bounce.log`, `-bounce-single.log` | `…camera_drops_again_while_restarting`, 8 consecutive runs + 1 | 8 x `1 passed`, `RUN 1..8 exit code: 0` |
| `2026-10-17-tester2-device-modeswitch.log` | `…unplug_while_mode_switch_pending`, all 9 gaps, 3 runs | 3 x `9 passed` |
| `2026-10-17-tester2-device-bootstart.log` | unplug in the program's first second, 10 runs x 2 offsets | 10 x `2 passed` |
| `2026-10-17-tester2-device-memory-{current,baseline}.log` | RSS and threads over 15 restarts | `2 passed`; `1 passed`. The `-attempt1` log is cut off (no result) and is redone below |
| `2026-10-17-tester2-probe-f1-*.log`, `-early-*.log`, `-node-*.log`, `-first-frames.log` | isolation probes, fresh process per run | checks 5 to 7 |
| `app-logs-attempt2/`, `-full/`, `-extra/` | the program's own log of every device run | grep list, check 8 |

Run by me this session (new logs, `2026-10-17-tester2b-*`, program logs in `app-logs-attempt2b/`):
- `tester2b-unit-poetry-2.log`, `tester2b-unit-gst-my_hailo_env-2.log`: the unit suites on the final state of every file (the first runs, `…unit-poetry.log` and `…unit-gst-my_hailo_env.log`, were before the last test was added to the uat file: `388 passed, 111 skipped`, `449 passed, 50 skipped`);
- `tester2b-device-random-flaps.log`: 3 seeds x 25 flaps in one process each (new test);
- `tester2b-device-long-absence.log`: a 135 s absence (new test);
- `tester2b-device-boot-pipeline-{current,baseline}.log`: Ctrl+C and a panel mode switch 0.05 to 0.4 s after the first pipeline's start,
  5 runs x 6 cases each, task code and baseline (new test, observation for T-11);
- `tester2b-device-stop-after-rebuild-{current,baseline}.log`: Ctrl+C 0.05 and 0.15 s after a mode-switch rebuild, 10 runs x 2 each, task
  code and baseline;
- `tester2b-device-memory-attempt1.log`: the attempt-1 memory comparison that was cut off.
Also: my own diff review, the outside-writes check, the `sudo`/`subprocess` search, the HailoRT source read, the restart-timing figure,
and the md5 check that the scratch copies used for red-before-green are the baseline (`f9be122ae72b`) and attempt 1 (`0e9317194952`, rebuilt by
me from `changes-1.patch`: identical).

## Diff review

```
for d in src tests scripts; do diff -ruN -x __pycache__ -x '*.pyc' .claude/baselines/T-03a/$d $d; done
```
(scratch copy: `/tmp/claude-1000/-home-sv-rpi5-Projects-Second-Vision-worktrees-kenth/f5d82ecb-06d5-47d1-b83e-43f222205897/scratchpad/task-diff.patch`, 3448 lines at 15:25; the uat file has grown since)

| File | Change | In the dev report? |
|---|---|---|
| `src/second_vision/pipeline/app.py` | +751 -3 | yes |
| `tests/test_camera_loss.py` | new (31 tests) | yes |
| `tests/uat/test_tc024_003_camera_removed.py` | new, the Tester's (51 device tests) | yes ("the Tester's, not edited") |
| `tests/uat/test_tc024_003_start_paths_gst.py` | new, the Tester's (4 gst tests) | no; it is the Tester's file, listed under "Test-only changes" |

- `scripts/`: no change. `core/`, `callbacks.py`, `workers/`, `firmware/`, `models/`: no change. The three deleted lines of `app.py` are
  comment lines of `shutdown()`'s docstring. No vision algorithm, `core/depth_utils.py` or `core/haptics.py` mapping is touched.
- `_release_old_pipeline()` has **no diff** (its order is unchanged), as the Developer says. `_drop_buffer` is now a `@staticmethod` of the
  class (the scope point of brief-2 is met). Outside the class only the constants, `import re` and `get_usb_video_devices` were added.
- Contracts (Rule 2): the only queue write is `_stop_motors()`, one all-zero dict with exactly the depth-dict keys on `serial_queue`
  (`put_nowait`, drop-oldest on `Full`). No new key, no `tts_queue` write.
- Threading (Rule 4): the watcher thread is a daemon, waits on `shutdown_event` with a 2 s timeout, never touches GStreamer; the
  hand-over is `GLib.idle_add`; state changes run on the main loop; no wait is unbounded.
- **Search of the diff for `sudo`, `subprocess`, `os.system`, `os.popen`, `os._exit`, `sys.exit`, `loop.quit`**: in `src/` the only hits are
  context lines that exist in the baseline (`import subprocess`; `os._exit` in `_force_exit_if_stuck`); the new comments say "never
  `loop.quit()`". None added. In the tests: `subprocess.Popen/run` (`ps`, `pgrep`, `fuser /dev/hailo0` without sudo, the program under
  test) and the word "sudo" in one docstring. Sudo runs only in the existing `camera_unplug` fixture (`tests/conftest.py`), which calls
  `.claude/tools/usb_sim.py` (`status`, `detach camera --for N`, `attach camera`).
- `python3 .claude/tools/check_outside_writes.py .claude/baselines/T-03a/.stamp` (run at the end): "Installed libraries and environments:
  nothing changed. Other checkouts and the workbench: nothing changed."

## Checks

| # | What (criterion / REQ / TC) | Method | Evidence level | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Scope, protected files, contracts, threading, no forbidden calls, changed-file list matches the report | my own diff review (above) | unit (static) | PASS | diff above; outside-writes check clean |
| 2 | Unit suites, both environments, final state of all files | `~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python scripts/uat_evidence.py --task T-03a --label tester2b-unit-poetry` and `my_hailo_env/bin/python scripts/uat_evidence.py … --label tester2b-unit-gst-my_hailo_env` (the same `pytest tests/ -q`) | unit | PASS | **`388 passed, 113 skipped in 37.14s`** (Poetry); **`449 passed, 52 skipped in 99.92s`** (my_hailo_env; 445 + my 4 gst tests); both run last, after the final edit of every file. The skips are the opt-in device tests, and in Poetry the gst tests (no `gi`) and harness tests that need pytest-timeout. The earlier 12:56 runs (`388 passed, 80 skipped`; `445 passed, 23 skipped`) agree. |
| 3 | The Developer's tests, red before green | current `tests/test_camera_loss.py` + `conftest.py` + `gst_helpers.py` against a scratch copy of baseline `src/` and of attempt 1's `src/` (never git) | mock-integration | PASS | baseline `29 failed, 2 passed` (the two passes are the guards `error_from_another_element_still_shuts_down` and `file_source_eos_keeps_library_looping`); attempt 1 `7 failed, 24 passed`; current `31 passed` (Developer). Matches dev-report-2 line for line; both scratch copies checked by md5 |
| 4 | Quality of the Developer's tests | read: `HailoStandIn` (an `identity` element named like a hailonet, fed through `Pad.chain`), the seven new tests | mock-integration | PASS, with a limit | They test behaviour the requirement names (stays up, waits, retries, mode recorded, a non-camera element still shuts down) as well as the mechanism (the stand-in is fed before it can be torn down). Each docstring says the plugin crash cannot show at gst level. Two tests monkeypatch private names; fine for guards. The real-plugin claim is checks 5 to 10 |
| 5 | 1b / F1: a camera is found but cannot be opened | probe, fresh process per run: the app's own `both` pipeline runs 3 s, torn down as `_release_old_pipeline()` does, 5 s pause, then the start on `/dev/video99`. `-probe-f1-*.log` | device-integration | PASS | attempt-1 shape (raw `set_state(PLAYING)`): **7 of 10 segfault** (exit 139), 2 of the survivors print the reference-count `CRITICAL`; same shape in a fresh process: **10 of 10 segfault**. `_launch_on_camera`: **20 of 20 clean**, 0 `CRITICAL`; fresh process **10 of 10 clean**; staged order with priming switched off **10 of 10 clean** (so the staged order alone fixes F1). Other nodes: `/dev/video1` (not a capture device) 5/5 clean, back to waiting; `/dev/video2` 3/3; camera held by another process 6/6 clean (one `CRITICAL` in run 5 is the probe releasing the library's never-watched pipeline, a probe artefact: no program log has it). Brief-2's bar was 10 fresh-process runs |
| 6 | F2 / the mechanism: a teardown soon after a start | probe, fresh process per run: first pipeline, torn down, 5 s, `_launch_on_camera('/dev/video0')`, then `_release_old_pipeline()` N ms after it returns. `-probe-early-*.log` | device-integration | PASS | with priming at 0, 100, 300, 600, 1000 ms: **8/8 clean each (40/40)**. Priming switched off, 0 ms: **0/6 clean**; 300 ms: 1/6. Attempt-1 shape (raw start) 0 ms: 1/6, 300 ms: 1/6. So the staged order alone does **not** fix F2; priming does. The segfault is at `app.py:909`, `element.set_state(Gst.State.NULL)` on a hailonet in `_release_old_pipeline`, where attempt 1's F2 was |
| 7 | Priming is safe for the protected callbacks | probe: pad probes on each hailonet's sink and src pads and extra handlers on `det_callback` / `depth_callback`, observation only; `_launch_on_camera` then 6 s, fresh process; modes both, depth, detection, none, plus "priming off" controls. `-probe-first-frames.log` | device-integration | PASS | each hailonet's **sink pad sees exactly one empty buffer** (size 0), then camera frames (both: 171 buffers on each sink pad); its **src pad sees camera frames only** (0 empty buffers); the **callbacks saw 0 empty or unreadable buffers** in all 7 primed runs (about 169 frames each); the first depth buffer carried its depth mask (`depth_masks=1`); the "priming off" controls give the same callback output. The start took 0.31 s in `both` |
| 8 | 1a: camera returns and drops again 0.0, 0.3, 1.0 s after "Camera found" (loose connector); a GStreamer `CRITICAL` counts as a finding | `…camera_drops_again_while_restarting`: 8 consecutive runs + 1 alone + 1 in the full file; `…bounce_sweep` (12 offsets, 0.00 to 1.50 s) once | device-integration | PASS | **10 of 10 passes** (`RUN 1..8 exit code: 0`, 81 to 86 s each). At 0.02 s: "start failed, back to waiting" (8/8); at 0.33 s: the pipeline had started and then a new loss, or the loss landed while the start ran (8/8); at 1.03 s: started, then lost (8/8). Sweep: 0.00 to 0.10 s "start failed, back to waiting"; 0.15 to 0.30 s "loss handled before the start finished"; 0.40 to 1.50 s "pipeline started, then lost". Over the **149 program logs that ran the task's code** (the 53 comparison logs of the baseline and attempt 1 excluded): `CRITICAL` 0, `Traceback` 0, `Fatal Python` 0, `Segmentation fault` 0, `MOCK` 0, `mock mode` 0, `HAILO_OUT_OF_PHYSICAL_DEVICES` 0, `not enough free devices` 0, `forcing exit` 0, `stopped acknowledging` 0, `Failed to start new pipeline` 0, `Exiting with error` 0, `could not be primed` 0, `Error tearing` 0, `did not reach NULL` 0, `ERROR |` 0; **149 of 149 end `# exit code: 0`**; 280 x "Camera found on", 208 x "Camera back", 27 x "did not start on … still looking". `CRITICAL` is in the test file's `NEVER` list, so every device test fails on one |
| 9 | 1c: camera goes during a panel mode-switch rebuild, every gap | `…unplug_while_mode_switch_pending` over `[at-once, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6 s]` (the file covers 1.0 to 1.6 s in 0.1 s steps), 3 runs, plus the full file | device-integration | PASS | `9 passed` x 3 (`RUN 1..3 exit code: 0`) = 27 of 27, plus 9 of 9 in the full file: **4 passes for each gap**. The switch stays announced and recorded, the program ends up running or waiting, no exit. 1.2, 1.3 and 1.4 s are the gaps that failed in attempt 1 |
| 10 | 1d + the rest of the file: the plain cases | the full device file once (`--run-device`), through `scripts/uat_evidence.py` | device-integration | PASS | `37 passed in 1287.06s`: all four modes, three unplugs in a row, another node, Ctrl+C and SIGTERM while waiting, during the teardown and during the restart (exit 0 in 0.5 to 1.0 s), SIGINT 0.0 to 0.8 s after "Camera found", camera held by another process, real glasses board and panel, mode switch during the loss, finer bounce sweep, mode switch while the camera restarts, unplug in the first second of the program's own pipeline |
| 11 | Unplug in the program's first second (the boot pipeline, started by the library, not primed) | `…unplug_during_the_programs_first_start[after-0.0s, after-0.2s]`, 10 runs of both, plus all five offsets (0.0 to 0.8 s) in the full file | device-integration | PASS | **20 of 20** (`RUN 1..10 exit code: 0`) **+ 5 of 5** in the full file; the detach command lands 0.02 s after "[MAIN] Starting pipeline" at the earliest, so the loss comes before the first frames. See "Residual risks" |
| 12 | 2: everything else keeps running; glasses heartbeat; panel; speech | the full file: fake board (ACKs, records), fake panel, real speech worker; `real_ports` with the real board and the real panel | device-integration | PASS | `real_ports`: board answered, panel port open, panel lines read during the loss, restarted in mode none; no "stopped acknowledging" in any log. Hearing the announcement is check 25 |
| 13 | 3: motors not left on a stale reading | asserted on the wire in the device tests (the last motor packet is zero after each loss); unit tests | unit + device-integration (fake board) | PASS | full file; `test_camera_loss.py::…keeps_program_up_stops_motors…`. Feeling the vibration stop is check 25 |
| 14 | 4: keeps checking; restarts on a camera; the restart is not slower | timing from the program's own log, the same method on both attempts: the `### TESTER: camera nodes back` mark to the first "Camera back" (script `…/scratchpad/restart_times.py`) | device-integration | PASS | **replug to "Camera back": attempt 1 (`app-logs/`) n=30, min 0.17, median 1.78, max 2.44 s** (the attempt-1 report says median 1.71: my method reproduces its n, min and max, the median is 0.07 s off); **attempt 2 full file n=42, median 1.57, min 0.20, max 2.52 s; extra logs n=35, median 1.61 s**. The restart itself, "Camera found on" to "Camera back", by mode, attempt 1 vs attempt 2: both 0.33 vs 0.34 s, depth 0.19 vs 0.19, detection 0.18 vs 0.18, none 0.03 vs 0.03. **No slowdown**: the 2 s look interval dominates the first figure and priming adds under 0.02 s to the second |
| 15 | 5: never mock mode | `grep` over the 149 program logs; case-insensitive `grep mock` over the added lines of `app.py` | unit + device log | PASS | 0 log hits; the only added line that says "mock" is the comment "Nothing here starts mock mode, speaks, or plays a motor pattern."; no `--mock`, no fallback |
| 16 | 6 / REQ-DTA-08: Ctrl+C and SIGTERM stop cleanly | the stop tests in check 10, 4 mid-transition stops, the boot-pipeline and post-rebuild stops of check 23 | device-integration | PASS | all exit 0 in 0.5 to 1.1 s, nothing left running (`app.leftovers() == []`), no `forcing exit` |
| 17 | Gst-level start paths the Developer read but did not run (exception while building, stop between PAUSED and priming, priming that raises, loss then stop while the restart is pending) | my `tests/uat/test_tc024_003_start_paths_gst.py`, four tests; run against the worktree, baseline, attempt 1 and a mutant with `_prime_hailonets` switched off | mock-integration | PASS | worktree `4 passed`; baseline `4 failed`; attempt 1 `3 failed, 1 passed`; mutant `3 failed, 1 passed` (red proves they test priming and the staged start). The mutant is a scratch copy |
| 18 | Developer's claim: the HailoRT source and the device measurements support the root cause | I fetched `gsthailonet.cpp` / `.hpp` v4.23.0 myself and read the lines in Read first 2; version and strings of the installed plugin | source + device | PASS (supported, not proven in the binary) | see Read first 2. No hailo-apps document says this (the Developer grepped the workbench; I did not repeat it) |
| 19 | Staged-order walk-through: stop during step 1/2/3, exception in `_on_pipeline_rebuilt`, `_failure_note`, START_* outcomes | read of `_launch_on_camera`, `_start_on_camera`, `_rebuild_on_camera`, the library's `shutdown()`; checks 10, 17 | mock-integration + device | PASS, one window open | A stop during steps 1 and 2 sees the parked empty pipeline; during step 3 the new pipeline only becomes `self.pipeline` after priming; an exception in `_on_pipeline_rebuilt` releases a primed pipeline and returns to waiting (gst test, check 17). **Open, as the Developer says (his residual (a)), and I agree it is not safe by construction:** the library's `shutdown()` sets the pipeline PAUSED, READY, **NULL** (`gstreamer_app.py:632-639`), so a stop landing in the few bytecodes between the `_shutting_down` check and `set_state(PLAYING)` takes the primed pipeline to NULL (`gst_hailonet_free` runs, the allocator is unreffed but the pointer is not cleared, `.cpp:164-165`); `_launch_on_camera` then sets it PLAYING again and the loop's exit cleanup takes it to NULL a second time, which would unref the same pointer twice. The window is microseconds wide; not reproduced in any run (SIGINT/SIGTERM 0.0 to 0.8 s after "Camera found", 0.05 to 0.4 s after the first start, 0.05 and 0.15 s after a rebuild: all exit 0). Not run on the device: the exception path, `START_FAILED` (needs a Hailo element to fail). Suggested follow-up, not a fail: re-check `_shutting_down` immediately before `set_state(PLAYING)` |
| 20 | Memory per restart (observation, not a pass/fail in the test) | `…memory_over_repeated_restarts`, 15 restarts each; the baseline's and attempt 1's code through scratch copies of their `run.sh` (`SV_UAT_RUN_SH`) | device-integration | PASS (recorded) | camera restart, RSS 194 to 276 MB: **5.8 MB per restart** (this code); attempt 1 197 to 307 MB: **5.2 MB per restart** (`tester2b-device-memory-attempt1.log`); mode switch: this code **5.0**, baseline **6.5** (196 to 242 MB). Threads flat (60 to 61). One run each, so a difference of 0.6 MB per restart between attempt 1 and this code is within noise. Growth predates this task; see "Findings" |
| 21 | 1a with a loose connector, many times in one process (new) | `…loose_connector_random_flaps[11, 12, 13]`: 25 flaps each, the camera away 1 to 3 s, back for 0 to 2.6 s or, after "Camera found on", 0 to 0.7 s; fixed seeds | device-integration | PASS | `3 passed in 339.37s`, `RUN 1 exit code: 0`. Seed 11: 25 flaps, 15 x "Camera found on", 2 x "did not start on", 13 x "Camera back", 13 x "Camera lost ("; seed 12: 17 / 3 / 14 / 14; seed 13: 15 / 3 / 12 / 12. So in one process each, 75 flaps made 47 starts, 8 of them refused by the camera (back to waiting), 39 reached running and were lost again. After every flap: same process, no shutdown, no forbidden line (incl. `CRITICAL`); at the end vision restarted, no dead node held, Ctrl+C exit 0. Logs `app-logs-attempt2b/*loose_connector*` |
| 22 | 4: keeps checking over a long absence; the "Still no USB camera" line (decision-1 Q5); idle cost (new) | `…long_absence_keeps_looking_and_says_so_once_a_minute`: camera away 135 s | device-integration | PASS | `1 passed in 142.45s`. The INFO line at **61.0 s and 122.0 s** after the wait began ("Still no USB camera after 61 s — still looking every 2 s"), none between; **0.4 % of one core** while waiting (20 s window); the camera then returned and vision restarted, Ctrl+C exit 0. Before this only a gst test (shortened period) covered the line |
| 23 | REQ-DTA-08 regression and the older O1 defect (T-11), observation (new) | `…boot_pipeline_stopped_or_switched_before_its_first_frame`: Ctrl+C or a panel mode switch 0.05, 0.15, 0.4 s after the first "[MAIN] Starting pipeline", 5 runs x 6 cases; and Ctrl+C 0.05 and 0.15 s after a mode-switch rebuild, 10 runs x 2 cases; each on this code and on a scratch copy of the baseline | device-integration | PASS (observation) | **First pipeline** (stop or switch before its first frame): this code **30 of 30** pass, baseline **30 of 30** pass: no crash in either (the first pipeline of a fresh process; see "Residual risks"). **After a rebuild** (Ctrl+C 0.05 / 0.15 s after "Pipeline rebuilt"): this code **0 of 20 crash** (all exit 0); baseline **16 of 20 exit -11** (7 of 10 at 0.05 s, 9 of 10 at 0.15 s; the Developer got 7 of 8). So O1 as brief-2 described it ("any pipeline start", baseline 3 of 5) is covered by priming for the rebuilt pipeline, with the baseline reproduced independently. Logs `…boot-pipeline-{current,baseline}.log`, `…stop-after-rebuild-{current,baseline}.log` |
| 24 | Real camera pulled by a person: no crash, vibration stops, restarts, another port | MANUAL-TESTS.md T-03a, steps 2, 3, 5 | user-observed | **BLOCKED** | needs a person (see "Manual steps") |
| 25 | The person hears the "<mode> mode" announcement during the loss and feels the motors stop and restart (criteria 2, 3, 4) | same session, steps 2 to 5 | user-observed | **BLOCKED** | same |

## Edge cases and failure conditions tried

Camera dropped 0.0 to 1.5 s after it returned (check 8); dropped 0.0 to 1.6 s after a panel mode switch (9); dropped before the first
frames of the program's own pipeline (11); 25 random flaps x 3 (21); 135 s absence (22); a node that does not exist, a non-capture node, a node
held by another process (5); a stop 0.0 to 0.8 s after "Camera found", during teardown, during restart (10), 0.05 to 0.4 s after the first
start, 0.05 and 0.15 s after a rebuild (23); a panel mode switch 0.05 to 0.4 s after the first start (23); repeated restarts, memory (20).
A Hailo element failing a start and priming failing on the real plugin could not be made to happen (see below).

## Residual risks

- **A stop in a microsecond window of the start** (check 19): by the source, a double unref is possible; no run hit it.
- **The program's first pipeline at boot is not primed** (the library's `run()` starts it). Stopping it, switching mode, or losing the
  camera in its first seconds passed on the device (25 of 25 unplugs, 30 of 30 stops and switches), and the baseline did the same, but the
  Developer's own reasoning is that the first hailonets a process allocates may sit in still-zeroed memory, which would hide the defect.
  Every real start is a fresh process, so the boot pipeline always is that case; I cannot exclude that a boot with more earlier allocations
  behaves differently.
- **A Hailo element that fails a start** (`START_FAILED`) was never reached on the device. The program then shuts down as today
  (decision-1 Q3), and the teardown of the unfed element may crash instead of exiting 1.
- **Priming failure on the real plugin** (no input format, an exception, a flow other than OK) was never made to happen; each branch logs a
  warning and the start goes on. Gst-level only (one branch by the Developer, one by me).
- The depth hailonet's stored sink-pad caps still show the priming's caps in `both` mode (the Developer's oddity). My probe saw identical
  callback output with and without priming; not chased.
- All unplugs are `usb_sim.py` (USB `authorized` = 0), not a cable pulled by a hand: checks 24 and 25 are the person's.

## Findings for the Lead (not failures of T-03a)

1. RSS grows about 5 to 6 MB per vision restart in attempt 1's, this task's and the baseline's code alike. A flaky connector could restart vision
   hundreds of times a day; where it stops, or whether the Pi's memory runs out, is unmeasured. A board item, not a reason to return T-03a.
2. T-11 (Ctrl+C within 0.15 s of a pipeline start): with this code the rebuilt-pipeline case is 0 of 20 against 16 of 20 on the baseline, and the
   first-pipeline case was 30 of 30 clean on both. T-11 could be re-scoped to "the boot pipeline, unprimed" or closed after the person runs the
   boot path a few times. Your call.
3. The Developer's disclosed residual (a) has a one-line shape of fix (re-check `_shutting_down` right before `set_state(PLAYING)`); it is not in
   the brief's scope, so I report it rather than ask for it.
4. Rule 8: the decision "every USB-camera pipeline starts camera-first with its hailonets primed, because HailoRT 4.23.0's hailonet unrefs an
   uninitialised allocator when taken down before its first buffer" belongs in `DECISIONS.md`, and the start order in `ARCHITECTURE.md`; the
   defect may be worth reporting to hailo-ai/hailort. Both are the user's to decide.

## Test-only changes

Permanent, under `tests/uat/` (for the Lead to review):
- `tests/uat/test_tc024_003_camera_removed.py`, 1383 lines, 51 device tests. Attempt 1's 22, then (earlier session) the gaps 1.0 and 1.6 s, `CRITICAL` in
  `NEVER`, `bounce_sweep`, `mode_switch_while_camera_restarting`, `stop_right_after_camera_found` x5, `camera_found_but_held_by_another_process`,
  `unplug_during_the_programs_first_start` x5, `memory_over_repeated_restarts` x2; and (this session) `loose_connector_random_flaps` x3,
  `long_absence_keeps_looking_and_says_so_once_a_minute`, `boot_pipeline_stopped_or_switched_before_its_first_frame` x8 (a stop or a panel switch at the
  first pipeline, and a stop after a rebuild). Names carry TC-024-003, each docstring quotes what it asserts. Run with `--run-device`. The
  boot-pipeline group is an observation for T-11, its docstring says so.
- `tests/uat/test_tc024_003_start_paths_gst.py`, 216 lines, 4 gst tests (mock-integration); the docstring quotes the workbook's expected result.

Temporary, outside the repository, **not removed** (under `/tmp/claude-1000/-home-sv-rpi5-Projects-Second-Vision-worktrees-kenth/`): the earlier
session's `56aa9637-501c-424e-8a8c-5732e695d84a/scratchpad/tester/` (`tprobe.py`, `tfirst.py`, `trun.sh`, batch scripts, scratch trees `red_base`,
`red_a1`, `red_mut`, `a1_run`, `base_run`) and mine, `f5d82ecb-06d5-47d1-b83e-43f222205897/scratchpad/` (`task-diff.patch`, `run_device.sh`,
`restart_times.py`, `found_back_by_mode.py`, `a1check/`, `hailort_src/` with the two HailoRT files, read as data only). None is imported by the repository.

## Deviations recorded

`docs/testing/DEVIATIONS.md`, the TC-024-003 entry: I added the groups of this session (random flaps and the 135 s absence; stop and mode switch at the
first pipeline and after a rebuild, with the baseline and attempt-1 comparison runs through `SV_UAT_RUN_SH`). The earlier session's additions were already
there.

## End state

`systemctl is-active second-vision.service`: inactive. No pipeline or `usb_sim` timer process running. `usb_sim.py status`: camera attached
(`authorized=1`, `/dev/video0` to `/dev/video3`). `sudo fuser -v /dev/hailo0`: no output (exit 1), so neither a stray process nor `hailort_service`
held the chip.

## Manual steps needed from a person

MANUAL-TESTS.md, T-03a (one real-camera confirmation; the Lead runs step 1 and checks the program is still running at step 3):
1. The Lead starts this branch the way the boot service would, with the boot service stopped.
2. Hold an object about 0.5 m in front of the camera. Do you feel vibration?
3. Unplug the camera, object still there. Does the vibration stop within a few seconds?
4. Wait about 30 s, flip a panel mode switch. Do you hear "<mode> mode"?
5. Plug the camera into a **different** USB port and show the object again. Does the vibration come back?
Before that session the real panel's speech and vibration switches must be on (in every real-port run they were off:
`tts_enabled = False`, `vibration_enabled = False`).
