# T-10a · Developer report, attempt 2

- Date / model: 2026-10-16 / developer-sonnet
- Summary: Moved the pytest settings into `tests/conftest.py` (markers registered, an unregistered marker is an
  error) so no `pyproject.toml` change is needed; made the device level opt-in with `--run-device`; added
  `--device` and `--max-minutes` to `scripts/uat_evidence.py`. **One deviation the Lead must confirm:** the
  brief's recipe `config.option.strict_markers = True` does not work in pytest 9.1.1 (measured and read in the
  source, below), so the error comes from a public `filterwarnings` entry instead. The real unplug test and
  `usb_sim.py` were **not run**.

## Not blocked

Nothing needs the user. One decision for the Lead is in "Deviations" (the strict-markers mechanism).

## Inspected before changing

- Attempt 1's `tests/conftest.py` (606 lines), `scripts/uat_evidence.py` (310) and `tests/test_harness.py`
  (1066), read in full; `brief-1`, `brief-2`, `dev-report-1`, `docs/testing/reports/README.md` (template).
- pytest 9.1.1 source, **identical files in both environments** (`cmp` on each: `main.py`, `config/__init__.py`,
  `config/argparsing.py`, `mark/structures.py`, `warnings.py`):
  - `_pytest/main.py` lines 83-88: `--strict-markers` is an `OverrideIniAction` with `ini_option="strict_markers"`.
  - `_pytest/config/argparsing.py` lines ~470-505: that action only appends `"strict_markers=true"` to
    `option.override_ini`; it sets `option.strict_markers = True` as a side effect that nothing reads.
  - `_pytest/config/__init__.py` lines ~1531-1538 (`determine_setup(... override_ini=ns.override_ini ...)`, which
    builds `_inicfg`) and `_getini` (~1722): the overrides are folded into the ini values while the command line is
    parsed, before conftest's `pytest_configure` runs.
  - `_pytest/mark/structures.py` lines 619-622: the marker lookup reads `config.getini("strict_markers")`
    (fallback `getini("strict")`). **It never reads `config.option.strict_markers`.**
  - `_pytest/warnings.py` lines 36 and 97: `config.getini("filterwarnings")` is read in every phase, including
    `when="collect"`, so a line added in `pytest_configure` is in force while test modules are imported.
- Callers: `device_skip_reason` is called by `pytest_runtest_setup`, `pytest_report_header` and the
  `device_level_reason` fixture (all three now pass the flag); `camera_unplug_skip_reason` only by the
  `camera_unplug` fixture and tests. `uat_evidence.run_pytest` and `build_header` have no other callers.
- Machine state when running: `second-vision.service` inactive, no pipeline process, no zombie.

## Hailo docs consulted

Not pipeline-related: nothing in this attempt touches GStreamer, HailoRT or `hailo_apps`. The facts relied on are
about pytest and are from its installed source (above) and from scratch runs (below).

## Files changed

| File | What changed | Why |
|---|---|---|
| `tests/conftest.py` | `pytest_configure` registers the `gst` and `device` markers and adds `error::pytest.PytestUnknownMarkWarning` to `filterwarnings`; new `pytest_addoption` (`--run-device`); `device_opt_in_reason`, `device_level_line`, `run_device_requested`; `device_skip_reason` takes keyword `run_device` (default False, checked first); the setup hook and the `device_level_reason` fixture pass the flag; `camera_unplug_skip_reason` checks the device conditions before the tool; header lines for the device level (`OFF (opt-in: add --run-device)`) and for markers; docstring commands updated | Brief A, B |
| `scripts/uat_evidence.py` | `--device` (adds `--run-device`), `--max-minutes` (float, default 30); pytest runs in its own process group; `stop_process_group` (SIGTERM, SIGKILL after 10 s, own group only); `PytestRun`; timed-out line in the log and the case table; exit 124; header lines "Device level requested" and "Time limit"; docstring | Brief B, C |
| `tests/test_harness.py` | 22 new tests (98 to 120), several updated (listed below); the scratch ini files are gone (see below) | All three |
| `tests/gst_helpers.py` | unchanged | - |

Checked with the README's baseline diff: `diff -rqN -x __pycache__ .claude/baselines/T-10a/{src,tests,scripts}` lists
exactly four files, the same four as attempt 1:

```
Files .claude/baselines/T-10a/tests/conftest.py and tests/conftest.py differ
Files .claude/baselines/T-10a/tests/gst_helpers.py and tests/gst_helpers.py differ
Files .claude/baselines/T-10a/tests/test_harness.py and tests/test_harness.py differ
Files .claude/baselines/T-10a/scripts/uat_evidence.py and scripts/uat_evidence.py differ
```

Nothing under `src/`, no existing test file, `pyproject.toml` untouched (`git status` shows it unmodified).
`check_outside_writes.py` against the stamp: nothing changed in installed libraries, environments, other
checkouts or the workbench.

## Deviations from the brief (read this one)

**A. `config.option.strict_markers = True` does not make a misspelt marker an error.** The brief asked me to confirm
this in the source and by a test; I did both and it fails the confirmation.

Scratch run (a `conftest.py` that does exactly what the brief says, one registered marker, one misspelt marker),
in both environments, same result:

```
..                                                                       [100%]
=============================== warnings summary ===============================
test_x.py:4: PytestUnknownMarkWarning: Unknown pytest.mark.gts - is this a typo? ...
2 passed, 1 warning in 0.00s
```

Cause: see the source lines above. The same scratch with `config.addinivalue_line("filterwarnings",
"error::pytest.PytestUnknownMarkWarning")` gives, in both environments:

```
ERROR collecting test_x.py
E   pytest.PytestUnknownMarkWarning: Unknown pytest.mark.gts - is this a typo? ...
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.11s
```

So `pytest_configure` does that (public API: `Config.addinivalue_line` and `pytest.PytestUnknownMarkWarning`).
Differences from a real `--strict-markers`: the message is "Unknown pytest.mark.gts - is this a typo?" instead of
"'gts' not found in `markers` configuration option", and `-p no:warnings` switches it off. Both are handled: the
header line says `sv-harness markers: gst, device registered; a misspelt marker is an error` or, with
`-p no:warnings`, `... is only a warning (the warnings plugin is off)` (tested). A real `--strict-markers` on the
command line also works alongside it (the settings tests pass with and without it, both environments). Other
routes I rejected: writing `config._inicfg` / `config._inicache` (private, and different in pytest 8 and 9), and a
`pyproject.toml` table (not allowed).

**B. The `device` marker description** is dev-report-1's text plus "opt-in: skipped unless --run-device is given",
because the marker is now opt-in.

**C. `camera_unplug_skip_reason` order changed** from "tool exists, then device conditions" to "device conditions,
then tool exists", so that without `--run-device` the reason is always the opt-in one, and nothing (not even a file
check) comes before it. No existing test depended on the old order.

**D. The two settings tests** keep their names but no longer read an ini file: there is none. They read the live
run's settings (`getini("markers")`, `getini("filterwarnings")`, and a real `pytest.mark.gts` lookup that must raise),
and the misspelt-marker test runs a scratch project holding **copies of the real `conftest.py` and `gst_helpers.py`
and no ini, no `pyproject.toml`, no `tox.ini`, no `setup.cfg`**, found by pytest the way it finds the real one.

## Tests added or updated

"Fails on the baseline" is meant as "fails on attempt 1's behaviour": the baseline folder has none of these files,
and I kept no copy of attempt 1. I rebuilt its behaviour in a scratch copy under the scratchpad (the same files with
two lines changed: no marker registration / no filter in `pytest_configure`, and `device_opt_in_reason` returning
None), and for the time limit a copy of the script with `max_seconds=None`. Real roundtrip test deselected in every
red run.

| Test | What it checks | Fails on attempt-1 behaviour? |
|---|---|---|
| `test_gst_and_device_markers_are_registered_and_strict_markers_is_on` (rewritten) | live run: both markers registered, the filter is in `filterwarnings`, a `gts` lookup raises, `gst` / `device` do not | yes (both envs) |
| `test_a_misspelt_marker_is_an_error_under_the_project_settings` (rewritten) | scratch project, real conftest, no ini: exit non-zero, "ERROR collecting", `PytestUnknownMarkWarning`, nothing passed | yes (both envs): exit 0, "2 passed, 1 warning" |
| `test_the_registered_markers_give_no_unknown_marker_warning_under_the_project_settings` (new) | same scratch project, `gst`, `device`, `timeout`, `parametrize`: exit 0, no word "warning" | yes (both envs) |
| `test_without_the_warnings_plugin_the_header_says_a_misspelt_marker_is_only_a_warning` (new) | `-p no:warnings` reported in the header; normal run says "is an error" | yes (both envs) |
| `test_the_device_level_is_opt_in_and_without_the_flag_nothing_is_looked_at_or_run` (new) | no flag: the exact opt-in reason, and the fake runner records zero commands | yes |
| `test_run_device_only_lifts_the_opt_in_and_never_bypasses_a_failing_condition` (new) | flag given: all clear gives None; service active, gst, chip, stray process each still skip with their own reason | no (passes: it describes behaviour that was already right) |
| `test_device_reason_is_the_first_failing_condition_in_order` (updated) | order with the flag: gst, chip, service, stray | n/a |
| `test_run_device_requested_is_true_only_for_the_flag`, `test_the_device_level_header_line_says_off_available_or_unavailable` (new) | flag reading (an unexpected error reads as "not asked for"); header text, conditions not asked without the flag | n/a |
| `test_a_device_test_skips_without_run_device_and_says_why` (new) | inner pytest: the full reason sentence, `1 passed, 1 skipped`, header says OFF | yes (my_hailo_env: the device test ran and raised) |
| `test_run_device_does_not_bypass_a_failing_condition_through_the_hook`, `test_the_device_header_line_with_the_flag_is_available_or_unavailable_with_a_reason` (new) | inner pytest with the flag and `gi` blocked: skipped, reason is the missing bindings, not the opt-in | n/a |
| `test_camera_unplug_makes_no_sudo_call_without_run_device` (new) | fake runner: zero calls without the flag; control: with the device reason clear the same function does run exactly `sudo -n <tool> status` | yes |
| `test_the_camera_unplug_fixture_skips_before_running_anything_without_run_device` (new) | the real fixture with the fake tool: skips with "opt-in", the fake tool's call log does not even exist | yes |
| `test_the_camera_unplug_fixture_with_the_flag_still_obeys_a_failing_condition` (new) | flag + blocked `gi`: skips, no tool call | n/a |
| `test_gst_and_device_tests_skip_with_a_reason_when_the_bindings_are_missing[gst/device]` (updated) | the device case now passes the flag so the missing bindings are what skips it | n/a |
| `test_the_harness_only_runs_the_commands_it_is_allowed_to` (updated) | the kill scan is split: `conftest.py` and `gst_helpers.py` may not signal at all; `uat_evidence.py` has exactly one `os.killpg(` and every `_signal_group(` call takes `process.pid`; still no sudo | n/a |
| `test_header_says_whether_the_device_level_was_requested_and_the_time_limit`, `test_header_has_every_required_fact` (updated helper) | new header lines | n/a |
| `test_the_device_option_adds_run_device_to_the_pytest_command_and_nothing_else` (new) | `pytest_command(..., device=True)` inserts `--run-device` once, otherwise identical | n/a |
| `test_device_adds_run_device_to_pytest_and_the_log_says_it_was_requested` (new) | the real script twice over a scratch `device` test (conftest as plugin): without `--device` the case is `SKIP (... opt-in ...)` and no flag in the pytest command; with it the header says "yes" and the command has the flag | n/a |
| `test_max_minutes_must_be_a_positive_number` (new) | `0`, `-1`, `nan`, `inf`, `soon` are refused (exit 2), nothing created | n/a |
| `test_a_run_that_outlasts_the_time_limit_is_stopped_with_the_output_so_far` (new) | stopped, output kept and echoed, own process group (pgid = pid, not ours), process gone | no such option before |
| `test_a_run_inside_the_time_limit_is_left_alone_and_keeps_its_exit_code` (new) | exit code 3 survives; `max_seconds=None` means no limit | n/a |
| `test_the_time_limit_stops_the_children_in_the_group_and_nothing_outside_it` (new) | the child of the hung process dies too; the test run itself is untouched | n/a |
| `test_a_process_that_ignores_sigterm_gets_sigkill_after_the_grace` (new) | exit code -9, elapsed between limit+grace and 15 s | n/a |
| `test_ctrl_c_stops_the_run_it_started_and_goes_on` (new) | `KeyboardInterrupt` reaches the caller and the group is gone | n/a |
| `test_the_script_stops_a_hung_pytest_writes_the_log_and_exits_124` (new) | whole script, a scratch test that sleeps 120 s, `--max-minutes 0.1`: back in under 60 s, exit 124, `TIMED OUT after 0.1 minutes: the run was stopped` in the output and first in the case table, the hung test's name kept, last line says exit code 124, the pytest process is gone | yes: without the cap it waited until pytest-timeout fired at 25 s, and the pytest it had started kept running |

Existing end-to-end test `test_evidence_script_end_to_end_on_scratch_tests` gained assertions for the two new header lines.

Red runs (attempt-1-like copy, real roundtrip deselected):

```
$ (scratch copy) my_hailo_env/bin/python -m pytest tests/test_harness.py --deselect <roundtrip> -k "<the new tests>"
8 failed, 1 passed, 111 deselected, 21 warnings in 5.51s
$ (scratch copy) <Poetry python> -m pytest ... (same selection)
8 failed, 1 passed, 111 deselected, 21 warnings in 2.55s
```

The "21 warnings" are the same 21 `PytestUnknownMarkWarning`s dev-report-1 reported. The 9th test (the one that passes) is the flag-lifts-only-the-opt-in test above.
The time-limit red run (the copy with no cap): `Failed: Timeout (>25.0s) from pytest-timeout.` for
`test_the_script_stops_a_hung_pytest_writes_the_log_and_exits_124`; the sleeping pytest it left behind was stopped by
me by the PID in the test's marker file (31841, a process the test started).

## Commands run and their actual results

`PP=~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python`. No scratch ini file, no
`pyproject.toml` change in any of these.

```
$ my_hailo_env/bin/python -m pytest tests/ -q          (run 1)
SKIPPED [1] tests/conftest.py:415: device test skipped: the device level is opt-in; add --run-device (it may detach the camera and use the Hailo chip) and stop second-vision.service first
414 passed, 1 skipped in 63.29s (0:01:03)

$ my_hailo_env/bin/python -m pytest tests/ -q          (run 2)
414 passed, 1 skipped in 63.20s (0:01:03)

$ $PP -m pytest tests/ -q                              (Poetry environment)
388 passed, 27 skipped in 36.95s
```

(An earlier pair of runs, before one assertion in a settings test was widened, gave the same counts.) Total
collected 415 = 295 existing + 120 in `test_harness.py`. In the Poetry environment the 27 skips are: 22 `gst` tests
(gi not installed), 4 tests that need pytest-timeout, and the one real `device` test, whose reason there is the
opt-in one. In `my_hailo_env` the one skip is the real roundtrip test, **skipped, not deselected**, with the reason
shown above.

**Misspelt marker is an error, both environments** (the committed test, verbose; settings tests only):

```
$ my_hailo_env/bin/python -m pytest tests/test_harness.py -v -k "markers_are_registered or misspelt_marker or registered_markers_give_no or warnings_plugin_the_header"
sv-harness markers: gst, device registered; a misspelt marker is an error
configfile: pyproject.toml
test_gst_and_device_markers_are_registered_and_strict_markers_is_on PASSED
test_a_misspelt_marker_is_an_error_under_the_project_settings PASSED
test_the_registered_markers_give_no_unknown_marker_warning_under_the_project_settings PASSED
test_without_the_warnings_plugin_the_header_says_a_misspelt_marker_is_only_a_warning PASSED
4 passed, 116 deselected in 3.32s
$ $PP ...same...          4 passed, 116 deselected in 1.67s
```

(`configfile: pyproject.toml` is pytest 9.1.1 anchoring on the file; it has no pytest table and I did not touch it.
The scratch-project test asserts that its own run printed no `configfile` line.) The same selection widened to
`-k "header or markers or misspelt"` (10 tests, including the four above) also passes with `--strict-markers`
added on the command line, in both environments (`10 passed` in each of the four runs: 2 environments, with and
without the flag).

**No `PytestUnknownMarkWarning`, both environments** (whole suite output saved and searched):

```
my_hailo_env:  414 passed, 1 skipped in 63.34s   lines containing "warning" (any case): 0; PytestUnknownMarkWarning: 0
Poetry:        388 passed, 27 skipped in 36.88s  lines containing "warning" (any case): 0; PytestUnknownMarkWarning: 0
```

**Leaks** (after the final runs): `ls /tmp` and the repo root listing identical before and after; 0 zombie processes;
no `pytest` or `uat_evidence` process left (the hang tests clean up and a test checks it). `hailort.log` in the repo
root grows during a run, as noted in dev-report-1 (git-ignored, an existing test talks to the Hailo runtime).

**Header of a real run** (`my_hailo_env`, no flag): `sv-harness device level: OFF (opt-in: add --run-device)`. With
`--collect-only -m device --run-device` the roundtrip test is collected (`1/415 tests collected`); nothing was run.
`conftest.device_skip_reason(run_device=True)` returns `None` on this machine right now, i.e. with the flag the
roundtrip **would** run (service inactive, `/dev/hailo0` present, no stray process).

**A live timed-out evidence run** (scratch test sleeping 300 s, `--max-minutes 0.1`): back in 6 s, exit 124:

```
Time limit:            0.1 minutes (--max-minutes)
...
TIMED OUT after 0.1 minutes: the run was stopped
UAT CASE TABLE
TIMED OUT after 0.1 minutes: the run was stopped
No usable JUnit XML was produced (FileNotFoundError ...); see pytest's output above.
TOTALS: 0 passed, 0 failed, 0 skipped (0 tests); cases: 0 PASS, 0 FAIL, 0 SKIP (0 cases); pytest exit code 124
```

## Acceptance criteria: self-check, not approval

| # | How it is addressed | Evidence level reached |
|---|---|---|
| 1 (refined) | `device` tests skip unless `--run-device` (reason sentence as the brief's); with it the old conditions apply in the old order and the reason names the failing one (fake-result tests + inner pytest with `gi` blocked). Registration and the unknown-marker error come from `conftest.py`, proven on the real repo settings (live-run test) and on a no-ini scratch project holding the real files, in both environments. **The error is a warning-turned-error, not `--strict-markers` (Deviation A).** | unit / mock-integration |
| 5 (refined) | `camera_unplug` skips for the opt-in reason before any command: the fake runner records 0 calls; the real fixture with the fake tool leaves the tool's call log absent. Real roundtrip **not run** | unit / mock-integration; device level not reached |
| 6 (refined) | `--device` and `--max-minutes` exist and are tested: command construction, header lines, an end-to-end run each, group kill, SIGKILL escalation, Ctrl-C, exit 124, the log lines | unit / mock-integration |
| 8 (refined) | Both environments pass with no scratch ini and no `pyproject.toml` change (result lines above); the real roundtrip shows as skipped in `my_hailo_env`; run twice, no leaks | unit |
| 1-4, 7 (brief-1) | unchanged from attempt 1, still covered by the same tests; the docstring commands are updated for `--run-device`, `--device`, `--max-minutes` | as attempt 1 |

## Not done, limitations

- `test_simulated_unplug_roundtrip_returns_the_camera` **not run**, and no `usb_sim.py` command of any kind was run
  by me. Still never exercised live: the `sudo -n <tool> status` call in `camera_unplug_skip_reason`, the parsing of
  the tool's real `status` output, `wait_for_nodes` on real `/dev/video*`.
- A timed-out log has no per-case verdicts: a killed pytest writes no JUnit XML. The tests that finished are visible
  in pytest's `-v` output inside the log, but the case table only carries the `TIMED OUT` line and the "no usable
  JUnit XML" note. The brief asked for the line, not for recovery from the text, and I did not add that.
- After a time-out (or Ctrl-C) during a `camera_unplug` test, pytest dies without running fixture teardown, so the
  camera is not re-attached by the fixture; only the tool's own `--for N` timer re-attaches it (10 s in the one test).
  If a sudo-run `usb_sim.py` is in the killed group, `killpg` may not be allowed to signal it (root-owned); that is
  caught and ignored. After any timed-out `--device` run, check `sudo .claude/tools/usb_sim.py status`.
- SIGKILL goes to the group even if pytest left promptly on SIGTERM, to catch a child that ignored SIGTERM, so a
  straggler gets less than the full 10 s. (`stop_process_group` docstring says so.) Pid reuse inside that window is
  not a practical concern.
- The residual gap from dev-report-1 (a test stuck in native code that ignores signals and is not a GLib loop) is
  still not stopped by the per-test timeout, but now cannot freeze an evidence run for more than `--max-minutes`.
  Running pytest directly, with no evidence script, has no such cap.
- `-p no:warnings` switches off the unknown-marker error (the header says so); a `-W ignore::pytest.PytestUnknownMarkWarning`
  on the command line would too, and nothing reports that.
- `hailort.log` growth during a suite run is not new (dev-report-1).
- Out-of-brief changes, kept small: the `-- ` passthrough and `SKIP (partial: ...)` from attempt 1 (accepted by the
  brief); the `Device level requested` / `Time limit` header lines; the header line for markers; `COLUMNS=400` in the
  inner pytest runs so skip reasons are not cut at 80 columns.

## Untested or simulated behaviour

- Everything about the real camera, as in attempt 1: the unplug, the nodes going and returning, passwordless `sudo -n`,
  the tool's real `status` text. The fixture and teardown are tested against a fake tool and fake command results only.
- The `device` skip conditions are tested with fake `systemctl` / `pgrep` results and (attempt 1) one live `pgrep` check.
  Nothing in attempt 2 added a live device check.
- "The service is active, with `--run-device`, still skips" is proven with a fake `systemctl` result, not with a real
  active service (I may not start one).
- The `--device` end-to-end test runs the scratch `device` test for real in `my_hailo_env` if the machine meets all
  conditions; that test only does `pass`.

## Risks and questions for the Lead

1. **Please confirm Deviation A** (warning-as-error instead of `--strict-markers`). If you want the literal
   `--strict-markers` behaviour, the only ways are a `pyproject.toml` `[tool.pytest.ini_options]` table (the user)
   or private pytest attributes; I recommend neither.
2. The Tester should run the roundtrip with `--run-device` (`my_hailo_env/bin/python -m pytest tests/ -m device -v -rs
   --run-device`) or through `scripts/uat_evidence.py --task T-10a --device`, with `second-vision.service` stopped, and
   check `status` afterwards. On this machine the conditions are met now.
3. What I would add to the dev dependencies (the user decides; `poetry.lock` must be regenerated by them): in
   `[tool.poetry.group.dev.dependencies]`, `pytest-timeout = ">=2.3"` (2.4.0 is installed in `my_hailo_env`). Optionally
   raise `pytest = ">=8.0.0"` to `">=9.1"` because the markers/filter behaviour above was verified only on 9.1.1
   (the `filterwarnings` route is also valid on pytest 8, but I did not run it there). Without `pytest-timeout` in the
   Poetry environment nothing breaks: the timeout tests skip and no warning appears.
4. Should the evidence script's case table recover per-test outcomes from the `-v` text after a time-out? Not done.
