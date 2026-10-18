# T-03a · Lead decision, attempt 1

- Date: 2026-10-17
- Decision: **RETURNED** (attempt 2, developer-opus, same baseline)
- Based on: brief-1.md, dev-report-1.md, test-report-1.md (Tester, Opus, resumed run), own diff
  review, and an independent reproduction of the Tester's isolation probe (below).

## Summary in plain words

The main case works on the real device: unplug the camera while the program runs, in any of the
four modes, and the program stays up, sends one zero to the motors, keeps the glasses heartbeat and
the panel going, and starts vision again about 1.7 s (median, 30 samples) after the camera is
back, in the mode selected at that moment, even on a different `/dev/video` node. Ctrl+C and SIGTERM
while the camera is missing end the program with exit 0 in under a second.

It is not good enough yet, because the program can still crash when the camera goes at an
unlucky moment: the camera drops again just as vision restarts on it (a loose connector), the
camera goes about a second after a panel mode switch, or a camera is found that cannot be opened.
That is the exact case this task exists for ("the program does not crash"), and a camera on a
wearer's head is more likely to have a loose connector than a clean unplug.

## Acceptance criteria

| # | Met? | Evidence (report + level) |
|---|---|---|
| 1 No crash | **No.** Plain unplug: yes. Three failure paths: no (F1, F2, F3 below) | test-report-1 checks 5, 6 PASS; checks 7, 8, 9 FAIL (device-integration) |
| 2 Everything else keeps running | Yes on the device, with a fake panel and fake board; the real boards ran once (check 11). Person's confirmation outstanding. When the program exits (F3), speech, link and panel exit with it | checks 10-12 PASS; 13 BLOCKED (person) |
| 3 Motors not left on a stale reading | Yes at the wire level (`AA 01 00 00 00 01` after the loss, then heartbeats only). Person's confirmation outstanding | check 14 PASS; 15 BLOCKED (person) |
| 4 Keeps checking; restarts on a camera | Yes for a clean replug (median 1.7 s, any node). **Not** when the camera cannot be opened at the first try (F1) | checks 16, 17 PASS; 9 FAIL; 18 BLOCKED (person) |
| 5 Never mock mode | Yes. No "MOCK" in any of ~50 app logs, no `--mock` in the new code | check 20 PASS |
| 6 Clean stop still works | Yes while waiting, during a teardown and during a restart (all exit 0, at most 1.01 s). A stop within 0.15 s of any pipeline start can segfault, but the baseline does the same (O1), so this is not a regression | checks 21, 22 PASS; 23 BLOCKED (person) |

Review items, all clean: only `pipeline/app.py` (class `SecondVisionApp` and module constants),
`tests/test_camera_loss.py` and the Tester's `tests/uat/test_tc024_003_camera_removed.py` changed,
which matches the Developer's and the Tester's lists. No protected file (vision algorithms,
workers, `scripts/`, `firmware/`) and no installed library changed
(`check_outside_writes.py`: nothing in either group). `app.py` has no `sudo` and no
`subprocess`/`os.system`/`os.popen` (the only hits are in the Tester's test file; the sudo there
goes through the `camera_unplug` fixture, which calls `usb_sim.py`). The `serial_queue` dict keeps
its five keys; `tts_queue` and the worker signatures are unchanged. Threading follows Rule 4: the
camera lookup is a daemon thread that never touches GStreamer and hands over with
`GLib.idle_add`; `trigger_rebuild()` stays schedule-only.

## What must be fixed (attempt 2)

All three come from the Tester's device runs. I reproduced F1's mechanism myself.

- **F1. Segfault when a Hailo pipeline is started on a camera that cannot be opened.**
  - Seen as: the camera drops again within about 0.1 s of "Camera found on …"; segfault 7 of 8
    tries, at `app.py:1299` `pipeline.set_state(PLAYING)`; native stack `gst_object_unref` called
    from `libgsthailo.so` (gdb logs in `evidence/`).
  - Isolation (Tester, 6 runs; and **Lead, 2026-10-17 05:12, 4 runs: 3 segfaults, 1 clean
    failure**): in one process the app's own `both` pipeline runs 3 s on the camera and is torn
    down as `_release_old_pipeline()` does; 5 s later the same pipeline on `/dev/video99` is set
    to PLAYING. Segfault (exit 139) in most runs. In a fresh process 1 of 15; with no Hailo element
    (mode `none`) 0 of 3.
  - Consequence: the designed "camera found but cannot start, go back to waiting" path
    (`_start_on_camera`) usually crashes on the real pipeline. The Developer's gst-level test of
    that path has no Hailo element, so it could not see this.
- **F2. Segfault in the camera-loss teardown** when the camera goes about 1.8 s after a mode-switch
  rebuild started (`app.py:889`, a hailonet's `set_state(NULL)`); once a reference-count CRITICAL
  and a survival.
- **F3. The program ends when the camera goes during a mode-switch rebuild** (the Developer's "R4
  residual"): at 1.2 s and 1.4 s after a panel switch, "Failed to start new pipeline", the library
  quits the loop, exit 0 (so speech, the glasses link and the panel stop); at 1.3 s the same window
  segfaulted. Criterion 1 says "no exit", so I am not accepting this as a residual risk.

Details, commands and logs: test-report-1.md "Failures". The brief for attempt 2 is brief-2.md.

## Observation recorded, not part of this task

- **O1 (pre-existing).** Ctrl+C or SIGTERM within about 0.15 s of a pipeline start segfaults in the
  library's `shutdown()` in 3 of 5 tries, on the baseline code as well as on this code. It is not
  caused by this task and is not blocked on it. Added to the board as T-11 (proposed). T-03a
  attempt 2 may fix it only if the same mechanism as F1/F2 covers it, and must say so.

## User-observed evidence

None yet. The one real-camera confirmation waits until F1 to F3 are fixed, because a real replug
can bounce and hit F1 (test-report-1, "Manual steps needed"). Before that session the real panel's
speech and vibration switches must be on (they were off in every real-port run).

## Outstanding risks accepted, and why

- **R1 (a teardown cannot be interrupted) stays accepted**, as the Developer disclosed it: the
  alarm zeroes the motors after 3 s and logs an error; speech, link and panel keep running. No exit
  fallback is wanted (the brief forbids inventing one).
- No other risk is accepted at this point.

## Answers to the Developer's questions in dev-report-1 (for attempt 2)

- Q2 (R4 residual): not accepted; fix it (F3).
- Q3 (a restart that a non-camera element fails shuts down): **confirmed**, keep it.
- Q4 (an exception while building the restart pipeline): **keep looking**, as now.
- Q5 (an info-level "still no camera" line): **yes**, one INFO line about every 60 s while waiting
  (not every 2 s: the journal already floods with Hailo driver warnings).
- Rule 8 documentation (ARCHITECTURE.md, DECISIONS.md): asked after acceptance, not now.

## Records updated

- TASK-BOARD.md: T-03a RETURNED, then IN-DEV (attempt 2); T-11 (proposed) added.
- TRACEABILITY.md: no change; TC-024-003 stays NOT-BUILT with partial evidence (the removal half).
- documentation/TASKS.md: nothing ticked.
- DISAGREEMENTS.md / REQUIREMENTS.md: none.
- `changes-1.patch`: the attempt-1 diff (app.py, both test files), kept for review. The next
  attempt's patch will replace it as the task's whole change.

## For the user to commit

Nothing yet. T-03a's files stay uncommitted until the task is accepted.
