# T-03a · Verification report, attempt 1

- Date: 2026-10-17. A resumed run: the first Tester run (Opus) was cut off by a usage limit at
  about 04:18. This report replaces its interim version.
- Overall: **FAIL**
- Inputs: brief-1.md, dev-report-1.md, baseline .claude/baselines/T-03a/
- Counts: 23 checks: **15 PASS, 3 FAIL, 5 BLOCKED** (the BLOCKED ones need a person).

## Read first

1. **The program can still crash, and once end, when the camera goes while a pipeline is
   starting or has only just started.** The plain case works on the device: the camera is
   unplugged while a pipeline runs, the program stays up, and vision restarts on replug. The
   following do not:
   - **F1.** The camera drops again within about 0.1 s of the program restarting vision on it
     (a loose connector). The program segfaults in 7 of 8 tries. The faulting call is
     `pipeline.set_state(PLAYING)` in `_start_on_camera` (app.py:1299). The native stack ends in
     `gst_object_unref`, called from the Hailo GStreamer plugin (`libgsthailo.so`).
     The same crash happens without the USB race. Set the app's own `both` pipeline to PLAYING
     on a `v4l2src` that cannot open, after an earlier pipeline ran in the same process: it
     crashes 5 times in 6. So the designed "camera found but cannot start → keep looking" path
     (brief design note 5, R4) usually crashes on the real pipeline. The Developer's gst-level
     test of that path uses a pipeline with no Hailo element, which never crashes.
   - **F2.** The camera goes 0.3 s after a mode-switch rebuild starts. The camera-loss teardown
     about 1.8 s later segfaults inside `_release_old_pipeline` (app.py:889, a hailonet's
     `set_state(NULL)`). In one bounce run a similar teardown printed a GStreamer reference-count
     CRITICAL but survived.
   - **F3.** This is the Developer's disclosed "R4 residual", reproduced. The camera goes
     1.2–1.4 s after a panel mode switch, between the node check and the library rebuild opening
     the camera:
     - at 1.2 s and 1.4 s, "Failed to start new pipeline", the library quits the loop, and the
       program ends (exit 0);
     - at 1.3 s, the same path segfaulted.
2. **There is also an older defect that this task did not cause (O1).** A Ctrl+C within about
   0.15 s of any pipeline start segfaults 3 times in 5. The baseline code does the same (3 of 5).
   It made one of the earlier device tests fail on its final stop; that test now waits 3 s
   before stopping.
3. **No checks are blocked on access.** The 5 BLOCKED checks are the person's confirmations
   (MANUAL-TESTS.md, T-03a). They are listed at the end. Before that session, the real panel's
   switches need speech and vibration turned on. In every real-port run they were off
   (`tts_enabled = False`, `vibration_enabled = False`, mode `none`).

## Interim claims re-checked, and what the earlier run left open

| Interim claim or open item | Checked against | Result |
|---|---|---|
| Only `app.py` and `tests/test_camera_loss.py` changed | my own diff (below) | Confirmed |
| Poetry `388 passed, 50 skipped`; my_hailo_env `437 passed, 1 skipped` | evidence/2026-10-17-tester-unit-poetry.log:976, …-unit-gst-my_hailo_env.log:1145 | Confirmed. Both ran at 03:50/03:51, after the last changes to `app.py` (03:34) and `test_camera_loss.py` (03:39). Re-run at the end anyway (see Regression), because `tests/uat/` did not exist then. |
| Red-before-green: `21 failed, 2 passed` on the baseline | evidence/2026-10-17-tester-red-before-green-baseline.log | Confirmed. `app_mod` was loaded from `/tmp/t03a_tester_red/src/...app.py` (the baseline copy), and each failure is behavioural ("the program never started looking for a camera"), apart from two `AttributeError`s for helpers the baseline lacks. The stray `ModuleNotFoundError: No module named 'second_vision'` line above pytest's output comes from a pre-check before pytest, not from the run. |
| First device run (`both`) PASS | evidence/2026-10-17-tester-device-explore-both.log | Confirmed (`1 passed`) |
| (not in the interim) 04:02 run: `real_ports` FAILED | evidence/2026-10-17-tester-device-run1.log; app-logs/…040640…real_ports.log | **A test-design error, not a product failure.** During the loss, the real panel sent its switch state (`[CONFIG] Mode: none`). The program logged "Mode none recorded while the camera is missing" and correctly restarted in `none`, which has no depth branch. The test still expected depth frames. The earlier run fixed the test at 04:17 (it now expects the panel's last mode). |
| (not in the interim) 04:17 rerun of `real_ports` | evidence/2026-10-17-tester-device-run2-partial.log (copied from `/tmp/t03a_device_run2.out`) | PASSED, against the 04:17:35 state of the file. Superseded by the final full run. |
| (not in the interim) 04:18 mode-switch-during-loss run | app-logs/…041804…mode_switch_during_loss….log | **No result.** The log stops 4.3 s in, at "Looking for a USB camera every 2 s", so the usage limit cut it off. Re-run below. |
| Which tests ran against the final state of the UAT file? | file mtimes; log start times | None of the earlier logs. The 04:02 run collected 12 items; the file then had 17. This run added 5 more (22) and re-ran the whole file once (full run, 04:45:58). Since then only `…mode_switch_during_loss…` changed (04:59:10, a 3 s settle before the stop), and it was re-run at 04:59:18. Every test therefore has a result against its final code. |

## Diff review

```
for d in src tests scripts; do diff -ruN -x __pycache__ .claude/baselines/T-03a/$d $d; done
```

- **Changed:** `src/second_vision/pipeline/app.py` (+383 lines, class `SecondVisionApp` and module
  constants only) and the new `tests/test_camera_loss.py`. `tests/uat/` is mine. That matches the
  Developer's report: no unreported file, nothing in `scripts/` or the workers.
- **Protected code:** untouched. `core/depth_utils.py`, `core/haptics.py`, `callbacks.py`,
  `firmware/` and the workers have no change.
  `python3 .claude/tools/check_outside_writes.py .claude/baselines/T-03a/.stamp` printed
  "Installed libraries and environments: nothing changed" and "Other checkouts and the
  workbench: nothing changed".
- **Contracts (Rule 2):** `MOTORS_OFF` is exactly `left, center, right, hazard, hazard_severity`,
  all 0/False, and goes through `serial_queue.put_nowait`. On the wire it is `AA 01 00 00 00 01`
  (the device tests assert it). `tts_queue` items and the worker signatures are unchanged.
- **Threading (Rule 4):**
  - `_camera_watch` is a daemon thread. It waits on `shutdown_event.wait(2.0)` and
    `handled.wait(0.5)`, and hands results over with `GLib.idle_add`. It never touches GStreamer.
  - `trigger_rebuild` stays schedule-only.
  - The teardown alarm (`threading.Timer`) only queues a zero dict.
  - Every new wait has a timeout. `set_state(NULL)` itself is unbounded; the Developer disclosed
    this as R1.
- **Not new, but noted:**
  - `_frames_at_camera_start` is not updated on a mode-switch rebuild, so frames from before a
    switch count as "this node gave frames". This affects only the node-order heuristic.
  - While the camera state is not OK, every ERROR/EOS from any element is dropped. Disclosed.

## Checks

Level: dev = `device-integration`, the real camera (simulated unplug) and the Hailo chip, glasses
and panel on pseudo-terminals unless "real ports" is said. App logs are under
`evidence/app-logs/`.

| # | What (criterion / REQ / TC) | Method | Evidence level | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Diff scope, protected files, contracts, threading | Own diff review; outside-writes check | review | PASS | See "Diff review" |
| 2 | Full unit suite (crit. 6, REQ-DTA-08 regression) | pytest in both environments | unit | PASS | Poetry `388 passed, 72 skipped`; my_hailo_env `437 passed, 23 skipped`. The extra 22 skips are the device tests, as expected without `--run-device`. evidence/2026-10-17-tester-unit-poetry-final.log, …-unit-gst-my_hailo_env-final.log |
| 3 | Developer's tests fail on the baseline | Their 23 tests against the baseline `src/` | unit / mock-integration | PASS | `21 failed, 2 passed` (the 2 passes are the guards). evidence/2026-10-17-tester-red-before-green-baseline.log |
| 4 | Adversarial gst-level checks (the earlier run's, not logged until now) | 6 tests in `/tmp/t03a_tester_checks`: mode switch already in flight at the loss; a thread hammering `trigger_rebuild` across loss, wait and restart; a non-camera error after recovery still shuts down; 50 camera errors + 5 EOS give one loss; the motor stop on a full queue never blocks; the watchdog during the loss neither builds nor quits | mock-integration | PASS | `6 passed in 9.56s`. evidence/2026-10-17-tester-adversarial-gst.log |
| 5 | Crit. 1: unplug while running, in `both`, `depth`, `detection`, `none` | `…unplug_replug_in_each_mode[*]` | dev | PASS | Same PID throughout; loss seen 0.06–0.08 s after the detach, torn down after 1.12–1.21 s; no shutdown, no traceback. evidence/2026-10-17-tester-device-full-final.log |
| 6 | Crit. 1: three unplugs in a row | `…three_unplugs_in_a_row` | dev | PASS | (torn down s, back s) `[(1.22, 7.9), (1.24, 8.4), (1.22, 8.6)]`; never a shutdown |
| 7 | Crit. 1 and 2: unplug while a panel mode switch is pending (brief, "Testing notes") | `…unplug_while_mode_switch_pending[gap]`, 7 gaps | dev | **FAIL** | at-once, 0.8 s, 1.1 s PASS (the "disappeared before the mode switch could rebuild — waiting" path). **1.2 s and 1.4 s:** "Failed to start new pipeline", the program ends (exit 0). **1.3 s:** segfault (exit -11) in the library rebuild's `set_state(PLAYING)`. **1.5 s:** segfault in the camera-loss teardown (exit -11). The 04:02 run had passed at-once, 0.8 s and 1.5 s. F2, F3 |
| 8 | Crit. 1, R2, R4: the camera drops again while vision restarts on it | `…camera_drops_again_while_restarting` (new): bounce at 1.0, 0.3 and 0.0 s after "Camera found on" | dev | **FAIL** | 1.02 s: recovered. 0.33 s: recovered, but the teardown printed `CRITICAL: gst_object_unref: assertion '((GObject *) object)->ref_count > 0' failed`. **0.03 s: segfault** at app.py:1299 `_start_on_camera`, **7 of 8 tries** (shakedown, 3 faulthandler runs, final run, 2 of 3 gdb runs). F1 |
| 9 | Design note 5 / R4: a camera that is found but cannot be started → back to waiting, not exit | Isolation probe on the real pipeline, no USB timing: the app's `both` string, `v4l2src device=/dev/video99`, set to PLAYING | dev (Hailo chip; camera only for the first pipeline) | **FAIL** | After a first pipeline ran 3 s on the camera and was torn down as `_release_old_pipeline()` does: **5 of 6 segfault**. Fresh process: 1 of 15. `none`-mode control (no Hailo element): 0 of 3. evidence/2026-10-17-tester-probe-failed-camera-start-after-a-run.log, …-probe-failed-camera-start.log, …-probe-failed-camera-start-2.log |
| 10 | Crit. 2: glasses link keeps its heartbeat; no "ESP32 stopped acknowledging" | Fake board records packets: ≥ 5 heartbeats after the teardown | dev (fake board) | PASS | In all four modes |
| 11 | Crit. 2: real glasses ESP32 and real panel during the loss | `…real_ports` (`/dev/serial0`, `/dev/ttyAMA3`) | dev (real ports) | PASS | The board answered ("UART Connection to ESP32 is WORKING"); no "stopped acknowledging" during 12 s+ of loss. The real panel's state burst during the loss gave "Mode none recorded while the camera is missing"; restart in `mode=none`; exit 0 |
| 12 | Crit. 2: a panel mode switch during the loss is announced, recorded, and builds nothing; the restart uses it | `…mode_switch_during_loss_is_announced_and_used` | dev (fake panel, real speech worker) | PASS | `[TTS] 'detection mode'` 0.22 s after the panel line; "Mode detection recorded while the camera is missing"; no "Generated Pipeline string", "Camera found on" or "Pipeline rebuilt" while missing; restart `(mode=detection)`; exit 0. evidence/2026-10-17-tester-device-mode-switch-during-loss-rerun.log. In the full run it failed only at the final stop, 0.16 s after the restart (O1, older defect) |
| 13 | Crit. 2: the announcement is heard | Person | user-observed | BLOCKED | Manual step 4. `[TTS] '…'` only shows that espeak was started |
| 14 | Crit. 3: one zero motor command after the loss, through `serial_queue`, then only heartbeats | Fake board: `AA 01 00 00 00 01` after the loss, no non-zero command after it | dev (fake board) + unit (Developer's tests) | PASS | Asserted in all four modes and in every bounce and stop test |
| 15 | Crit. 3: the wearer feels the vibration stop | Person | user-observed | BLOCKED | Manual step 3 |
| 16 | Crit. 4: keeps checking; restarts in the current mode, same process, Hailo re-acquired | Device tests above: `Camera back … (mode=…)`, depth frames again, no `HAILO_OUT_OF_PHYSICAL_DEVICES` / "not enough free devices" | dev | PASS | Replug to "Camera back": 0.17–2.44 s, median 1.71 s, 30 samples. No Hailo device error in any app log |
| 17 | Crit. 4: camera returns under another `/dev/video*` node | `…camera_returns_on_another_node` (old nodes held open) | dev | PASS | Old video0–3, new video4–7: "Camera back: vision pipeline running on /dev/video4 (mode=both)"; afterwards back on `/dev/video0` |
| 18 | Crit. 4: a real replug into another port; vibration responds again | Person | user-observed | BLOCKED | Manual step 5 |
| 19 | Crit. 1: a real unplug (not simulated) | Person | user-observed | BLOCKED | Manual step 3 |
| 20 | Crit. 5: never mock mode | No "MOCK" line in any app log (unit tests: no `mock*` thread) | unit + dev (log) | PASS | `grep -l MOCK evidence/app-logs/*.log` gives 0 files; no `--mock` anywhere in the new code |
| 21 | Crit. 6: Ctrl+C and SIGTERM while the camera is missing | `…clean_stop_while_camera_missing[sigint/sigterm]` | dev | PASS | Exit 0 in 0.56 s / 0.66 s; last motor command zero; nothing left in the session; "Exiting successfully"; `/dev/hailo0` not held; no "Error during cleanup" in any log (design note 7, R5) |
| 22 | Crit. 6: Ctrl+C / SIGTERM during the loss teardown, and during the restart | `…stop_mid_transition[*]` (new) | dev | PASS | Exit 0 in 0.51–1.01 s, 4 of 4 here and 4 of 4 in the shakedown; last motor command zero; no leftovers. A stop just after a restart can still hit O1 |
| 23 | Crit. 6: the unit's stop bound | Covered by 21 and 22: all stops ≤ 1.01 s, so `SHUTDOWN_FORCE_EXIT_SECONDS` and `TimeoutStopSec=10` are never reached | dev | BLOCKED | Measured outside systemd. A stop under the real unit (`systemctl stop`) is the person's session (service control is not mine) |

## Regression

```
~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -v -rA -p no:cacheprovider
  ======================= 388 passed, 72 skipped in 36.96s =======================
my_hailo_env/bin/python -m pytest tests/ -v -rA -p no:cacheprovider
  ================== 437 passed, 23 skipped in 88.45s (0:01:28) ==================
```

Full device run of the final UAT file:
`PYTHONFAULTHANDLER=1 SV_UAT_APP_LOG_DIR=docs/testing/reports/T-03a/evidence/app-logs my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a --device --tests tests/uat/test_tc024_003_camera_removed.py --label tester-device-full-final --max-minutes 45`
gave `TOTALS: 16 passed, 6 failed, 0 skipped (22 tests)`. The 6 failures: the four gaps 1.2–1.5 s,
the bounce test, and the mode-switch test's stop (O1, passed on re-run).

## Edge cases and failure conditions tried

- Unplug in every mode, including `none`; three in a row; a camera that returns on new nodes.
- Unplug at seven moments around a panel mode switch (at once, 0.8 s to 1.5 s after).
- A loose connector: the camera returns and drops again 0.0, 0.3 and 1.0 s into the restart.
- Ctrl+C and SIGTERM: while waiting, during the teardown, during the restart.
- Real glasses board and real panel during the loss; the panel's own state burst arrived mid-loss.
- Mid-transition threading at gst level: `trigger_rebuild` hammered from a thread, 50 errors in
  a burst, a full `serial_queue`, the library watchdog firing during the loss.
- A spontaneous camera dropout, which the brief asked me to flag: none. Every "Camera lost (" in
  all app logs follows one of my detaches within 2 s.
- The cost of looking for a camera: two "USB camera detected" lines about 10 ms apart, then
  "Camera found on" about 0.1 s later. That is consistent with the Developer's 96–97 ms per look.
  CPU while waiting was not measured.

## Failures

**F1. Segfault when vision is started on a camera that cannot be opened (Hailo pipeline)**
- Reproduce (automated):
  `my_hailo_env/bin/python -m pytest 'tests/uat/test_tc024_003_camera_removed.py::test_tc024_003_con08_device_camera_drops_again_while_restarting' --run-device -v`
  (boot service stopped). By hand:
  1. Run the program as the boot launcher does.
  2. Unplug the camera, wait for "Looking for a USB camera every 2 s", plug it back.
  3. Within about 0.1 s of "Camera found on /dev/video0 — starting the vision pipeline",
     unplug it again.
- Expected: "The vision pipeline did not start on /dev/video0 — still looking for a camera"; the
  program stays up (brief criterion 1; design note 5; R4).
- Actual: `Fatal Python error: Segmentation fault`; exit -11 (7 of 8 tries). Python stack: `app.py`
  line 1299 `_start_on_camera` ← 1224 `_on_camera_found` ← GLib main loop. Native stack (gdb):
  `#0 gst_object_unref` ← `#1 libgsthailo.so` ← `#2 gst_element_change_state` ← … ←
  `#9 gst_element_change_state` (the pipeline) ← … ← `g_main_loop_run`.
  evidence/2026-10-17-tester-bounce-segfault-gdb-backtrace-2.log, -3.log; app logs …043525,
  …043800, …043820, …043849, …045309.
- Isolation (check 9): no USB race is needed. Set the app's own pipeline to PLAYING with a
  `v4l2src` that cannot open, after an earlier pipeline ran in the process: 5 of 6 crash. Without
  Hailo elements it never crashes. In one gdb run (1 of 3) the same bounce took the intended
  "did not start … still looking" path, so it is a race inside the Hailo plugin, not a fixed
  outcome. The crash is in an installed library, so it has to be avoided from the app side.
  For example, the app could make sure the camera opens before any Hailo element changes
  state; that is the Developer's design choice.

**F2. Segfault while tearing down after a camera loss that came just after a pipeline start**
- Reproduce: `…unplug_while_mode_switch_pending[after-1.5s]` (app-logs/…045121…after-1.5s….log).
  Panel `M:depth`; 1.5 s later the camera is detached; the rebuilt pipeline had started 0.3 s
  earlier ("[SWAP] -> depth: first frame after 1.60s").
- Expected: "Camera lost: vision pipeline stopped and Hailo device released …"; the program stays up.
- Actual: "Camera lost (error from source …)", then about 1.8 s later `Fatal Python error:
  Segmentation fault` at `app.py` line 889, `element.set_state(Gst.State.NULL)` on a hailonet, in
  `_release_old_pipeline` ← `_release_old_pipeline_with_alarm` ← `_release_after_camera_loss`;
  exit -11. Related: in the bounce run at 0.33 s the loss teardown printed `(GStreamer): CRITICAL:
  gst_object_unref: assertion '((GObject *) object)->ref_count > 0' failed` and survived
  (app-logs/…045309…, 47.151 s).

**F3. Program ends when the camera goes during a mode-switch rebuild (R4 residual)**
- Reproduce: `…unplug_while_mode_switch_pending[after-1.2s]` and `[after-1.4s]`. Panel `M:depth`,
  then the camera is detached 1.2–1.4 s later. That lands after the rebuild's node check and
  before `v4l2src` opens (about 0.3 s: the library's 0.2 s sleep plus `parse_launch`).
- Expected: the program stays up and waits for a camera (criterion 1: "no exit"; criterion 2:
  the switch "neither ends the program …").
- Actual: `ERROR | gstreamer.gstreamer_app | Failed to start new pipeline`, then "Exiting
  successfully", `[MAIN] Done.`, exit 0. Speech, the glasses link and the panel stop with it. At
  1.3 s the same window segfaulted instead (library `gstreamer_app.py` line 599
  `_rebuild_pipeline` ← app.py 802), which is the F1 mechanism. The Developer disclosed this
  window (question 2). Under the boot service, `Restart=always` brings the program back after
  5 s, as today.

## Observations (not failures of this task)

- **O1. Pre-existing: Ctrl+C just after a pipeline starts can segfault.** Camera present, no
  loss: a panel switch to detection, then SIGINT 0.15 s after "Pipeline rebuilt". Current code:
  3 of 5 exit -11 (evidence/2026-10-17-tester-stop-right-after-mode-switch-no-loss.log). The
  baseline `src/`, run from `/tmp/t03a_tester_baseline_run`: also 3 of 5
  (…-no-loss-BASELINE.log). The crash is in the library's `shutdown()`, `set_state(NULL)`
  (gstreamer_app.py:638). T-03a did not cause it, but every camera return adds one more
  "just started" moment. Worth its own task. Rule 8: the Lead may want it in TASKS.md.
- **O2.** The real panel's switch state during these runs was speech off, vibration off, mode
  `none`. The hands-on session needs them on.
- **O3.** At info level the journal shows that the program is still looking only once, at the
  loss (the Developer's question 5). Not a requirement; noted for the Lead.

## Blocked

Only the person's confirmations, as the brief plans (checks 13, 15, 18, 19, 23). No access is
missing. They are worth running only after F1–F3 are fixed, because a real replug that bounces
can hit F1.

## Simulated only

- The unplug: `usb_sim.py` (USB `authorized` = 0). The real cable pull is manual step 3.
- The glasses board and the panel are pseudo-terminals in every test but `real_ports`. Motor
  commands and heartbeats are read from the fake board, not felt.
- Speech: `[TTS] '<text>'` shows that the worker started espeak; nobody listened.
- Developer's tests (gst level): `videotestsrc` stands in for the camera, with no Hailo element.

## Test-only changes

- **Permanent:** `tests/uat/test_tc024_003_camera_removed.py`, 22 device tests (marker `device`;
  they skip without `--run-device`). The earlier Tester run wrote most of it. This run:
  - checked it in full;
  - added `wait_for(interval=…)`, `last_of()`, `wait_no_pending_timer(interval=…)` and
    `SHORT_DETACH_SECONDS`;
  - added `test_tc024_003_con08_device_camera_drops_again_while_restarting` and
    `test_tc024_003_con08_device_stop_mid_transition[4]`;
  - added a 3 s settle before the stop in `…mode_switch_during_loss…` (see O1).

  It calls sudo only through the conftest `camera_unplug` fixture (`usb_sim.py`).
- **Temporary, kept** so the Lead can re-run:
  - `/tmp/t03a_tester_checks/` (the 6 adversarial gst tests; `src` is a symlink to the worktree);
  - `/tmp/t03a_tester_baseline_run/` (baseline `src/` + `run.sh`; env and models symlinked);
  - the scripts in `/tmp/claude-1000/-home-sv-rpi5-Projects-Second-Vision-worktrees-kenth/1ecbb3a0-be16-4c85-95ae-6bc8129b001c/scratchpad/`:
    `bounce_under_gdb.py`, `probe_failed_start.py`, `probe_failed_start_after_run.py`,
    `stop_after_mode_switch.py`.
- **Removed:** `/tmp/t03a_tester_red` (its log is saved). `/tmp/t03a_device_run1.out` and
  `run2.out` were also removed; their content is in evidence. The Developer's and the Lead's
  `/tmp/t03a_*` folders were left alone.
- **End state:** camera attached (`usb_sim.py status`: authorized=1, video0–3); no usb_sim timer
  pending; nothing of mine running; `sudo fuser -v /dev/hailo0` lists nothing (exit 1, so not
  even `hailort_service` held the device node then).

## Deviations recorded

DV-001 in DEVIATIONS.md, written by the earlier run and extended by this one. The extension
covers the bounce and mid-transition stop scenarios, the usb_sim timer used as the replug,
`PYTHONFAULTHANDLER=1`, and one diagnostic run under gdb.

## Manual steps needed from a person

One session, boot service stopped. It needs the camera, Hailo, glasses ESP32, panel and
earphones (MANUAL-TESTS.md, T-03a). Run it after F1–F3 are fixed. Before starting, set the panel's
speech and vibration switches **on**.

1. The Lead starts this branch the way the boot service would.
2. Hold an object about 0.5 m in front of the camera. *Do you feel vibration?*
3. Unplug the camera, object still there. *Does the vibration stop within a few seconds?* The
   Lead checks the program is still running. (Checks 15, 19.)
4. Wait about 30 s, then flip a panel mode switch. *Do you hear "<mode> mode"?* (Check 13.)
5. Plug the camera firmly into a **different** USB port and show the object again. *Does the
   vibration come back?* (Check 18.) Plug it in one clean movement: a bouncing connector can hit
   F1.
6. The Lead stops the service with `systemctl stop` while the camera is unplugged. *Does it stop
   within 10 s, motors off?* (Check 23.)

## Questions for the Lead

1. F3 is the window the Developer flagged as their question 2. Criterion 1 says "no exit", and
   the brief's testing notes require the "unplug while a mode switch is pending" case. I have
   marked it FAIL. Accepting it as a residual risk is your decision.
2. F1 and F2 crash inside the Hailo plugin, which is an installed library. A fix must keep the
   app out of that state rather than patch the library (Rule 1).
3. O1 predates this task. Does it get its own task?
