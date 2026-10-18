# T-03b · Start-up without a camera: stay up, keep looking, never mock · Brief, attempt 1

- Date: 2026-10-17
- Requirements: REQ-CON-08, **its "absent at start-up" half**, quoted from REQUIREMENTS.md:
  > when the device runs as deployed (started at boot) and the camera is absent at start-up or
  > removed while running:
  > - the program does not crash;
  > - every other part keeps running: the glasses and panel links, speech, and the alerts above;
  > - the device keeps checking the USB ports for a camera;
  > - when a camera appears, the vision pipeline starts on it.
  >
  > It must never fall back to the testing mock mode.

  Decision D-13 (team, 2026-10-12): mock mode is reachable **only** by `--mock`, never
  automatically. Regression: REQ-DTA-08 (Ctrl+C stops cleanly, no zombie processes), and everything
  T-03a made work (camera removed while running).
- UAT cases: TC-024-002, partly (`uat-snapshot.yaml`, `Test Cases!A262:G262`): step "1. Power on
  Raspberry Pi 5", input "USB Camera is not Connected". The REQ-CON-08 part is this task. Its
  REQ-CON-07 part (the *beep beep* and the spoken "Critical Error: Camera cannot be reached" every
  10 s) is T-03c and waits for T-00. The workbook's own wording "proceed in mock mode temporarily"
  is overruled by D-13 (DISAGREEMENTS), so it is **not** tested; its opposite is.
- Priority / depends on: P1 / T-03a (AWAITING-HANDS-ON: its code stays in the tree and this task
  builds on it; only the person's real-camera pull is missing).
- Developer: **developer-sonnet**. Opus bar checked: *very high risk from the start* needs the
  Hailo/GStreamer lifecycle change to be one that Sonnet cannot do step by step; here the hard part
  (a Hailo element must not be taken down before its first buffer) is already solved and written in
  `_launch_on_camera` by T-03a, and this task reuses that machinery. *Escalation*: no attempt yet.
- Tester model: **sonnet**.
- Baseline: `.claude/baselines/T-03b/` (taken 2026-10-17 16:09, before attempt 1, **with T-03a's
  changes in it**, so the diff shows only this task). Per-task diff:
  `for d in src tests scripts; do diff -ruN -x __pycache__ -x '*.pyc' .claude/baselines/T-03b/$d $d; done`.

## Goal

Start the program the way the boot service does (`scripts/sv-main.sh`) with **no camera attached**,
and it must come up and stay up: the glasses link, the panel reader and speech run from the first
second, the program keeps checking the USB ports, and the moment a camera appears the vision
pipeline starts on it, in the mode selected at that moment. No path anywhere starts mock mode
unless the person typed `--mock`.

## What happens today (Lead's reading of the code, 2026-10-17; **not verified: check each point first**)

1. **Launcher, `scripts/sv-main.sh`.** Under the boot service (`SV_SERVICE=1`) it waits up to 60 s
   for `/dev/video0` and then `exit 1`s (systemd restarts it after 5 s, `Restart=always`). So with no
   camera the program never runs: no speech, no link, no panel, which is the opposite of the
   requirement. Without the service variable, lines 69 to 74 run `--mock` when `$INPUT` does not
   exist ("Falling back to --mock"). That is the automatic mock fallback D-13 forbids. The board
   already records the decision (2026-10-15): remove it. The service unit's comment, "Wait for the
   camera and hard-fail instead of mock mode", goes stale with it.
2. **`src/second_vision/main.py:160`.** `if pre_args.mock or not HAILO_AVAILABLE:` runs mock mode
   when `hailo_apps` fails to import, with workers (speech, glasses link) already started. Same
   violation of D-13. (The launcher guards this only under `SV_SERVICE=1`, by importing the
   library first, `sv-main.sh:49-56`.)
3. **The first pipeline, `app.py`.** `GStreamerApp.__init__` builds the pipeline (`create_pipeline()`,
   `gst_app.py:444`, from `self.get_pipeline_string()`); the library's `run()` then does
   `set_state(PAUSED)` and `set_state(PLAYING)` on it (`gstreamer_app.py:724-726`) and ends in
   `sys.exit()`. With `--input /dev/video0` and no such node, the camera element fails there. T-03a
   showed that a Hailo pipeline whose start fails, or that is taken down before its first frame, can
   **segfault** (`gsthailonet.cpp` unrefs an allocator that is only set on the first buffer, see
   `reports/T-03a/dev-report-2.md` answer 1). T-03a's `_launch_on_camera` avoids that for every
   restart; the **first** pipeline at launch does not go through it. Nobody has run the program with
   the camera absent from the start: T-03a's tests unplug it *after* a start. Whether the program
   survives, exits or crashes today is **unknown**: measure it before designing.

## In scope

- `src/second_vision/main.py`: the automatic mock decision (point 2) and, if you need it, where the
  app is told the camera is missing.
- `src/second_vision/pipeline/app.py`: class `SecondVisionApp` and the module constants only. The
  start-up path when the USB camera node does not exist (point 3). T-03a's waiting machinery
  (`CAMERA_WAITING`, the watcher thread, `_start_on_camera`, `_launch_on_camera`,
  `_park_empty_pipeline`) is the thing to reuse; read `reports/T-03a/dev-report-2.md` and the
  docstrings of `_launch_on_camera` and `_prime_hailonets` first.
- `scripts/sv-main.sh`: remove the `--mock` fallback; with no camera, start the program instead of
  waiting 60 s and exiting. If you keep a wait for a camera that is still enumerating, say why and
  how long: it delays the speech and the link, and T-03c will need the alert to start early. The
  app already polls every 2 s (T-03a), so no wait should be needed. You may add one environment
  variable that lets a test replace `run.sh` with a stub (default unchanged), so the launcher can
  be tested without the Hailo chip.
- `scripts/second-vision.service`: **comments only** (the stale sentence). The installed copy under
  `/etc/systemd` is the user's to refresh; do not touch `install-service.sh`.
- `tests/`: new tests (see Verification). `tests/test_camera_loss.py` may be extended; do not weaken
  its tests. `tests/uat/` belongs to the Tester: do not edit it, say in your report if a test there
  needs to change.

## Out of scope: must not change

- Anything T-03a decided and tested: the camera-loss handling, `_release_old_pipeline()`'s order,
  `_launch_on_camera`'s staged start and priming, request coalescing, the `MOTORS_OFF` dict, the
  "Still no USB camera" line. If this task needs a change in one of them, **stop and report why**.
- The protected files: the vision algorithms in `core/depth_utils.py` and `core/haptics.py`, the
  detection processing in `pipeline/callbacks.py`, installed libraries and environments (Rule 1),
  `firmware/`.
- Interface contracts (Rule 2): queue dictionaries and keys; the worker entry signatures (Rule 3);
  `core/config.py` public API.
- A camera that is present at start-up keeps starting through the library's `run()`, with the same
  command line from the launcher as today. (That boot pipeline is not primed; it is T-11's
  question, not this task's. Do not widen the work.)
- No new user-facing speech or vibration (T-03c, T-00).
- `--mock` itself keeps working exactly as before when asked for.
- No `sys.exit`, `os._exit`, `loop.quit()` or process restart as a way of "handling" a missing
  camera, and no process-per-pipeline redesign. The requirement is that the program **stays up**.
  (Exiting non-zero is right only for a hailo_apps import that fails without `--mock`, below.)

## Acceptance criteria

Each restates the requirement. The device ones are what the Tester will run.

1. **No crash at start-up without a camera.** Started as the boot launcher starts it, with the
   camera not attached: the program starts and is still running, in the same process, after at least
   120 s: no exit, no segfault, no traceback, no GStreamer `CRITICAL` in its log. It writes the T-03a
   INFO line "Still no USB camera after N s" about once a minute.
2. **Every other part keeps running** from the first second, without waiting for a camera: the
   glasses link (heartbeat, ACK/link state), the panel reader, and the speech worker. A panel mode
   switch while no camera is attached is recorded and announced ("<mode> mode") and does not crash or
   end the program. The motors receive no vibration while there is no camera.
3. **The device keeps checking and starts vision when a camera appears.** With the camera plugged in
   at 1 s, 10 s and 90 s after start, on the same node or another one, in each of the four modes
   (`both`, `depth`, `detection`, `none`), vision starts on it, in the mode current at that moment.
   Three start-ups in a row, each with the camera appearing later.
4. **A camera that appears but cannot be opened, or drops again just after it appears**, ends in
   "running" or "waiting", never in a crash or an exit (T-03a's behaviour, now reached from a
   start-up that began without a camera).
5. **Never mock mode, anywhere.**
   - `scripts/sv-main.sh` never passes `--mock`, with or without `SV_SERVICE=1`, camera or not.
   - `main.py`, when `hailo_apps` cannot be imported and `--mock` was not given, does **not** enter
     mock mode and does not start any worker: it prints a clear message saying what is missing and
     how to ask for mock mode on purpose, and exits non-zero (the one exit this brief allows; systemd
     will retry and the cause is visible in the log).
   - With `--mock`, mock mode behaves as before.
6. **Clean stop.** Ctrl+C and SIGTERM while waiting for a camera, and right after the camera
   appears, exit 0 within about 1 s, nothing left running (REQ-DTA-08).
7. **With the camera present at start-up, nothing changes**: the launcher builds the same command
   line as before (including the `--use-frame` display rule and the missing-panel-port case) and
   vision starts as it does today.
8. **T-03a still holds.** Its unit suites and its device cases for the camera removed while running
   still pass (the Tester picks and cites the subset: at least one plain removal per mode, one
   bounce run, and the mode-switch gaps "at once" and 1.2 s).

## Verification expectations

| Criterion | Minimum evidence level | Hardware needed |
|---|---|---|
| 1, 2, 3, 4, 6 | `device-integration` (simulated unplug: `usb_sim.py detach camera --for N` before the launch, `attach camera` later); fake board and panel on pseudo-terminals as in T-03a, the real ones once | Camera, Hailo chip |
| 5 (launcher) | `unit`: the launcher run against a stub `run.sh` records its arguments; plus a source check that `--mock` appears nowhere on an `exec` path | none |
| 5 (`main.py`) | `unit`: `main()` with the import flag false and no `--mock` exits non-zero before any worker thread starts, and does not call the mock runner; with `--mock` it does | none |
| 7 | `unit` (launcher arguments, camera present) + one `device-integration` plain start | Camera |
| 8 | as T-03a | Camera, Hailo chip |
| the person's confirmation | `user-observed`, once, together with T-03a's (MANUAL-TESTS.md): boot or start with no camera plugged in, plug it in later | camera, boards, earphones |

## Fixed means repeated

The start-up path is a race with the camera (it can appear at any moment) and with the Hailo
element's first buffer. Before you hand over, show on the device, with the counts in the report:
- **at least 20 fresh-process starts with no camera**, the camera appearing 0.5 to 5 s later at
  random: every one ends with vision running, none crashes or exits;
- **at least 10 fresh-process starts with no camera, stopped 0.1 to 3 s after launch** by Ctrl+C or
  SIGTERM: every one exits 0, no segfault;
- the launcher and `main.py` unit tests, with each new test shown to **fail on the baseline** before
  it is trusted (the baseline copy is `.claude/baselines/T-03b/`; rebuild a scratch tree, never use
  git).
A rate that is not zero is reported as such, with the cause if known. The Tester repeats these
independently with larger counts.

## Design notes from the Lead (hints; you own the design)

1. **First find out what happens today** (device, brief "What the Developer may do"): start the
   program with the camera detached and read the log. Keep that log as evidence of the baseline.
2. **Do not start a Hailo pipeline on a node that is not there.** The easiest safe shape is: when
   `get_source_type()` says USB camera and the node does not exist at launch, the app never lets the
   library's `run()` set the pre-built pipeline to PAUSED/PLAYING. It goes straight to the waiting
   state T-03a built (parked empty pipeline, watcher thread), and the watcher starts vision through
   `_start_on_camera` when a camera appears, which is already the staged, primed start. Overriding
   `run()`, `create_pipeline()` or `__init__`-time handling in `SecondVisionApp` are the places to
   look; the library's `run()` ends in `sys.exit()` after the loop and expects `self.pipeline` to
   exist, and your parked empty pipeline satisfies both (T-03a's `_park_empty_pipeline`).
3. **The pre-built pipeline that is never started** holds `hailonet` elements that stay in NULL.
   Find out (HailoRT source, tag v4.23.0, `gsthailonet.cpp`, and measurement) whether simply dropping
   the references is safe, or whether it triggers the same unref. T-03a's reports hold the method:
   fresh process per run, many runs, exit code 139 means SIGSEGV. If it is not safe, do not build the
   Hailo pipeline at all when the node is missing.
4. **The mode at start-up** is the config default until the panel says otherwise (check
   `SystemConfig`); a mode switch while waiting is already guarded in `trigger_rebuild()` (T-03a).
   Confirm that, do not re-implement.
5. **Hailo docs first (Rule 6).** For anything that starts, stops or stages the pipeline, cite what
   you checked: the workbench `/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps`
   (`.hailo/memory/`, `.hailo/instructions/gstreamer-pipelines.md`, `.hailo/toolsets/`, `SV-Docu/`) or
   <https://github.com/hailo-ai/hailo-apps>, and the HailoRT source for `hailonet`. T-03a found no
   hailo-apps document on this; say so again if true.
6. **Do not "fix" it by exiting**, and do not hide a residual crash behind a longer wait. If staying
   up cannot be reached from the app side, **stop and report** with measurements.

## What the Developer may do on the device (this attempt)

Same rules as T-03a attempt 2, restated:
- **Before every device run:** `systemctl is-active second-vision.service` must say `inactive` and
  `pgrep -af "main.py|second_vision|Parallel-Depth"` must find nothing but your own shell; if either
  fails, stop and report, and stop nothing yourself. `sudo .claude/tools/usb_sim.py status` must show
  the camera attached.
- **One device job at a time.** Never two processes on the Hailo chip or the camera.
- **You may run:** your own scratch scripts under the scratchpad that use the camera and the Hailo
  chip; `sudo .claude/tools/usb_sim.py status|detach|attach camera` as a **single command, no pipes,
  no redirects, no here-documents next to it** (the hook refuses those); `sudo fuser -v /dev/hailo0`
  and, only with the service stopped, `sudo fuser -k /dev/hailo0` for a stale process of yours (put
  both outputs in the report); the Tester's tests one at a time with `--run-device`
  (`SV_UAT_APP_LOG_DIR` set to scratch, not `reports/T-03b/evidence/`, which is the Tester's); `gdb`
  on a process you started.
- **usb_sim defect:** an older `--for N` timer can re-attach a newer detach early. Use `--for 20` or
  less, call `attach` when done, wait the previous `--for` before the next detach, and check the
  camera is still absent before relying on it.
- **At the end:** camera attached, nothing of yours running, `sudo fuser -v /dev/hailo0` output in the
  report. Never leave the camera detached.
- Your device runs are your investigation and smoke test; they are not the evidence for the decision.

## References

- AGENTS.md rules 1 (no installed library edits), 2 (contracts), 3 (worker signatures), 4
  (threading, state changes via `GLib.idle_add`), 6 (Hailo docs first), 7 (verify before you trust).
- `reports/T-03a/`: `brief-1.md` (design notes, testing notes), `brief-2.md`, `dev-report-2.md`,
  `test-report-2.md`, `decision-2.md`. T-03a's Tester file `tests/uat/test_tc024_003_camera_removed.py`
  (`AppRun`, the fake board and panel, `SV_UAT_RUN_SH`) shows how the program is started and observed
  under test.
- Library, read only: `hailo_apps/python/core/gstreamer/gstreamer_app.py` (`__init__`,
  `create_pipeline`, `bus_call`, `shutdown`, `run`).
- DISAGREEMENTS (D-13), REQUIREMENTS (REQ-CON-08), `scripts/second-vision.service`.

## Open risks / questions: stop and report instead of guessing

- **R1. The first pipeline's Hailo elements.** See design note 3. A rate that is not zero is a
  finding, not something to hide.
- **R2. The launcher is the boot path.** A mistake in `sv-main.sh` stops the device from starting at
  all, with the wearer not knowing. Test every branch (camera present or not, panel port present or
  not, display or not, `SV_SERVICE=1` or not) against the stub, and read the final script line by
  line. Never start or stop the service (the user's).
- **R3. A camera that enumerates late at boot** (node present but not openable for a moment): this is
  T-03a's "found but cannot be opened, back to waiting"; do not add a launcher wait for it.
- **R4. Anything in this brief that the code contradicts.** If the code shows today's behaviour is
  different from "What happens today", report the difference first.
- **R5. Do not weaken a test to make it pass** (R11 of T-03a): if a test of the Tester's expects the
  wrong thing, say so with evidence; the Tester fixes its own tests.

## Amendment A (Lead, after dev-report-1; applies to the Tester and to any attempt 2)

The Developer found, and measured on the baseline and with a camera attached, that a Ctrl+C or
SIGTERM in the first ~0.5 s after launch (before Python has installed any signal handler: the
imports, then `GStreamerApp.__init__` at about 0.33 s and `main()`'s SIGTERM handler at about
0.57 s) does not exit 0, with or without a camera. That window is the same in the baseline, does not
depend on the camera, and is not part of REQ-CON-08 or REQ-DTA-08 as written ("Ctrl+C while detecting
and speaking stops both threads"). **Decision (Lead, 2026-10-17): it is not part of T-03b.** Criterion
6 and the "stopped 0.1 to 3 s after launch" count mean: **stops from the moment the program has printed
its first `[MAIN]` line, or from 0.7 s after launch if that is later**, all exit 0. The Tester measures
stops before that point as an **observation** (rate, same on the baseline), not a pass/fail, and
reports them for T-11's scope (early signal handlers in `main.py`, about 15 lines, proposed to the
user). Nothing else in the brief changes.
