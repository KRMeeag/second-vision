# T-10a · Test levels and evidence run · Brief, attempt 2

- Date: 2026-10-16
- Requirements / cases / priority / Developer / Tester model: unchanged from [brief-1](brief-1.md).
  Developer: developer-sonnet. Tester: sonnet.
- Baseline: .claude/baselines/T-10a/ (unchanged; the per-task diff still shows the whole task).
- Why attempt 2: the Lead's review of [dev-report-1](dev-report-1.md) found one reproducible
  failure and one hazard. The Tester has **not** run yet; it runs after this attempt.

## What attempt 1 got right (keep it, do not rework it)

`tests/conftest.py`, `tests/gst_helpers.py`, `tests/test_harness.py` (98 tests) and
`scripts/uat_evidence.py` are accepted in design: the pre-import, the skip functions, the
`signal` timeout with the GLib guard, the helpers, the `camera_unplug` fixture and its fake-tool
tests, the case table, and the `SKIP (partial: …)` rule for a case with some tests passed and
some skipped. The `--` passthrough in `uat_evidence.py` is accepted too. Change only what is
listed below.

## Changes required

### A. Settings without `pyproject.toml` (the failure)

Neither the Developer nor the Lead may write `pyproject.toml` (the role hook allows only
`src/`, `tests/`, `scripts/` and reports to the Developer). So **do not use it**. Move the
settings into `tests/conftest.py`, in a `pytest_configure` hook:

- register the markers `gst` and `device` with `config.addinivalue_line("markers", …)`, with
  the descriptions from dev-report-1's proposed table;
- turn `--strict-markers` on programmatically (set `config.option.strict_markers = True`;
  confirm in the pytest 9.1.1 source, and by a test, that a misspelt marker is then an
  **error**, in both environments);
- `testpaths` and `-rs` are not needed; drop them.

Then the two settings tests (`test_gst_and_device_markers_are_registered_and_strict_markers_is_on`,
`test_a_misspelt_marker_is_an_error_under_the_project_settings`) must pass on the **real repo**
settings, with no `pyproject.toml` change and no scratch ini file, in both environments, and
there must be no `PytestUnknownMarkWarning` in either run.

### B. The device level becomes opt-in (the hazard)

Dev-report-1 found that a plain `my_hailo_env/bin/python -m pytest tests/ -q` would, on this
machine, really detach the camera for 10 s. Nobody running the whole suite by habit expects
that, and the Developer and Tester must not do it by accident. New rule:

- Add a command-line option `--run-device` (`pytest_addoption` in `tests/conftest.py`).
- **Without it, every `device` test skips**, with the reason in words, for example
  `device test skipped: the device level is opt-in; add --run-device (it may detach the camera
  and use the Hailo chip) and stop second-vision.service first`.
- **With it**, the existing conditions apply unchanged, in the same order (gst, `/dev/hailo0`,
  service, stray process), and the skip reason says which one failed.
- `camera_unplug` follows the same rule: without `--run-device` it skips for that reason
  before it ever runs `sudo`.
- The pytest header line for the device level says `OFF (opt-in: add --run-device)` when the
  flag is absent, and `AVAILABLE` / `UNAVAILABLE - <reason>` when it is given.
- `scripts/uat_evidence.py` gets `--device`, which adds `--run-device` to the pytest command.
  Its log header says whether the device level was requested.
- Update the tests that assumed the old rule, and add tests that: a `device` test skips
  without the flag; the flag does not bypass a failing condition (service active → still
  skips); `camera_unplug` makes no `sudo` call without the flag (assert on the fake runner).
- Update the commands in `tests/conftest.py`'s docstring: the device level is
  `my_hailo_env/bin/python -m pytest tests/ -q --run-device`, and the evidence run with the
  device level is `… scripts/uat_evidence.py --task <ID> --device`.
- The real roundtrip test stays unrun by you.

### C. The evidence run must not freeze

A hung test that no timeout can stop (the residual gap in dev-report-1) would freeze an
evidence run forever. Give `scripts/uat_evidence.py` an overall wall-clock cap:

- `--max-minutes N`, default 30. When it elapses the script terminates **the pytest process it
  started** (SIGTERM, then SIGKILL after a 10 s grace; its own child and process group only,
  never by name), writes everything captured so far, adds a line `TIMED OUT after N minutes:
  the run was stopped` to the log and the case table, and exits with code 124.
- Unit-test it with a scratch test that sleeps (a `tmp_path` file; use a tiny cap, e.g. via an
  internal seconds argument, so the test takes a second or two).

## Out of scope

Everything out of scope in brief-1 stays so, including `pyproject.toml` (not touched) and
`src/`. Do not change any existing test file.

## Acceptance criteria

Brief-1's criteria 1 to 8 still apply, with these refinements:

1. (criterion 1) `device` tests skip unless `--run-device` is given, and then only when every
   condition holds. Registration and `--strict-markers` come from `conftest.py` and are proven
   on the real repo settings in both environments.
2. (criterion 5) `camera_unplug` makes no `sudo` call without `--run-device`.
3. (criterion 6) The evidence script has `--device` and `--max-minutes`, tested as above.
4. (criterion 8) The full suite passes in both environments **with no scratch ini file and no
   `pyproject.toml` change**: report the real result lines. Without `--run-device`, the real
   roundtrip test is *skipped* (not deselected), so the report must show it skipped.

## Report

Write `dev-report-2.md` in the template, with the real result lines of:
- `my_hailo_env/bin/python -m pytest tests/ -q` (twice);
- the Poetry-environment run;
- the baseline-diff file list;
- the proof that a misspelt marker is an error in both environments;
- the proof that no `PytestUnknownMarkWarning` appears.

Still answer: what you would add to the dev dependencies (the user decides). Do not run the
real unplug test or any `usb_sim.py` command; the Tester does.

## For attempt > 1: failures to fix

From my review of dev-report-1 (reproducible by running the suite on the real repo settings):
`2 failed, 364 passed, 27 skipped, 21 warnings` in the Poetry environment, the 2 being the
settings tests above; and the device hazard (B).
