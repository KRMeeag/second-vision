# T-17 · The demo-run tool, and the performance tools' battery columns · Brief, attempt 1 (verification)

- Date: 2026-10-19
- This is a **verification brief** for work built outside the loop (demo track item F8, 2026-10-19, author:
  a Sonnet session). Attempt 1 is the author's build; this brief is the Tester's first pass. Common rules:
  [reports/DEMO/VERIFICATION-PLAN.md](../DEMO/VERIFICATION-PLAN.md). Read them first.
- Requirement (DEMO-TRACK.md, item F8, quoted): "**The demo run.** One written demo sequence (normal use;
  camera out and back; glasses board out and back; earphones out and back; panel out and back; lens covered;
  the performance chart) and a small script that starts the program, timestamps what it says and sends, and
  saves an evidence log per step. Rehearse it once with the user on the real device: that rehearsal is also
  the per-device hands-on confirmation for T-03a, T-03b and F0 to F6."
- Also (DEMO-HANDOFF.md, 2026-10-19): the measuring tools read the X1202 battery gauge: `perf_sample.py` records
  the gauge's charge and voltage (I2C 0x36, read-only, no root); its summary gives the drain rate and flags a
  run whose charge rose (charger in); `--stop-at-battery PCT` ends a rundown in time; `perf_run.py` passes it
  on and stops the app as soon as the sampler ends; `perf_plot.py` draws a battery panel.
- UAT cases: none directly. The tool serves the failsafe cases (TC-022 to TC-025, TC-024) by showing, in one
  run, what the program says and sends; REQ-PERF-01 (battery rundown) by its battery columns.
- Priority / depends on: P2, but **runs first**: the user runs this tool in the rehearsal. Depends on the
  sentences and log lines of T-03c1, T-01a, T-04a, T-02b, T-13 (it listens for them).
- Developer: the demo-track author (Sonnet). Tester model: **sonnet**. Opus bar not met (a test tool; no
  pipeline lifecycle).
- Baseline: none for this slice. `docs/testing/tools/` is outside the baseline's `src/`, `tests/`, `scripts/`.
  Its tests are `tests/test_demo_run.py` and `tests/test_perf_tools.py` (new files, absent from the baseline).

## Goal

The presenter can run `my_hailo_env/bin/python docs/testing/tools/demo_run.py` on the Pi and be walked through
seven steps. After each action the tool ticks, correctly, what the program said and sent, and says `MISS`
only when the program really did not do it. It starts the program the way the boot service does and ends it
the way the service does, never leaves a program running, never uses sudo, never touches the hardware, and
keeps an evidence folder that tells the truth. The battery columns of the measuring tools read the gauge
read-only and report a rundown correctly. **A wrong tick in the rehearsal (an `OK` for a line that never
came, or a `MISS` for a line that did) is the failure that matters here.**

## In scope (what the Tester reads and runs)

- `docs/testing/tools/demo_run.py` (403 lines): the step table `STEPS`, `watch()`, `preflight()`,
  `with_display()`, `run()`, `write_summary()`.
- `docs/testing/tools/perf_sample.py` (battery reading, summary, `--stop-at-battery`), `perf_run.py`
  (passes the flag on, stops the app when the sampler ends), `perf_plot.py` (battery panel), `perf_npu.py`
  (the NPU sampler fix mentioned in DEMO-HANDOFF.md).
- `tests/test_demo_run.py` (21 tests), `tests/test_perf_tools.py` (needs `psutil`: `my_hailo_env` only).
- The program's own output lines the tool listens for, in `src/`: `[TTS] '<text>'` (`workers/tts_worker.py`),
  `[PATTERN] playing '<name>'` (`core/haptic_patterns.py`), `[CHECK] ...` (`core/status_board.py`),
  `[ALERT] raised`, `[SERIAL] ESP32 stopped acknowledging` / `responding again`, `[CONFIG] No heartbeat` /
  `Control panel is back`, `[EARPHONES] not found` / `found again`, `[VIEW] camera view is now`.

## Out of scope: must not change

Nothing under `src/`, `scripts/`, `firmware/`, installed libraries. The Tester may add permanent tests only
under `tests/uat/`. The tool is not production code, but you may not edit it either: report defects.

## Acceptance criteria

1. **The written sequence (F8).** `demo_run.py --list` shows the seven steps in the order the requirement gives
   (normal use; camera; glasses board; earphones; panel; lens covered; performance chart). Each action says
   what to tell the panel, what the presenter does, and what the program should do within how many seconds.
   The windows are consistent with the cadences in REQUIREMENTS section 8: camera alert at once and every
   10 s; glasses alert about 2.5 s after the cable is pulled; earphones within 1 to 2 s; panel 20 to 31 s
   (30 s of silence, `B:alive` every 10 s); the camera view after three one-second checks.
   The chart step names files that exist (`docs/testing/reports/T-15/evidence/...`).
2. **Every thing it waits for is something the real program prints.** For each `Expect` pattern in `STEPS`,
   show the line of `src/` that prints it and produce a real instance by running that component (the real
   speech worker with a stub `espeak-ng`, the real `serial_worker` against a fake board, the real alert
   scheduler, the real status reporter, ...), then check the pattern matches it. Do **not** reuse the author's
   stand-in program as the source of lines: the stand-in only proves the tool agrees with itself. A pattern
   with no matching real line is a `FAIL` (the rehearsal would show `MISS` on a device that worked).
   Include the negative checks: the step-1 "an object announced" pattern must not match a failsafe sentence;
   `^\[ALERT\] raised` must not appear for a once-only "back" sentence raised during step 1.
3. **The timing semantics (the part that is easy to get wrong).** Delays are counted from the presenter's
   Enter; a reaction before the Enter counts (negative delay) only if it is after the prompt appeared; a line
   from before the prompt never counts; a line after its `within` window does not count; `count=2` needs two
   lines; an `absent` expectation passes only if the line never comes in its window; a step that went well
   moves on at once, and none waits past its longest window. Check `watch()` with your own scripted `LogBook`
   and clock, including the boundary (a line at exactly `within`), a clock that jumps, and lines that arrive in
   a burst.
4. **Start and stop like the service.** It runs `scripts/sv-main.sh` (check the arguments the launcher passes:
   `--input`, `--serial-port`, `--config-port`, `--use-frame` only with a display), reads its output without
   losing lines or blocking (stdout and stderr merged, the reader thread), and on finish, on `q`, on Ctrl+C
   of the tool, on EOF of stdin and on an exception stops the program with SIGINT to its process group and
   waits up to 20 s, killing it only if it does not exit; the exit code and time are written down. **After the
   tool exits no process of the program is left** (`pgrep -af "second_vision|sv-main|run.sh"` is empty), in
   each of those exit routes, tested with the stand-in program and with a stand-in that ignores SIGINT.
5. **It refuses safely.** With `second-vision.service` active, with another copy of the program running, or
   with no `/dev/video0` it prints why, starts nothing and exits non-zero; a missing glasses port, panel port
   or earphones skips only that step, with the reason in the summary. `preflight()` only reads (no write, no
   sudo, no service control). Check what `pgrep -af second_vision/main.py` would and would not match on the
   command line `scripts/run.sh` actually produces (does it find a copy started by `sv-main.sh`?).
6. **Never uses sudo or touches hardware.** `grep -n "sudo\|usb_sim\|serial\.Serial\|/sys/\|os.system\|shell=True"`
   finds nothing that acts on hardware; the only subprocesses are `systemctl is-active`, `pgrep` and the
   program. (The quick check of the Lead repeats this.)
7. **The evidence folder tells the truth.** `demo-<date>-<time>/summary.md`, `session.log` (every line,
   timestamped, nothing dropped while a prompt waits), `steps/NN-key.log`. The counts in the summary match the
   rows (seen / missed / skipped); a skipped step is a `skipped` row, never `ok`; the presenter's words are
   kept against the right step; the folder is written even when the run is cut short; the exit code is 0
   only when nothing was missed; two runs in the same second do not overwrite each other (or the tool says
   so).
8. **The battery columns (perf tools).** `perf_sample.battery()` reads the gauge at I2C 0x36 **read-only
   without root** and returns a charge percentage and a voltage that agree with the vendor's register
   conversion (check the arithmetic against the X1202/MAX17048 datasheet formulas, name where you checked;
   on this Pi the gauge read 4.18 V and 98.4 % on 2026-10-19; a fresh read now is allowed and is
   `physical-measurement`). With no gauge the tools run on without a battery column and do not crash. The
   summary's drain rate is right on a synthetic series (a falling series, a flat one, one with a rising
   charge: flagged "charger in"). `--stop-at-battery PCT` ends the sampling when the charge is at or below
   PCT and not before; `perf_run.py` then stops the app and exits cleanly, and leaves no process; `perf_plot.py`
   draws the panel with and without battery data. Claims in DEMO-HANDOFF.md ("the gauge cannot say whether the
   charger is plugged in; GPIO6 is unconfigured") are checked read-only, or marked unverified.
9. **The author's tests test the requirement.** `tests/test_demo_run.py` and `tests/test_perf_tools.py`: for
   each, is the test checking the behaviour above or only the implementation's own constants? The tests that
   pin sentences to `src/` constants (`core/connections.py`, `core/camera_view.py`, `pipeline/app.py`) are
   good only if the constants are the ones the program really says; check at least three by running the
   emitter.
10. **Nothing else changes.** No file under `src/`, `scripts/` or `firmware/` is touched by the tool or its
    tests (diff the tree before and after a full suite run).

## Verification expectations

| Criterion | Minimum evidence level | Hardware needed |
|---|---|---|
| 1, 3, 5, 7 | `unit` (with scripted logs, a fake clock, a stand-in program) | none |
| 2 | `mock-integration` (each real emitter run with fake hardware) | none |
| 4, 6 | `unit` / `mock-integration` (a stand-in program, including one that ignores SIGINT); a static grep | none |
| 8 | `unit` on synthetic series; `physical-measurement` for one real read of the gauge (read-only, optional) | the X1202 gauge (I2C), no root |
| 9, 10 | review, then `unit` | none |
| Optional, only if the device is free (VERIFICATION-PLAN rule 9) | `device-integration`: run the tool for real with `--steps lens` or `--steps chart` and answers from a pipe: it starts `sv-main.sh`, sees `Camera view: clear`, stops it with exit 0 and leaves nothing running; `perf_run.py --duration 30 --stop-at-battery 100` stops at once | the camera, the Hailo chip |

## References

- AGENTS.md rules 2, 4, 7. `docs/testing/reports/DEMO/F8.md` (the author's account; claims to check),
  `docs/testing/DEMO-CHECKLIST.md`, `docs/testing/reports/DEMO/evidence/F8-mutations.txt` (read after your own
  mutants).
- The emitters: `src/second_vision/workers/tts_worker.py` (`[TTS]`), `core/haptic_patterns.py`,
  `core/status_board.py`, `core/alerts.py`, `workers/serial_worker.py`, `workers/config_reader.py`,
  `core/connections.py`, `pipeline/app.py`, `core/camera_view.py`.

## Addendum, 2026-10-20 (Lead, fresh session): the scope grew; this Tester pass is a clean restart

The first Tester of this attempt died on 2026-10-19 without a report and its scratch files are gone, so this
pass starts from nothing. Since the brief above was written the perf tools gained three things, which are now in
scope for criterion 8 (and the last sentence of criterion 8, "GPIO6 is unconfigured", is **superseded**: check it
read-only instead):

- **8a. The charger line.** `perf_sample.ac_power()` reads the HAT's power-loss line (GPIO6, as an input, through
  `gpiod`; vendor script `~/x120x/pld-trixie.py` is the model) and returns `"1"` charger in, `"0"` on battery,
  `""` unreadable. Check it against the vendor script's own reading (read-only), name where you checked, and check
  it never drives the line (input only), never needs root, and returns `""` (not a crash) when `gpiod` or the
  line is missing or busy. A fresh read now is allowed; **the charger is in at this moment** (the Lead read `"1"`,
  3.578 V, at 01:50 on 2026-10-20), so the on-battery value `"0"` can only be tested on synthetic input.
- **8b. The voltage stop.** `--stop-at-volts V` (and `battery_stop(row, threshold, volts)`): the sampler ends after
  **two readings in a row** at or under V; an unread value never stops; **a sample with the charger in never
  stops** (the cell voltage is high then; and a charger-in sample must not count toward the two in a row, or
  reset the count: decide from the code and test which, then say whether it is right for a rundown). The
  percentage stop (`--stop-at-battery`) still works. Boundary: a reading exactly at V stops; one 0.001 V above
  does not. `perf_run.py` passes both flags to the sampler and stops the app as soon as the sampler ends.
- **8c. The wait mode.** `perf_run.py --wait-for-battery` (needs `--stop-at-volts` or `--stop-at-battery`):
  `wait_for_battery(read, sleep, clock, limit)` returns True only when the line reads `"0"`; `""` is **not**
  "out" (a run on an unreadable line would measure nothing); False after `limit` (2 h) with the charger in.
  Without the flag, a rundown **refuses** to start while the charger is in (`"1"`), with a message that says so.
  The flag without a rundown option is refused. Test all of this with injected `read`, `sleep` and `clock`; do not
  start a 2-hour wait. Never start a real rundown: the Pi must stay on the charger.
- **8d. The summary.** `battery_lines()` now also reports "on battery in N of M samples", flags a run where the
  charger was in for part ("THE CHARGER WAS IN for the rest"), and returns "on the charger throughout" when no
  sample was on battery. Check each branch on synthetic series, including a series with no `ac_power` column (an
  old CSV) and a series with some `""`.
- **Evidence the author left**, to be **reproduced, not copied**: `tests/test_perf_tools.py` (12 tests; needs
  `psutil`, so `my_hailo_env` only, the Poetry run skips it) and `reports/T-15/evidence/rundown-tool-mutations.txt`
  (10 of 10 deliberate breakages caught). Make your own mutants of 8a to 8d.
- **Power rule (new, binding).** Before every full suite, long test or device test, read the battery and the
  charger line (`my_hailo_env/bin/python -I -c "import sys; sys.path.insert(0,'docs/testing/tools'); import perf_sample as p; print(p.battery(), repr(p.ac_power()))"`).
  With the charger out (`"0"`) and the cell under 3.6 V, hold the heavy work and write documents instead. Now:
  charger in, so go ahead, under `nice -n 19`. **Never kill a process by name or by a search over command lines;**
  stop only a process ID you started and recorded (the previous Tester's cleanup loop killed the Lead).
- **Live gauge read.** One real read of the gauge and the line is allowed (physical-measurement, read-only).
  Compare with the vendor script's reading if it runs without root; do not run anything that drives the pins.
- **Test baselines now (Lead, 2026-10-20):** the author's last full runs were Poetry 1072 passed / 217 skipped and
  `my_hailo_env` 1175 passed / 117 skipped, 0 failed. Reproduce, do not copy.

## Open risks / questions

- **R1.** If the tool's `MISS` can occur for a line that the real program does print (a wrong pattern, a
  wrong window), that is a `FAIL`, and the most valuable finding of this pass. Quote the real line.
- **R2.** `systemctl is-active` and `pgrep` are read-only; the Tester may run them. Do not start the real
  program unless the device is free and the check is the optional one above.
