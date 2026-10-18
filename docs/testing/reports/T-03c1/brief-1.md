# T-03c1 · Camera status in speech · Brief, attempt 1

- Date: 2026-10-18
- Requirements (quoted from REQUIREMENTS.md):
  - REQ-CON-07, its **spoken half**: "when the camera can't be reached (absent at start-up or
    disconnected while running), all three motors give a *beep beep* pattern and the device says
    'Critical Error: Camera cannot be reached'. It repeats every 10 seconds." The *beep beep* is
    **T-03c2** (blocked on the user's answer D-15) and is not part of this task.
  - REQ-CON-09: "when the camera comes back, the device says the camera has been reconnected and
    returns to normal operation."
- UAT cases (verbatim in `uat-snapshot.yaml`): TC-024-002 (power on, "USB Camera is not Connected"),
  TC-024-003 (camera connected at power-on, then disconnected), TC-024-004 (power on, disconnect,
  reconnect: 'There should be a quick "camera has been reconnected" audio output and the device
  should return to its normal state'). The workbook's "proceed in mock mode temporarily" is
  overruled by D-13 and is not tested. The REQ-CON-08 halves (no crash, vision restarts) were built
  and accepted/awaiting-hands-on in T-03a and T-03b and must keep working.
- Priority / depends on: P1 / T-00a (ACCEPTED: the alert scheduler `user_data.alerts`), T-03a, T-03b
  (their code is in the tree).
- Developer: **developer-sonnet**. Opus bar checked: the change is a few non-blocking calls to the
  alert scheduler from places in `pipeline/app.py` that already run on the main loop; no GStreamer or
  Hailo state change is added, so the lifecycle bar is not met.
- Tester model: **sonnet**.
- Baseline: `.claude/baselines/T-03c1/` (taken 2026-10-18 08:50, before attempt 1, with T-00a, T-03a
  and T-03b in it). Per-task diff:
  `for d in src tests scripts; do diff -ruN -x __pycache__ -x '*.pyc' .claude/baselines/T-03c1/$d $d; done`.

## Goal

While a camera is missing, the wearer hears "Critical Error: Camera cannot be reached" at once and
again every 10 seconds, whether the camera was absent when the program started or was lost while it
ran. The moment the vision pipeline is running on a camera again the repeating alert stops and the
device says "Camera has been reconnected" once. Every loss and every return does this, not only the
first. A device whose camera was never lost says nothing new.

## What exists today (Lead's reading of the code, 2026-10-18; **not verified: check each point first**)

1. **The alert scheduler** (T-00a, accepted): `user_data.alerts` (`core/alerts.py`).
   `raise_alert(name, text, interval=None, repeat=True)`, `clear(name)`, `is_raised(name)`, `raised()`.
   Read its module docstring first. Raising the same name with the same text and interval changes
   nothing (so it can be called again safely); a once-only alert (`interval=None`) **stays raised
   after it was spoken** until cleared, and raising it again unchanged speaks nothing. It is built in
   `SecondVisionUserData.__init__` (`main.py`) and the alert worker runs it. The queue item is exactly
   `{"announce": text}` (Rule 2: do not change).
2. **The camera state machine** in `pipeline/app.py` (T-03a/T-03b): `CAMERA_OK`, `CAMERA_LOST`,
   `CAMERA_WAITING`, `CAMERA_STARTING` (lines 179-182). The places that matter (line numbers as read
   today; confirm):
   - `_on_camera_lost` (about 1109): runs on the main loop (bus watch), sets `CAMERA_LOST`, schedules
     the teardown. A camera loss is known here, before the teardown.
   - `_start_without_camera` (about 1680): constructor time, no camera at start-up, state `CAMERA_WAITING`.
   - `_wait_for_camera` (about 1185): the end of every teardown, state `CAMERA_WAITING`; also reached
     from `_back_to_waiting` after a start the camera could not make.
   - `_start_on_camera` (about 1326): on `START_RUNNING` the state becomes `CAMERA_OK` and
     `_camera_missing_since` is cleared. This is the moment vision is running again.
   - `START_FAILED` goes to `_shut_down_after_failed_start`; `START_STOPPED` is a shutdown in progress.
3. **Nobody raises an alert yet.** `grep -rn "raise_alert" src/` should find only the scheduler itself.
4. **Speech switch.** When the wearer has speech switched off (`config.tts_enabled`), the worker drops
   the alert and the scheduler counts it as delivered (T-00a). Whether failsafe alerts should override
   the switch is the user's open question D-15: **do not change it here.**
5. **Mock mode** (`--mock`) has no `SecondVisionApp`, so it has no camera and no camera alert. Nothing
   changes there.

## In scope

- `src/second_vision/pipeline/app.py`: the hooks. Suggested shape (you own the design):
  - one small helper that raises the missing-camera alert (`name` for example `"camera-missing"`,
    text `"Critical Error: Camera cannot be reached"`, `interval=10.0`) **and clears the reconnect
    alert** (see criterion 5), called when the loss is known (`_on_camera_lost`) and in
    `_start_without_camera`; harmless if called again (`raise_alert` is idempotent);
  - on `START_RUNNING`, in `_start_on_camera`: if the missing-camera alert is raised, clear it and
    raise the reconnect alert once (`"camera-reconnected"`, text `"Camera has been reconnected"`,
    `interval=None`). `alerts.is_raised("camera-missing")` is a cheap way to know the camera was
    missing, without a new flag.
  - take the scheduler from `getattr(self.user_data, "alerts", None)` and do nothing when it is absent
    (the standalone user data in `app.py` has none). Never let an exception here break the camera
    handling: catch, log, continue.
  - The wording constants may live at the top of `app.py` next to the other tunables.
- `src/second_vision/core/alerts.py`: **one docstring line only** (T-00a finding F-3): a `repeat`
  callable runs on the scheduler's thread and must return at once; one that blocks delays every alert
  and the worker's stop. No behaviour change. (The Tester's docstring test extracts the usage block from
  `__doc__`: keep that block as it is.)
- `tests/`: new tests (see Verification). `tests/uat/` belongs to the Tester: do not edit it.

## Out of scope: must not change

- The vibration pattern of the camera alert (T-03c2) and every vibration/motor path.
- The pipeline lifecycle itself: staged start, priming, teardown, watcher, rebuild, shutdown. Add no
  GStreamer state call; the new calls only talk to the scheduler. Camera state names and their
  transitions stay exactly as they are.
- `core/alerts.py` behaviour, `tts_worker.py`, `core/priority.py`, `serial_worker.py`, `config_reader.py`,
  `core/config.py`, `callbacks.py`, `core/depth_utils.py`, `core/haptics.py`, `firmware/`, installed libraries.
- The queue dictionary keys (Rule 2). The speech switch behaviour (D-15).
- No camera-blocked, dark or blurry detection (T-13, T-14).

## Acceptance criteria

1. **Absent at start-up (REQ-CON-07, TC-024-002).** Started with no camera, the program offers exactly
   `{"announce": "Critical Error: Camera cannot be reached"}` within about 2 s of the main loop starting
   and again every 10 s (start to start, tolerance of 1 s plus whatever speech is under way), for as long
   as no camera is present. Checked over at least 60 s (six utterances).
2. **Lost while running (REQ-CON-07, TC-024-003).** With a camera running in each mode (detection,
   depth, both), when the camera is lost the same alert begins within about 2 s of the loss being
   noticed (not after the teardown, which can be held back for seconds) and repeats every 10 s.
3. **Returns (REQ-CON-09, TC-024-004).** When the vision pipeline is running on a camera again, nothing
   more is spoken for the missing-camera alert, and `{"announce": "Camera has been reconnected"}` is
   spoken exactly once, within about 3 s of the pipeline running (more if another utterance is under
   way). It applies after a start-up without a camera as well as after a loss while running.
4. **A camera that appears but cannot start** (the node is there but cannot be opened, or vanishes
   again; T-03b's cases) keeps the missing-camera alert going and says nothing about reconnecting.
5. **Every cycle.** Three loss-and-return cycles in a row (and a start-up without a camera followed by
   two cycles) each give the missing-camera alert and then exactly one "Camera has been reconnected".
   (The scheduler keeps a once-only alert raised after it was spoken; the second reconnect would be
   silent if it were not cleared. This is the mistake to avoid.)
6. **Nothing new when nothing is wrong.** A program that starts with a camera and never loses it, in
   any mode, speaks neither sentence. Mode switches do not either.
7. **Stopping and the other parts.** Ctrl+C while the missing-camera alert is raised: exit 0 in the
   time T-03b measured (the stop must not wait for the alert); the glasses link, the panel reader and
   the other workers keep running exactly as in T-03a/T-03b.
8. **Rule 4.** The new calls never block the main loop (measure the time of one `raise_alert`/`clear`
   from the hook: well under 50 ms), run on the thread that already runs the surrounding code, and add
   no GStreamer call. `GLib.idle_add` rules for state changes are untouched.
9. **Nothing else changes.** T-03a's and T-03b's device suites still pass (the Tester reruns them), as
   do all existing unit tests. The only existing tests you may change are ones that assert something
   this task changes by definition; name each in your report.
10. **F-3 docstring line** is in `core/alerts.py`; `tests/uat/test_t00a_alert_scheduler.py`'s docstring
    test still passes.

## Verification expectations

| Criterion | Minimum evidence level | Hardware needed |
|---|---|---|
| 1 to 6 logic | `unit`: the app's methods with a fake `user_data.alerts` and a fake clock-free scheduler, or the real scheduler with a recording mailbox; see how `tests/test_camera_loss.py` and `tests/test_startup_without_camera.py` drive the app without a device | none |
| 1 to 7 together | `device-integration`: the real program (`main.py`, the pipeline path as T-03a/T-03b's device tests start it) with a **stub `espeak-ng` first on `PATH`** that records each phrase with its start time (the stub of `tests/test_alerts_speech.py` is a model), and the **simulated unplug** `sudo .claude/tools/usb_sim.py detach camera --for <s>` / `attach camera` (the `camera_unplug` fixture). Real time for the 10 s cadence | the Pi camera, the Hailo chip. Stop `second-vision.service` is already stopped; check it, never stop it yourself |
| 7 | `device-integration`: Ctrl+C timing while the alert is raised, 10 trials, against T-03b's envelope | same |
| the person hears it | `user-observed`, once, in the same hands-on session as T-03a and T-03b (one real pull and replug; real `espeak-ng`, earphones) | earphones, the camera |

**Fixed means repeated.** The hooks are small but they sit next to the T-03a/T-03b races (a loss during
a mode switch, a camera that bounces, a stop during a start). Show with counts in the report: at least
**10 loss-and-return cycles** with the simulated unplug in the pipeline path (mixed modes), in none of which
the sentence is missing, doubled or out of order, plus the bounce case (a camera that is gone for 1 s and
back, 10 times). Show that each new test fails on the baseline (scratch tree built from
`.claude/baselines/T-03c1/`, never git) or explain why it cannot.

## Notes from the Lead (hints; you own the design)

1. A camera that vanishes and returns within the 10 s: the first alert is spoken, then the reconnect
   sentence. That is correct; do not suppress the first one.
2. Clearing the missing-camera alert does not cut an utterance under way (T-00a). So the reconnect sentence
   waits for the end of "Critical Error...". Say so in the report with the measured delay.
3. `_camera_missing_since` already marks "missing" for T-03b's log lines; it is not the right trigger for
   the alert (it is cleared in `_start_on_camera`, which is also where you clear the alert, but it is set in
   different places). Prefer the scheduler's own `is_raised`.
4. T-03c2 (the *beep beep*) will hook into the same two places later: keep the helper small and named
   so it can be extended.
5. The hardware may drop out with no unplug in progress (seen once, 2026-10-15). If you see it, say so
   prominently in the report.

## References

- AGENTS.md rules 2, 3, 4, 7 and (for anything touching the pipeline) 6. The change adds no Hailo or
  GStreamer call; say so in the report and name the lines you read to be sure.
- `core/alerts.py` (docstring), `pipeline/app.py` camera sections, `tests/test_camera_loss.py`,
  `tests/test_startup_without_camera.py`, `tests/uat/test_tc024_003_camera_removed.py`,
  `tests/uat/test_tc024_002_camera_absent_at_start.py`, `tests/test_alerts_speech.py` (stub espeak).
- `reports/T-03a/` and `reports/T-03b/` decision files (residuals accepted there).

## Open risks / questions: stop and report instead of guessing

- **R1.** Anything in "What exists today" that the code contradicts: report the difference first.
- **R2.** If you find a path where the camera is missing but the state machine never reaches
  `_on_camera_lost` / `_wait_for_camera` / `_start_without_camera` (a start that fails some other way),
  report it; do not widen the hooks silently.
- **R3.** Device safety: before any device run, `systemctl is-active second-vision.service` must be
  `inactive` and `sudo fuser -v /dev/hailo0` empty. A "Second Vision performance run" terminal window on
  the desktop is finished and holds nothing. If a device is busy, stop and report.
- **R4.** Do not weaken a test to make it pass. A Tester test that expects the wrong thing is the Tester's
  to fix.

## Continuation note (Lead, 2026-10-18 13:35): attempt 1 was interrupted, not restarted

The first Developer run was stopped by the user (account switch) at about 11:36, with no
`dev-report-1.md` written. The tree holds its part-way work: the hooks in `pipeline/app.py`
(`_raise_camera_missing`, `_announce_camera_back`, `_confirm_camera_back`, a 1 s confirmation
window `CAMERA_BACK_CONFIRM_SECONDS` because a camera held by another process fails a few
milliseconds after the pipeline "runs"), the F-3 docstring line in `core/alerts.py`, and two new test
files `tests/test_camera_alerts.py` (unit) and `tests/test_camera_alerts_device.py` (device), with
logs in `reports/T-03c1/evidence/`. Same baseline (`.claude/baselines/T-03c1/`), same brief, same
attempt number: you are finishing attempt 1.

What to do, in this order:
1. **Do not trust any of it.** Read the per-task diff against the baseline and every new test. The
   old logs are the previous run's claims; they are not evidence for this report. Judge the design
   yourself, including the 1 s confirmation window (it departs from the brief's "clear on
   START_RUNNING" shape; it is acceptable only if criteria 3 to 5 still hold and you can say why it is
   needed from your own measurement; if you find it needless or harmful, change it).
2. Keep the hooks small and named so T-03c2 can add its vibration in `_raise_camera_missing` and the
   confirmation path.
3. Rerun, now, with the camera free (check `systemctl is-active second-vision.service` is
   `inactive` and `sudo fuser -v /dev/hailo0` is empty first): the unit tests in both environments,
   the **whole** device file once (`--run-device`, in `my_hailo_env`), the 10-cycle, bounce, held-camera,
   never-lost and Ctrl+C cases included. Run long ones as background jobs writing to
   `reports/T-03c1/evidence/`, and read the summary lines only. Note from the old logs
   (`suite-hailo-env-2-launched-in-background-SIGINT-ignored.log`): a pytest launched with `&` ignores
   SIGINT, so the Ctrl+C trials must be run so that the signal reaches the app (say how in the
   report).
4. Show each new test fails on the baseline (scratch tree from `.claude/baselines/T-03c1/`, never git)
   or explain why it cannot.
5. Write `docs/testing/reports/T-03c1/dev-report-1.md` (template in `reports/README.md`), listing every
   file changed, the measured numbers (first sentence delay, gaps, reconnect delay, cycles, bounces,
   Ctrl+C times, one `raise_alert`/`clear` call time), anything you did not verify, and a camera drop
   with no unplug in progress if you see one.
6. The camera must end attached (`/sys/bus/usb/devices/1-1/authorized` = 1).
