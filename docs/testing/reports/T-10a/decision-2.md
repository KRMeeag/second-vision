# T-10a · Lead decision, attempt 2

- Date: 2026-10-16
- Decision: **ACCEPTED**
- Based on: brief-1.md, brief-2.md, dev-report-1.md, dev-report-2.md, test-report-2.md, own diff review.
- Attempt 1 was RETURNED by the Lead's review before the Tester ran (see brief-2): the settings
  could not be written to `pyproject.toml` by any role, and the device level would have detached the
  camera on a plain suite run.

## Acceptance criteria

T-10a is an enabler, so its criteria restate the board note (TASK-BOARD.md, "T-10"), not a REQ.

| # | Met? | Evidence (report + level) |
|---|---|---|
| 1 Markers and automatic skips | Yes. `device` is also opt-in (`--run-device`, Lead decision in brief-2) | test-report-2 checks 2, 7, 8, 10: unit + mock-integration; decoy stray-process run uses the real `pgrep` |
| 2 Real modules win in any file order | Yes | checks 6, 13: 295 existing tests pass in both environments; mutation-checked |
| 3 Default timeout, no warning without the plugin | Yes, with a stated residual gap (a native hang that is neither signal-interruptible nor a GLib loop) | check 14; Developer's measured table in dev-report-1 |
| 4 GStreamer helpers | Yes | check 15: 22 `gst` tests |
| 5 Simulated-unplug fixture | Yes. **First live use of `usb_sim.py` by the workflow**: the camera returned each time | checks 16, 17, 18: device-integration; independent 20 ms poller saw `/dev/video0..3` vanish and return about 0.1 s later; 15 frames captured afterwards; fixture re-attached after a failing test, a timed-out test, an exception and a refused second detach |
| 6 Evidence run | Yes | checks 11, 12, 19: header, case table that never reads a skip as a pass, `--device`, `--max-minutes` stops a SIGTERM-ignoring run (exit 124) |
| 7 Commands documented | Yes, in `tests/conftest.py`'s docstring; copied to docs/testing/README.md by the Lead | check 20 ran each command |
| 8 No regressions, no leaks | Yes | checks 4, 5, 21: `414 passed, 1 skipped` (`my_hailo_env`, twice), `388 passed, 27 skipped` (Poetry), `415 passed` through the evidence script with the device level; no warnings |

## Deviation accepted

`config.option.strict_markers = True` (brief-2) has no effect in pytest 9.1.1; the Tester
reproduced that. The Developer's replacement (`error::pytest.PytestUnknownMarkWarning` added to
`filterwarnings`, plus marker registration) gives the required result, a misspelt marker is a
collection error, in both environments. It is switched off by `-p no:warnings`, and the header says
so. Accepted: it meets the outcome of the criterion, with no `pyproject.toml` change.

## User-observed evidence

None. T-10a adds no device behaviour for a person to judge.

## Outstanding risks accepted, and why

- **Stale `--for` timer in `.claude/tools/usb_sim.py` (Tester observation 1).** An earlier
  `detach --for N` leaves a timer that re-attaches whichever later detach is current after N
  seconds. In the Tester's run a 60 s detach was re-attached after 7 s. The tool is `.claude/`,
  which the Lead never edits. **Mitigation for T-03a's device tests**: use a short `--for` (at most
  20 s), call `attach` explicitly when done, and leave at least that long between a detach and the
  next test that depends on the camera staying away; a test that needs the camera absent must
  assert it is still absent before relying on it. **For the user (summary): the tool should
  cancel the old timer on attach, or tag timers with the detach they belong to.**
- **A bare `pytest` (no path) imports `scripts/test_motor_packet.py`,** which opens `/dev/serial0`
  and sends motor packets (pre-existing, unchanged from the baseline). Every command in this
  workflow passes `tests/`. **For the user**: add `testpaths = ["tests"]` under
  `[tool.pytest.ini_options]` in `pyproject.toml`. No agent can write that file.
- **The "10 s detach" in the roundtrip test is really about 0.1 s,** because it attaches as soon as
  the nodes vanish. Tests that need the camera gone for longer must hold the detach themselves.
- **The residual hang gap** (see criterion 3) and `hailort.log` growth are pre-existing or stated.
- **Leftover logs** from the cut-off first Tester run (`2026-10-16-device-roundtrip.log`,
  `-suite-my_hailo_env-run1.log`, `-suite-poetry-env.log`) are unverified and superseded by the
  `tester-attempt2-` logs. They are not relied on.
- `check_outside_writes.py` listed one file in the base checkout: `second-vision-repo/hailort.1.log`
  (07:58:47). It is a git-ignored HailoRT log, written by whatever ran there at that time (the
  boot service runs the base checkout); no agent of this workflow was working in the base checkout.
  Not a finding.

## Records updated

- TASK-BOARD.md: T-10a ACCEPTED; active slot cleared.
- TRACEABILITY.md: no case affected (enabler); totals unchanged.
- docs/testing/README.md: new "Test levels" section with the commands.
- documentation/TASKS.md: n/a (T-10 is not a top-level box there).
- DISAGREEMENTS.md / REQUIREMENTS.md: none.

## For the user to commit

Changes of this task (also `reports/T-10a/changes-2.patch`):
- `tests/conftest.py` (new)
- `tests/gst_helpers.py` (new)
- `tests/test_harness.py` (new)
- `scripts/uat_evidence.py` (new)
- `docs/testing/reports/T-10a/` (briefs, reports, decision, patch, evidence logs)
- `docs/testing/README.md` (test levels), `docs/testing/TASK-BOARD.md`
