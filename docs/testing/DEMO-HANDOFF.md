# Demo handoff: start here in a new session

For a fresh Claude Code session in `worktrees/kenth`, written 2026-10-18 18:25. Demo day is
**2026-10-19**. Read this file, then [DEMO-TRACK.md](DEMO-TRACK.md), then do the item the user names.
Nothing else needs reading up front: not HANDOFF.md, the task board or old transcripts.

## Where things stand

- **The plan:** [DEMO-TRACK.md](DEMO-TRACK.md) holds items F0 to F8. It gives the status table, the rules,
  the user's decisions, where the code is, and what each item must do. F0's code has been
  reviewed (below).
- **F1 is done in code** (2026-10-18 evening; [reports/DEMO/F1.md](reports/DEMO/F1.md)). The pattern player
  is `user_data.patterns.play(...)` in `core/haptic_patterns.py`; F2, F3 and F4 call it. Failsafe alerts
  are now spoken with the speech switch off. Its device check (feel each pattern; the bench snippet is in
  F1.md) waits for the batched device session.
  - The user chose plain multiply for the pattern strength. At the 30 % floor, strong is duty 76,
    under the unmeasured motor start threshold of 90: check that it is felt.
- **F2 is done in code** (2026-10-18 night; [reports/DEMO/F2.md](reports/DEMO/F2.md)). The glasses board
  lost gives "Vibration is not working", every 30 s while depth is on and once while it is off. Back gives
  the reconnect *beep beep* and the D-17 sentence. Shared wording and rules now live in
  `core/connections.py`; F3 extended it and F6 will. Its device check is in F2.md.
  - Found: the panel never sends `depth_enabled` or `detection_enabled`. Depth and detection on are read
    from `pipeline_mode`.
- **F3 is done in code** (2026-10-18 night; [reports/DEMO/F3.md](reports/DEMO/F3.md)). The earphones lost
  gives the *strong weak* ×3 pattern (every 30 s while detection is on, once while off, never spoken); back
  gives the reconnect *beep beep* and the D-17 sentence. They are found by the D-18 rule (a USB sound card
  that can play, read from `/proc/asound`), checked once a second by `workers/earphones_worker.py`. Their
  logic is in `core/connections.py`; `alerts.raise_alert(speak=False)` is the new silent alert that lets
  D-17 see them. Its device check is in F3.md.
  - DEMO-TRACK says to use `usb_sim.py`'s rule; D-18 decided another. The code follows D-18 (same answer on
    this Pi). F3.md says why.
- **F4 is done in code** (2026-10-19; [reports/DEMO/F4.md](reports/DEMO/F4.md)). Each time the camera alert is
  *spoken* ("Critical Error: Camera cannot be reached", every 10 s) the camera *beep beep* is played with it. It
  is done by a new hook on the alert scheduler, `raise_alert(..., on_speak=callable)`, called when the speech
  worker starts the utterance; `pipeline/app.py` `_raise_camera_missing` uses it. The return sentence has no
  vibration. Its device check is in F4.md.
- **F6 is done in code** (2026-10-19; [reports/DEMO/F6.md](reports/DEMO/F6.md)). Panel silent for 30 s, or its
  port cannot be opened: "Controller board cannot be reached. Using the current settings." at once and every
  5 minutes. Back: the D-17 sentence ("Controller board connected" while another part is out). The panel
  worker's loop (`workers/config_reader.py`) now reports lost and back (named change, AGENTS.md Rule 3); the
  wording and rules are in `core/connections.py`. Its device check is in F6.md.
- **F0 is reviewed** (2026-10-19; [reports/DEMO/F0.md](reports/DEMO/F0.md)): the Developer's camera alerts have no defect
  that I could find, and the tests catch 14 of 14 deliberate breakages. Two out-of-date comments in `pipeline/app.py`
  (about "open question D-15" and "no motor pattern") were fixed. The 14 device-level tests are opt-in and were not run.
- **F5 is done in code** (2026-10-19; [reports/DEMO/F5.md](reports/DEMO/F5.md)). Once a second a one-shot buffer probe on
  the source's last element (`source_fps_caps`, RGB) hands one frame to `core/camera_view.py`: blocked or dark (95th
  percentile under 40 or mean under 14), too bright (mean over 240), three checks in a row, spoken every 30 s, "Camera view
  is back." once. One check costs about 1 ms (0.1 % of one core); real GStreamer at 30 fps unharmed. The device
  performance run was done with the 20-minute NPU run (2026-10-19, [reports/T-15/measurement-2.md](reports/T-15/measurement-2.md):
  no frame-rate cost). **Not done:** the device check. Five choices to confirm are listed in F5.md.
- **The failsafe checks are on screen and in the terminal** (2026-10-19; [reports/DEMO/STATUS-DISPLAY.md](reports/DEMO/STATUS-DISPLAY.md)),
  at the user's request, for testing and for the panel watching the TV. `core/status_board.py` reads the five checks
  (camera, camera view, glasses board, earphones, control panel) and the last sentence said; `workers/status_worker.py`
  prints each change as a `[CHECK]` line and keeps it on `user_data.status`; the detection overlay draws it on the
  picture (`core/status_view.py`, through `depth_view.put_plated`); with no live picture the app shows a dark status
  screen. The depth window is not touched. Preview: `reports/DEMO/evidence/status-display-preview.png`.
- **F5 rules changed after real footage** (2026-10-19; end of [reports/DEMO/F5.md](reports/DEMO/F5.md)). On the device a finger
  and a phone flash were missed. Now: 16x16 block averages (noise cancels); dark = blocks' 95th percentile under 40 or
  mean under 14; too bright = 40 % of blocks at white (230+; this camera's white is about 241, so "mean over 240" could
  never fire). Both real frames are test fixtures in `tests/data/`. The screen shows the measured numbers live, top right;
  `SV_VIEW_LOG=1` prints every check. **The user still has to check a dim and a normal room stay clear.**
- **The NPU run is done** (2026-10-19, 06:46 to 07:06; [reports/T-15/measurement-2.md](reports/T-15/measurement-2.md)): NPU
  median 85 %, both models at 30 FPS, stability PASS, no throttling. The user started it from their terminal (auto
  mode blocks an agent's service restart). The NPU sampler's empty capture under a terminal is fixed in
  `docs/testing/tools/perf_npu.py`.
- **F8's script and written sequence are done** (2026-10-19; [reports/DEMO/F8.md](reports/DEMO/F8.md)):
  `docs/testing/tools/demo_run.py` starts the program, walks the presenter through 7 steps, ticks what the program said
  and sent, and keeps an evidence folder. **Next: the rehearsal with the user on the device**, which is also the
  device check of F0 to F6 (the checklist's parts cover what the rehearsal does not). It needs the boot service
  stopped: ask the user.
- **F7 is done** (2026-10-19; [reports/DEMO/F7.md](reports/DEMO/F7.md)): an alert being said is never cut by an object
  warning, and one that falls due while a warning is said goes next (D-16). The speech worker's preemption check
  (`tts_worker._cuts`) and the scheduler's offer rule changed (named, Rule 3). Under urgent warnings one per frame an alert
  was never heard; now within 1.3 s. DISAGREEMENTS.md D-16 still says "needs a small task": the Lead updates it after
  the demo.
- **The team docs have the demo track's contracts** (2026-10-19, the user said yes): `documentation/ARCHITECTURE.md`
  (the threads as started today, a "Failsafe alerts → speech, vibration and the screen" contract section, D-16 in
  "Preemption", the File Map's stale lines fixed, new rows in the shared-files map) and `documentation/DECISIONS.md`
  D42 (D-16, built; D32–D33 marked amended).
- **The measuring tools read the battery** (2026-10-19): `perf_sample.py` records the X1202 gauge's charge and voltage
  (I2C 0x36, read-only, no root), its summary gives the drain rate and flags a run whose charge rose (charger in), and
  `--stop-at-battery PCT` ends a rundown in time; `perf_run.py` passes it on and stops the app as soon as the sampler
  ends; `perf_plot.py` draws a battery panel. The gauge cannot say whether the charger is plugged in (no rate
  register on this model), and GPIO6 (the HAT's power-loss line) is unconfigured, so nothing reads it. Tests:
  `tests/test_perf_tools.py`.
- **The flaky timing test is fixed** (`test_camera_alerts.py::test_t03c1_rule4_one_hook_call_takes_well_under_50_ms`):
  up to three attempts. A hook that blocks 60 ms every 500th call still fails all three; one preemption does not.
- **The fix ranking** for after the demo is [FIX-RANKING.md](FIX-RANKING.md); **one check per task** (the Tester's) is
  the Lead's rule now (user, 2026-10-19; `.claude/agents/lead.md` step 8).
- **Three mock-mode tests no longer leave the program running when a check fails** (2026-10-19): three such copies had
  run for hours after mutation runs (the user stopped them). `test_status_display.py`, `test_earphones_alerts.py` and
  `test_alerts_speech.py` now stop their copy in a `finally`.
- **The agents are paused.** No Lead, Developer or Tester run is alive. **Do not start the Lead** or
  `lead_unattended.sh` until the user says so.
- **The device was free at 09:38 on 2026-10-19:** `second-vision.service` inactive, no other copy of the program. The
  service runs the **base checkout** (`second-vision-repo`), not this worktree, so this worktree's code is not what
  starts at boot. Do not stop the service or the app. Ask the user first, every time.
- **The hardware is normal:**
  - the camera (USB 1-1) and the earphones (USB 3-1) are attached and authorized;
  - `hailort.service` runs as usual (HAILO_MONITOR=0, no override in `/run`; restored and checked after the NPU run);
  - nothing that this workflow queued is still waiting.

## The working tree (uncommitted, keep it)

Everything that the agents built is uncommitted in this worktree, and the F items build on it as it is.
Do not revert, stash or reset anything.

- **Accepted:**
  - the test levels and harness: `tests/conftest.py`, `tests/gst_helpers.py`, `scripts/uat_evidence.py`;
  - the alert scheduler: `core/alerts.py`, `workers/alert_worker.py`.
- **Built, waiting for a person's check:**
  - the camera-loss survival work in `pipeline/app.py`, `main.py`, `scripts/sv-main.sh` and
    `scripts/second-vision.service`;
  - their tests.
- **Built and reviewed (F0, 2026-10-19; [reports/DEMO/F0.md](reports/DEMO/F0.md)):**
  - the spoken camera alerts in `pipeline/app.py` (and, since F1 to F4, `core/alerts.py`);
  - `tests/test_camera_alerts*.py`. No defect found; the device check is pending.
  - The tree as it was before this work is in `.claude/baselines/T-03c1/`; the brief is
    `reports/T-03c1/brief-1.md`.
- **Test baseline (last full run, 2026-10-19, after F7, the battery columns and the timing-test fix):** the Poetry
  suite gave 1056 passed, 217 skipped, 0 failed; `my_hailo_env` gave 1159 passed, 117 skipped, 0 failed
  (`evidence/DOCS-BATTERY-*-full.txt`; `tests/test_perf_tools.py` needs psutil, so Poetry skips it). With the long runs on
  (`T00A_LONG=1`), every speech and alert test: 505 passed, 0 failed. The four former strict `xfail` tests now pass
  and carry no marker.
  - **A flaky timing test:** in the run before (after F8's tool), `test_camera_alerts.py::test_t03c1_rule4_one_hook_call_takes_well_under_50_ms`
    failed once at 50.8 ms against its 50 ms limit (the slowest of about 3,000 calls, while the whole suite loaded the
    Pi); alone it passed 10 of 10 at 0.3 to 0.6 ms (`evidence/F8-*-full.txt`). One preemption is enough to fail it.
    Not changed: it is F0's test.

## How this user works

- **No git writes.** The user commits. Never add a Claude co-author line.
- **Ask before any device step,** after checking `systemctl is-active second-vision` and
  `pgrep -af "Parallel-Depth|pytest"`.
  - sudo is allowed only for `sudo -n .claude/tools/usb_sim.py` (the simulated camera or earphones
    unplug) and for read-only checks.
  - The exception: before a device step on this worktree's code, the service has to be stopped. The user
    does that or approves it.
- **"Pause" or "stop cleanly"** means let the running work finish and stop at a clean point. **"Right now"**
  means stop at once. Say up front how long a clean stop will take.
- **Explain IDs in plain words.** Never ask about a bare REQ-, TC-, D- or F-number.
- **Checks against the Hailo docs need no permission** (AGENTS.md Rule 6). Do them, and say where you
  checked.
- **Never** edit an installed library or the vision algorithms, never switch into mock mode by itself, and
  never flash ESP32 firmware.
- **Keep sessions small:** send long output to `docs/testing/reports/DEMO/evidence/` and read only its
  summary lines. Do not edit `TASK-BOARD.md`; record status in DEMO-TRACK's table and notes in
  `reports/DEMO/<item>.md`.

## Order, and what to type

Code-only items come first, while the device is busy. **F1 first,** because F2, F3 and F4 use its
pattern player. The order:
1. F1, F2, F3, F4, F6 (code only, while the device is busy): **done**;
2. F0 (review; its device check can wait for the batch): **review done**;
3. F5: **code done**;
4. one batched device session for F0 to F6;
5. F8 (the rehearsal with the user).

To start an item, the user opens a new session and types:

> Read `docs/testing/DEMO-HANDOFF.md` and do item F1.

Use Sonnet for most items and Opus for F1 and F5. When an item is done, update its row in DEMO-TRACK's
table, the "Where things stand" section here, **and its row and device steps in
[DEMO-CHECKLIST.md](DEMO-CHECKLIST.md)** (the file the user runs the device session from), so the next session
starts from the truth.

## Left for the user (not for the session)

- **Commit the work.** First run `git add -A docs/handoffs`: the index still holds the old dated
  evidence file names (shown as `AD` in `git status`).
- **The real camera pull and replug.** F8's rehearsal covers it.
- **The NPU measurement was done on 2026-10-19** (20 minutes; NPU median 85 %; F5's performance check
  with it): `reports/T-15/measurement-2.md`. The user starts such a run from their own terminal, since
  auto mode blocks an agent's service restart. The old note, kept for the record: `.claude/tools/queued_npu_run.sh`. It
  stops and restarts the Lead, so read it first.
- **The battery rundown** (REQ-PERF-01), when the device can be spared for hours: charge to 100 %, unplug the
  charger, stop the boot service, then
  `my_hailo_env/bin/python docs/testing/tools/perf_run.py --duration 36000 --stop-at-battery 5 --label rundown`.
- **After demo day:** the Lead reconciles the task board from DEMO-TRACK.md and resumes the workbook,
  in the order of [FIX-RANKING.md](FIX-RANKING.md), with one check per task (the Tester's; user,
  2026-10-19). The user starts the Lead; it stays paused until then.
