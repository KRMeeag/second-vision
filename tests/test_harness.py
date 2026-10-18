"""
Tests of the test harness itself (T-10a): tests/conftest.py, tests/gst_helpers.py and
scripts/uat_evidence.py.

Unit tests need nothing. Tests marked `gst` need the real GStreamer bindings (my_hailo_env) and
are skipped with a reason elsewhere. One test is marked `device`
(test_simulated_unplug_roundtrip_returns_the_camera): it unplugs the real camera in software for
10 s through .claude/tools/usb_sim.py. The device level is OPT-IN: it is skipped unless pytest is
given `--run-device`, and then only where its other conditions are met (see tests/conftest.py).
Every other test uses a FAKE tool or fake command results; none runs sudo.

Several tests start a second pytest in a subprocess against a scratch test file under tmp_path,
loading tests/conftest.py as a plugin (`-p conftest`). That is how behaviour that only exists
inside a pytest run (skips, timeouts, fixtures' teardown) is shown without putting hanging or
failing tests in the committed suite.

Run:  my_hailo_env/bin/python -m pytest tests/test_harness.py -v
      (the real camera roundtrip is skipped; add --run-device to run it, service stopped first)
"""

import _thread
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import gst_helpers  # noqa: E402


def _load_by_path(name: str, path: Path) -> types.ModuleType:
    """Import a file under a private name (conftest.py is ambiguous as `import conftest`)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclasses look the module up while it executes
    spec.loader.exec_module(module)
    return module


harness = _load_by_path("sv_harness_conftest", TESTS_DIR / "conftest.py")
evidence = _load_by_path("sv_uat_evidence", REPO_ROOT / "scripts" / "uat_evidence.py")

needs_timeout_plugin = pytest.mark.skipif(
    importlib.util.find_spec("pytest_timeout") is None,
    reason="pytest-timeout is not installed in this environment",
)


def _done(returncode=0, stdout="", stderr=""):
    """A fake subprocess result."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _recording_run(*results):
    """A fake `subprocess.run` that records each command and answers from `results` in turn."""
    calls = []
    queue = list(results)

    def run(command, **kwargs):
        calls.append(list(command))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    run.calls = calls
    return run


def run_inner_pytest(tmp_path, source, *args, blocker=False, timeout=90):
    """
    Run a second pytest on a scratch file in `tmp_path`, with tests/conftest.py as a plugin.

    There is no ini file: the markers and the rest of the settings come from conftest.py, as in
    the real suite. `blocker=True` puts a fake `gi` package first on the path whose import fails,
    which makes the GStreamer bindings look absent exactly as they are in the Poetry environment.
    """
    test_file = tmp_path / "test_inner.py"
    test_file.write_text(textwrap.dedent(source))
    paths = [str(TESTS_DIR)]
    if blocker:
        package = tmp_path / "blocker" / "gi"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise ImportError(\"No module named 'gi' (blocked by the test)\")\n")
        paths.insert(0, str(tmp_path / "blocker"))
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_TIMEOUT")}
    env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["COLUMNS"] = "400"                  # pytest cuts a skip reason at the terminal width
    command = [sys.executable, "-m", "pytest", "--rootdir", str(tmp_path),
               "-p", "no:cacheprovider", "-p", "conftest", *args, str(test_file)]
    return subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=timeout)


# ============================================================
# Criterion 1: markers, strict markers, automatic skips
# ============================================================
def test_gst_and_device_markers_are_registered_and_strict_markers_is_on(pytestconfig):
    # The settings of THIS run. There is no pytest table in pyproject.toml: tests/conftest.py's
    # pytest_configure registers the markers and makes an unregistered one an error. (It cannot
    # set `config.option.strict_markers`: pytest 9.1.1 reads the ini value, see conftest.py.)
    markers = "\n".join(pytestconfig.getini("markers"))
    assert "gst:" in markers and "device:" in markers, f"markers registered: {markers!r}"
    assert harness.UNKNOWN_MARKER_IS_AN_ERROR in pytestconfig.getini("filterwarnings")
    # An unregistered marker is an error: the warning turned into an exception (this file's filter),
    # or pytest's own Failed if the person also gave --strict-markers on the command line.
    with pytest.raises((pytest.PytestUnknownMarkWarning, pytest.fail.Exception), match="gts"):
        getattr(pytest.mark, "gts")                 # looked up while this run's settings are active
    getattr(pytest.mark, "gst"), getattr(pytest.mark, "device")      # registered: no error, no warning


def _scratch_project(tmp_path, source):
    """
    A scratch project holding copies of the REAL tests/conftest.py and tests/gst_helpers.py and one
    test file, with NO ini file, no pyproject.toml and no tox.ini: exactly what the real repo has.
    pytest finds conftest.py by itself, as it does in the real suite. Returns the project folder.
    """
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    for name in ("conftest.py", "gst_helpers.py"):
        (tests / name).write_text((TESTS_DIR / name).read_text())
    (tests / "test_scratch.py").write_text(textwrap.dedent(source))
    return project


def _run_scratch_project(project, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_TIMEOUT", "PYTHONPATH")}
    env["COLUMNS"] = "400"
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "tests/", *args],
                          cwd=project, env=env, capture_output=True, text=True, timeout=90)


def test_a_misspelt_marker_is_an_error_under_the_project_settings(tmp_path):
    project = _scratch_project(tmp_path, """
        import pytest
        @pytest.mark.gts
        def test_x():
            pass
        def test_other():
            pass
    """)
    result = _run_scratch_project(project, "-q")
    out = result.stdout + result.stderr
    assert not any((project / name).exists() for name in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"))
    assert "configfile" not in out, "a settings file was picked up; this must run on conftest.py alone"
    assert result.returncode != 0, out
    assert "ERROR collecting" in out and "PytestUnknownMarkWarning" in out and "gts" in out
    assert "passed" not in out, "the run must stop at the misspelt marker"


def test_the_registered_markers_give_no_unknown_marker_warning_under_the_project_settings(tmp_path):
    project = _scratch_project(tmp_path, """
        import pytest
        @pytest.mark.gst
        def test_gst():
            pass
        @pytest.mark.device
        def test_device():
            pass
        @pytest.mark.timeout(30)
        def test_timeout():
            pass
        @pytest.mark.parametrize("n", [1, 2])
        def test_builtin_marker(n):
            pass
    """)
    result = _run_scratch_project(project, "-q", "-rs")
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "PytestUnknownMarkWarning" not in out and "warning" not in out.lower()
    assert "the device level is opt-in" in out         # the device test skipped, it did not error


def test_without_the_warnings_plugin_the_header_says_a_misspelt_marker_is_only_a_warning(tmp_path):
    result = run_inner_pytest(tmp_path, "def test_x():\n    pass\n", "-v", "-p", "no:warnings")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sv-harness markers: gst, device registered; a misspelt marker is only a warning" in result.stdout
    normal = run_inner_pytest(tmp_path, "def test_x():\n    pass\n", "-v")
    assert "sv-harness markers: gst, device registered; a misspelt marker is an error" in normal.stdout


def test_probe_gst_says_in_words_when_gi_is_missing():
    def importer(name):
        raise ImportError("No module named 'gi'")

    bindings, reason = gst_helpers.probe_gst(importer)
    assert bindings is None
    assert "not installed" in reason and "No module named 'gi'" in reason and "my_hailo_env" in reason


def test_probe_gst_refuses_a_stand_in_gi_module():
    # What tests/test_pipeline_modes.py installs when the real gi is absent: no init_check.
    modules = {
        "gi": types.SimpleNamespace(require_version=lambda *a: None),
        "gi.repository.Gst": types.SimpleNamespace(init=lambda *a: None),
        "gi.repository.GLib": types.SimpleNamespace(),
    }
    bindings, reason = gst_helpers.probe_gst(modules.__getitem__)
    assert bindings is None and "stand-in" in reason


def test_probe_gst_reports_a_namespace_that_cannot_load_and_a_failed_init():
    def cannot_load(name):
        return types.SimpleNamespace(require_version=lambda *a: (_ for _ in ()).throw(
            ValueError("Namespace Gst not available")))

    bindings, reason = gst_helpers.probe_gst(cannot_load)
    assert bindings is None and "could not be loaded" in reason and "Namespace Gst not available" in reason

    modules = {
        "gi": types.SimpleNamespace(require_version=lambda *a: None),
        "gi.repository.Gst": types.SimpleNamespace(init_check=lambda argv: (False, [])),
        "gi.repository.GLib": types.SimpleNamespace(),
    }
    bindings, reason = gst_helpers.probe_gst(modules.__getitem__)
    assert bindings is None and "failed to initialise" in reason


def test_probe_gst_succeeds_with_working_bindings_and_initialises_once_per_call():
    calls = []
    gst = types.SimpleNamespace(init_check=lambda argv: calls.append(argv) or (True, []))
    glib = types.SimpleNamespace()
    modules = {"gi": types.SimpleNamespace(require_version=lambda *a: None),
               "gi.repository.Gst": gst, "gi.repository.GLib": glib}
    bindings, reason = gst_helpers.probe_gst(modules.__getitem__)
    assert reason is None and bindings == (gst, glib)
    assert calls == [None]


def test_hailo_device_reason_names_the_missing_device_node():
    assert harness.hailo_device_skip_reason(lambda path: False) == (
        "the Hailo chip is not present (/dev/hailo0 does not exist)")
    seen = []
    assert harness.hailo_device_skip_reason(lambda path: seen.append(path) or True) is None
    assert seen == ["/dev/hailo0"]


@pytest.mark.parametrize("state", ["active", "activating", "reloading", "deactivating"])
def test_service_reason_for_every_active_like_state(state):
    run = _recording_run(_done(0 if state == "active" else 3, stdout=f"{state}\n"))
    reason = harness.service_skip_reason(run)
    assert reason is not None and state in reason and "second-vision.service" in reason
    assert run.calls == [["systemctl", "is-active", "second-vision.service"]]


@pytest.mark.parametrize("state", ["inactive", "failed", "unknown", "dead"])
def test_service_reason_is_none_for_states_that_do_not_own_the_devices(state):
    assert harness.service_skip_reason(_recording_run(_done(3, stdout=f"{state}\n"))) is None


def test_service_reason_when_systemctl_cannot_answer_is_a_skip_not_a_pass():
    assert "cannot tell" in harness.service_skip_reason(_recording_run(_done(1, stdout="")))
    assert "cannot tell" in harness.service_skip_reason(_recording_run(FileNotFoundError("systemctl")))
    assert "cannot tell" in harness.service_skip_reason(
        _recording_run(subprocess.TimeoutExpired("systemctl", 10)))


def test_parse_pgrep_reads_pid_and_command():
    text = "123 python3 src/second_vision/main.py --input usb\n456 /bin/sh -c x\nnot a line\n"
    assert harness.parse_pgrep(text) == [(123, "python3 src/second_vision/main.py --input usb"),
                                         (456, "/bin/sh -c x")]


def _fake_proc(root, tree):
    """Write a fake /proc: {pid: ppid}. The comm field holds spaces and a parenthesis on purpose."""
    for pid, ppid in tree.items():
        (root / str(pid)).mkdir()
        (root / str(pid) / "stat").write_text(f"{pid} (odd name ) x) S {ppid} 1 1 0 -1 0\n")
    (root / "not-a-pid").mkdir()


def test_process_tree_is_the_pid_its_ancestors_and_its_descendants_only(tmp_path):
    #  1 -- 100 (agent) -- 200 (shell) -- 300 (pytest) -- 400 -- 500
    #         |- 250 (another shell) -- 260        700 (unrelated)
    _fake_proc(tmp_path, {1: 0, 100: 1, 200: 100, 300: 200, 400: 300, 500: 400, 250: 100, 260: 250, 700: 1})
    assert harness.process_tree_pids(300, str(tmp_path)) == {1, 100, 200, 300, 400, 500}


def test_process_tree_of_this_process_contains_it_and_its_parent():
    tree = harness.process_tree_pids()
    assert os.getpid() in tree and os.getppid() in tree


def test_stray_process_reason_shape_and_exclusions():
    command = ["pgrep", "-af", "main.py|second_vision|Parallel-Depth"]
    own = {300, 200}
    # Nothing matched: pgrep exits 1.
    run = _recording_run(_done(1))
    assert harness.stray_process_skip_reason(own, run) is None
    assert run.calls == [command]
    # Only our own tree (the shell that holds the pattern in its command line) and a pgrep: no stray.
    out = "200 /bin/bash -c python -m pytest second_vision main.py\n300 python -m pytest\n301 pgrep -af main.py\n"
    assert harness.stray_process_skip_reason(own, _recording_run(_done(0, stdout=out))) is None
    # A stale pipeline and the boot service's process are seen, own tree is not named.
    out += "900 python3 src/second_vision/main.py --input usb\n901 python3 /x/Parallel-Depth-Detection.py\n"
    reason = harness.stray_process_skip_reason(own, _recording_run(_done(0, stdout=out)))
    assert "pid 900" in reason and "main.py --input usb" in reason and "pid 901" in reason
    assert "pid 200" not in reason and "pid 300" not in reason and "pgrep" not in reason


def test_stray_process_reason_when_pgrep_cannot_answer_is_a_skip():
    assert "cannot tell" in harness.stray_process_skip_reason(set(), _recording_run(_done(2, stderr="bad")))
    assert "cannot tell" in harness.stray_process_skip_reason(set(), _recording_run(FileNotFoundError("pgrep")))


def test_device_reason_is_the_first_failing_condition_in_order():
    nothing = lambda: None  # noqa: E731
    active = _recording_run(_done(0, stdout="active\n"))
    missing = lambda path: False  # noqa: E731
    present = lambda path: True  # noqa: E731
    # With the device level asked for (run_device=True) the conditions apply in this order.
    assert harness.device_skip_reason(lambda: "no gst", missing, active, set(), run_device=True) == "no gst"
    assert "/dev/hailo0" in harness.device_skip_reason(nothing, missing, active, set(), run_device=True)
    assert "second-vision.service is active" in harness.device_skip_reason(
        nothing, present, active, set(), run_device=True)
    # service inactive (stdout "inactive"), but a stray process: pgrep answers second.
    both = _recording_run(_done(3, stdout="inactive\n"), _done(0, stdout="900 python main.py\n"))
    assert "pid 900" in harness.device_skip_reason(nothing, present, both, set(), run_device=True)
    clear = _recording_run(_done(3, stdout="inactive\n"), _done(1))
    assert harness.device_skip_reason(nothing, present, clear, set(), run_device=True) is None
    assert clear.calls == [["systemctl", "is-active", "second-vision.service"],
                           ["pgrep", "-af", "main.py|second_vision|Parallel-Depth"]]


def _must_not_be_asked(*args, **kwargs):
    raise AssertionError("this condition must not be looked at before the device level is asked for")


def test_the_device_level_is_opt_in_and_without_the_flag_nothing_is_looked_at_or_run():
    run = _recording_run(_done(0, stdout="active\n"))
    reason = harness.device_skip_reason(_must_not_be_asked, _must_not_be_asked, run, set())   # no flag
    assert reason == harness.DEVICE_OPT_IN_REASON
    assert reason == ("the device level is opt-in; add --run-device (it may detach the camera and use "
                      "the Hailo chip) and stop second-vision.service first")
    assert harness.device_skip_reason(_must_not_be_asked, _must_not_be_asked, run, set(),
                                      run_device=False) == reason
    assert run.calls == [], "no systemctl, no pgrep without the flag"
    assert harness.device_opt_in_reason(True) is None and harness.device_opt_in_reason(False) == reason


def test_run_device_only_lifts_the_opt_in_and_never_bypasses_a_failing_condition():
    nothing = lambda: None  # noqa: E731
    missing = lambda path: False  # noqa: E731
    present = lambda path: True  # noqa: E731
    flag = dict(run_device=True)
    # Every other condition holds: the flag is what lets the device level run.
    ok = _recording_run(_done(3, stdout="inactive\n"), _done(1))
    assert harness.device_skip_reason(nothing, present, ok, set(), **flag) is None
    # The service is active: still skipped, and the reason is the service, not the opt-in.
    active = harness.device_skip_reason(nothing, present, _recording_run(_done(0, stdout="active\n")),
                                        set(), **flag)
    assert "second-vision.service is active" in active and "opt-in" not in active
    # The other three conditions, one at a time.
    assert harness.device_skip_reason(lambda: "no gst", present, ok, set(), **flag) == "no gst"
    assert "/dev/hailo0" in harness.device_skip_reason(nothing, missing, ok, set(), **flag)
    stray = _recording_run(_done(3, stdout="inactive\n"), _done(0, stdout="900 python main.py\n"))
    assert "pid 900" in harness.device_skip_reason(nothing, present, stray, set(), **flag)


def test_run_device_requested_is_true_only_for_the_flag():
    class Config:
        def __init__(self, value):
            self.value = value

        def getoption(self, name):
            if isinstance(self.value, BaseException):
                raise self.value
            assert name == "run_device"
            return self.value

    assert harness.run_device_requested(Config(True)) is True
    assert harness.run_device_requested(Config(False)) is False
    assert harness.run_device_requested(Config(None)) is False
    assert harness.run_device_requested(Config(ValueError("no such option"))) is False    # opt-out is the safe side


def test_the_device_level_header_line_says_off_available_or_unavailable():
    asked = []
    assert harness.device_level_line(False, lambda: asked.append(1)) == "OFF (opt-in: add --run-device)"
    assert asked == [], "the conditions are not looked at (no systemctl, no pgrep) without the flag"
    assert harness.device_level_line(True, lambda: None) == "AVAILABLE"
    assert harness.device_level_line(True, lambda: "second-vision.service is active: stop it") == (
        "UNAVAILABLE - second-vision.service is active: stop it")


def _code_lines(path):
    """The module's lines without comment lines and docstring-opening lines (what the scans look at)."""
    return "\n".join(line for line in path.read_text().splitlines()
                     if not line.strip().startswith(("#", '"""')))


def test_the_harness_only_runs_the_commands_it_is_allowed_to():
    # Static guard on the harness modules: nothing that starts, stops, restarts or kills, and no
    # sudo except the launcher constant that the unplug fixture uses.
    script = REPO_ROOT / "scripts" / "uat_evidence.py"
    for path in (TESTS_DIR / "conftest.py", TESTS_DIR / "gst_helpers.py", script):
        code = _code_lines(path)
        assert not re.search(r"systemctl[\"',\s]+(start|stop|restart|kill|reload|enable|disable)", code), path
        assert not re.search(r"\b(pkill|killall|fuser|os\.kill\b|\.terminate\(|\.kill\()", code), path
    # tests/conftest.py and gst_helpers.py never signal anything at all.
    for path in (TESTS_DIR / "conftest.py", TESTS_DIR / "gst_helpers.py"):
        assert "killpg" not in _code_lines(path) and "signal.SIG" not in _code_lines(path), path
    # The evidence script may stop ONE thing: the process group of the pytest it started, by pid.
    code = _code_lines(script)
    assert len(re.findall(r"\bos\.killpg\(", code)) == 1, "one place sends signals to a process group"
    calls = [m for m in re.findall(r"\b_signal_group\(([^,)]+)", code) if m != "pgid: int"]
    assert calls and set(calls) == {"process.pid"}, f"signals may only go to the child's own group: {calls}"
    assert harness.SUDO_LAUNCHER == ("sudo", "-n")
    conftest_sudo = [ln for ln in (TESTS_DIR / "conftest.py").read_text().splitlines()
                     if '"sudo"' in ln or "'sudo'" in ln]
    assert conftest_sudo == ['SUDO_LAUNCHER = ("sudo", "-n")']
    assert '"sudo"' not in script.read_text()


@pytest.mark.parametrize("marker", ["gst", "device"])
def test_gst_and_device_tests_skip_with_a_reason_when_the_bindings_are_missing(tmp_path, marker):
    # For the device level the flag is given, so it is the missing bindings that stop the test.
    flag = ["--run-device"] if marker == "device" else []
    result = run_inner_pytest(tmp_path, f"""
        import pytest
        @pytest.mark.{marker}
        def test_needs_gst():
            raise AssertionError("must not run")
        def test_plain():
            pass
    """, "-q", "-rs", *flag, blocker=True)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert "1 passed, 1 skipped" in out
    assert f"{marker} test skipped:" in out and "not installed" in out
    assert not re.search(r"\b\d+ (failed|error)", out)


DEVICE_TEST_SOURCE = """
    import pytest
    @pytest.mark.device
    def test_needs_a_device():
        raise AssertionError("must not run")
    def test_plain():
        pass
"""


def test_a_device_test_skips_without_run_device_and_says_why(tmp_path):
    result = run_inner_pytest(tmp_path, DEVICE_TEST_SOURCE, "-v", "-rs")
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert "1 passed, 1 skipped" in out and not re.search(r"\b\d+ (failed|error)", out)
    assert ("device test skipped: the device level is opt-in; add --run-device (it may detach the camera "
            "and use the Hailo chip) and stop second-vision.service first") in out
    assert "sv-harness device level: OFF (opt-in: add --run-device)" in out


def test_run_device_does_not_bypass_a_failing_condition_through_the_hook(tmp_path):
    # The bindings are blocked (a failing condition), the flag is given: still skipped, and the
    # reason is now the failing condition, not the opt-in. (The service-active case is covered
    # with fake command results above; the hook passes the same flag to the same function.)
    result = run_inner_pytest(tmp_path, DEVICE_TEST_SOURCE, "-v", "-rs", "--run-device", blocker=True)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    assert "1 passed, 1 skipped" in out
    assert "device test skipped: the GStreamer Python bindings (gi / Gst) are not installed" in out
    assert "opt-in" not in out
    assert "sv-harness device level: UNAVAILABLE - the GStreamer Python bindings" in out


def test_the_device_header_line_with_the_flag_is_available_or_unavailable_with_a_reason(tmp_path):
    result = run_inner_pytest(tmp_path, "def test_x():\n    pass\n", "-v", "--run-device")
    line = next(l for l in result.stdout.splitlines() if l.startswith("sv-harness device level:"))
    assert line == "sv-harness device level: AVAILABLE" or line.startswith("sv-harness device level: UNAVAILABLE - ")
    assert "OFF" not in line and "opt-in" not in line


# ============================================================
# Criterion 2: the real modules win, in any order
# ============================================================
@pytest.mark.gst
def test_real_gst_and_glib_are_loaded():
    import gi
    from gi.repository import GLib, Gst

    assert hasattr(gi, "_gi"), "gi is a stand-in, not PyGObject"
    major, minor, micro, nano = Gst.version()
    assert major >= 1
    assert Gst.version_string().startswith("GStreamer")
    assert Gst.is_initialized()
    assert GLib.MainLoop().is_running() is False


@pytest.mark.gst
def test_second_vision_app_is_built_on_the_real_gstreamer_app():
    reason = harness.PREIMPORT.get("hailo_apps.python.core.gstreamer.gstreamer_app")
    if reason:
        pytest.skip(f"hailo_apps could not be imported here: {reason}")
    from hailo_apps.python.core.gstreamer import gstreamer_app as library

    from second_vision.pipeline import app as app_mod

    assert getattr(library, "__file__", None), "gstreamer_app is a stand-in module (no file)"
    assert "site-packages" in library.__file__
    assert library.GStreamerApp in app_mod.SecondVisionApp.__mro__
    base = [c for c in app_mod.SecondVisionApp.__mro__ if c.__name__ == "GStreamerApp"][0]
    assert base.__module__ == "hailo_apps.python.core.gstreamer.gstreamer_app"
    assert hasattr(base, "bus_call"), "the stub GStreamerApp has no bus_call; the library's has"
    from gi.repository import Gst
    assert app_mod.Gst is Gst and hasattr(app_mod.Gst, "version_string")


REAL_MODULES_CHECK = """
def test_the_real_modules_won():
    import gi
    from hailo_apps.python.core.gstreamer import gstreamer_app
    assert hasattr(gi, "_gi"), "gi is the stand-in installed by test_a_stubs.py"
    assert getattr(gstreamer_app, "__file__", None), "gstreamer_app is the stand-in module"
"""


def _scratch_repo(tmp_path, with_conftest):
    """
    A scratch copy of the layout: tests/test_a_stubs.py is tests/test_pipeline_modes.py (the file
    that installs the stand-ins), collected FIRST (alphabetical order), then test_z_real.py checks
    which modules are in place. With `with_conftest`, tests/conftest.py and gst_helpers.py are
    copied in, so pytest loads conftest the way it does in the real suite.
    """
    repo = tmp_path / "repo"
    tests = repo / "tests"
    tests.mkdir(parents=True)
    (repo / "src").symlink_to(REPO_ROOT / "src")          # the copy finds src/ at ../src, as the original does
    (repo / "pytest.ini").write_text("[pytest]\nmarkers =\n    gst: x\n    device: x\n")
    (tests / "test_a_stubs.py").write_text((TESTS_DIR / "test_pipeline_modes.py").read_text())
    (tests / "test_z_real.py").write_text(REAL_MODULES_CHECK)
    if with_conftest:
        for name in ("conftest.py", "gst_helpers.py"):
            (tests / name).write_text((TESTS_DIR / name).read_text())
    return repo


def _run_scratch_repo(repo):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_TIMEOUT", "PYTHONPATH")}
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--no-header", "tests/"],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.gst
def test_real_modules_win_even_though_the_stub_file_is_collected_first(tmp_path):
    result = _run_scratch_repo(_scratch_repo(tmp_path, with_conftest=True))
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert "21 passed" in result.stdout, result.stdout[-1500:]       # the 20 stub-file tests + the check


@pytest.mark.gst
def test_control_without_conftest_the_stub_file_wins(tmp_path):
    # The same layout without tests/conftest.py: the stand-ins are installed first, so the check
    # fails. This is what the pre-import in conftest.py fixes (the problem the brief names).
    result = _run_scratch_repo(_scratch_repo(tmp_path, with_conftest=False))
    assert result.returncode == 1, result.stdout[-3000:]
    assert "test_the_real_modules_won" in result.stdout and "stand-in" in result.stdout
    assert "20 passed" in result.stdout and "1 failed" in result.stdout


# ============================================================
# Criterion 3: default timeout
# ============================================================
@needs_timeout_plugin
def test_default_timeout_is_60_seconds_with_the_signal_method(tmp_path):
    result = run_inner_pytest(tmp_path, "def test_x():\n    pass\n", "-v")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "timeout: 60.0s" in result.stdout and "timeout method: signal" in result.stdout
    assert "sv-harness timeout: 60 s per test, method signal" in result.stdout
    # An explicit --timeout wins over the default.
    again = run_inner_pytest(tmp_path, "def test_x():\n    pass\n", "-v", "--timeout=7")
    assert "timeout: 7.0s" in again.stdout


@needs_timeout_plugin
def test_a_hung_test_fails_with_a_timeout_message_and_the_run_goes_on(tmp_path):
    started = time.monotonic()
    result = run_inner_pytest(tmp_path, """
        import time
        def test_hangs():
            time.sleep(60)
        def test_runs_after():
            pass
    """, "-q", "--timeout=2")
    assert time.monotonic() - started < 30
    assert result.returncode == 1
    assert "1 failed, 1 passed" in result.stdout
    assert "Timeout (>2.0s) from pytest-timeout" in result.stdout


@needs_timeout_plugin
@pytest.mark.gst
def test_a_stuck_glib_main_loop_fails_at_the_time_limit_and_the_run_goes_on(tmp_path):
    # pytest-timeout's signal method alone cannot stop this (the loop never lets Python run its
    # handler); the autouse guard in conftest quits the loop and fails the test.
    started = time.monotonic()
    result = run_inner_pytest(tmp_path, """
        from gi.repository import GLib
        def test_stuck_in_glib_loop():
            GLib.MainLoop().run()
        def test_runs_after():
            pass
    """, "-q", "--timeout=3", timeout=45)
    assert time.monotonic() - started < 40
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 1 passed" in result.stdout
    assert "a GLib main loop was still running at the test's time limit" in result.stdout


@needs_timeout_plugin
def test_without_the_plugin_nothing_errors_and_no_warning_appears(tmp_path):
    # `-p no:timeout` is what the Poetry environment looks like. The marker still works under
    # --strict-markers, and there is no warning about timeout settings.
    result = run_inner_pytest(tmp_path, """
        import pytest
        @pytest.mark.timeout(5)
        def test_x():
            pass
    """, "-q", "--strict-markers", "-p", "no:timeout")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout and "warning" not in (result.stdout + result.stderr).lower()


# ============================================================
# Criterion 4: GStreamer helpers
# ============================================================
@pytest.mark.gst
def test_make_test_pipeline_names_its_source_and_tears_down_without_hanging(gst_pipeline):
    from gi.repository import Gst

    assert gst_pipeline.get_by_name("source") is not None
    assert gst_pipeline.get_by_name("sink") is not None
    assert gst_pipeline.get_state(0)[1] == Gst.State.NULL
    started = time.monotonic()
    assert gst_helpers.teardown_pipeline(gst_pipeline) is True
    assert time.monotonic() - started < 2


@pytest.mark.gst
def test_a_running_test_pipeline_is_torn_down_to_null_in_bounded_time():
    from gi.repository import Gst

    pipeline = gst_helpers.make_test_pipeline("videotestsrc", "fakesink")
    pipeline.set_state(Gst.State.PLAYING)
    pipeline.get_state(2 * Gst.SECOND)
    started = time.monotonic()
    assert gst_helpers.teardown_pipeline(pipeline, timeout=5) is True
    assert time.monotonic() - started < 5


@pytest.mark.gst
def test_make_test_pipeline_with_a_missing_element_says_which():
    with pytest.raises(gst_helpers.GstUnavailable, match="no_such_element_xyz"):
        gst_helpers.make_test_pipeline(source="no_such_element_xyz")


@pytest.mark.gst
def test_load_gst_is_idempotent():
    first = gst_helpers.load_gst()
    assert gst_helpers.load_gst() is first
    Gst = first[0]
    assert Gst.init_check(None)[0] is True and Gst.is_initialized()
    assert gst_helpers.gst_unavailable_reason() is None


@pytest.mark.gst
def test_bus_watch_receives_the_posted_error_on_the_main_loop_from_the_named_element(gst_pipeline):
    from gi.repository import Gst

    seen = []

    def on_message(bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            seen.append((message.src.get_name(), err.message, debug, threading.get_ident()))

    with gst_helpers.bus_watch(gst_pipeline, on_message):
        assert gst_helpers.post_error(gst_pipeline, "source", "boom", "dbg") is True
        assert seen == [], "the watch must fire on the main loop, not at post time"
        assert gst_helpers.run_main_loop(lambda: bool(seen), seconds=3) is True
    assert seen == [("source", "boom", "dbg", threading.get_ident())]


@pytest.mark.gst
def test_post_error_accepts_an_element_object_and_other_names(gst_pipeline):
    from gi.repository import Gst

    names = []

    def on_message(bus, message):
        if message.type == Gst.MessageType.ERROR:
            names.append(message.src.get_name())

    with gst_helpers.bus_watch(gst_pipeline, on_message):
        gst_helpers.post_error(gst_pipeline, gst_pipeline.get_by_name("sink"))
        gst_helpers.post_error(gst_pipeline, "source")
        assert gst_helpers.run_main_loop(lambda: len(names) == 2, seconds=3)
    assert names == ["sink", "source"]


@pytest.mark.gst
def test_post_error_for_an_unknown_element_name_is_a_clear_error(gst_pipeline):
    with pytest.raises(LookupError, match="no element named 'nope'"):
        gst_helpers.post_error(gst_pipeline, "nope")


@pytest.mark.gst
def test_post_eos_arrives_as_eos_from_the_named_element(gst_pipeline):
    from gi.repository import Gst

    seen = []

    def on_message(bus, message):
        if message.type == Gst.MessageType.EOS:
            seen.append(message.src.get_name())

    with gst_helpers.bus_watch(gst_pipeline, on_message):
        assert gst_helpers.post_eos(gst_pipeline, "source") is True
        assert gst_helpers.run_main_loop(lambda: bool(seen), seconds=3)
    assert seen == ["source"]


@pytest.mark.gst
def test_the_app_style_bus_handler_sees_the_element_through_message_src(gst_pipeline):
    # The shape the library's bus_call uses: connect("message", handler, loop); the handler reads
    # message.type and message.src.get_name().
    from gi.repository import Gst

    log = []

    def bus_call(bus, message, loop):
        if message.type == Gst.MessageType.ERROR:
            log.append(("error", message.src.get_name()))
        elif message.type == Gst.MessageType.EOS:
            log.append(("eos", message.src.get_name()))
        return True

    with gst_helpers.bus_watch(gst_pipeline, bus_call, "loop-placeholder"):
        gst_helpers.post_error(gst_pipeline, "source")
        gst_helpers.post_eos(gst_pipeline, "sink")
        assert gst_helpers.run_main_loop(lambda: len(log) == 2, seconds=3)
    assert log == [("error", "source"), ("eos", "sink")]


@pytest.mark.gst
def test_bus_watch_is_removed_on_exit(gst_pipeline):
    seen = []
    with gst_helpers.bus_watch(gst_pipeline, lambda bus, message: seen.append(message.type)):
        pass
    gst_helpers.post_error(gst_pipeline, "source")
    gst_helpers.run_main_loop(seconds=0.3)
    assert seen == []


@pytest.mark.gst
def test_run_main_loop_for_a_fixed_time_runs_idle_callbacks_and_stops():
    from gi.repository import GLib

    ran = []
    GLib.idle_add(lambda: ran.append(1) or GLib.SOURCE_REMOVE)
    started = time.monotonic()
    assert gst_helpers.run_main_loop(seconds=0.3) is True
    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 1.5
    assert ran == [1]


@pytest.mark.gst
def test_run_main_loop_stops_early_when_the_predicate_becomes_true():
    from gi.repository import GLib

    flag = []
    GLib.timeout_add(100, lambda: flag.append(1) or GLib.SOURCE_REMOVE)
    started = time.monotonic()
    assert gst_helpers.run_main_loop(lambda: bool(flag), seconds=10) is True
    assert time.monotonic() - started < 2


@pytest.mark.gst
def test_run_main_loop_returns_false_when_the_predicate_never_becomes_true():
    started = time.monotonic()
    assert gst_helpers.run_main_loop(lambda: False, seconds=0.3) is False
    assert 0.25 <= time.monotonic() - started < 1.5


@pytest.mark.gst
@pytest.mark.parametrize("seconds", [0, -1, gst_helpers.MAX_LOOP_SECONDS + 1])
def test_run_main_loop_refuses_unbounded_or_silly_durations(seconds):
    with pytest.raises(ValueError):
        gst_helpers.run_main_loop(seconds=seconds)


@pytest.mark.gst
def test_run_main_loop_re_raises_an_error_from_the_predicate_and_leaves_no_sources():
    def broken():
        raise RuntimeError("predicate failed")

    with pytest.raises(RuntimeError, match="predicate failed"):
        gst_helpers.run_main_loop(broken, seconds=3)
    # A later run is unaffected by leftovers from the failed one.
    assert gst_helpers.run_main_loop(seconds=0.1) is True


# ============================================================
# Criterion 5: simulated-unplug fixture (fake tool; the real roundtrip is the device test below)
# ============================================================
FAKE_TOOL = '''\
#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
cfg = json.loads((here / "config.json").read_text())
node, state = Path(cfg["node"]), here / "state"
with (here / "calls.log").open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
cmd = sys.argv[1:]
if cmd == ["status"]:
    if cfg.get("status_rc"):
        print(cfg.get("status_err", ""), file=sys.stderr)
        sys.exit(cfg["status_rc"])
    if node.exists():
        print("Cameras attached now:")
        print("  1-1 abcd:0001 \\"Fake Cam\\" authorized=1  nodes: " + str(node))
    else:
        print("No camera attached now.")
        if state.exists():
            print("Detached by this tool: 1-1 abcd:0001")
elif len(cmd) == 4 and cmd[:2] == ["detach", "camera"] and cmd[2] == "--for" and cmd[3].isdigit():
    node.unlink(missing_ok=True)
    state.write_text("x")
elif cmd == ["attach", "camera"]:
    if not cfg.get("attach_does_nothing"):
        node.touch()
        state.unlink(missing_ok=True)
else:
    sys.exit(2)
'''


def make_fake_tool(tmp_path, **config):
    """A fake usb_sim.py under tmp_path, with a camera node file and a call log. Returns (tool, node, log)."""
    folder = tmp_path / "fake-tool"
    folder.mkdir()
    node = folder / "video0"
    node.touch()
    (folder / "config.json").write_text(json.dumps({"node": str(node), **config}))
    tool = folder / "usb_sim.py"
    tool.write_text(FAKE_TOOL.format(python=sys.executable))
    tool.chmod(0o755)
    return tool, node, folder / "calls.log"


def logged_calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_default_tool_path_is_resolved_from_conftest_inside_the_repo():
    assert harness.USB_SIM_TOOL == REPO_ROOT / ".claude" / "tools" / "usb_sim.py"
    assert harness.REPO_ROOT == REPO_ROOT


def test_unplug_commands_have_exactly_the_three_allowed_shapes():
    tool = harness.USB_SIM_TOOL
    run = _recording_run(_done(0, stdout="Cameras attached now:\n  x nodes: /dev/video0\n"))
    unplug = harness.CameraUnplug(tool, ["/dev/video0"], run=run)
    unplug.status()
    unplug.detach(10)
    unplug.attach()
    sudo = ["sudo", "-n", str(tool)]
    assert run.calls == [sudo + ["status"], sudo + ["detach", "camera", "--for", "10"], sudo + ["attach", "camera"]]
    assert str(tool).endswith(".claude/tools/usb_sim.py")


@pytest.mark.parametrize("seconds", [0, 601, -5, "10", 10.5, True, None])
def test_detach_refuses_a_duration_the_tool_would_refuse(seconds):
    run = _recording_run(_done(0))
    with pytest.raises(ValueError):
        harness.CameraUnplug(harness.USB_SIM_TOOL, run=run).detach(seconds)
    assert run.calls == []


def test_parse_status_nodes_reads_the_tools_status_output():
    text = ('Cameras attached now:\n  1-1 046d:0825 "Logi Cam" authorized=1  nodes: /dev/video0, /dev/video1\n'
            "Detached by this tool: nothing\n")
    assert harness.parse_status_nodes(text) == ["/dev/video0", "/dev/video1"]
    assert harness.parse_status_nodes("No camera attached now.\n") == []


def test_fake_tool_roundtrip_removes_and_restores_the_nodes_with_only_allowed_commands(tmp_path):
    tool, node, log = make_fake_tool(tmp_path)
    unplug = harness.CameraUnplug(tool, [str(node)], launcher=(sys.executable,))
    assert unplug.nodes_present()
    unplug.detach(10)
    assert unplug.wait_for_nodes(False, 3) is True
    assert not node.exists()
    unplug.attach()
    assert unplug.wait_for_nodes(True, 3) is True
    assert node.exists()
    calls = logged_calls(log)
    allowed = (["status"], ["detach", "camera", "--for", "10"], ["attach", "camera"])
    assert all(call in allowed for call in calls), calls
    assert ["detach", "camera", "--for", "10"] in calls and ["attach", "camera"] in calls


def test_wait_for_nodes_never_waits_longer_than_its_bound(tmp_path):
    tool, node, log = make_fake_tool(tmp_path, attach_does_nothing=True)
    unplug = harness.CameraUnplug(tool, [str(node)], launcher=(sys.executable,))
    unplug.detach(10)
    started = time.monotonic()
    assert unplug.wait_for_nodes(True, 0.6) is False
    assert time.monotonic() - started < 3
    assert unplug.wait_for_nodes(False, 0.6) is True


def test_restore_raises_a_clear_error_when_the_camera_does_not_come_back(tmp_path):
    tool, node, log = make_fake_tool(tmp_path, attach_does_nothing=True)
    unplug = harness.CameraUnplug(tool, [str(node)], launcher=(sys.executable,))
    unplug.detach(10)
    with pytest.raises(harness.CameraRestoreError, match="did not return within 0.6 s"):
        unplug.restore(wait=0.6)
    assert ["attach", "camera"] in logged_calls(log)


def test_restore_still_waits_when_the_attach_command_itself_fails():
    run = _recording_run(FileNotFoundError("sudo"))
    unplug = harness.CameraUnplug(harness.USB_SIM_TOOL, ["/nonexistent/video0"], run=run,
                                  exists=lambda path: False, sleep=lambda s: None,
                                  monotonic=iter([0.0, 0.1, 0.2, 99.0, 99.0]).__next__)
    with pytest.raises(harness.CameraRestoreError) as info:
        unplug.restore(wait=1)
    assert "attach could not run" in str(info.value) and "did not return" in str(info.value)


def test_skip_reason_when_the_tool_is_missing(tmp_path):
    reason, nodes = harness.camera_unplug_skip_reason(tmp_path / "nope.py", launcher=(sys.executable,))
    assert "tool" in reason and "missing" in reason and nodes == []


def test_skip_reason_passes_the_device_conditions_through_before_running_anything(tmp_path):
    tool, node, log = make_fake_tool(tmp_path)
    reason, _ = harness.camera_unplug_skip_reason(
        tool, launcher=(sys.executable,), device_reason="second-vision.service is active: stop it")
    assert reason == "second-vision.service is active: stop it"
    assert logged_calls(log) == []


def test_skip_reason_when_sudo_would_ask_for_a_password(tmp_path):
    tool, _node, _log = make_fake_tool(tmp_path)
    run = _recording_run(_done(1, stderr="sudo: a password is required\n"))
    reason, _ = harness.camera_unplug_skip_reason(tool, run=run)
    assert "passwordless sudo" in reason and "not set up" in reason
    assert run.calls == [["sudo", "-n", str(tool), "status"]]


def test_skip_reason_when_sudo_or_the_status_check_cannot_run(tmp_path):
    tool, _node, _log = make_fake_tool(tmp_path)
    reason, _ = harness.camera_unplug_skip_reason(tool, run=_recording_run(FileNotFoundError("sudo")))
    assert "cannot run sudo" in reason
    reason, _ = harness.camera_unplug_skip_reason(tool, run=_recording_run(_done(2, stderr="odd failure")))
    assert "failed (exit 2)" in reason and "odd failure" in reason


def test_skip_reason_when_no_camera_is_attached_or_an_old_detach_is_pending(tmp_path):
    tool, node, _log = make_fake_tool(tmp_path)
    node.unlink()
    reason, _ = harness.camera_unplug_skip_reason(tool, launcher=(sys.executable,))
    assert "no USB camera" in reason
    (tool.parent / "state").write_text("x")
    reason, _ = harness.camera_unplug_skip_reason(tool, launcher=(sys.executable,))
    assert "still detached" in reason and "attach camera" in reason


def test_skip_reason_is_none_with_a_camera_and_returns_its_nodes(tmp_path):
    tool, node, _log = make_fake_tool(tmp_path)
    assert harness.camera_unplug_skip_reason(tool, launcher=(sys.executable,)) == (None, [str(node)])


TOOL_OVERRIDES = """
    import sys
    from pathlib import Path
    import pytest

    @pytest.fixture
    def usb_sim_tool():
        return Path({tool!r})

    @pytest.fixture
    def usb_sim_launcher():
        return (sys.executable,)
"""

# The same, and the device level is declared available: for the tests of the fixture's own work.
FIXTURE_OVERRIDES = TOOL_OVERRIDES + """
    @pytest.fixture
    def device_level_reason():
        return None
"""


def test_fixture_reattaches_on_teardown_after_a_failing_test(tmp_path):
    tool, node, log = make_fake_tool(tmp_path)
    source = FIXTURE_OVERRIDES.format(tool=str(tool)) + """
    def test_fails_after_detach(camera_unplug):
        camera_unplug.detach(10)
        assert camera_unplug.wait_for_nodes(False, 5)
        assert False, "this test fails on purpose"
    """
    result = run_inner_pytest(tmp_path, source, "-q")
    assert "1 failed" in result.stdout and not re.search(r"\b\d+ error", result.stdout)
    calls = [c for c in logged_calls(log) if c != ["status"]]
    assert calls == [["detach", "camera", "--for", "10"], ["attach", "camera"]]
    assert node.exists(), "the camera node must be back after the failing test"


@needs_timeout_plugin
def test_fixture_reattaches_on_teardown_after_a_timed_out_test(tmp_path):
    tool, node, log = make_fake_tool(tmp_path)
    source = FIXTURE_OVERRIDES.format(tool=str(tool)) + """
    import time
    def test_hangs_after_detach(camera_unplug):
        camera_unplug.detach(10)
        time.sleep(60)
    """
    result = run_inner_pytest(tmp_path, source, "-q", "--timeout=2")
    assert "Timeout (>2.0s)" in result.stdout and "1 failed" in result.stdout
    assert [c for c in logged_calls(log) if c != ["status"]][-1] == ["attach", "camera"]
    assert node.exists()


def test_fixture_teardown_error_is_reported_when_the_camera_never_returns(tmp_path):
    tool, node, log = make_fake_tool(tmp_path, attach_does_nothing=True)
    source = FIXTURE_OVERRIDES.format(tool=str(tool)) + """
    def test_detaches(camera_unplug):
        camera_unplug.restore_wait = 0.6
        camera_unplug.detach(10)
    """
    result = run_inner_pytest(tmp_path, source, "-q")
    assert "1 passed" in result.stdout and "1 error" in result.stdout
    assert "did not return within 0.6 s" in result.stdout


def test_fixture_skips_in_words_when_the_tool_is_missing(tmp_path):
    source = FIXTURE_OVERRIDES.format(tool=str(tmp_path / "missing.py")) + """
    def test_uses_unplug(camera_unplug):
        raise AssertionError("must not run")
    """
    result = run_inner_pytest(tmp_path, source, "-q", "-rs")
    assert "1 skipped" in result.stdout and "camera_unplug skipped" in result.stdout
    assert "is missing" in result.stdout


def test_camera_unplug_makes_no_sudo_call_without_run_device(tmp_path):
    # In process, with a fake runner that records every command it is asked to run.
    tool, node, log = make_fake_tool(tmp_path)
    run = _recording_run(_done(0, stdout=f"Cameras attached now:\n  1-1 abcd:0001 nodes: {node}\n"))
    no_flag = harness.device_skip_reason(run=run)                       # what the fixture is given
    assert no_flag == harness.DEVICE_OPT_IN_REASON
    assert harness.camera_unplug_skip_reason(tool, run=run, device_reason=no_flag) == (no_flag, [])
    assert run.calls == [], "no sudo, no status, no systemctl, no pgrep without --run-device"
    # The control: with the flag and every device condition clear, the same call does run sudo.
    clear = harness.camera_unplug_skip_reason(tool, run=run, device_reason=None)
    assert clear == (None, [str(node)])
    assert run.calls == [["sudo", "-n", str(tool), "status"]]


def test_the_camera_unplug_fixture_skips_before_running_anything_without_run_device(tmp_path):
    # The real fixture, its tool replaced by the fake one (the launcher is python, not sudo) and
    # device_level_reason NOT overridden, so the real flag logic decides. The fake tool logs every
    # call it gets: an empty log means not even `status` was started.
    tool, node, log = make_fake_tool(tmp_path)
    source = TOOL_OVERRIDES.format(tool=str(tool)) + """
    def test_uses_unplug(camera_unplug):
        raise AssertionError("must not run")
    """
    result = run_inner_pytest(tmp_path, source, "-q", "-rs")
    assert "1 skipped" in result.stdout, result.stdout + result.stderr
    assert "camera_unplug skipped: the device level is opt-in; add --run-device" in result.stdout
    assert logged_calls(log) == [] and not log.exists()
    assert node.exists()


def test_the_camera_unplug_fixture_with_the_flag_still_obeys_a_failing_condition(tmp_path):
    tool, node, log = make_fake_tool(tmp_path)
    source = TOOL_OVERRIDES.format(tool=str(tool)) + """
    def test_uses_unplug(camera_unplug):
        raise AssertionError("must not run")
    """
    result = run_inner_pytest(tmp_path, source, "-q", "-rs", "--run-device", blocker=True)
    assert "1 skipped" in result.stdout, result.stdout + result.stderr
    assert "camera_unplug skipped: the GStreamer Python bindings (gi / Gst) are not installed" in result.stdout
    assert "opt-in" not in result.stdout
    assert not log.exists() and node.exists()


@pytest.mark.device
def test_simulated_unplug_roundtrip_returns_the_camera(camera_unplug):
    """
    The live use of .claude/tools/usb_sim.py: detach the camera for 10 s, its video nodes
    disappear within 5 s; attach, they return within 15 s. Runs only with `--run-device`
    (skipped, with the reason, otherwise) and when every device condition holds. NOT run by the
    Developer; the Tester runs it. Teardown re-attaches even if an assertion here fails.
    """
    assert camera_unplug.nodes, "the status check found no video nodes for the camera"
    camera_unplug.detach(10)
    assert camera_unplug.wait_for_nodes(present=False, timeout=5), (
        f"the camera's video nodes {camera_unplug.nodes} were still there 5 s after the detach")
    camera_unplug.attach()
    assert camera_unplug.wait_for_nodes(present=True, timeout=15), (
        f"the camera's video nodes {camera_unplug.nodes} did not return within 15 s of the attach")


# ============================================================
# Criterion 6: the evidence run (canned JUnit XML; pytest itself is not launched here)
# ============================================================
@pytest.mark.parametrize("text, expected", [
    ("tests/uat/test_tc024_003_hazard.py::test_a", ["TC-024-003"]),
    ("tests.uat.test_tc_024_003.test_a", ["TC-024-003"]),
    ("test_TC-024-003_something", ["TC-024-003"]),
    ("Tc-001-002 and tc001_003", ["TC-001-002", "TC-001-003"]),
    ("test_x[tc024_003]", ["TC-024-003"]),
    ("tc024_003 tc024_003", ["TC-024-003"]),
    ("etc024_003", []),
    ("tc24_3", []),
    ("tc024_0031", []),
    ("test_depth_utils.py::test_scene", []),
], ids=["path-style", "dotted-with-underscore", "dashed-upper-case", "two-in-one-name", "in-brackets",
        "repeated", "inside-a-longer-word", "too-few-digits", "too-many-digits", "no-id"])
# The ids are explicit on purpose: a parametrize id that looked like a case number would put a
# made-up UAT case into the evidence log's case table.
def test_case_ids_are_found_and_normalised(text, expected):
    assert evidence.find_case_ids(text) == expected


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="9">
 <testcase classname="tests.uat.test_tc001_001" name="test_pass_a" time="0.1"/>
 <testcase classname="tests.uat.test_tc001_001" name="test_pass_b" time="0.1"/>
 <testcase classname="tests.uat.test_tc002_001" name="test_pass" time="0.1"/>
 <testcase classname="tests.uat.test_tc002_001" name="test_fail" time="0.1"><failure message="assert 1 == 2">tb</failure></testcase>
 <testcase classname="tests.uat.test_tc003_001" name="test_device" time="0"><skipped type="pytest.skip" message="device test skipped: the Hailo chip is not present (/dev/hailo0 does not exist)">/x.py:1: reason</skipped></testcase>
 <testcase classname="tests.uat.test_tc003_001" name="test_device_two" time="0"><skipped type="pytest.skip" message="device test skipped: the Hailo chip is not present (/dev/hailo0 does not exist)">r</skipped></testcase>
 <testcase classname="tests.uat.test_tc004_001" name="test_unit" time="0.1"/>
 <testcase classname="tests.uat.test_tc004_001" name="test_gst" time="0"><skipped type="pytest.skip" message="gst test skipped: not installed">r</skipped></testcase>
 <testcase classname="tests.uat.test_tc005_001" name="test_teardown_broke" time="0.1"><error message="teardown boom">tb</error></testcase>
 <testcase classname="tests.test_depth_utils" name="test_scene[a]" time="0.1"/>
</testsuite></testsuites>
"""


def test_junit_parsing_gives_passed_failed_and_skipped_with_reasons():
    results = {r.name: r for r in evidence.parse_junit(JUNIT)}
    assert results["tests.uat.test_tc001_001.test_pass_a"].status == "passed"
    assert results["tests.uat.test_tc002_001.test_fail"].status == "failed"
    skipped = results["tests.uat.test_tc003_001.test_device"]
    assert skipped.status == "skipped" and "/dev/hailo0" in skipped.reason
    assert results["tests.uat.test_tc005_001.test_teardown_broke"].status == "failed"


def test_cases_aggregate_per_case_and_a_skip_never_reads_as_a_pass():
    cases = {c.case_id: c for c in evidence.aggregate(evidence.parse_junit(JUNIT))}
    assert set(cases) == {"TC-001-001", "TC-002-001", "TC-003-001", "TC-004-001", "TC-005-001"}
    assert cases["TC-001-001"].status == "PASS"                       # all passed
    assert cases["TC-002-001"].status == "FAIL"                       # one failed
    assert cases["TC-005-001"].status == "FAIL"                       # errored
    assert cases["TC-003-001"].status == "SKIP"                       # all skipped
    assert "/dev/hailo0" in cases["TC-003-001"].detail
    assert cases["TC-003-001"].detail.count("the Hailo chip") == 1    # the same reason once
    # Part of the case did not run: not PASS.
    assert cases["TC-004-001"].status == "SKIP" and "partial: 1 passed, 1 skipped" in cases["TC-004-001"].detail


def test_expected_failures_are_reported_as_skips_with_their_kind():
    xml = ('<testsuites><testsuite><testcase classname="t.test_tc009_001" name="x">'
           '<skipped type="pytest.xfail" message="known bug"/></testcase></testsuite></testsuites>')
    case = evidence.aggregate(evidence.parse_junit(xml))[0]
    assert case.status == "SKIP" and "expected failure: known bug" in case.detail


def test_case_table_lists_each_case_with_verdict_reason_and_test_names():
    table = evidence.render_case_table(evidence.aggregate(evidence.parse_junit(JUNIT)))
    lines = table.splitlines()
    assert lines[0].startswith("TC-001-001  PASS")
    assert "tests.uat.test_tc001_001.test_pass_a [passed]" in lines[1]
    assert any(l.startswith("TC-003-001  SKIP (device test skipped: the Hailo chip") for l in lines)
    assert any(l.startswith("TC-002-001  FAIL") for l in lines)
    assert "PASS" not in next(l for l in lines if l.startswith("TC-003-001"))


def test_case_table_says_so_when_no_case_ids_exist():
    assert "No UAT case IDs" in evidence.render_case_table([])
    assert "No test results" in evidence.render_case_table([], any_tests=False)


def test_totals_line_counts_tests_cases_and_the_exit_code():
    results = evidence.parse_junit(JUNIT)
    line = evidence.totals_line(results, evidence.aggregate(results), 1)
    assert line == ("TOTALS: 5 passed, 2 failed, 3 skipped (10 tests); cases: 1 PASS, 2 FAIL, 2 SKIP "
                    "(5 cases); pytest exit code 1")


def test_levels_are_read_from_the_pytest_header():
    output = ("platform linux\nsv-harness gst level: AVAILABLE (GStreamer 1.26.2)\n"
              "sv-harness device level: UNAVAILABLE - second-vision.service is active\nrootdir: /x\n")
    assert evidence.parse_levels(output) == {
        "gst": "AVAILABLE (GStreamer 1.26.2)", "device": "UNAVAILABLE - second-vision.service is active"}
    assert evidence.parse_levels("no harness here") == {}


def _header(**override):
    from datetime import datetime
    values = dict(task="T-10a", command_line="/py scripts/uat_evidence.py --task T-10a",
                  when=datetime(2026, 10, 16, 5, 30, 1), git_commit="cb0b20e", dirty=True,
                  python="/py (3.13.5)", pytest_version="pytest 9.1.1", service_state="inactive",
                  levels={"gst": "AVAILABLE (GStreamer 1.26.2)", "device": "UNAVAILABLE - no /dev/hailo0"},
                  pytest_cmd="python -m pytest -v -rA tests/", device_requested=True, max_minutes=30.0)
    values.update(override)
    return evidence.build_header(**values)


def test_header_has_every_required_fact():
    header = _header()
    for expected in ("/py scripts/uat_evidence.py --task T-10a", "2026-10-16 05:30:01", "cb0b20e",
                     "with uncommitted task changes", "/py (3.13.5)", "pytest 9.1.1",
                     "second-vision.service: inactive", "AVAILABLE (GStreamer 1.26.2)",
                     "UNAVAILABLE - no /dev/hailo0"):
        assert expected in header, expected


def test_header_says_whether_the_device_level_was_requested_and_the_time_limit():
    asked = _header(device_requested=True)
    assert "Device level requested: yes (--device: pytest was given --run-device)" in asked
    assert "Time limit:            30 minutes (--max-minutes)" in asked
    not_asked = _header(device_requested=False, max_minutes=0.5)
    assert "Device level requested: no (device tests are skipped; add --device to run them)" in not_asked
    assert "Time limit:            0.5 minutes" in not_asked


def test_header_only_says_uncommitted_when_git_reports_changes():
    assert "uncommitted" not in _header(dirty=False) and "clean tree" in _header(dirty=False)
    assert "could not tell" in _header(dirty=None)
    assert "was not loaded" in _header(levels={})


def test_log_name_label_and_no_overwrite(tmp_path):
    assert evidence.derive_label([], None) == "all"
    assert evidence.derive_label([], "my run/x") == "my-run-x"
    assert evidence.derive_label(["tests/test_harness.py", "tests/uat/test_tc024_003.py::test_a"], None) \
        == "test_harness+test_tc024_003"
    first = evidence.choose_log_path(tmp_path, "all", "2026-10-16")
    assert first.name == "2026-10-16-all.log"
    first.write_text("x")
    assert evidence.choose_log_path(tmp_path, "all", "2026-10-16").name == "2026-10-16-all-2.log"


def test_evidence_script_end_to_end_on_scratch_tests(tmp_path):
    # Runs the real script, which launches a real (inner) pytest on three scratch tests, and checks
    # the log, the case table and that the exit code is pytest's.
    (tmp_path / "test_tc901_001_pass.py").write_text("def test_ok():\n    pass\n")
    (tmp_path / "test_tc901_002_fail.py").write_text("def test_bad():\n    assert 1 == 2\n")
    (tmp_path / "test_tc901_003_skip.py").write_text(
        "import pytest\ndef test_skipped():\n    pytest.skip('needs the camera')\n")
    out = tmp_path / "evidence"
    tests = [str(tmp_path / n) for n in ("test_tc901_001_pass.py", "test_tc901_002_fail.py", "test_tc901_003_skip.py")]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_TIMEOUT")}
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "uat_evidence.py"), "--task", "T-scratch",
                             "--tests", *tests, "--label", "scratch", "--out", str(out)],
                            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 1, result.stdout + result.stderr
    logs = list(out.glob("*-scratch.log"))
    assert len(logs) == 1
    text = logs[0].read_text()
    for expected in ("UAT EVIDENCE LOG", "Task:                  T-scratch", "pytest:                pytest ",
                     "Git commit:", "second-vision.service:", "PYTEST OUTPUT", "UAT CASE TABLE"):
        assert expected in text, expected
    assert "TC-901-001  PASS" in text and "TC-901-002  FAIL" in text
    assert "TC-901-003  SKIP (needs the camera)" in text
    assert "TC-901-003  PASS" not in text
    assert "Device level requested: no" in text and "Time limit:            30 minutes" in text
    assert "--run-device" not in next(l for l in text.splitlines() if l.startswith("pytest command:"))
    last = text.strip().splitlines()[-1]
    assert last == ("TOTALS: 1 passed, 1 failed, 1 skipped (3 tests); cases: 1 PASS, 1 FAIL, 1 SKIP "
                    "(3 cases); pytest exit code 1")


def test_arguments_after_a_bare_double_dash_go_to_pytest():
    assert evidence.split_passthrough(["--task", "T-1", "--", "-m", "not device"]) == (
        ["--task", "T-1"], ["-m", "not device"])
    assert evidence.split_passthrough(["--task", "T-1"]) == (["--task", "T-1"], [])
    command = evidence.pytest_command(["tests/"], "/x/junit.xml", ["-m", "not device"])
    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert command[3:] == ["-v", "-rA", "--color=no", "--junitxml=/x/junit.xml", "-m", "not device", "tests/"]


def test_the_device_option_adds_run_device_to_the_pytest_command_and_nothing_else():
    plain = evidence.pytest_command(["tests/"], "/x/junit.xml", ["-k", "tc024"])
    with_device = evidence.pytest_command(["tests/"], "/x/junit.xml", ["-k", "tc024"], device=True)
    assert "--run-device" not in plain
    assert with_device == plain[:7] + ["--run-device"] + plain[7:]
    assert with_device.count("--run-device") == 1 and with_device[-1] == "tests/"
    assert evidence.pytest_command(["tests/"], "/x/junit.xml", [], device=False) == (
        evidence.pytest_command(["tests/"], "/x/junit.xml", []))


def test_evidence_script_refuses_a_task_id_that_is_not_a_plain_id(tmp_path):
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "uat_evidence.py"),
                             "--task", "../../etc", "--out", str(tmp_path / "o")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "plain ID" in result.stderr
    assert not (tmp_path / "o").exists()


def _run_evidence_script(*args, env_extra=None, timeout=120):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_TIMEOUT")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "uat_evidence.py"), *args],
                          cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def test_device_adds_run_device_to_pytest_and_the_log_says_it_was_requested(tmp_path):
    # tests/conftest.py is loaded as a plugin (`-- -p conftest`) so that pytest knows --run-device.
    # The scratch test only passes, so it does no harm wherever the device level is available.
    scratch = tmp_path / "test_tc902_001_device.py"
    scratch.write_text("import pytest\n@pytest.mark.device\ndef test_marked_device():\n    pass\n")
    plugin = {"PYTHONPATH": str(TESTS_DIR)}
    off, on = tmp_path / "off", tmp_path / "on"
    result_off = _run_evidence_script("--task", "T-scratch", "--tests", str(scratch), "--label", "x",
                                      "--out", str(off), "--", "-p", "conftest", env_extra=plugin)
    result_on = _run_evidence_script("--task", "T-scratch", "--tests", str(scratch), "--label", "x",
                                     "--out", str(on), "--device", "--", "-p", "conftest", env_extra=plugin)
    assert result_off.returncode == 0 and result_on.returncode == 0, result_off.stdout + result_on.stdout
    log_off, log_on = (next(folder.glob("*-x.log")).read_text() for folder in (off, on))
    header_off, header_on = (log.split("PYTEST OUTPUT")[0] for log in (log_off, log_on))
    # Without --device: the header says so, pytest got no --run-device, the case is a SKIP for opt-in.
    command_off, command_on = (next(l for l in h.splitlines() if l.startswith("pytest command:"))
                               for h in (header_off, header_on))
    assert "Device level requested: no" in header_off and "--run-device" not in command_off
    assert "sv-harness device level: OFF (opt-in: add --run-device)" in log_off
    assert "TC-902-001  SKIP (device test skipped: the device level is opt-in; add --run-device" in log_off
    # With --device: the header says so and shows the flag in the pytest command; the skip is not for opt-in.
    assert "Device level requested: yes" in header_on and "--run-device" in command_on
    assert "OFF (opt-in" not in log_on
    case_line = next(l for l in log_on.splitlines() if l.startswith("TC-902-001"))
    assert "opt-in" not in case_line and ("PASS" in case_line or case_line.startswith("TC-902-001  SKIP"))


def test_max_minutes_must_be_a_positive_number(tmp_path):
    for bad in ("0", "-1", "nan", "inf", "soon"):
        result = _run_evidence_script("--task", "T-scratch", "--max-minutes", bad, "--out", str(tmp_path / "o"),
                                      timeout=30)
        assert result.returncode == 2 and "--max-minutes" in result.stderr, (bad, result.stderr)
    assert not (tmp_path / "o").exists()
    assert evidence.positive_minutes("0.05") == 0.05 and evidence.positive_minutes("30") == 30.0


# ---- the time limit -----------------------------------------------------------------------
def _alive(pid):
    """True if `pid` is a running process (a zombie that nobody has reaped yet counts as gone)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _wait_gone(*pids, within=5.0):
    limit = time.monotonic() + within
    while time.monotonic() < limit and any(_alive(pid) for pid in pids):
        time.sleep(0.05)
    return [pid for pid in pids if _alive(pid)]


HANG = "import os, time; print('started', os.getpid(), os.getpgid(0), flush=True); time.sleep(60)"
CHILD_AND_HANG = (
    "import os, subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print('started', os.getpid(), os.getpgid(0), child.pid, os.getpgid(child.pid), flush=True)\n"
    "time.sleep(60)\n")
IGNORES_SIGTERM = (
    "import os, signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('started', os.getpid(), os.getpgid(0), flush=True)\n"
    "time.sleep(60)\n")


class _Echo:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text

    def flush(self):
        pass


def test_a_run_that_outlasts_the_time_limit_is_stopped_with_the_output_so_far():
    echo = _Echo()
    started = time.monotonic()
    run = evidence.run_pytest([sys.executable, "-c", HANG], max_seconds=1.5, grace=5, echo=echo)
    assert time.monotonic() - started < 10
    assert run.timed_out is True
    assert run.exit_code != 0
    word, pid, pgid = run.output.split()
    assert word == "started" and echo.text == run.output                # what was captured, and echoed
    assert int(pgid) == int(pid) != os.getpgid(0), "pytest runs in a process group of its own"
    assert _wait_gone(int(pid)) == []


def test_a_run_inside_the_time_limit_is_left_alone_and_keeps_its_exit_code():
    run = evidence.run_pytest([sys.executable, "-c", "print('done'); raise SystemExit(3)"],
                              max_seconds=30, echo=_Echo())
    assert (run.exit_code, run.timed_out, run.output) == (3, False, "done\n")
    run = evidence.run_pytest([sys.executable, "-c", "print('no limit')"], max_seconds=None, echo=_Echo())
    assert (run.exit_code, run.timed_out) == (0, False)


def test_the_time_limit_stops_the_children_in_the_group_and_nothing_outside_it():
    run = evidence.run_pytest([sys.executable, "-c", CHILD_AND_HANG], max_seconds=2, grace=5, echo=_Echo())
    assert run.timed_out
    _, pid, pgid, child, child_pgid = run.output.split()
    assert pgid == pid == child_pgid, "the child is in the group that was signalled"
    assert _wait_gone(int(pid), int(child)) == []
    assert _alive(os.getpid()) and os.getpgid(0) != int(pgid)          # this test run was not touched


def test_a_process_that_ignores_sigterm_gets_sigkill_after_the_grace():
    started = time.monotonic()
    run = evidence.run_pytest([sys.executable, "-c", IGNORES_SIGTERM], max_seconds=2, grace=1.5, echo=_Echo())
    elapsed = time.monotonic() - started
    assert run.timed_out and run.exit_code == -signal.SIGKILL, run
    assert 3.4 <= elapsed < 15, "the limit (2 s) plus the grace (1.5 s) before SIGKILL"
    assert _wait_gone(int(run.output.split()[1])) == []


def test_ctrl_c_stops_the_run_it_started_and_goes_on():
    echo = _Echo()
    timer = threading.Timer(1.5, _thread.interrupt_main)          # what Ctrl-C does to the main thread
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            evidence.run_pytest([sys.executable, "-c", CHILD_AND_HANG], max_seconds=60, grace=5, echo=echo)
    finally:
        timer.cancel()
    _, pid, _, child, _ = echo.text.split()
    assert _wait_gone(int(pid), int(child)) == []


def test_the_script_stops_a_hung_pytest_writes_the_log_and_exits_124(tmp_path):
    marker = tmp_path / "pytest_pid.txt"
    (tmp_path / "test_tc903_001_hangs.py").write_text(
        f"import os, time\ndef test_hangs():\n    open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(120)\n")
    out = tmp_path / "evidence"
    started = time.monotonic()
    result = _run_evidence_script("--task", "T-scratch", "--tests", str(tmp_path / "test_tc903_001_hangs.py"),
                                  "--label", "hang", "--out", str(out), "--max-minutes", "0.1", timeout=90)
    assert time.monotonic() - started < 60, "the script must not wait for the 120 s sleep"
    assert result.returncode == 124, result.stdout + result.stderr
    assert "TIMED OUT after 0.1 minutes: the run was stopped" in result.stdout
    (log,) = out.glob("*-hang.log")
    text = log.read_text()
    stopped = "TIMED OUT after 0.1 minutes: the run was stopped"
    output_part, table_part = text.split("UAT CASE TABLE")
    assert stopped in output_part.split("PYTEST OUTPUT")[1], "in the log, after pytest's output"
    assert table_part.lstrip("= \n").startswith(stopped), "and first in the case table"
    assert "test_tc903_001_hangs.py::test_hangs" in output_part, "everything captured so far is kept"
    assert "Time limit:            0.1 minutes" in text
    assert text.strip().splitlines()[-1].endswith("pytest exit code 124")
    assert marker.exists(), "pytest never reached the test: the 6 s limit is too short on this machine"
    assert _wait_gone(int(marker.read_text())) == [], "the pytest this script started is gone"
