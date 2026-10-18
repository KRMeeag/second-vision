# T-03b · Verification report, attempt 1

- Date: 2026-10-17
- Overall: **BLOCKED** (no check failed). Every automated check passes at the evidence level the brief
  requires (`device-integration` for criteria 1 to 4, 6 and 8, `unit` for 5, both for 7). The one required
  check that is not done is the brief's `user-observed` row (a person, a real start with the camera unplugged,
  together with T-03a's hands-on test); it is check 9 and is listed under "Manual steps needed".
- Counts: 8 criteria PASS, 0 FAIL, 1 BLOCKED (the person's confirmation). Observations that are not criteria
  (O-1 to O-6) are listed below, one of them (O-1) names a small defect in `app.py` for the Lead to route.
- Inputs: brief-1.md (with Amendment A), dev-report-1.md, baseline `.claude/baselines/T-03b/`
- Commit under test: `cb0b20e` with uncommitted task changes. Service `second-vision.service` was `inactive` and no
  pipeline process existed before every device job (logged at the top of each evidence file).
- Tester's permanent test file: `tests/uat/test_tc024_002_camera_absent_at_start.py` (90 tests: 53 `unit`, 37
  `device`). Deviation entry: DV-002 in `docs/testing/DEVIATIONS.md`.

## Diff review

Command: `for d in src tests scripts; do diff -ruN -x __pycache__ -x '*.pyc' .claude/baselines/T-03b/$d $d; done`

- Changed files: `src/second_vision/main.py`, `src/second_vision/pipeline/app.py`, `scripts/sv-main.sh`,
  `scripts/second-vision.service`, new `tests/test_launcher.py`, `tests/test_main_startup.py`,
  `tests/test_startup_without_camera.py`. This is exactly the Developer's "Files changed" table: **no unreported
  file**, nothing under `tests/uat/` touched by the Developer, `firmware/` untouched.
- **Protected files**: `core/depth_utils.py`, `core/haptics.py`, `pipeline/callbacks.py`, `core/config.py`, `workers/`
  are byte-identical to the baseline (`diff -rq`). `check_outside_writes.py .claude/baselines/T-03b/.stamp`: "nothing
  changed" for installed libraries/environments and for other checkouts (re-run at the end of the work; evidence
  `2026-10-17-tester-end-state.log`).
- **T-03a code**: the `app.py` diff is additions plus a 3-line guard at the top of `_connect_callback` (state WAITING
  returns). `_release_old_pipeline`, `_launch_on_camera`, `_prime_hailonets`, `_start_on_camera`, `_camera_watch`,
  `MOTORS_OFF` and the "Still no USB camera" line are untouched.
- **Search of the added lines for `sudo|subprocess|os.system|os.popen|sys.exit|os._exit|loop.quit`**:
  - `main.py`: `sys.exit(1)` in the refusal path (hailo_apps unimportable, no `--mock`), the one exit the brief allows.
  - `app.py`: `GLib.idle_add(self.loop.quit)` in `run()`, taken only when `_shutting_down` is already true (a stop was
    already requested; the library's own `shutdown()` does the same); not a way of handling a missing camera, and it
    goes through `GLib.idle_add` (Rule 4).
  - nothing else; none of the words appears in the added lines of the three test files either.
- **Rules**: Rule 2 (queue keys) and Rule 3 (worker signatures) unchanged. Rule 4: the watcher thread is started from
  the loop (`_begin_waiting` via `GLib.idle_add`), not from the constructor; no new unbounded wait. Rule 1: no library
  edit.
- **`scripts/sv-main.sh` read line by line (63 lines)**: header comments; `REPO_ROOT`/`cd`; `RUN="${SV_RUN_SH:-$REPO_ROOT/scripts/run.sh}"`
  (the one test variable; unset it is the old line); `SERIAL_PORT`/`CONFIG_PORT`/`INPUT` defaults unchanged;
  the `--use-frame` rule unchanged; if `$INPUT` is missing, three `echo` lines and carry on; two `exec "$RUN" ...` lines
  (panel port missing / present), equal to the baseline's. There is no `sleep`, no `exit`, no other `exec`, and `--mock`
  appears only in comments (lines 8 and 16): **no executed path can pass `--mock`**. The removed `SV_SERVICE=1`
  hailo_apps import check is covered by `main()` refusing itself (check 5b).
- `scripts/second-vision.service`: comment lines only (verified in the diff; settings checked by my unit test).

## Checks

| # | What (criterion / REQ / TC) | Method | Evidence level | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Crit. 1: no crash at start-up without a camera, >= 120 s, same process, no traceback/CRITICAL/segfault, "Still no USB camera after N s" about once a minute | `scripts/sv-main.sh` (`SV_SERVICE=1`), camera detached by `usb_sim` before launch, 124 s of waiting, then the camera appears | device-integration | **PASS** | same PID, alive at 124 s, "Still no USB camera" at 60.9 s and 121.9 s, no forbidden line, CPU 0.5 % of one core. `evidence/2026-10-17-tester-crit1-2-no-camera-125s.log`, `...-applog-no-camera-125s.log` |
| 1b | Crit. 1, repeated: 25 fresh-process starts, no camera, left 8 s, Ctrl+C | fresh process per run | device-integration | **PASS** | 25 of 25 alive at 8 s, exit 0, no forbidden line (the baseline tree, same schedule: 24 of 25 clean, 1 GStreamer CRITICAL). `...-crit1-25-starts-no-camera-new.log`, `...-obs-25-starts-no-camera-baseline.log` |
| 1c | Crit. 1: the waiting program does not leak | threads/open files/RSS at 15 s vs 120 s (about 52 looks) | device-integration | **PASS** | threads 8 -> 8, open files 22 -> 22, RSS 104016 -> 104240 kB, no child left. `...-crit1-waiting-leaks-nothing.log` |
| 1d | Design claim: no Hailo pipeline exists while waiting | outside the process: open fds, pipeline-string log line, mapped libraries | device-integration | **PASS** | no `/dev/hailo*` and no `/dev/video*` open, no "Generated Pipeline string"; `libgsthailo.so` (the hailonet plugin), `libdepth_postprocess.so`, `libyolo_hailortpp_postprocess.so` are mapped only after vision starts. `...-crit1-no-hailo-while-waiting.log` |
| 2 | Crit. 2: glasses link, panel reader, speech run from the first second; a panel mode switch while waiting is recorded and announced; no vibration | fake glasses board (ACKs, records) and fake panel; 124 s | device-integration | **PASS** | 124 heartbeats in 124 s, 1 motor packet in 124 s and it is the zero command, 0 non-zero; `M:depth` -> "[CONFIG] Mode: depth", "depth mode" announced, "recorded while the camera is missing"; program alive. Same log as 1 |
| 2b | Crit. 2 on the real boards, once | `/dev/serial0` + `/dev/ttyAMA3` (the launcher's defaults), 25 s without camera, then camera | device-integration | **PASS** (motors' feel: see 9) | "UART Connection to ESP32 is WORKING", no "stopped acknowledging", panel link up; the real panel reported tts off, vibration off, mode `none` within 1.3 s and vision then started in `none`. `...-crit2-real-ports.log` |
| 3 | Crit. 3: camera appears at 1 s, 10 s, 90 s, in each of the four modes, vision starts in the mode current then | 12 runs (delay x mode), panel switch before the camera; "Camera found" once, "Camera back ... (mode=X)", one pipeline string, depth frames (both/depth), camera node and Hailo device open by the program (detection), clean stop | device-integration | **PASS** | 12 of 12 in 526 s. `...-crit3-camera-appears-matrix.log`, `...-applog-camera-at-90s-both.log` |
| 3b | Crit. 3 "three start-ups in a row" and the brief's count | 25 fresh starts, camera 0.5 to 5 s later (seeded random), modes cycled, SIGINT/SIGTERM alternately | device-integration | **PASS** | 25 of 25 (brief asks 20). `...-crit3-count25.log` |
| 3c | Crit. 3 "or another node" | old nodes held open while the camera is away; the camera returns as `/dev/video4..7`; vision starts there | device-integration | **PASS** | `...-crit3-another-node.log`, `...-applog-another-node.log` (the camera was renumbered back to `/dev/video0` afterwards) |
| 3d | Crit. 2/3: panel switch around the start | switch to `detection` 0 to 4 s after the attach (before, during, after the first pipeline start), then `depth` | device-integration | **PASS** | 7 of 7: ends in `detection`, then `depth` with frames. `...-crit3-mode-switch-around.log` |
| 3e | Crit. 3: ten switches in 2 s while waiting | last mode wins, one pipeline start | device-integration | **PASS** | `...-crit3-rapid-switches.log` |
| 4 | Crit. 4: a camera that appears but cannot be opened | the Tester's own process streams from `/dev/video0` right after the attach, 15 s, then released | device-integration | **PASS** | alive, 3 start attempts and 3 losses in 15 s, no forbidden line, vision (frames) after release. `...-crit4-held.log` |
| 4b | Crit. 4: drops again just after it appears | 8 runs, detach 0.6 to 1.0 s after "Camera found" | device-integration | **PASS** | 8 of 8 lost-then-running, exit 0. `...-crit4-drops-again.log` |
| 4c | Crit. 4, finer | 20 runs, second detach 0 to 2.5 s after the nodes return (before the program's look, during, after) | device-integration | **PASS** | 20 of 20 running again with frames, exit 0. `...-crit4-gone-again-sweep.log` |
| 5a | Crit. 5 launcher: never `--mock`, same command line | the real `sv-main.sh` against a recording stub `run.sh`: camera x panel port x {headless, DISPLAY, WAYLAND, both} x {no `SV_SERVICE`, 0, 1} = 48 cases; exit status passed through; source check (no executed line names mock, 2 `exec`s both `$RUN`, no `sleep`/`exit`, `bash -n`); unit file has no stale "wait/hard-fail" comment | unit | **PASS** | 53 passed (`...-uat-unit.log`); red on the baseline launcher, see "Red before green" |
| 5b | Crit. 5 `main.py`: hailo_apps unimportable and no `--mock` -> clear message, non-zero exit, no worker, no mock; with `--mock` mock mode as before | real `main.py` in a child with `hailo_apps` made un-importable; fake glasses board must receive no byte | unit | **PASS** | exit 1, stderr names hailo_apps, `--mock` and D-13, no glasses packet; with `--mock`: banner, link runs, SIGINT exit 0. Same log |
| 5c | Crit. 5 on the device | the NEVER list (includes "MOCK") on every device run | device-integration | **PASS** | no "MOCK" line in any run of this tree |
| 6 | Crit. 6 with Amendment A: Ctrl+C / SIGTERM while waiting and right after the camera appears exit 0 within about 1 s, nothing left | 30 fresh starts (SIGINT 15, SIGTERM 15): 8 stopped 0.7 to 3 s after launch (or after the first `[MAIN]` line if later) while waiting + 7 stopped 0 to 1.5 s after "Camera found", per signal; plus the 25 + 25 + 38 stops of other runs | device-integration | **PASS** | 30 of 30 exit 0 in 0.36 to 1.12 s, no leftover, no forbidden line, program not holding `/dev/hailo0`. `...-crit6-stops.log` |
| 7a | Crit. 7: camera present, same command line | the 24 camera-present launcher cases of check 5a (48 includes them) | unit | **PASS** | `...-uat-unit.log` |
| 7b | Crit. 7: plain start, vision as today | 3 fresh starts through the launcher with a wrapper that records the exact arguments and `exec`s the real `run.sh`; frames flow; no "No camera at start-up"/"Camera found"; one pipeline string; 2 panel switches each rebuild; exit 0 | device-integration | **PASS** | arguments equal `--input /dev/video0 --width 640 --height 480 --serial-port P --config-port Q`. `...-crit7-plain-start.log` |
| 8 | Crit. 8: T-03a still holds | unit: `tests/test_camera_loss.py` etc. in the full suites; device, a subset of T-03a's own file `tests/uat/test_tc024_003_camera_removed.py`: plain removal in each mode (4), `bounce_sweep` (1), mode-switch gaps `at-once` and `after-1.2s` (2), clean stop while the camera is missing SIGINT/SIGTERM (2), camera returns on another node (1) | unit + device-integration | **PASS** | 10 of 10 in 564.67 s. `...-crit8-t03a-subset.log`; suites below |
| 9 | The person's confirmation (brief, "Verification expectations", `user-observed`): start or boot with no camera plugged in, plug it in later; feel that the motors stay still; hear speech | a person | user-observed | **BLOCKED** | not possible for an agent. See "Manual steps needed" |

## Regression

- `~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -v -rA -p no:cacheprovider`
  -> `487 passed, 164 skipped in 45.39s` (the skips are the `gst` tests that need `gi` and the opt-in `device` tests).
  Evidence `...-unit-poetry-final.log` (and the run before my file was completed, `...-unit-poetry.log`: `434 passed, 129 skipped in 37.59s`, same as the Developer).
- `my_hailo_env/bin/python -m pytest tests/ -v -rA -p no:cacheprovider` (gst level included, device not requested)
  -> `564 passed, 87 skipped in 120.99s`. Evidence `...-unit-hailoenv.log`.

## Red before green (checked by me, scratch copies of the baseline, no git)

- `tests/test_main_startup.py` on baseline `src/`: 2 failed, 3 passed (the refusal and the import-error attribute fail;
  the 3 passing ones are behaviours that did not change). `...-red-unit.log`.
- `tests/test_launcher.py` on the baseline `sv-main.sh` + a recording `run.sh` stub (`SV_LAUNCHER_UNDER_TEST`): 21 failed,
  20 passed. `...-red-unit.log`.
- `tests/test_startup_without_camera.py` (gst) on the baseline tree: 15 failed, 1 passed, 7 errors in 423.70 s (the
  passing one is "present camera keeps the library path"). `...-red-gst.log`. All three match the Developer's counts.
- My own unit-level tests on baseline `src/` + baseline launcher (the slow `SV_SERVICE=1`/no-camera cases cut to one,
  the baseline waits 60 s in each): 19 failed (every camera-absent launcher case, the launcher source check, the `main()`
  refusal), 25 passed (24 camera-present launcher cases and the `--mock` run, as they should). `...-red-uat-unit.log`.
- **Quality of the Developer's tests**: they state the requirement (no pipeline built when the node is missing, WAITING,
  vision in the mode current at that time, stop while waiting, stop landing during construction, the launcher's command
  line per branch, `main()` refusal before any worker), not the implementation's own choices; the `gst` tests use
  stand-ins for the camera and the Hailo element and say so. I found no test that only mirrors the code.

## Independent check of the Developer's two design claims

- **Dropping a pre-built, never-started Hailo pipeline is safe.** Baseline `src/`, the app's real pipeline built (1 to 2
  `hailonet` elements, 30 to 53 elements, confirmed by printing the count), no state change, references dropped, `gc.collect()`,
  exit; fresh process per run: mode `both` 15/15, `depth` 15/15, `detection` 15/15, `both` with the pipeline set to NULL
  first 15/15: **60 of 60 survived, exit 0**. `...-r1-drop-unstarted-pipeline.log` (scripts in the session scratchpad).
  So the Developer's R1 answer holds (their count: 40 of 40).
- **A hailonet-free waiting start is what the code does**: see check 1d (no Hailo device or camera node open, no pipeline
  string, the hailonet plugin library not mapped until vision starts).
- **"What happens today" (baseline, no camera)**: not reproduced at the Developer's rate. 25 fresh starts through the new launcher
  with the baseline's `run.sh`/`src`: 0 SIGSEGV, 1 run with a GStreamer `CRITICAL gst_object_unref: assertion '((GObject *)
  object)->ref_count > 0' failed` (the same family as the unref the Developer describes), 24 clean; the Developer saw 4 SIGSEGV in
  25 with a different harness. The baseline defect is real but its rate is a few percent and not pinned by my sample;
  this tree: 25 of 25 clean and nothing Hailo is built. `...-obs-25-starts-no-camera-baseline.log`,
  `...-applog-baseline-no-camera-gst-critical.log`.

## Edge cases and failure conditions tried

- Camera appearing from 1.0 s before the launch to 1.5 s after it (race sweep, 19 offsets x 2 = **38 runs: 38 of 38 ended
  with vision and exit 0, no forbidden line**). Attach commands up to 0.35 s after launch found the node already present at
  the constructor's look (the library's boot path, 18 runs); from 0.4 s on the waiting path ran (20 runs). `...-obs-race-sweep.log`.
- Camera that appears but is held by another process; camera that vanishes again at 14 different phases; panel switches before,
  during and after the first pipeline start; ten rapid switches; real panel's own state respected while waiting.
- Long wait (125 s and 120 s): CPU, threads, files, memory flat.
- Stops from 0.2 s to 3 s after launch, and after "Camera found": see check 6 and O-1.
- Launcher: 48-case matrix, exit status pass-through, source scan; `main()` refusal and `--mock`.
- Not run: `DISPLAY` set (see O-5), a real boot, a real cable pull.

## Failures

None.

## Observations for the Lead (not criteria)

**O-1. Stops before the first `[MAIN]` line / 0.7 s (Amendment A): measured, same schedule on both trees, and one cause is in `app.py`.**
20 signals at 0.19 to 0.66 s after launch, SIGINT/SIGTERM alternately, no camera, through the new launcher
(`...-obs-early-stops-new.log`; baseline `...-obs-early-stops-baseline.log`):
this tree **9 of 20 exit 0**, 8 killed by the signal (exit -2 / -15), 3 exit 1, none lost; baseline **0 of 20** exit 0
(15 killed by a signal, one of them SIGSEGV (-11) for a SIGINT at 0.56 s; 3 ended by the 5 s "forcing exit" timer after
"Shutdown during pipeline rebuild - quitting main loop directly", the window the Developer fixed; 1 exit 1 with the
AttributeError below; 1 SIGINT at 0.39 s lost, the program went on). In this tree every stop from 0.39 s on exited 0 (9 of 9);
the failures are all at 0.19 to 0.35 s. No child was left in either series (a first, aborted series of mine saw a short-lived
`hailortcli fw-control identify` child, the library's architecture probe, survive an early kill; the final runs showed none 1.5 s
later). (My first baseline series was discarded: the scratch baseline tree lacked the `models/` link, so those baseline runs
failed on their own at 0.49 s; kept as `...-obs-early-stops-baseline-INVALID-no-models-link.log`, not used.)
**The Developer's analysis is incomplete for the exit-1 cases of this tree** (3 of 20, SIGINT at 0.33 to 0.34 s): the cause is
not only the missing handlers. `GStreamerApp.__init__` installs `self.shutdown` as the SIGINT handler early (while its
architecture probe runs), but `SecondVisionApp.__init__` sets `self._shutting_down = False` only after `super().__init__()`
returns (app.py line 363). A SIGINT in between runs `SecondVisionApp.shutdown()` -> `AttributeError: 'SecondVisionApp' object has
no attribute '_shutting_down'` -> "Error detecting Hailo architecture" -> exit 1 with a traceback (all three exit-1 logs of the
final series show it). Evidence: `...-applog-early-sigint-0p33s-AttributeError.log`. The baseline has the same defect (1 of its
20). It is outside the brief's bound, so it is not a failure; for T-11: class-level defaults for the attributes `shutdown()`
reads (as T-03a did for `_camera_state`), plus the early signal handlers in `main.py` the Developer proposed.
**O-2. The camera can race `create_pipeline()`.** The Developer wrote that the node reappears about 1.5 s after the attach
command, so the camera never raced the constructor. Measured here: the nodes are back about 0.2 s after the `attach` command
(app log: attach at 1.000 s, nodes seen at 1.203 s). With the attach at 0.35 s or earlier the node exists at the constructor's
look (about 0.5 s) and the library's boot path runs (the camera present at start; T-11's question, and not primed). 18 such runs,
all fine. The boundary between the two paths is crisp (0.35 s / 0.4 s).
**O-3. Baseline instability** is real but at a lower measured rate than the Developer's (above).
**O-4.** `hailortcli fw-control identify` is started by the library's architecture detection at every start (about 0.3 s after
launch); only relevant if a stop lands in that moment.
**O-5. `--use-frame` (a `DISPLAY`) with no camera was not run.** The agent session is headless (`/tmp/.X11-unix/X0` exists on
the Pi, the boot service sets no `DISPLAY`); the library starts its display process in `run()` as after a camera loss. The
launcher's `--use-frame` rule is covered by the unit matrix.
**O-6. The unit file under `/etc/systemd` still has the old comment sentences** until the user re-runs `install-service.sh`
(comments only; the launcher it runs is the repo's).

## Claims in the Developer's report that I could not confirm, or that differ

- "4 of 25 baseline starts killed by SIGSEGV": not reproduced (O-3, 0 of 25; one CRITICAL).
- "The camera never raced `create_pipeline()`": differs (O-2).
- "The 3 failing early stops were all before any signal handler was installed (imports)": partly; one of the causes is the
  `_shutting_down` ordering in `SecondVisionApp.__init__` (O-1).
- "Whether the motors vibrated correctly was not observed (needs a person)": agreed, check 9.
- Everything else I checked agrees: the file list, no protected file touched, the red/green counts (2 of 5, 21 of 41, 15 of 16),
  R1 (60 of 60 here), vision starting in the right mode in all four modes at 1, 10 and 90 s, the "Still no USB camera" lines
  at about 61 s and 122 s, the real boards, 14/14 + my 10/10 of T-03a's device cases.

## Blocked

- Check 9 (the person's confirmation, `user-observed`).

## Simulated only

- The unplug and replug are `usb_sim.py` (the kernel's USB `authorized` switch), not a pulled cable and not a power-on.
- The glasses ESP32 and the control panel are pseudo-terminals in every run but check 2b.
- Detection output in `detection` and `none` modes is judged by the process holding the camera node and the Hailo device and
  staying up (there is no per-frame line); depth frames were counted in `both` and `depth`.
- The Hailo element in the `gst` unit tests is a stand-in.

## Test-only changes

- **Permanent**: `tests/uat/test_tc024_002_camera_absent_at_start.py` (new, 90 tests). Unit level (53, no marker): the
  launcher against a recording stub (48-case matrix, exit-status pass-through, source scan, unit-file scan), `main()` refusal
  and `--mock` in a real child. Device level (37, opt-in `--run-device`): checks 1 to 8 above. It imports the T-03a harness
  (`AppRun`, `FakeGlasses`, `FakePanel`, `CameraHolder`, `wait_no_pending_timer`) from
  `tests/uat/test_tc024_003_camera_removed.py` and adds `LauncherRun` (starts `scripts/sv-main.sh`). Two tests are observations
  and assert nothing about the early stops; the `baseline` variants skip unless `SV_UAT_BASELINE_RUN_SH` names a scratch
  copy of the baseline's `run.sh`. Run: `my_hailo_env/bin/python -m pytest tests/uat/test_tc024_002_camera_absent_at_start.py -v -rA --run-device`.
  **Review points for the Lead**: the file starts the real program (through `sv-main.sh`) and uses sudo only through the
  conftest `camera_unplug` fixture (`usb_sim.py`); it contains no other `sudo`.
- **Temporary** (session scratchpad, not in the repo): the scratch baseline trees `redtree`, `redtree2`, `baselauncher`
  (**removed**), the driver/runner scripts, `r1_drop.py`, `r1_loop.sh`, the per-run app logs in `scratchpad/applogs/` (**kept**
  until the session ends; representative ones copied to `evidence/`).
- Nothing else in the repository was modified by me except this report, `docs/testing/DEVIATIONS.md` (DV-002) and the evidence logs.

## Deviations recorded

DV-002 in `docs/testing/DEVIATIONS.md` (start by hand through the launcher, not a power-on; software unplug; stand-in boards;
only the REQ-CON-08 half; additions beyond the workbook's single start).

## End state (hardware)

`systemctl is-active second-vision.service` = `inactive`; no program or `usb_sim` timer running; `usb_sim.py status`: camera
`0c45:0261` authorized, nodes `/dev/video0..3`; `sudo fuser -v /dev/hailo0` printed nothing, exit code 1 (no process holds the
chip; no `hailort_service` holder either at that moment). Evidence: `evidence/2026-10-17-tester-end-state.log`. The service was
never started or stopped by me.

## Manual steps needed from a person

1. Together with T-03a's hands-on test (MANUAL-TESTS.md): with the glasses and panel connected and earphones on, unplug the USB
   camera, power the Pi on (or start `scripts/sv-main.sh` by hand with the service stopped), and observe for about 2 minutes:
   the program stays up, no beeps/vibration from the motors, a panel mode switch is spoken ("depth mode"), the glasses link
   works. Then plug the camera in: detections/depth start within a few seconds in the panel's mode. Pull it again once: the
   program waits again.
2. Confirm by feel that the three motors do not vibrate while there is no camera (the Tester sees only the zero command on the
   wire and the real board's ACK).
3. (User's, not a test) re-run `scripts/install-service.sh` to refresh the installed unit's comments (O-6); the first real boot
   after this lands is the only test of the service itself.
4. (Optional) one start with `DISPLAY` set and no camera (O-5).
