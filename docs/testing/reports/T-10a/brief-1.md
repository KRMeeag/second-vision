# T-10a · Test levels, GStreamer helpers, simulated-unplug fixture, evidence run · Brief, attempt 1

- Date: 2026-10-16
- Requirements: none. This is an enabler (TASK-BOARD.md, "T-10"): it adds no device behaviour.
  It lets later tasks check REQ-CON, REQ-PNL, REQ-PWR and REQ-DTA automatically instead of
  asking a person. T-03a's tests need it.
- UAT cases: none directly.
- Priority / depends on: P1 / the user installed pytest 9.1.1 and pytest-timeout in `my_hailo_env`
  (done 2026-10-16; the Lead checked `my_hailo_env/bin/python -m pytest --version`).
- Developer: **developer-sonnet**. Why: test-side code only, no change to `src/`, no Hailo or
  GStreamer lifecycle work, clear acceptance criteria (board note on T-10).
- Tester model: sonnet.
- Baseline: .claude/baselines/T-10a/ (taken 2026-10-16, before attempt 1).
- Starting point, measured by the Lead on 2026-10-16: `tests/` has 295 passing tests, and both
  environments give the same result:
  - Poetry environment (`~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q`): `295 passed`;
  - `my_hailo_env/bin/python -m pytest tests/ -q`: `295 passed`.

  The real `gi`, `Gst` 1.26.2, `hailo` and `hailo_apps...gstreamer_app` import fine in
  `my_hailo_env`.

## Goal

After this task, a later task can write three kinds of automated test and run each with one
command:

| Level | Marker | Needs | Runs in |
|---|---|---|---|
| unit | none | nothing | either environment |
| GStreamer | `gst` | real GStreamer bindings, no devices | `my_hailo_env` |
| device | `device` | the Hailo chip, camera or boards, and the boot service stopped | `my_hailo_env` |

A `gst` or `device` test **skips by itself, with a plain-words reason**, when its needs are not
met. A test that hangs **fails after a timeout** instead of freezing the run. There is also a
helper for posting a pipeline error and running the GLib loop briefly, a fixture for the
simulated camera unplug, and one command that runs the automated tests and saves a dated
evidence log listing each case ID with PASS / FAIL / SKIP.

## The problem to solve first (the Lead found it by reading)

`tests/test_pipeline_modes.py` installs **fake** `gi`, `gi.repository`, `hailo`,
`hailo_apps...buffer_utils` and `hailo_apps...gstreamer_app` modules into `sys.modules` at
import time, with `setdefault`, so they apply only if nothing real was imported first. Pytest
imports every test file while collecting, so in a whole-suite run in `my_hailo_env` those fakes
win unless something imports the real modules earlier. A `gst` test needs the **real** `Gst`,
`GLib`, and (for T-03a) the real `GStreamerApp` as the base of `SecondVisionApp`.

The fix belongs in a `tests/conftest.py` that, when the real bindings import, imports them
**before** any test file is collected, so the existing `setdefault` stubs become no-ops. Do
not rewrite `test_pipeline_modes.py`'s stubs, and do not change any existing test's assertions.
If you find you cannot avoid touching an existing test file, stop and say why in the report.
In the Poetry environment (no GStreamer) the existing stubbed behaviour must be unchanged.

## In scope

- `tests/conftest.py` (new): marker registration, automatic skips, the real-module pre-import,
  the default timeout, shared fixtures.
- `tests/gst_helpers.py` (new, an ordinary module the tests import; rootless `tests/` is on
  `sys.path` under pytest's default import mode): the GStreamer helpers.
- `tests/test_harness.py` (new): tests of the harness itself, listed under "Acceptance criteria".
- `scripts/uat_evidence.py` (new): the evidence-run command.
- `pyproject.toml`: **only** a `[tool.pytest.ini_options]` table (markers, default options,
  test paths). **Do not touch any dependency section** (`dependencies`,
  `[tool.poetry.group.*]`): `poetry.lock` is committed, and changing a dependency list makes it
  stale and breaks `poetry install` for the team. The user decides about adding `pytest-timeout`
  there; say in your report what you would put.
- Nothing under `src/`.

## Out of scope: must not change

- Everything under `src/`, `firmware/`, `models/`, `hardware/`, the Excel workbook.
- Installed libraries and environments (`my_hailo_env`, the Poetry environments). No `pip`, no
  `poetry`, no `apt`.
- Existing test files' contents (the eight `tests/test_*.py` files).
- Other files under `scripts/`.
- The vision algorithms (AGENTS.md; nothing here goes near them).
- Mock mode: no code you write may start mock generators or pass `--mock`.
- No running real hardware: you do not unplug anything, start `main.py`, or stop services (see
  "Boundaries" below for the sudo exception, which the *Tester* uses, not you).

## Acceptance criteria

1. **Markers and automatic skips.**
   - `gst` and `device` are registered, and `--strict-markers` is on in the pytest settings, so a
     misspelt marker is an error.
   - A `gst` test is skipped, with a reason stating what is missing, when `gi` / `Gst` cannot be
     imported or initialised.
   - A `device` test is skipped, with the reason, when any of these holds:
     - the `gst` condition;
     - `/dev/hailo0` does not exist;
     - `systemctl is-active second-vision.service` reports an active-like state
       (`active`, `activating`, `reloading`, `deactivating`);
     - `pgrep -af "main.py|second_vision|Parallel-Depth"` finds a process that is **not** the
       pytest process or its parents or children (so a stale pipeline holding the Hailo chip, or
       the boot service's process, is seen).
   - The skip decision is made by small functions that take no hidden state, so they can be
     unit-tested with a fake `subprocess` result. The only commands the harness may run are
     `systemctl is-active …` and `pgrep …`. Nothing it runs may start, stop, restart or kill
     anything, and none may use `sudo` (except the fixture in criterion 5).
   - In the Poetry environment, `pytest tests/ -q` shows the `gst` / `device` tests as
     **skipped**, never failed or erroring.
2. **The real modules win, in any order.**
   - In `my_hailo_env`, a whole-suite run (`my_hailo_env/bin/python -m pytest tests/ -q`) gives
     every test the **real** `gi.repository.Gst` and `GLib`, and `second_vision.pipeline.app`
     imports against the **real** `GStreamerApp`, regardless of file order. Prove it with a
     `gst` test that asserts both (e.g. `Gst.version()` works; the class's `__mro__` includes
     the library's `GStreamerApp` from `hailo_apps…gstreamer_app`, not the stub).
   - All 295 existing tests still pass in both environments.
   - The pre-import must not require the Hailo device, root or the camera. It may log, never
     print noise on a passing run.
3. **Default timeout.**
   - Every test has a default timeout of 60 s when `pytest-timeout` is installed, with the
     `thread` method not required. Pick the method that works for a test stuck in a GLib loop
     or a native call and say which, with why.
   - A test that exceeds it **fails** with a clear timeout message; the run goes on.
   - Prove it with a test that hangs on purpose in a *separate scratch file* under `/tmp`, run by
     `pytest` against `tests/conftest.py`'s settings (or an equivalent you explain), not in the
     committed suite.
   - When `pytest-timeout` is not installed (the Poetry environment), nothing errors and no
     warning appears for it.
4. **GStreamer helpers** (`tests/gst_helpers.py`), each with a `gst` test of its own:
   - post an error message from a named element in a pipeline onto the bus, as that element
     (`Gst.Message.new_error` with the element as source), so a handler that looks at
     `message.src` sees the element;
   - post an end-of-stream message the same way;
   - run the GLib main loop briefly, with a hard upper bound, and either for a fixed time or
     **until a predicate becomes true** (returns whether it did; never waits unbounded);
   - build a minimal pipeline whose source element is named `source` (e.g. `videotestsrc` or
     `fakesrc` into `fakesink`) for tests that need one, and tear it down in a way that does
     not hang.

   Show with a test that a bus watch really receives the posted error on the main loop and that
   `message.src.get_name()` is the element's name.
5. **Simulated-unplug fixture** (`tests/conftest.py`, marker `device`): a fixture `camera_unplug`
   that wraps `sudo -n .claude/tools/usb_sim.py detach camera --for <s>` and
   `sudo -n .claude/tools/usb_sim.py attach camera`, with `status` as a check.
   - It must run **exactly** those command shapes (the role hook allows nothing else), with the
     repo-relative path to the tool resolved from `conftest.py`'s own location.
   - It skips (reason in words) if the tool is missing, `sudo -n` would ask for a password
     (check with the tool's `status`, which needs no root), the service is active, or the device
     marker conditions fail.
   - It **always re-attaches on teardown**, even when the test fails or times out, and waits
     (bounded) until a `/dev/video*` node of the camera is back.
   - Unit-test it with a **fake tool** (a small script under `tmp_path` and a path override), not
     the real one: the commands it issues, the teardown re-attach after a failing test, and the
     skip reasons.
   - One `device` test, `test_simulated_unplug_roundtrip_returns_the_camera`, uses the fixture
     for real: detach for 10 s, assert the camera's video nodes disappear within 5 s, attach,
     assert they return within 15 s. **You do not run it.** The Tester does, as the first live
     use of the tool. In your report say it was not run.
6. **Evidence run** (`scripts/uat_evidence.py`): one command, run with `my_hailo_env/bin/python`.
   - `my_hailo_env/bin/python scripts/uat_evidence.py --task <ID> [--tests PATH ...] [--out DIR]`
     runs pytest over `tests/` (or the given paths) with `-v -rA` and a JUnit XML to a temp
     file, and writes `DIR/<YYYY-MM-DD>-<what>.log` where `DIR` defaults to
     `docs/testing/reports/<ID>/evidence/` (created if absent). Pick `<what>` sensibly
     (`unit`, `all`, or a name from `--label`).
   - The log's header gives: the command line, date and time, `git rev-parse --short HEAD`,
     and the words "with uncommitted task changes" when `git status --porcelain` is not empty;
     the Python executable and `pytest --version`; and the environment's relevant facts
     (`second-vision.service` state, whether the `gst` / `device` levels were available).
   - Then pytest's full output, then a **case table**: one line for each distinct UAT case ID
     found in test names or node IDs (patterns `tc024_003`, `tc_024_003`, `TC-024-003` →
     reported as `TC-024-003`), with `PASS`, `FAIL` or `SKIP (reason)`, aggregated per case: all
     tests of the case passed → PASS; any failed or errored → FAIL; all skipped → SKIP; the
     tests' names listed beside it. A final line: totals, and the pytest exit code.
   - Its own exit code equals pytest's, so a CI-style caller can use it.
   - It runs only `pytest` (as `sys.executable -m pytest`) and read-only `git` commands plus
     `systemctl is-active`. No sudo, no service control.
   - Unit-test the parts that don't need pytest to be launched: parsing the case ID, the
     aggregation, the header text, using canned JUnit XML.
7. **Documentation of the commands.** Put the exact command for each level in the module
   docstring of `tests/conftest.py` (unit, `gst`, `device`, evidence run, the Poetry-environment
   command). Do not edit files under `docs/testing/` (the Lead copies them).
8. **No regressions, no leaks.**
   - The full suite passes in both environments, with the test counts you report.
   - The suite leaves no zombie process, no `/tmp` file outside `tmp_path`, and no change
     outside `tests/`, `scripts/uat_evidence.py` and `pyproject.toml`.
   - Running the suite twice in a row gives the same result.

## Verification expectations

| Criterion | Minimum evidence level | Hardware needed |
|---|---|---|
| 1, 2, 3, 4, 6, 7, 8 | unit / mock-integration (real GStreamer, no devices) | none |
| 5, the real unplug | device-integration | the camera, the boot service stopped, `sudo` for `usb_sim.py` only |

Criterion 5's real roundtrip is run by the **Tester** (not the Developer) and is the first live
use of the tool. The Tester also saves its evidence logs under `reports/T-10a/evidence/`.

## References

- AGENTS.md: Rules 1 (no library edits), 4 (threading: GLib state changes only on the main
  loop; this task just reads the bus), 6 (Hailo docs first, say where you checked), 7.
- Hailo / GStreamer facts you rely on must be checked, not remembered:
  - bus message construction: GStreamer docs for `Gst.Message.new_error`, `Gst.Message.new_eos`
    and `Gst.Bus.post` (online: <https://gstreamer.freedesktop.org/documentation/gstreamer/gstmessage.html>);
  - the library's bus handler: `my_hailo_env/lib/python3.13/site-packages/hailo_apps/python/core/gstreamer/gstreamer_app.py`
    lines 475–505 (`bus_call`, `on_eos`), read only;
  - the workbench `/home/sv-rpi5/Projects/Test-Projects/ai-test/hailo-apps/.hailo/memory/common_pitfalls.md`
    for GLib loop and signal pitfalls.
- `.claude/tools/usb_sim.py` (read only, local): the command shapes and what `status` prints.
- `tests/test_pipeline_modes.py` lines 1–110: the stubs, so you know what the pre-import must
  beat.
- Existing test style: plain pytest functions, docstrings stating what the test pins.

## Open risks: stop and report instead of guessing

- **R1, hidden coupling.** Pre-importing the real modules in `conftest.py` changes what
  `test_pipeline_modes.py` runs against (the real `GStreamerApp` instead of the stub). Run it
  in `my_hailo_env` and tell the Lead if any test there changes behaviour. Do not "fix" them.
- **R2, import side effects.** Importing real `hailo_apps` may touch the Hailo runtime, create
  files, or need environment variables from `/usr/local/hailo/resources/.env`. Report what you
  see. A `device` marker that needs the chip must not be satisfied merely by the import
  succeeding.
- **R3, `Gst.init` twice or in the wrong thread.** Keep `Gst.init(None)` idempotent.
- **R4, timeouts vs GLib loops.** `pytest-timeout`'s `signal` method cannot interrupt a thread
  stuck in native code. If neither method can stop such a hang under your helpers, say so
  rather than claim it is bounded.
- **R5, a skip hiding a pass.** A skipped `gst` or `device` test must never read as a pass in
  the evidence log. Check this explicitly.
- **R6, the pgrep self-match.** `pgrep -af` matches its own shell command. Exclude the
  harness's own process tree and any `pgrep` itself.

## For attempt > 1: failures to fix

Not applicable (attempt 1).
