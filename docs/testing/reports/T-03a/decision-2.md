# T-03a · Lead decision, attempt 2

- Date: 2026-10-17 16:06
- Decision: **AWAITING-HANDS-ON**. Every check that can run without a person passes. The only
  evidence missing is the person's one real-camera confirmation (MANUAL-TESTS.md, T-03a).
- Models: Developer **developer-sonnet** (continued the Opus session's partial work), Tester
  **tester on Sonnet** (two sessions; the first was cut off by the usage limit and the second
  resumed from its logs). Opus bar checked and not met: no Sonnet attempt was returned for being
  beyond it, and Sonnet got every repeat count to zero.
- Based on: brief-2.md (with Amendment A), dev-report-2.md, test-report-2.md, my own diff review,
  and the checks I ran myself, listed at the end.

## Summary in plain words

Attempt 1 left three ways to crash the program when the camera went at an unlucky moment (a
camera that is found but cannot be opened, a camera lost just after a restart, and a camera lost
during a panel mode switch). All three are gone on the device. If the camera is pulled while the
program runs, in any of the four modes, or bounces on and off like a loose connector, the program
stays up, sends zeros to the motors, keeps the glasses link, the panel and speech running, and starts
vision again by itself when a camera comes back (about 1.6 to 1.8 s after the replug, the same as
before). Ctrl+C and SIGTERM still stop it cleanly.

The cause was found in the HailoRT source: the Hailo element (`hailonet`, version 4.23.0) frees a
memory pointer it has not yet set if it is taken down before its first camera frame. That is why a
failed start, or a stop in the first 0.6 s, crashed. The fix is in this repo: the camera element is
opened alone first (a camera that cannot open now fails before any Hailo element has moved), and
each Hailo element is fed one empty buffer, dropped before it reaches any callback, so it can be
taken down safely. As a side effect, the older "Ctrl+C right after a mode switch crashes" defect
(board T-11) is gone for the rebuilt pipeline: baseline 16 of 20 crash, this code 0 of 20.

## Acceptance criteria

| # | Met? | Evidence (report + level) |
|---|---|---|
| 1 No crash (1a loose connector, 1b camera that cannot open, 1c mode-switch rebuild, 1d plain cases) | **Yes** at device-integration (simulated unplug). Person's real pull outstanding | test-report-2 checks 5, 6, 8, 9, 10, 11, 21. F1 probe: 30 of 30 clean in fresh processes (attempt-1 shape: 17 of 20 segfault). Bounce test: 10 of 10. Each mode-switch gap, 0 to 1.6 s: 4 passes each. Full device file: `37 passed`. 3 x 25 random flaps in one process each: passed |
| 2 Everything else keeps running | Yes at device-integration with a fake board and panel and a real speech worker; the real board and panel ran too. Person's confirmation outstanding | checks 10, 12; 25 BLOCKED (person) |
| 3 Motors not left on a stale reading | Yes at the wire level: the last motor packet after each loss is all zeros. Person's confirmation outstanding | checks 13; 25 BLOCKED (person) |
| 4 Keeps checking; restarts on a camera | Yes, any node, median 1.57 to 1.61 s from replug to "Camera back" (attempt 1: 1.78 s by the same method); 135 s absence: one "Still no USB camera" line at 61 s and 122 s, 0.4 % of a core | checks 14, 22 PASS; 24 BLOCKED (person) |
| 5 Never mock mode | Yes. No "mock" in any of the 149 program logs of this code; no `--mock` in the added code | check 15 |
| 6 Clean stop (REQ-DTA-08) | Yes: all exit 0, 0.5 to 1.1 s | checks 10, 16, 23 |

## Review items

- **Changed files** (against `.claude/baselines/T-03a/`): `src/second_vision/pipeline/app.py`
  (+751 -3), `tests/test_camera_loss.py` (new, 31 tests), `tests/uat/test_tc024_003_camera_removed.py`
  (new, the Tester's, 51 device tests) and `tests/uat/test_tc024_003_start_paths_gst.py` (new, the
  Tester's, 4 gst tests). Nothing in `scripts/`, `core/`, `pipeline/callbacks.py`, `workers/`,
  `firmware/`. This matches both reports. Saved as `changes-2.patch` (3629 lines).
- **Protected files and libraries:** none changed. `check_outside_writes.py` against the stamp:
  nothing changed in either group.
- **`sudo` and code that runs commands:** the diff of `app.py` adds none (the only hits are comments
  that say "never `loop.quit()`"). The tests use `subprocess` for `ps`, `pgrep`, `fuser` (no sudo)
  and the program under test; sudo runs only through the existing `camera_unplug` fixture, which
  calls `.claude/tools/usb_sim.py`.
- **Contracts and threading:** the only queue write is one all-zero dict with exactly the depth
  dict's keys on `serial_queue`. The watcher thread is a daemon, waits on `shutdown_event`, never
  touches GStreamer; the hand-over is `GLib.idle_add`. No wait is unbounded. `_release_old_pipeline()`
  has no diff, so its order is unchanged. `_drop_buffer` is now a staticmethod in the class.
- **Callers (Rule 7):** `_launch_on_camera` is called by `_start_on_camera` and `_rebuild_on_camera`;
  `_rebuild_on_camera` by `_rebuild_pipeline`; `_prime_hailonets` and `_abandon_start` by
  `_launch_on_camera`. Nothing unused.
- **Hailo documentation (Rule 6):** no hailo-apps document describes the defect. The claim rests on
  the HailoRT v4.23.0 source (`gsthailonet.cpp`, read by the Developer and again by the Tester) and on
  measurements on this Pi, both stated in the reports. The binary was not disassembled; the match is
  by version and strings. The docstrings say so.
- **DEVIATIONS.md:** I read the Tester's DV-001. The added runs go beyond the workbook's single
  disconnection and keep its intent (the camera disappears, the program must survive and look
  again). Accepted.

## Checks I ran myself

- Poetry-environment suite, whole `tests/`: `388 passed, 113 skipped` (the skips are the opt-in
  device tests and the `gst` tests that need GStreamer).
- `my_hailo_env`: `tests/test_camera_loss.py` and `tests/uat/test_tc024_003_start_paths_gst.py`:
  `35 passed`.
- Evidence logs: every summary line cited by the Tester matches (full device file `37 passed`, random
  flaps `3 passed`, long absence `1 passed`, memory, unit suites). Of the 105 program logs in
  `app-logs-attempt2b`, the only 16 with a crash marker or a non-zero exit are the baseline
  comparison runs (their `run.sh` is the Tester's copy of the baseline, whose `app.py` I checked has
  the baseline's checksum). The 20 runs of this code in the same test are all `exit code: 0`. In
  the other three log folders, none has a crash marker; the one log not ending in exit 0 is the cut-off
  memory run of the first Tester session.
- Device end state: `second-vision.service` inactive, camera attached, nothing holding the Hailo chip
  (`sudo fuser -v /dev/hailo0` printed nothing).

## Residual risks, accepted and written down

1. **A stop in a window a few bytecodes wide.** A Ctrl+C or SIGTERM that lands in `_launch_on_camera`
   between the `_shutting_down` check and `set_state(PLAYING)` (lines 1471 to 1475) can take the
   primed pipeline to NULL twice, which the HailoRT source says unrefs the allocator twice. Both the
   Developer and the Tester name it. Not reproduced in any run (stops 0 to 0.8 s after "Camera found",
   0.05 to 0.4 s after the first start, 0.05 and 0.15 s after a rebuild: all exit 0). Consequence if it
   ever hit: a crash while the program is already stopping. Re-checking the flag just before
   `set_state` would narrow it but not close it (the signal handler runs on the same thread between
   bytecodes), so I do not return the task for it. It goes to T-11's scope.
2. **The program's first pipeline at boot is not primed** (the library's `run()` starts it). Stopping
   it, switching mode or losing the camera in its first seconds passed 30 of 30 (and 25 of 25 unplugs)
   on this code and on the baseline alike. The Developer's reasoning is that a fresh process's first
   allocations may sit in zeroed memory, which would hide the defect; it is not excluded. Goes to T-11.
3. **A Hailo element that itself fails a start** (`START_FAILED`) was never reached on the device;
   it still shuts down as today, as decision-1 asked, and the unfed element's teardown might crash
   instead of exiting 1. Priming that fails on the real plugin was never made to happen (each branch
   logs a warning and the start goes on).
4. All unplugs were `usb_sim.py`, not a hand pulling a cable.

## Questions for the user (nobody to answer now; none blocks the next task)

1. **Documentation (AGENTS.md Rule 8).** Should the settled design choice go in the team docs:
   "every USB-camera pipeline is started camera-first and its Hailo elements are fed one empty buffer
   first, because HailoRT 4.23.0's `hailonet` frees an unset pointer if taken down before its first
   frame"? Proposed places: `DECISIONS.md` for the choice, `ARCHITECTURE.md` for the start order. The
   defect itself may be worth reporting to hailo-ai/hailort. Your call; I have written nothing in
   `documentation/`.
2. **Memory growth on every vision restart.** The Tester measured the program's memory growing 5 to 6 MB
   each time vision restarts (camera replug or mode switch), in attempt 1's code, this code and the
   untouched baseline alike. A flaky connector could restart vision hundreds of times a day; where it
   stops, or whether the Pi's memory runs out, is unmeasured. Not caused by this task. I propose it on
   the board as T-12 (P2, a measurement first). Make it a task?
3. **T-11 (Ctrl+C right after a pipeline start).** The rebuilt-pipeline case is fixed by this task
   (baseline 16 of 20 crash, this code 0 of 20). What remains is the boot pipeline (not primed, never seen
   to crash) and residual 1. I propose re-scoping T-11 to those and doing it after the real-hardware
   session. Agreed?

## Waiting for a person

T-03a is added to MANUAL-TESTS.md, "Waiting for a person": one real camera pull and replug. Before
that session the real panel's speech and vibration switches must be on (in every real-port run they
were off). Until the person confirms, TC-024-003's removal half (REQ-CON-08) stays AWAITING-HANDS-ON,
not VERIFIED. If the confirmation fails, it becomes attempt 3.

## Next

T-03b (start-up without a camera; no automatic mock mode) may start: T-03a is not blocking it, since
the changes stay in the tree.
