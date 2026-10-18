# T-03b · Lead decision, attempt 1

- Date: 2026-10-17 19:35
- Decision: **AWAITING-HANDS-ON**. Every check that can run without a person passes. Only the person's
  one real start without a camera is missing (MANUAL-TESTS.md, T-03b, done in the same session as T-03a's).
- Models: Developer **developer-sonnet**, Tester **tester on Sonnet**. Opus bar not met and not needed.
- Based on: brief-1.md (with my Amendment A), dev-report-1.md, test-report-1.md, my own diff review and
  the checks listed at the end.

## Summary in plain words

Start the program the way the boot service does, with no camera attached, and it now comes up and stays
up. The glasses link, the panel and speech run from the first second; the program looks for a USB camera
every 2 s, writes "Still no USB camera" about once a minute, and starts vision on the camera the moment
one appears, in whichever mode the panel has selected by then. Nothing starts mock mode by itself any
more: the launcher never passes `--mock`, and `main.py` refuses (message plus exit 1, before any worker)
if the Hailo library cannot be imported, unless `--mock` was typed.

Before this, the launcher either waited 60 s and quit (under the boot service) or silently ran mock mode
(by hand), so a device with no camera at power-on did nothing useful. The Developer also measured the
old start as unsafe: with the camera absent from the start, the library's start of the Hailo pipeline
sometimes crashed (4 of 25 by the Developer; 0 of 25 plus one GStreamer `CRITICAL` by the Tester, so a
few percent either way). The new start builds no Hailo pipeline at all while there is no camera
(the Tester confirmed from outside the process: no Hailo device and no camera node open, the hailonet
plugin not even loaded), so that defect cannot be reached from here.

## Acceptance criteria

| # | Met? | Evidence (report + level) |
|---|---|---|
| 1 No crash at start-up without a camera, 120 s, "Still no USB camera" about once a minute | **Yes** | test-report-1 checks 1, 1b, 1c, 1d. 124 s in one process, lines at 60.9 s and 121.9 s; 25 of 25 fresh starts clean; threads, open files and memory flat; 0.5 % of a core. device-integration |
| 2 Other parts keep running from the first second; panel switch recorded and announced; no vibration | **Yes** (real boards once too). Person's feel of the motors outstanding | checks 2, 2b, 3d, 3e. 124 heartbeats in 124 s, one motor packet and it is the zero command; "depth mode" announced. device-integration |
| 3 Keeps checking; vision starts when a camera appears, any time, any node, four modes | **Yes** | checks 3, 3b, 3c: camera at 1, 10, 90 s in all four modes 12 of 12; 25 of 25 at random 0.5 to 5 s; another node; 38 of 38 across a race sweep from 1 s before launch to 1.5 s after. device-integration |
| 4 Camera appears but cannot be opened, or drops again | **Yes** | checks 4, 4b, 4c: 3 start attempts and no crash with the camera held by another process; 8 of 8 and 20 of 20 for drops. device-integration |
| 5 Never mock mode | **Yes** | checks 5a, 5b, 5c: 48 launcher cases against a recording stub, no executed line names `--mock`; `main()` refuses with exit 1 and no worker, no byte to the glasses board; `--mock` still works; no "MOCK" line in any device run. unit + device |
| 6 Clean stop, exit 0 in about 1 s (Amendment A bound) | **Yes** | check 6: 30 of 30 exit 0 in 0.36 to 1.12 s, nothing left. Earlier stops: see residual 1. device-integration |
| 7 Camera present at start: nothing changes | **Yes** | checks 7a, 7b: the command line is identical in all 24 camera-present cases; both `exec` lines are byte-for-byte the baseline's; 3 plain starts record the same arguments. unit + device |
| 8 T-03a still holds | **Yes** | check 8: 10 of 10 of T-03a's own device cases (each mode, bounce, gaps "at once" and 1.2 s, stops, another node) and both unit suites |

## Review items

- **Changed files** (against `.claude/baselines/T-03b/`): `src/second_vision/main.py`,
  `src/second_vision/pipeline/app.py` (additions plus a 3-line guard in `_connect_callback`),
  `scripts/sv-main.sh`, `scripts/second-vision.service` (comments only), `tests/test_launcher.py`,
  `tests/test_main_startup.py`, `tests/test_startup_without_camera.py`, and the Tester's
  `tests/uat/test_tc024_002_camera_absent_at_start.py` (90 tests). Matches both reports; saved as
  `changes-1.patch` (2065 lines).
- **Protected files and libraries:** none changed; `check_outside_writes.py`: nothing changed in either
  group. T-03a's code (`_release_old_pipeline`, `_launch_on_camera`, `_prime_hailonets`,
  `_start_on_camera`, the watcher, `MOTORS_OFF`) is untouched.
- **`sudo` and code that runs commands:** none in the added `src/` or `scripts/` lines. `sys.exit(1)` in
  `main.py` is the one exit the brief allows; `GLib.idle_add(self.loop.quit)` in `run()` is taken only
  when a stop was already requested, through `idle_add` (Rule 4). Tests use `subprocess` for the program
  and `ps`/`pgrep`; sudo only through the existing `camera_unplug` fixture.
- **The boot path (`scripts/sv-main.sh`) read line by line by me:** variables and `FRAME_FLAG` rule
  unchanged; if the camera node is missing, three `echo` lines and carry on; two `exec "$RUN" ...` lines,
  identical to the baseline's; no `sleep`, no `exit`, no `--mock` on an executed line. `RUN` can be
  replaced by `SV_RUN_SH` (the one test seam; unset it is the old line).
- **Contracts and threading:** Rule 2 queue keys and Rule 3 worker signatures unchanged; the watcher is
  started from the main loop (`_begin_waiting` via `GLib.idle_add`), not from the constructor; no new
  unbounded wait.
- **Callers (Rule 7):** `create_pipeline` is called by the library's `__init__`; `run()` by
  `main._run_pipeline_mode`; `_begin_waiting` by `run()`; `_start_without_camera` by `create_pipeline`.
- **Hailo documentation (Rule 6):** none describes this; the claim that dropping a pre-built, never-started
  Hailo pipeline is safe rests on the HailoRT v4.23.0 source and on measurement (Developer 40 of 40, Tester
  60 of 60 fresh processes). The design does not need it: nothing Hailo is built.
- **DEVIATIONS.md:** I read DV-002. The start by hand through the launcher, the software unplug, the stand-in
  boards and the extra runs go beyond the workbook's single power-on and keep its intent. Accepted.

## Checks I ran myself

- Poetry-environment suite: `487 passed, 166 skipped` (the Tester's `487 passed, 164 skipped`; two more
  skips because its file has changed since: the passes match).
- `my_hailo_env`: the new and T-03a test files together (`test_startup_without_camera`, `test_main_startup`,
  `test_launcher`, `test_camera_loss`, `tests/uat/test_tc024_002…` unit part, `test_tc024_003_start_paths_gst`):
  `150 passed, 37 skipped` (the skips are the opt-in device tests).
- Evidence summaries match the report: checks 1, 3, 3b, 4c, 6, 8 and the race sweep all end `passed` and
  `pytest exit code: 0`; no crash line in the check logs.
- Device end state: service `inactive`, camera attached, nothing holding the Hailo chip.

## Residual risks, accepted and written down

1. **A stop in the first ~0.5 s after launch does not always exit 0.** Python has installed no handler
   yet (imports, then the library's `__init__`, then `main()`'s SIGTERM handler). Same with a camera, same on
   the baseline (0 of 20 exit 0 on the baseline against 9 of 20 here, all stops from 0.39 s on clean). The
   Tester found one more cause that the Developer missed: `SecondVisionApp.__init__` sets `_shutting_down`
   only after `super().__init__()` returns (`app.py:363`), so a SIGINT in between ends in an `AttributeError`
   traceback and exit 1. Under systemd a stop that early does not arise in service use and a SIGINT-killed
   process counts as a clean stop. **Decision: not part of T-03b (Amendment A); goes to T-11** (early signal
   handlers in `main.py`, class-level defaults for the attributes `shutdown()` reads).
2. **A camera that is already attached at launch is started by the library's own `run()`, not primed.**
   The boundary is crisp: the camera appearing at 0.35 s or earlier after launch takes this path, from 0.4 s on
   the waiting path (18 and 20 runs, all fine). T-11 as before.
3. **`--input usb` with no camera** still ends in the library's `exit(1)` inside `GStreamerApp.__init__`,
   before our code runs. The launcher never passes `usb`; it is a development flag. Not changed (it would need
   a library change).
4. **`DISPLAY` set with no camera** was not run (the session is headless). The launcher's `--use-frame` rule
   is covered by the unit matrix.
5. All unplugs and replugs are `usb_sim.py`; the real boot is the person's step.

## Questions for the user (nobody to answer now; none blocks the next task)

1. **Documentation (Rule 8).** D-13 ("mock mode only by `--mock`") is now enforced in three places: the
   launcher never passes it, `main()` refuses without Hailo, and the app starts with no pipeline when the
   camera is missing. Should this go in `DECISIONS.md` (the enforcement) and `ARCHITECTURE.md` (the
   start-up path: the constructor parks an empty pipeline, `run()` starts the watcher, vision starts through
   the staged, primed start)? Together with the T-03a question already asked.
2. **T-11 scope.** With T-03a and T-03b done, T-11 would be "stopping the program while it starts": early
   signal handlers in `main.py` and the class-level defaults (residual 1), the unprimed boot pipeline (2),
   and the few-bytecode stop window in `_launch_on_camera` (T-03a residual 1). Keep as one task, split, or
   leave?
3. **Re-install the service unit.** The unit under `/etc/systemd` still carries the old comment sentences
   until you re-run `scripts/install-service.sh` (comments only; the launcher it runs is the repo's, so the
   next boot already behaves as described). The first real boot after this lands is the only test of the
   service itself, which no agent may start.

## Waiting for a person

T-03b is added to MANUAL-TESTS.md, "Waiting for a person", to be done in the same session as T-03a's: start
with the camera unplugged, watch two minutes (no vibration, a panel switch is spoken), plug the camera in
(vision starts in the panel's mode), pull it again once (waits again). If it fails, it becomes attempt 2.

## Next

T-00 (alert output foundation) is next in the plan. It needs a split and, per the board's own note, two
decisions that are the user's (see the T-00 brief).
