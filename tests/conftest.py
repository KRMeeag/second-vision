"""
Test harness for Second Vision: test levels, automatic skips, timeouts, shared fixtures.

Three kinds of automated test, each run with one command from the repository root:

    level      marker   needs                                        runs in
    unit       none     nothing                                      either environment
    GStreamer  gst      the real GStreamer bindings, no devices      my_hailo_env
    device     device   Hailo chip / camera / boards, service stopped my_hailo_env, OPT-IN

The device level is OPT-IN. Without `--run-device` every `device` test is skipped (it may detach
the camera and use the Hailo chip, so nobody gets it by running the whole suite by habit).

Commands (repository root; `PY` is `my_hailo_env/bin/python`):

    unit only (what the Poetry environment can do; gst and device tests skip by themselves):
        ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q
        (in the base checkout, not a worktree:  poetry run pytest tests/ -q)
    unit + gst; the device tests are SKIPPED (opt-in), nothing touches a device:
        my_hailo_env/bin/python -m pytest tests/ -q
    gst level only:
        my_hailo_env/bin/python -m pytest tests/ -m gst -v
    the device level too (opt-in; the boot service must be stopped first, the user does that):
        my_hailo_env/bin/python -m pytest tests/ -q --run-device
    device level only:
        my_hailo_env/bin/python -m pytest tests/ -m device -v -rs --run-device
    evidence run: runs the tests and saves a dated log with a PASS / FAIL / SKIP line per UAT case:
        my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a [--tests PATH ...] [--label NAME] [--out DIR]
        (log goes to docs/testing/reports/<task>/evidence/<date>-<label>.log)
    the evidence run with the device level (adds --run-device to pytest):
        my_hailo_env/bin/python scripts/uat_evidence.py --task <ID> --device
        (--max-minutes N stops a frozen run after N minutes, default 30; the script then exits 124)

A `gst` or `device` test never fails because its needs are missing: it is SKIPPED, and the
reason is in the output (`-rs` shows it, and the evidence log's case table repeats it).
A skipped test is never a pass.

What this file does
-------------------
* Registers the `gst` and `device` markers and makes a misspelt marker an ERROR (pytest_configure
  below). There is no pytest settings table in pyproject.toml, so the settings live here. In
  pytest 9.1.1 `config.option.strict_markers = True` does NOT do it (`--strict-markers` is an ini
  override that is read once at start-up; the marker lookup reads the ini value), so the error
  comes from `filterwarnings = error::pytest.PytestUnknownMarkWarning`, added with the public
  `config.addinivalue_line`. `-p no:warnings` switches that off; the report header says so.
* Adds the `--run-device` option (the device level is opt-in, see above).
* Imports the REAL gi / Gst / GLib / hailo / hailo_apps ... gstreamer_app modules BEFORE any
  test file is collected, when they exist. tests/test_pipeline_modes.py installs stand-ins with
  `sys.modules.setdefault(...)` at import time; with the real modules already loaded those calls
  do nothing, so every test sees the real Gst / GLib and `second_vision.pipeline.app` is built on
  the library's real GStreamerApp, whatever the file order. Where the real modules do not exist
  (the Poetry environment) nothing is imported and the stand-ins work exactly as before.
  The import needs no Hailo device, no root and no camera (it opens no device file).
* Skips `gst` / `device` tests with a reason in words (small functions below, each taking the
  thing it asks about as an argument so it can be unit-tested with a fake result).
* Gives every test a default timeout when pytest-timeout is installed (see DEFAULT_TIMEOUT_SECONDS).
* Provides the `gst_pipeline` and `camera_unplug` fixtures.

Timeouts: which method, and why
-------------------------------
The default is pytest-timeout's `signal` method (SIGALRM). Tried on this Pi, in my_hailo_env, against
tests that hang on purpose: it fails the test with "Timeout (>Ns) from pytest-timeout." and the run goes
on for a Python sleep, a busy loop, a lock wait, and native calls the signal interrupts (libc sleep/pause).
It does NOT stop a test sitting inside `GLib.MainLoop().run()` (PyGObject 3.50's run() never gives
Python the chance to run the handler): that test froze until killed. The `thread` method does stop it,
but only by `os._exit(1)` of the whole pytest process, so the run does NOT go on and no summary is
written. So:
  - `signal` is the default (a hung test fails, the run continues);
  - the autouse `_glib_main_loop_guard` fixture below closes the GLib gap: it makes every
    `GLib.MainLoop.run()` inside a test quit at the test's time limit and fail the test;
  - what neither can stop is a thread stuck in native code that is neither interruptible by a signal
    nor a `GLib.MainLoop.run()` (for example a driver call that never returns, or a loop run through
    ctypes). Only `@pytest.mark.timeout(N, method="thread")` ends that, and it ends the whole run
    with a stack dump. Such a hang is NOT bounded by this harness.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:               # pytest's default import mode already does this
    sys.path.insert(0, str(TESTS_DIR))

import gst_helpers  # noqa: E402  (needs TESTS_DIR on sys.path first)

log = logging.getLogger("second_vision.tests.harness")

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_TIMEOUT_METHOD = "signal"
GUARD_MARGIN_SECONDS = 0.5     # the GLib guard quits loops this much before the limit

SERVICE_NAME = "second-vision.service"
ACTIVE_STATES = ("active", "activating", "reloading", "deactivating")
HAILO_DEVICE = "/dev/hailo0"
PGREP_PATTERN = "main.py|second_vision|Parallel-Depth"
COMMAND_TIMEOUT_SECONDS = 10

USB_SIM_RELATIVE = Path(".claude") / "tools" / "usb_sim.py"
USB_SIM_TOOL = REPO_ROOT / USB_SIM_RELATIVE
SUDO_LAUNCHER = ("sudo", "-n")
DEFAULT_DETACH_SECONDS = 10
RESTORE_WAIT_SECONDS = 20.0

RUN_DEVICE_FLAG = "--run-device"
DEVICE_OPT_IN_REASON = ("the device level is opt-in; add --run-device (it may detach the camera "
                        "and use the Hailo chip) and stop second-vision.service first")
MARKER_LINES = (
    "gst: needs the real GStreamer Python bindings (my_hailo_env), no devices; "
    "skipped with a reason when they are missing",
    "device: needs the Hailo chip, the camera or the boards, and second-vision.service stopped; "
    "opt-in: skipped unless --run-device is given, and with a reason when a need is not met",
)
# Why a filter and not `config.option.strict_markers = True`: see "What this file does" above.
UNKNOWN_MARKER_IS_AN_ERROR = "error::pytest.PytestUnknownMarkWarning"
UNKNOWN_MARKER_ENFORCED = pytest.StashKey[bool]()


# ============================================================
# Pre-import of the real modules (before any test file is collected)
# ============================================================
REAL_MODULES = (
    "hailo",
    "hailo_apps.python.core.common.buffer_utils",
    "hailo_apps.python.core.gstreamer.gstreamer_app",
)


def preimport_real_modules(importer: Callable[[str], object] = importlib.import_module):
    """
    Import the real GStreamer bindings and hailo modules, if they exist.

    Returns `{module name: None if imported, else "ErrorType: message"}`, with `"gi"` first.
    If the GStreamer bindings are missing nothing else is tried (they would fail the same
    way) and the dict says so. A module that fails to import is left alone, so the
    stand-in in tests/test_pipeline_modes.py still covers it.
    """
    outcome: dict[str, str | None] = {}
    reason = gst_helpers.gst_unavailable_reason()
    outcome["gi"] = reason
    if reason is not None:
        return outcome
    for name in REAL_MODULES:
        try:
            importer(name)
            outcome[name] = None
        except Exception as exc:  # noqa: BLE001 - any failure just means "keep the stand-in"
            outcome[name] = f"{type(exc).__name__}: {exc}"
    return outcome


PREIMPORT = preimport_real_modules()
for _name, _error in PREIMPORT.items():
    log.debug("real module %s: %s", _name, _error or "imported")


# ============================================================
# Skip decisions: small functions, the thing they ask about is an argument
# ============================================================
def gst_skip_reason() -> str | None:
    """None if the real GStreamer bindings are usable; otherwise why not, in words."""
    return gst_helpers.gst_unavailable_reason()


def hailo_device_skip_reason(exists: Callable[[str], bool] = os.path.exists) -> str | None:
    """None if /dev/hailo0 exists; otherwise a reason. (Existing is all this can tell.)"""
    if not exists(HAILO_DEVICE):
        return f"the Hailo chip is not present ({HAILO_DEVICE} does not exist)"
    return None


def service_skip_reason(run: Callable = subprocess.run) -> str | None:
    """
    None if `systemctl is-active second-vision.service` says it is not running.

    Active-like states (active, activating, reloading, deactivating) give a reason. If
    systemctl cannot be run or says nothing, the answer is "cannot tell", which is also a
    reason to skip: a device test must not run next to a service that may own the devices.
    """
    try:
        result = run(["systemctl", "is-active", SERVICE_NAME], capture_output=True,
                     text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot tell whether {SERVICE_NAME} is running (systemctl failed: {exc})"
    state = (result.stdout or "").strip()
    if not state:
        return f"cannot tell whether {SERVICE_NAME} is running (systemctl printed nothing)"
    if state in ACTIVE_STATES:
        return (f"{SERVICE_NAME} is {state}: it owns the camera, the Hailo chip and the serial "
                "ports, so it must be stopped first (the user does that)")
    return None


def _read_ppid(pid: int, proc: str) -> int | None:
    """Parent pid from `<proc>/<pid>/stat`; None if the process is gone or unreadable."""
    try:
        text = Path(proc, str(pid), "stat").read_text()
        # "pid (comm) S ppid ..." - comm may hold spaces and parentheses, so split after the last ")".
        return int(text[text.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def process_tree_pids(pid: int | None = None, proc: str = "/proc") -> set[int]:
    """`pid` (default: this process), all of its ancestors, and all of its descendants."""
    pid = os.getpid() if pid is None else pid
    parents: dict[int, int] = {}
    try:
        names = os.listdir(proc)
    except OSError:
        names = []
    for name in names:
        if name.isdigit():
            ppid = _read_ppid(int(name), proc)
            if ppid is not None:
                parents[int(name)] = ppid
    tree = {pid}
    cursor = pid                                   # ancestors: follow the parent chain upwards
    while cursor in parents and parents[cursor] > 0 and parents[cursor] not in tree:
        cursor = parents[cursor]
        tree.add(cursor)
    descendants = {pid}                            # descendants of `pid` only, not of its ancestors
    grew = True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in descendants and child not in descendants:
                descendants.add(child)
                grew = True
    return tree | descendants


def parse_pgrep(output: str) -> list[tuple[int, str]]:
    """`pgrep -af` output ("PID command line" per line) as [(pid, command line)]."""
    found = []
    for line in output.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            found.append((int(pid), command.strip()))
    return found


def stray_process_skip_reason(own_pids: set[int] | None = None,
                              run: Callable = subprocess.run) -> str | None:
    """
    None if no pipeline process is running other than this test run.

    Runs `pgrep -af "main.py|second_vision|Parallel-Depth"`, drops the pytest process, its
    parents and children (`own_pids`, default: process_tree_pids()) and any pgrep, and gives a
    reason naming what is left: a stale pipeline holding the Hailo chip, or the boot service's
    process. This pattern also matches any unrelated process whose command line contains
    "main.py"; the reason shows the command so a person can judge.
    """
    own = process_tree_pids() if own_pids is None else own_pids
    try:
        result = run(["pgrep", "-af", PGREP_PATTERN], capture_output=True, text=True,
                     timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot tell whether a pipeline process is running (pgrep failed: {exc})"
    if result.returncode == 1:                      # pgrep: no process matched
        return None
    if result.returncode != 0:
        return (f"cannot tell whether a pipeline process is running (pgrep exited "
                f"{result.returncode}: {(result.stderr or '').strip()})")
    others = [(pid, cmd) for pid, cmd in parse_pgrep(result.stdout or "")
              if pid not in own and Path(cmd.split(" ")[0]).name != "pgrep"]
    if not others:
        return None
    shown = "; ".join(f"pid {pid}: {cmd[:160]}" for pid, cmd in others[:3])
    more = f" (and {len(others) - 3} more)" if len(others) > 3 else ""
    return (f"another Second Vision process is running and may hold the Hailo chip or the "
            f"camera ({shown}{more})")


def device_opt_in_reason(run_device: bool) -> str | None:
    """None if the user asked for the device level (`--run-device`); otherwise the opt-in reason."""
    return None if run_device else DEVICE_OPT_IN_REASON


def device_skip_reason(gst_reason: Callable[[], str | None] = gst_skip_reason,
                       exists: Callable[[str], bool] = os.path.exists,
                       run: Callable = subprocess.run,
                       own_pids: set[int] | None = None,
                       *, run_device: bool = False) -> str | None:
    """
    None if a `device` test may run now; otherwise the first reason it may not.

    In order: the device level was not asked for (`run_device` is False, the default: nothing
    else is looked at and no command is run), the gst conditions, /dev/hailo0 missing, the boot
    service active-like, a stray pipeline process. `--run-device` only lifts the first; it never
    bypasses the others. Each check is passed in so it can be tested without a machine.
    """
    return (device_opt_in_reason(run_device)
            or gst_reason()
            or hailo_device_skip_reason(exists)
            or service_skip_reason(run)
            or stray_process_skip_reason(own_pids, run))


def run_device_requested(config) -> bool:
    """True only if `--run-device` was given. Anything unexpected reads as "not asked for"."""
    try:
        return bool(config.getoption("run_device"))
    except (ValueError, AttributeError):
        return False


def device_level_line(run_device: bool, reason: Callable[[], str | None]) -> str:
    """The header text for the device level; `reason` is only called when the flag was given."""
    if not run_device:
        return f"OFF (opt-in: add {RUN_DEVICE_FLAG})"
    why = reason()
    return "AVAILABLE" if why is None else f"UNAVAILABLE - {why}"


# ============================================================
# Hooks
# ============================================================
def pytest_addoption(parser):
    """`--run-device`: opt in to the device level (it may detach the camera, use the Hailo chip)."""
    parser.addoption(
        RUN_DEVICE_FLAG, action="store_true", default=False, dest="run_device",
        help="run the `device` tests (they may detach the camera in software and use the Hailo "
             "chip; stop second-vision.service first). Without it they are skipped.")


def enforce_unknown_marker_error(config) -> bool:
    """
    Make a marker that is not registered an error. True if that is now in force.

    Adds `error::pytest.PytestUnknownMarkWarning` to the `filterwarnings` ini list, which is
    public API. It stays off (False) if the warnings plugin is disabled with `-p no:warnings`.
    """
    try:
        config.addinivalue_line("filterwarnings", UNKNOWN_MARKER_IS_AN_ERROR)
    except ValueError:                    # the ini option is not registered: no warnings plugin
        return False
    return config.pluginmanager.has_plugin("warnings")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Markers, unknown markers are errors, default timeout (if pytest-timeout is installed)."""
    for line in MARKER_LINES:
        config.addinivalue_line("markers", line)
    config.stash[UNKNOWN_MARKER_ENFORCED] = enforce_unknown_marker_error(config)
    if (config.pluginmanager.hasplugin("timeout")
            or config.pluginmanager.hasplugin("pytest_timeout")):
        # Set before pytest-timeout reads its settings (hence tryfirst), and only where the
        # user gave neither --timeout, PYTEST_TIMEOUT nor an ini value.
        if (config.getoption("timeout", None) is None
                and not os.environ.get("PYTEST_TIMEOUT") and not config.getini("timeout")):
            config.option.timeout = float(DEFAULT_TIMEOUT_SECONDS)
        if config.getoption("timeout_method", None) is None and not config.getini("timeout_method"):
            config.option.timeout_method = DEFAULT_TIMEOUT_METHOD
    else:
        # So `@pytest.mark.timeout(...)` is not an error under --strict-markers without the plugin.
        config.addinivalue_line(
            "markers", "timeout(seconds, method=None): needs pytest-timeout; ignored without it")


def _timeout_header(config) -> str:
    timeout = getattr(config, "_env_timeout", None)
    if not (config.pluginmanager.hasplugin("timeout") or config.pluginmanager.hasplugin("pytest_timeout")):
        return "none (pytest-timeout is not installed)"
    if not timeout:
        return "none (disabled by --timeout=0 or settings)"
    return f"{timeout:g} s per test, method {getattr(config, '_env_timeout_method', '?')}"


def pytest_report_header(config):
    """Lines under the pytest header: which levels can run here, and the timeout."""
    run_device = run_device_requested(config)
    try:
        gst_reason = gst_skip_reason()
        gst_line = (f"AVAILABLE ({gst_helpers.loaded_bindings()[0].version_string()})"
                    if gst_reason is None else f"UNAVAILABLE - {gst_reason}")
        # Without the flag no command is run for the device level (no systemctl, no pgrep).
        device_line = device_level_line(run_device, lambda: device_skip_reason(run_device=True))
    except Exception as exc:  # noqa: BLE001 - a header must never break the run
        gst_line = device_line = f"UNKNOWN ({type(exc).__name__}: {exc})"
    imported = [n for n, err in PREIMPORT.items() if err is None and n != "gi"]
    failed = {n: err for n, err in PREIMPORT.items() if err is not None and n != "gi"}
    real = ("real gi/Gst/GLib + " + ", ".join(imported)) if PREIMPORT.get("gi") is None else "none (stand-ins apply)"
    enforced = config.stash.get(UNKNOWN_MARKER_ENFORCED, False)
    markers = ("gst, device registered; a misspelt marker is an error" if enforced else
               "gst, device registered; a misspelt marker is only a warning (the warnings plugin is off)")
    return [
        f"sv-harness gst level: {gst_line}",
        f"sv-harness device level: {device_line}",
        f"sv-harness markers: {markers}",
        f"sv-harness timeout: {_timeout_header(config)}",
        f"sv-harness real modules pre-imported: {real}"
        + (f"; not importable: {failed}" if failed else ""),
    ]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Skip `gst` / `device` tests whose needs are not met, before any fixture runs."""
    if item.get_closest_marker("device") is not None:
        reason = device_skip_reason(run_device=run_device_requested(item.config))
        if reason:
            pytest.skip(f"device test skipped: {reason}")
    elif item.get_closest_marker("gst") is not None:
        reason = gst_skip_reason()
        if reason:
            pytest.skip(f"gst test skipped: {reason}")


# ============================================================
# The GLib main-loop guard (the gap in pytest-timeout's `signal` method)
# ============================================================
def effective_timeout(request) -> float | None:
    """The time limit that applies to this test: its `timeout` marker, else the run's setting."""
    marker = request.node.get_closest_marker("timeout")
    if marker is not None:
        value = marker.args[0] if marker.args else marker.kwargs.get("timeout")
    else:
        value = getattr(request.config, "_env_timeout", None)
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


@pytest.fixture(autouse=True)
def _glib_main_loop_guard(request, monkeypatch):
    """
    Make a `GLib.MainLoop.run()` inside a test stop at the test's time limit, and fail the test.

    pytest-timeout's `signal` method cannot interrupt PyGObject's main loop (see the module
    docstring), so without this a test that waits on a loop that never quits would freeze the
    run. Active only when the real GLib is loaded and a time limit applies; otherwise a no-op.
    """
    bindings = gst_helpers.loaded_bindings()
    limit = effective_timeout(request)
    if bindings is None or not limit:
        yield
        return
    GLib = bindings[1]
    deadline = time.monotonic() + max(limit - GUARD_MARGIN_SECONDS, 0.1)
    original_run = GLib.MainLoop.run
    fired = {"yes": False}

    def guarded_run(self, *args, **kwargs):
        def stop(*_user_data) -> bool:     # GLib.Source.set_callback passes user_data (None)
            fired["yes"] = True
            self.quit()
            return GLib.SOURCE_REMOVE

        source = GLib.timeout_source_new(max(int((deadline - time.monotonic()) * 1000), 1))
        source.set_callback(stop)
        source.attach(self.get_context())
        try:
            result = original_run(self, *args, **kwargs)
        finally:
            source.destroy()
        if fired["yes"]:
            pytest.fail(f"Timeout (>{limit:g}s): a GLib main loop was still running at the test's "
                        "time limit; the guard in tests/conftest.py stopped it", pytrace=False)
        return result

    monkeypatch.setattr(GLib.MainLoop, "run", guarded_run)
    yield


# ============================================================
# Fixtures: GStreamer
# ============================================================
@pytest.fixture
def gst_pipeline():
    """A minimal `fakesrc name=source ! fakesink name=sink` pipeline in NULL, torn down after."""
    pipeline = gst_helpers.make_test_pipeline()
    try:
        yield pipeline
    finally:
        gst_helpers.teardown_pipeline(pipeline)


# ============================================================
# Fixtures: the simulated camera unplug
# ============================================================
class CameraRestoreError(RuntimeError):
    """The camera did not come back after the fixture's re-attach."""


def parse_status_nodes(text: str) -> list[str]:
    """Video device nodes listed on the `nodes:` lines of `usb_sim.py status` output."""
    nodes: list[str] = []
    for line in text.splitlines():
        match = re.search(r"\bnodes:\s*(.+)$", line)
        if match:
            nodes += re.findall(r"\S*video\d+", match.group(1))
    return nodes


class CameraUnplug:
    """
    Drives `.claude/tools/usb_sim.py` for tests. Runs exactly three command shapes:

        sudo -n <tool> status
        sudo -n <tool> detach camera --for <seconds>
        sudo -n <tool> attach camera

    `launcher`, `run`, `exists`, `sleep` and `monotonic` are arguments only so the class can
    be tested against a fake tool; the fixture always uses sudo -n and the real tool.
    """

    def __init__(self, tool: Path, nodes: Sequence[str] = (), *,
                 launcher: Sequence[str] = SUDO_LAUNCHER, run: Callable = subprocess.run,
                 exists: Callable[[str], bool] = os.path.exists,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 restore_wait: float = RESTORE_WAIT_SECONDS) -> None:
        self.tool = Path(tool)
        self.nodes = list(nodes)
        self.restore_wait = restore_wait
        self._launcher = tuple(launcher)
        self._run = run
        self._exists = exists
        self._sleep = sleep
        self._monotonic = monotonic

    def command(self, *args: str) -> list[str]:
        """The full command line for one tool action."""
        return [*self._launcher, str(self.tool), *args]

    def _call(self, *args: str, timeout: float = 60):
        return self._run(self.command(*args), capture_output=True, text=True, timeout=timeout)

    def status(self):
        """`sudo -n <tool> status`; the CompletedProcess (needs no root of its own)."""
        return self._call("status")

    def detach(self, seconds: int = DEFAULT_DETACH_SECONDS):
        """Detach the camera for `seconds` (the tool re-attaches by itself after that)."""
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 600:
            raise ValueError(f"seconds must be a whole number from 1 to 600, got {seconds!r}")
        result = self._call("detach", "camera", "--for", str(seconds))
        if result.returncode != 0:
            pytest.fail(f"usb_sim detach failed (exit {result.returncode}): "
                        f"{(result.stdout or '').strip()} {(result.stderr or '').strip()}", pytrace=False)
        return result

    def attach(self):
        """`attach camera`; returns the CompletedProcess without judging it."""
        return self._call("attach", "camera")

    def nodes_present(self) -> bool:
        """True if any of the camera's recorded video nodes exists."""
        return any(self._exists(node) for node in self.nodes)

    def _camera_back(self) -> bool:
        if self.nodes_present():
            return True
        try:
            found = parse_status_nodes(self.status().stdout or "")   # node numbers can change on re-attach
        except (OSError, subprocess.SubprocessError):
            return False
        if found:
            self.nodes = found
            return True
        return False

    def wait_for_nodes(self, present: bool, timeout: float, interval: float = 0.5) -> bool:
        """Wait at most `timeout` s for the camera's nodes to be present (or all gone). Never longer."""
        limit = self._monotonic() + timeout
        while True:
            if (self._camera_back() if present else not self.nodes_present()):
                return True
            if self._monotonic() >= limit:
                return False
            self._sleep(interval)

    def restore(self, wait: float | None = None) -> None:
        """
        Always called on teardown: attach the camera and wait (bounded) for its nodes.

        Never skips the wait because the attach command failed. If the camera does not come
        back, raises CameraRestoreError (the test ends in an ERROR); the tool's own timer from
        `detach --for` still re-attaches the camera after the detach time.
        """
        wait = self.restore_wait if wait is None else wait
        problems = []
        try:
            result = self.attach()
            if result.returncode != 0:
                problems.append(f"attach exited {result.returncode}: {(result.stderr or '').strip()}")
        except Exception as exc:  # noqa: BLE001 - go on to the wait whatever happened
            problems.append(f"attach could not run ({type(exc).__name__}: {exc})")
        if not self.wait_for_nodes(True, wait):
            problems.append(f"the camera's video nodes {self.nodes} did not return within {wait:g} s")
        if problems:
            raise CameraRestoreError(
                "; ".join(problems) + ". The tool's own timer re-attaches the camera after the "
                "detach time; check `sudo .claude/tools/usb_sim.py status`.")


def camera_unplug_skip_reason(tool: Path, *, launcher: Sequence[str] = SUDO_LAUNCHER,
                              run: Callable = subprocess.run,
                              device_reason: str | None = None) -> tuple[str | None, list[str]]:
    """
    Whether the unplug fixture can be used: `(reason or None, camera video nodes)`.

    Checked in order: the `device` conditions (`device_reason`, which starts with "the device
    level is opt-in: add --run-device" and includes "the service is active"); the tool exists;
    `sudo -n <tool> status` works without a password; a camera is attached; no earlier detach
    is still pending. Nothing is run, and in particular no sudo, until the first two pass.
    """
    if device_reason:
        return device_reason, []
    if not Path(tool).exists():
        return f"the simulated-unplug tool {USB_SIM_RELATIVE} is missing ({tool})", []
    command = [*launcher, str(tool), "status"]
    try:
        result = run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS * 3)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot run sudo for the simulated-unplug tool ({type(exc).__name__}: {exc})", []
    out, err = result.stdout or "", result.stderr or ""
    if result.returncode != 0:
        if "password" in err.lower() or "terminal is required" in err.lower():
            return ("passwordless sudo for the simulated-unplug tool is not set up "
                    "(`sudo -n` asked for a password)"), []
        return (f"`sudo -n {USB_SIM_RELATIVE} status` failed (exit {result.returncode}): "
                f"{err.strip() or out.strip()}"), []
    if "Detached by this tool" in out:
        return ("the camera is still detached by an earlier run; re-attach it first with "
                f"`sudo {USB_SIM_RELATIVE} attach camera`"), []
    nodes = parse_status_nodes(out)
    if not nodes:
        return "no USB camera is attached, so there is nothing to unplug", []
    return None, nodes


@pytest.fixture
def usb_sim_tool() -> Path:
    """Path of the simulated-unplug tool; resolved from this file, overridable by a test."""
    return USB_SIM_TOOL


@pytest.fixture
def usb_sim_launcher() -> tuple[str, ...]:
    """How the tool is started: `sudo -n`. A test of the fixture itself overrides this."""
    return SUDO_LAUNCHER


@pytest.fixture
def device_level_reason(request) -> str | None:
    """
    Why the device level cannot run now (None if it can). Without `--run-device` it is the
    opt-in reason and nothing is run. A test of the fixture itself overrides it.
    """
    return device_skip_reason(run_device=run_device_requested(request.config))


@pytest.fixture
def camera_unplug(usb_sim_tool, usb_sim_launcher, device_level_reason):
    """
    Simulate unplugging the USB camera. Use with `@pytest.mark.device`.

        camera_unplug.detach(10)                       # sudo -n <tool> detach camera --for 10
        camera_unplug.wait_for_nodes(False, 5)         # its /dev/video* nodes are gone
        camera_unplug.attach()                         # sudo -n <tool> attach camera
        camera_unplug.wait_for_nodes(True, 15)         # and back

    Skips (reason in words) if `--run-device` was not given (before any command, sudo included,
    is run), a device condition fails (the boot service is active ...), the tool is missing,
    passwordless sudo is not set up, or no camera is attached. Teardown ALWAYS
    re-attaches and waits (bounded) for the nodes, even if the test failed or timed out. A
    `thread`-method timeout kills the process before teardown; the detach time then re-attaches.
    """
    reason, nodes = camera_unplug_skip_reason(usb_sim_tool, launcher=usb_sim_launcher,
                                              device_reason=device_level_reason)
    if reason:
        pytest.skip(f"camera_unplug skipped: {reason}")
    unplug = CameraUnplug(usb_sim_tool, nodes, launcher=usb_sim_launcher)
    try:
        yield unplug
    finally:
        unplug.restore()
