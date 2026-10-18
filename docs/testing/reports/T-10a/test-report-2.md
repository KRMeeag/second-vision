# T-10a · Verification report, attempt 2

- Date: 2026-10-16 (09:41 to 10:03 local)
- Overall: **PASS**. 21 checks: 21 passed, 0 failed, 0 blocked. One deviation from the brief for the Lead to confirm
  (strict-markers mechanism, check 2) and five observations (none is a failure of this task), listed under "Observations".
- Inputs: brief-1.md, brief-2.md, dev-report-1.md, dev-report-2.md, baseline `.claude/baselines/T-10a/`.
- Tester: sonnet. HEAD `cb0b20e` with uncommitted task changes.

## Not blocked

Nothing needs the user. No physical step is required by this task (it is an enabler; the simulated unplug is a software
switch, see "Simulated only").

## State at start (re-checked by me, ~09:41, and again before every device step)

- `systemctl is-active second-vision.service`: inactive. Stray pipeline process (`pgrep -af "main.py|second_vision|Parallel-Depth"`): none.
- `/dev/hailo0` present. Camera Arducam 0c45:0261 attached, nodes `/dev/video0..3`. `/tmp/usb_sim.log` absent (Pi rebooted 08:56).
- The three logs the cut-off Tester run left (`2026-10-16-device-roundtrip.log`, `2026-10-16-suite-my_hailo_env-run1.log`,
  `2026-10-16-suite-poetry-env.log`) were **not relied on and not modified**. Every log I rely on is named
  `2026-10-16-tester-attempt2-*.log` and was produced by me in this session.
- Note on the old `device-roundtrip.log` (`1 passed in 0.50s`): that speed looked too fast to me, so I measured it
  independently (check 16). A real roundtrip takes about 0.3 s, so that log was plausible.

## Diff review

`for d in src tests scripts; do diff -rqN -x __pycache__ .claude/baselines/T-10a/$d $d; done` lists exactly four files, all new:
`tests/conftest.py`, `tests/gst_helpers.py`, `tests/test_harness.py`, `scripts/uat_evidence.py`. Same four as dev-report-2.

- Changed but not reported: none. Outside the brief's scope: none. Nothing under `src/`, no existing test file touched
  (`git status`: only these four files plus the reports folder are untracked), `pyproject.toml` not modified.
- Protected code (vision algorithms, `core/depth_utils.py`, `core/haptics.py`, installed libraries, `firmware/`): untouched.
- `scripts/test_motor_packet.py` is identical to the baseline (see Observation 2 for why I looked).
- Interface contracts (Rule 2) and threading rules (Rule 4): not touched by this task (test-side code only; no worker, queue or callback).
- sudo in the diff (the Lead checks this): the only `"sudo"` literal in `tests/conftest.py` is `SUDO_LAUNCHER = ("sudo", "-n")`
  (a test asserts that), used only by `CameraUnplug` for the three shapes `sudo -n <tool> status|detach camera --for N|attach camera`.
  `scripts/uat_evidence.py` has no sudo; its only signal is `os.killpg` on the group of the pytest it started.
  `grep -n 'sudo\|usb_sim\|killpg\|systemctl'` over the three non-test files agrees.

## Checks

Levels: `unit` = no hardware; `mock-int` = real pytest/GStreamer processes, hardware replaced by a fake tool or fake command
results; `device-int` = the real camera, real `usb_sim.py`, real `sudo -n`.
Evidence files are in `docs/testing/reports/T-10a/evidence/` (prefix `2026-10-16-tester-attempt2-`, omitted below).

| # | What (criterion) | Method | Level | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Diff scope: four new files, nothing else, no protected code | baseline diff, `git status`, grep | unit | PASS | Diff review above |
| 2 | Brief-2 A: `gst`/`device` registered and a misspelt marker is an **error**, on the real repo settings (no `pyproject.toml` table, no ini), both environments | the live-run settings test in both full suites; a scratch project holding copies of the real `conftest.py`/`gst_helpers.py` and no ini file, run in both environments with and without `--strict-markers` | mock-int | PASS, **with a deviation** (see below) | `misspelt-marker.log`: both envs `exit(pytest)=2`, `ERROR collecting ... PytestUnknownMarkWarning: Unknown pytest.mark.gts`; with `--strict-markers` `'gts' not found in markers configuration option`; the clean file (gst, device, parametrize) exits 0 with no warning |
| 3 | No `PytestUnknownMarkWarning` or warnings summary in either full run | grep of all three full logs | unit | PASS | 0 lines `warnings summary`, 0 `PytestUnknownMarkWarning` in `suite-my_hailo_env-run1/run2.log`, `suite-poetry-env.log`. (The two `WARNING second_vision.pipeline.app ... Unknown pipeline_mode 'sideways'` lines are an existing test's captured log output.) |
| 4 | Full suite, `my_hailo_env`, run 1 and run 2 (same result twice) | `my_hailo_env/bin/python -m pytest tests/ -v -rA` twice | unit + mock-int | PASS | run 1 `414 passed, 1 skipped in 63.67s`; run 2 `414 passed, 1 skipped in 63.74s`; `suite-my_hailo_env-run1.log`, `...-run2.log` |
| 5 | Full suite, Poetry environment | `<Poetry python> -m pytest tests/ -v -rA` | unit | PASS | `388 passed, 27 skipped in 37.82s`: 22 `gst` (gi not installed), 4 `needs pytest-timeout`, 1 `device` (opt-in reason); `suite-poetry-env.log`. No failure or error. |
| 6 | The 295 existing tests still pass in both environments | counted PASSED lines outside `test_harness.py` | unit | PASS | 295 in each environment; 415 collected = 295 + 120 |
| 7 | Brief-2 B: without `--run-device` every `device` test **skips** with the reason (skipped, not deselected), and runs nothing | the real roundtrip test alone, then inside the full suite; `usb_sim.log` checked afterwards | mock-int | PASS | `device-without-flag.log`: `SKIPPED ... device test skipped: the device level is opt-in; add --run-device (it may detach the camera and use the Hailo chip) and stop second-vision.service first`; `/tmp/usb_sim.log` still absent. Same skip line in both full-suite logs. |
| 8 | With `--run-device` the other conditions still apply, in order, and the reason names the failing one | (a) real `pgrep` with a decoy process whose command line contains `second_vision`; (b) Poetry environment with the flag; (c) the Developer's fake-result tests; (d) my mutant m6 | mock-int | PASS | (a) `device-stray-process-decoy.log`: `UNAVAILABLE - another Second Vision process is running ... (pid 14168: python3 -c ... second_vision_decoy_by_tester)`, test SKIPPED, `usb_sim.log` line count unchanged (19 to 19), pytest's own process not flagged (R6). (b) `documented-commands.log` (a): skip reason is the missing gi. (c)(d) see Mutation check |
| 9 | `camera_unplug` makes no `sudo` call without the flag; and with the flag still obeys a failing condition | unit tests (fake runner), inner pytest with the fake tool, mutant m2 | unit / mock-int | PASS | tests pass in both full suites; mutant m2 fails 6 tests including `test_camera_unplug_makes_no_sudo_call_without_run_device` |
| 10 | Header line for the device level: `OFF (opt-in: add --run-device)`, `AVAILABLE`, `UNAVAILABLE - <reason>` | read the real headers | mock-int | PASS | OFF in both full-suite logs; AVAILABLE in `device-roundtrip-run1.log`; UNAVAILABLE in the decoy log |
| 11 | `uat_evidence.py --device` adds `--run-device`; the log header says the device level was requested; whole suite via the script | the real script over `tests/` with `--device`, then the mutant m7 | device-int | PASS | `evidence-script-device-full.log`: header `Device level requested: yes (--device: pytest was given --run-device)`, `device level: AVAILABLE`, pytest command contains `--run-device`, `415 passed, 0 skipped`, exit 0 (the real roundtrip ran inside it, see 17); mutant m7 fails 2 tests |
| 12 | Brief-2 C: `--max-minutes` stops a run that no per-test timeout can stop | real script over a scratch test that blocks SIGALRM, ignores SIGTERM and has a SIGTERM-ignoring child, `--max-minutes 0.1` | mock-int | PASS | `evidence-script-timeout-cap.log`: back after 16.3 s (6 s cap + 10 s grace, SIGKILL), exit **124**, `TIMED OUT after 0.1 minutes: the run was stopped` in the output and first in the case table; test process 9987 and its child 9992 both gone; no `uat_evidence_*` dir left. Mutant m3 (cap ignored) fails the end-to-end test |
| 13 | Criterion 2: real `Gst`/`GLib` and the library's `GStreamerApp` in every order | the Developer's tests; my own runs with the stub file first, last, and alone; the control (no conftest) | mock-int | PASS | `documented-commands.log` (b, b2, b4): 22 passed either order; `test_control_without_conftest_the_stub_file_wins` passes (it proves the stand-ins win without conftest), `test_real_modules_win_even_though_the_stub_file_is_collected_first` passes. No test in `test_pipeline_modes.py` changed behaviour (20 passed in both envs, R1) |
| 14 | Criterion 3: default 60 s `signal` timeout; user settings win; plugin off or absent causes no error or warning | header of real runs; `--timeout=5`, `PYTEST_TIMEOUT=7`, `-p no:timeout`, Poetry env with `@pytest.mark.timeout(3)` | mock-int | PASS | `sv-harness timeout: 60 s per test, method signal`; `5 s`, `7 s` with the overrides; `-p no:timeout` and the Poetry env run `@pytest.mark.timeout(3)` with `1 passed`, no warning. Hung-test and stuck-GLib-loop tests pass in the suite. Residual gap (R4) as documented, see Observation 6 |
| 15 | Criterion 4: every helper has its own `gst` test; the bus watch receives the posted error as the named element | the 22 `gst` tests | mock-int | PASS | `-m gst`: `22 passed, 393 deselected in 11.40s` (`documented-commands.log` c) |
| 16 | **Criterion 5 live: the real roundtrip, run 1** (`--run-device`, camera attached, service stopped) | `my_hailo_env/bin/python -m pytest tests/test_harness.py::test_simulated_unplug_roundtrip_returns_the_camera -v -rA --run-device`, with an independent read-only poller (20 ms) started first | **device-int** | PASS | `device-roundtrip-run1.log`: `1 passed in 0.52s` (call 0.28 s, setup 0.11 s, teardown 0.05 s). `device-roundtrip-run1-crosscheck.log`: poller saw `nodes[0123] authorized=1` then `nodes[----] authorized=0 STATE-FILE` (09:45:56.745), `authorized=1` (+60 ms), `nodes[0123]` back (+100 ms), state file removed (+200 ms). The tool's own log: `detached 1-1 ... re-attach in 10 s`, `attached ... nodes: /dev/video0..3`, then `attach: nothing detached by this tool` (the fixture's teardown attach). `status` afterwards: attached, no state file. 15 frames then captured with `gst-launch-1.0 v4l2src ... num-buffers=15 ! fakesink`, exit 0 |
| 17 | Criterion 5 live, run 2: the same roundtrip inside the whole suite through the evidence script | `scripts/uat_evidence.py --task T-10a --device --label tester-attempt2-evidence-script-device-full` | device-int | PASS | `evidence-script-device-full.log` `415 passed`; crosscheck file: detach 09:57:15, nodes back 09:57:16, state file gone; `status` attached |
| 18 | Criterion 5: teardown **always** re-attaches (failing test, timed-out test, exception, refused second detach, bad seconds), with the real tool | scratch tests using the real `camera_unplug` and `detach(60)` so that a quick return can only be the teardown | device-int | PASS | `device-failure-paths.log`: 5 tests FAILED on purpose, **no ERROR** (so `restore()` succeeded each time). `...-crosscheck.log`: after the 4 s timeout the camera was back at 09:48:38 (not 60 s later); the refused second detach logged `refused: already detached` and the camera was back at once; `detach(0)` raised `ValueError` before any command. A first attempt with conftest at the scratch root skipped all 5 with `the simulated-unplug tool ... is missing`: the missing-tool skip works and the path is resolved from conftest's own location |
| 19 | Criterion 6: the case table is never kinder than the tests (R5) and handles all outcomes | the real script over a scratch file with pass, skip, partial, fail, `gst`, `device`, xfail, dashed IDs, parametrize; both environments | mock-int | PASS | `evidence-script-case-table-my_hailo_env.log` and `...-poetry.log`: `TC-901-002 SKIP`, `TC-901-003 SKIP (partial: 1 passed, 1 skipped: ...)`, `TC-901-004 FAIL`, `TC-901-006 SKIP (device test skipped: the device level is opt-in ...)`, `TC-901-007 SKIP (expected failure: known problem)`, `TC-901-008 PASS` (2 params), `TC-901-005` PASS in my_hailo_env and `SKIP (gst test skipped: ... gi ...)` in Poetry; totals line and exit code 1 correct. Mutant m5 (skip counted as pass) fails 6 tests |
| 20 | Criterion 7: the documented commands work | ran each: Poetry `tests/ -q`; `tests/ -q`; `-m gst`; `-m device -rs` without and with the flag; `uat_evidence.py --device`; `--max-minutes` | mock-int / device-int | PASS | `documented-commands.log`, checks 4, 5, 11, 12, 15, 16. `-m "not gst and not device"`: `392 passed, 23 deselected` |
| 21 | Criterion 8: no leaks, no zombies, repeatable | /tmp, repo root, process and zombie listings before and after | unit | PASS (with notes) | No zombies; no pytest/uat_evidence/usb_sim/timer process left (the roundtrip's own `--for` timer fires silently 10 s later and was gone before I looked again); no `uat_evidence_*` temp dir from a real run; repo root unchanged except the git-ignored `hailort.log` (Observation 4); `/tmp/pytest-of-sv-rpi5` is pytest's normal `tmp_path` root; `/tmp/usb_sim.log` is the tool's own log. Runs 1 and 2 give the same result |

### Deviation for the Lead to confirm (check 2)

Brief-2 asked for `config.option.strict_markers = True`. I verified the Developer's claim independently:
a scratch `conftest.py` with exactly that line, one registered marker and one misspelt marker gives `2 passed, 1 warning` in **both**
environments (the marker lookup in `_pytest/mark/structures.py` lines ~619-622 reads `config.getini("strict_markers")`, never
`config.option`). The Developer's replacement, `config.addinivalue_line("filterwarnings", "error::pytest.PytestUnknownMarkWarning")` from
`pytest_configure`, does make a misspelt marker an error in both environments (check 2) and a real `--strict-markers` on the command line works
alongside it. Differences from `--strict-markers`: the message is `Unknown pytest.mark.gts - is this a typo?`, and
`-p no:warnings` or `-W ignore::pytest.PytestUnknownMarkWarning` switch the error off (`-p no:warnings` is reported in the header;
`-W ignore::...` is not, so the header would still say "is an error"; both are explicit user overrides). I judge the required
behaviour met; the mechanism differs from the brief's text.

### Mutation check (red-before-green by my own mutants)

One change per scratch copy of `tests/` and `scripts/` (under my scratchpad), then `tests/test_harness.py` with the real roundtrip
deselected so no mutant could touch the camera. The control is the unmutated copy. Evidence: `mutation-check.log`.

| Mutant | Result |
|---|---|
| control | `119 passed, 1 deselected` |
| m1 unknown marker no longer an error | 3 failed (the two settings tests and the `-p no:warnings` header test) |
| m2 device level not opt-in | 6 failed |
| m3 evidence script ignores `--max-minutes` | 1 failed (the end-to-end script test) |
| m4 `camera_unplug` teardown does not re-attach | 3 failed |
| m5 an all-skipped case counted as PASS | 6 failed |
| m6 device skip ignores the service check | 2 failed |
| m7 `--device` does not add `--run-device` | 2 failed |

All 7 mutants are caught. The Developer's tests test the requirements, not only their own choices.

## Regression

- `my_hailo_env/bin/python -m pytest tests/ -v -rA` (twice): `414 passed, 1 skipped in 63.67s` and `414 passed, 1 skipped in 63.74s`.
- Poetry: `388 passed, 27 skipped in 37.82s`.
- With the device level and the real roundtrip, through the evidence script: `415 passed in 64.20s`.
- The 295 existing tests: 295 passed in each environment.

## Edge cases and failure conditions tried

- The device test with and without the flag; with the flag and a stray process; with the flag in the environment without gi.
- Failing, timed-out, raising and double-detach tests against the real tool (check 18); `detach(0)`.
- A missing tool (skip with reason, check 18).
- The evidence script: a test nothing can stop (SIGALRM blocked, SIGTERM ignored, child process); a kill while the camera is
  detached (Observation 3); a mix of pass/skip/partial/fail/xfail/gst/device cases in both environments.
- Pytest-timeout plugin absent, disabled, overridden by option and environment variable.
- Marker typos with and without `--strict-markers`, `-p no:warnings`, `-W ignore`, `-o filterwarnings=ignore`.
- Order independence of the real-module pre-import (stub file first, last, alone).

## Failures

None.

## Blocked

None.

## Observations (not failures of this task; for the Lead to decide)

1. **A stale `--for` timer from an earlier detach cuts short a later detach (tool, `.claude/tools/usb_sim.py`).** Reproduced with the
   real tool using only hook-allowed shapes (`stale-timer-edge-case.log`): `detach --for 10`, `attach` at once, `detach --for 60`
   three seconds later. The first detach's timer fired at +10 s and re-attached the camera that the second detach had asked to
   keep away for 60 s: `detached 09:50:09`, `timer: re-attaching the camera 09:50:16` (7 s instead of 60 s). The timer is a
   `sleep N; ... attach` shell that nothing cancels. Effect: any test that detaches within `DEFAULT_DETACH_SECONDS` (10 s) of an
   earlier `camera_unplug` test can have its detach ended early, and a later task that needs the camera gone for a while (T-03a
   style) must not start within the previous test's `--for` window. The fixture cannot fix this; the tool could (a token in the
   state file that the timer checks). Not part of this diff.
2. **A bare `pytest` (no path) from the repo root imports `scripts/test_motor_packet.py`, which at import time opens `sys.argv[1]` or
   `/dev/serial0` and writes motor packets (80/150/220) to the glasses ESP32.** Pre-existing: the file is identical to the baseline. It
   matters here because brief-1 proposed `testpaths = ["tests"]` in `pyproject.toml` (a protection) and brief-2 dropped it, and every
   documented command passes `tests/` explicitly. I saw it because my path-less `pytest -m device --collect-only` and `pytest --run-device -m device` checks
   (`run-device-without-path.log`) stopped with `SerialException: could not open port -m` / `--collect-only` (the script takes
   its first command-line argument as the port name, so no real port was opened); **I did not run any bare `pytest` with no arguments at
   all** (then the script would have opened `/dev/serial0`). Suggestion for the user, who owns `pyproject.toml`: `[tool.pytest.ini_options] testpaths = ["tests"]`,
   or rename or guard the bench script.
3. **After a time-out of an evidence run during a `camera_unplug` test, the camera stays detached until the tool's own timer fires.**
   The Developer states this; I verified it live (`timeout-while-detached.log`): `uat_evidence.py --device ... --max-minutes 0.05`
   killed pytest 3.3 s in (exit 124), `status` then showed `Detached by this tool`, and the tool's timer re-attached the camera at
   `10:01:47` (12 s after the detach), `timer: re-attaching the camera`. The safety net works as designed.
4. **`hailort.log` in the repo root grows by about 20 KB per run** (131 bytes to 167 KB across my runs). Not caused by this task: the
   unchanged `test_pipeline_modes.py` alone writes an identical 20,832-byte `hailort.log` in the baseline and in the current tree
   (compared in a scratch working directory). Both `hailort*.log` and `tests/hailort.log` (0 bytes, created 06:14 by the Developer's
   run) are git-ignored (`*.log`).
5. The "detach for 10 s" in the roundtrip is, in effect, a 0.1 s outage (the test attaches as soon as it has seen the nodes vanish; the 10 s is
   the tool's safety timer). That matches the brief's wording. A later test that needs the camera gone for a while must hold the
   detach itself (sleep, then attach).
6. Residual gap (Developer's R4, accepted in brief-2): a test stuck in native code that ignores signals and is not a GLib loop is not stopped by the
   per-test timeout, and running `pytest` directly has no wall-clock cap. Only the evidence script has one (`--max-minutes`, verified in check 12).
7. Cosmetic: the fixture teardown always calls `attach camera` again, so the tool's log gets `attach: nothing detached by this tool`
   after every successful roundtrip.

## Simulated only

- "Service active": tested with fake `systemctl` results (Developer's tests and my mutant m6), not with a real active service (I may not start one).
- `sudo -n` asking for a password: tested with a fake runner. Passwordless `sudo -n` and the tool's real `status` output were exercised
  live (checks 16-18).
- The camera "unplug" is the kernel's software `authorized` flag, not a pulled cable. A person's confirmation per device and scenario is
  still needed by later tasks whose criterion is a physical unplug; this task needs none.

## Test-only changes

- Permanent (`tests/uat/`): **none**.
- Temporary, all under my session scratchpad `/tmp/claude-1000/-home-sv-rpi5-Projects-Second-Vision-worktrees-kenth/4928a64f-7d74-4298-8a1a-cee17a4ec396/scratchpad/`
  (not removed; they are outside the repo): `runlog.sh`, `poller.py` (read-only observer), `mutate.py` and `run_mutants.sh` with the
  mutant copies `ctl m1..m7`, scratch projects `marker_proj`, `brief_recipe`, `unplug_proj`, `unplug2`, `unplug3`, `ev_proj`, `hang_proj`, `cwd_new`, `cwd_base`,
  `decoy_check.sh`.
- Removed: `/tmp/uat_evidence_wxlzkz1v`, an orphan temp directory recreated by the orphaned pytest of my own mutant m3 (the unmutated script cleans up
  its temp directory; none exists after any real run). Processes I started (poller x2, decoy) were stopped by PID.

## Deviations recorded

None in `DEVIATIONS.md` (no UAT case was run with different steps; this task has no UAT case). The strict-markers mechanism is a deviation
of the Developer from the brief, described above for the Lead.

## Manual steps needed from a person

None for this task.

## Evidence files (all in `docs/testing/reports/T-10a/evidence/`, prefix `2026-10-16-tester-attempt2-`)

`suite-my_hailo_env-run1.log`, `suite-my_hailo_env-run2.log`, `suite-poetry-env.log`, `device-without-flag.log`,
`device-roundtrip-run1.log`, `device-roundtrip-run1-crosscheck.log`, `device-failure-paths.log`, `device-failure-paths-crosscheck.log`,
`device-stray-process-decoy.log`, `evidence-script-device-full.log`, `evidence-script-device-full-crosscheck.log`,
`evidence-script-case-table-my_hailo_env.log`, `evidence-script-case-table-poetry.log`, `evidence-script-timeout-cap.log`,
`misspelt-marker.log`, `mutation-check.log`, `documented-commands.log`, `run-device-without-path.log`, `stale-timer-edge-case.log`,
`timeout-while-detached.log`, `final-device-state-and-usb_sim-log.log`.
The cut-off run's three logs (no prefix) are untouched and not relied on.

Final device state at 10:03: `status` shows the camera attached with nodes `/dev/video0..3`, no `/run/usb_sim_camera`, no pending timer,
`authorized=1`, 15 frames captured, `second-vision.service` inactive. `/tmp/usb_sim.log` holds every action of this report
(`final-device-state-and-usb_sim-log.log`).
