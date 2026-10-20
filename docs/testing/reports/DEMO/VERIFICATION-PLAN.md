# Tester passes for the demo-track build: plan and common rules

Lead, 2026-10-19. DEMO-TRACK.md promised that each demo item would get "a Tester pass" once the normal
loop resumed. This file is that plan. **The Tester's check is the one check per task** (user, 2026-10-19; Lead
instructions, step 8): a Tester `PASS` plus the Lead's scripted quick checks accepts a task. The Lead does
not review the code.

The demo items (F0 to F8, the status display) were built in a fast lane by sessions that wrote their own
tests and their own reports (`reports/DEMO/F*.md`). **Those reports are claims, not evidence.** Every
number in them (tests passed, mutants caught, milliseconds) has to be reproduced, not copied.

## The slices, in the order they will run

One Tester at a time, one slice at a time, each ending in a decision file before the next starts.

| # | Slice (board tasks) | Demo item | Report folder | Why this place in the order |
|---|---|---|---|---|
| 1 | **T-17** the demo-run tool and the performance tools | F8, battery columns | `reports/T-17/` | The user runs this tool in the rehearsal, and a wrong tick would waste the rehearsal |
| 2 | **T-00b, T-00c** vibration patterns and the speech/vibration overrides | F1 | `reports/T-00b/` | Everything below builds on it, and it can leave the motors running |
| 3 | **T-00d** an alert in progress finishes | F7 | `reports/T-00d/` | Same files as slice 2 (`alerts.py`, `tts_worker.py`) |
| 4 | **T-03c1, T-03c2** camera alerts, spoken and vibrated | F0, F4 | `reports/T-03c1/` (this pass is attempt 2 there; attempt 1 was the interrupted build) | The camera is the wearer's eyes |
| 5 | **T-01a, T-01b** glasses board lost and back | F2 | `reports/T-01a/` | The motors |
| 6 | **T-04a, T-04b** earphones lost and back | F3 | `reports/T-04a/` | The speech output |
| 7 | **T-02b, T-02c** control panel lost and back (not T-02a) | F6 | `reports/T-02b/` | |
| 8 | **T-13** camera blocked, too dark, too bright | F5 | `reports/T-13/` | |
| 9 | **T-16** the failsafe checks on screen and in the terminal | status display | `reports/T-16/` | Reads the state of all the others |

Each slice has `brief-1.md` (`brief-2.md` for T-03c1), a stub `dev-report-1.md` that points at the author's report,
the Tester's `test-report-<n>.md`, and the Lead's `decision-<n>.md`. The `changes-<n>.patch` of a slice is
the whole-tree diff for the shared files; see "The baseline is shared" below.

## The baseline is shared

The tree before the demo track is `.claude/baselines/T-03c1/` (`src/`, `tests/`, `scripts/`; stamp 2026-10-18
08:15). It is used for **every** slice. Do not take a new one and do not touch it. Consequences:

- `for d in src tests scripts; do diff -ruN -x __pycache__ -x '*.pyc' .claude/baselines/T-03c1/$d $d; done`
  shows **all** the demo track's changes, not one slice's. Each brief lists the files and functions that
  belong to its slice ("Scope: this slice's share"). Hunks of another slice in the same file are not yours to
  judge, but **do** read them if they touch the code under test.
- The tree is committed in git (the user's commits `0df4bb2`, `b0d8d2b`, `9e36a81`), so `git status` is clean.
  Read-only git (`git log`, `git diff 51e9137 HEAD -- <file>`) shows history; never use git to get old code
  back.
- `docs/testing/tools/` is outside the baseline (it holds no `src/`, `tests/` or `scripts/`). Slice 1 has no
  diff to review; the Tester reads the tool whole.
- A red-before-green check ("does the new test fail without the feature?") is done in a scratch copy under
  `/tmp` built from the baseline's `src/` plus the new test files (never git).

## Common rules for every pass

1. **Independence.** Derive the checks from the requirements (`REQUIREMENTS.md`), the verbatim UAT cases
   (`uat-snapshot.yaml`) and the user's recorded decisions (`DISAGREEMENTS.md` D-15 to D-19), **not** from the
   author's report. Read the author's tests, but judge them: do they test the requirement or only the author's
   own choices? Write your own checks for each acceptance criterion in `tests/uat/` (permanent: the TC ID in
   the name, the workbook's expected result in the docstring) or `/tmp` (temporary).
2. **Callers exist (AGENTS.md Rule 7).** For every new function, class or hook in the slice, grep for a caller in
   `src/` that runs in the deployed program (not only in tests). "Implemented but never called" is this
   project's recurring bug (the heartbeat). Say where each caller is.
3. **Protected code.** Report any change to the vision algorithms (`core/depth_utils.py`, `core/haptics.py`, the
   detection processing in `pipeline/callbacks.py`), installed libraries, `firmware/`. The one known touch is
   T-16's overlay hunk in `callbacks.py`; slice 9 confirms it is display drawing only.
4. **Contracts and threading (Rule 2, Rule 4).** Queue item keys unchanged. Every worker honours
   `shutdown_event`; every wait has a timeout; nothing blocks a GStreamer callback; pipeline state changes go
   through `GLib.idle_add`. A `PatternPlayer`, `AlertScheduler` or `CameraViewWatch` call from a callback or
   worker must return at once.
5. **Mutation check, your own.** In a scratch copy under `/tmp`, remove one behaviour at a time and confirm a
   test fails. At least 10 mutants per slice, chosen from the acceptance criteria and **not copied from the
   author's list** (`evidence/F*-mutations.txt` shows theirs; read it afterwards to see what you missed). A
   survivor is a finding: either a missing test (say which) or a behaviour that does not matter (say why).
   Save the table in `evidence/`.
6. **Repeat the timing-sensitive tests.** Run each new test file of the slice 10 times in a row in each
   environment, once more with a CPU load running beside it (two busy loops under `nice`), and report any
   flake with its failure text. Known flake to watch: `tests/test_camera_alerts.py::test_t03c1_rule4_one_hook_call_takes_well_under_50_ms`
   (the author made it retry three times; judge whether the retry hides a real stall).
7. **Both environments, full suites,** under `nice -n 19`:
   - Poetry: `nice -n 19 ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q`
     (the author's baseline: 1056 passed, 217 skipped, 0 failed, about 5 min);
   - `my_hailo_env`: `nice -n 19 my_hailo_env/bin/python -m pytest tests/ -q`
     (1159 passed, 117 skipped, 0 failed, about 7.5 min).
   The `T00A_LONG=1` runs are opt-in and long (18 min); slice 3 requires them, the others need not.
   Record the actual result lines. A failure on its own is not yet a finding: run it alone and 10 times,
   and say whether it is flaky or deterministic.
8. **Do not disturb the user.** The Pi is shared with the user's rehearsal of the demo. Run everything heavy under
   `nice -n 19`; never leave a copy of the program running; stop what you start (by its PID).
9. **The hardware rule (the freeze is lifted, 2026-10-19 12:14).** Device-integration checks (the real camera,
   the Hailo chip, the real boards, the simulated unplug) are allowed **only if the device is free right
   then**: `systemctl is-active second-vision.service` prints `inactive`, and
   `pgrep -af "main.py|second_vision|demo_run|sv-main"` finds no copy of the program, and (for the Hailo chip)
   `sudo fuser -v /dev/hailo0` is empty. If anything is running: do not wait, do not stop it, mark that check
   `BLOCKED (device busy)` and carry on with the rest. Re-check before every device action, because the user
   may start the rehearsal at any time. sudo only as `.claude/tools/usb_sim.py` (and the one `fuser` form), and
   never `--for` longer than the check needs; end every simulated unplug with `attach`.
10. **Evidence levels and what "PASS" needs here.** The required evidence for this pass is `unit`,
    `mock-integration` and `gst` (real GStreamer, no device). **Device-integration is wanted where each brief
    names it and the device is free, but a device-integration check that could not run is `BLOCKED (device
    busy)` or `BLOCKED (needs a person)` and does not by itself make the overall result BLOCKED.** Say in the
    report's first lines which required-level checks passed and which optional device checks ran. The person's
    check (the real unplug, feeling, hearing) is `user-observed`; list it once, under "Manual steps needed",
    pointing at the part of `DEMO-CHECKLIST.md` that covers it, and do not re-invent the procedure.
11. **Defects.** Any reproducible failure of an acceptance criterion is a `FAIL` with: the exact steps (a command
    or a test), expected, actual, and the smallest reproduction you can make. Do not fix production code; the
    Lead returns it to a Developer.
12. **Questions for the user** go in your report under "Questions", in plain words, with options. Do not
    resolve a requirement ambiguity yourself.
13. **The report** is `reports/<folder>/test-report-<n>.md` from the template in `reports/README.md`, with
    evidence logs in `reports/<folder>/evidence/<YYYY-MM-DD>-<what>.log` (command, date and time,
    `git rev-parse --short HEAD`, "with uncommitted task changes" if any, and pytest's `-v -rA` output).

## How the Lead decides after each pass

- `PASS` and the quick checks clean (no write outside the repo, no `sudo` or process-running code added, the
  diff's file list inside the scope) -> **AWAITING-HANDS-ON** if the slice has a device level that only the
  person can confirm (all of them do, except slice 1), else **ACCEPTED**. The task goes to "Waiting for a
  person" in `MANUAL-TESTS.md`, pointing at `DEMO-CHECKLIST.md`.
- `FAIL` -> **RETURNED**: attempt *n+1* goes to a Developer on Sonnet (the freeze is lifted) with the
  Tester's reproduction quoted, then to the Tester again, who reruns the failed checks plus the regression
  suite.
- `BLOCKED` -> parked with the reason written down and the user asked.

## What none of these passes can show

Whether a sentence is heard in the earphones, whether a pattern is felt, whether the real board or panel
reconnects: those are the user's, in the rehearsal (`reports/DEMO/F8.md`, `DEMO-CHECKLIST.md`). A mocked test
never proves a physical part works (AGENTS.md, TRACEABILITY "Evidence").
