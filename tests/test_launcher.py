"""
T-03b: the boot launcher scripts/sv-main.sh (REQ-CON-08, "camera absent at start-up").

What it must do: build the same command line as before when a camera is attached, and, when no
camera is attached, start the program anyway (so the glasses link, the panel and the speech run
and the program looks for a camera itself) instead of waiting for the camera and exiting, or
falling back to mock mode. Mock mode is never passed, with or without SV_SERVICE=1 (decision D-13).

Unit level, hardware-free: the launcher is run for real (bash), but its last step, scripts/run.sh,
is replaced by a stub that records the arguments it was called with. The environment variable
SV_RUN_SH points the launcher at the stub. Nothing else is started: no program, no camera, no
Hailo chip, no service.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_launcher.py -v

SV_LAUNCHER_UNDER_TEST (an environment variable) names another sv-main.sh: a copy of the baseline
launcher, to show that these tests fail on it (red before green). The launcher is run only if it
can be redirected to the stub, so a test run can never start the real program: either it honours
SV_RUN_SH, or its own scripts/run.sh is a stub (a scratch tree built for that purpose, marked
STUB-RUN-SH inside).
"""

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = Path(os.environ.get("SV_LAUNCHER_UNDER_TEST") or REPO / "scripts" / "sv-main.sh")
SERVICE_UNIT = LAUNCHER.parent / "second-vision.service"
STUB = """#!/bin/bash
# STUB-RUN-SH: records its arguments, starts nothing.
printf '%s\\n' "$@" > "$STUB_ARGS_FILE"
exit 0
"""
LAUNCH_SECONDS = 5.0     # the launcher with a stub takes milliseconds; the old one waited up to 60 s


def redirectable(launcher: Path) -> bool:
    if "SV_RUN_SH" in launcher.read_text():
        return True
    sibling = launcher.parent / "run.sh"
    return sibling.exists() and "STUB-RUN-SH" in sibling.read_text()


@pytest.fixture
def launch(tmp_path):
    """launch(...) -> (arguments the stub was called with or None, completed process, seconds)."""
    if not redirectable(LAUNCHER):
        pytest.fail(f"{LAUNCHER} cannot be pointed at a stub run.sh: refusing to run it, it would "
                    "start the real program", pytrace=False)
    stub = tmp_path / "stub-run.sh"
    stub.write_text(STUB)
    stub.chmod(0o755)
    args_file = tmp_path / "args.txt"
    camera = tmp_path / "video0"
    panel = tmp_path / "ttyAMA3"
    panel.touch()

    def run(*, camera_present: bool, panel_present: bool = True, display=None, wayland=None,
            service=None, extra_env=None):
        if camera_present:
            camera.touch()
        elif camera.exists():
            camera.unlink()
        if panel_present:
            panel.touch()
        elif panel.exists():
            panel.unlink()
        if args_file.exists():
            args_file.unlink()
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
               "SV_RUN_SH": str(stub), "STUB_ARGS_FILE": str(args_file),
               "SERIAL_PORT": "/dev/test-serial", "CONFIG_PORT": str(panel), "INPUT": str(camera),
               # The old launcher waited this long for a camera under SV_SERVICE=1; kept short so
               # a run against the old launcher fails quickly instead of waiting 60 s. The current
               # launcher ignores it.
               "CAMERA_WAIT_SECONDS": "2"}
        if display is not None:
            env["DISPLAY"] = display
        if wayland is not None:
            env["WAYLAND_DISPLAY"] = wayland
        if service is not None:
            env["SV_SERVICE"] = service
        env.update(extra_env or {})
        began = time.monotonic()
        done = subprocess.run([str(LAUNCHER)], env=env, capture_output=True, text=True,
                              timeout=30, cwd=tmp_path)
        took = time.monotonic() - began
        args = args_file.read_text().splitlines() if args_file.exists() else None
        return args, done, took

    run.camera_path = str(camera)
    run.panel_path = str(panel)
    return run


def expected(camera: str, panel: str | None, frame: bool) -> list[str]:
    """The command line the launcher has always built (baseline sv-main.sh, camera present)."""
    args = ["--input", camera, "--width", "640", "--height", "480"]
    if frame:
        args.append("--use-frame")
    args += ["--serial-port", "/dev/test-serial"]
    if panel is not None:
        args += ["--config-port", panel]
    return args


DISPLAYS = [
    pytest.param({}, False, id="headless"),
    pytest.param({"display": ":0"}, True, id="DISPLAY"),
    pytest.param({"wayland": "wayland-0"}, True, id="WAYLAND_DISPLAY"),
]
SERVICES = [pytest.param(None, id="no-SV_SERVICE"), pytest.param("0", id="SV_SERVICE=0"),
            pytest.param("1", id="SV_SERVICE=1")]


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("display, frame", DISPLAYS)
@pytest.mark.parametrize("panel_present", [True, False], ids=["panel-port", "no-panel-port"])
def test_t03b_con08_camera_attached_builds_the_same_command_line(launch, panel_present, display, frame,
                                                                 service):
    """Criterion 7: with a camera attached nothing changes (display rule, missing panel port included)."""
    args, done, took = launch(camera_present=True, panel_present=panel_present, service=service, **display)

    assert done.returncode == 0, done.stderr
    assert args == expected(launch.camera_path, launch.panel_path if panel_present else None, frame)
    assert took < LAUNCH_SECONDS


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("display, frame", DISPLAYS)
@pytest.mark.parametrize("panel_present", [True, False], ids=["panel-port", "no-panel-port"])
def test_t03b_con08_no_camera_still_starts_the_program_and_never_mock(launch, panel_present, display, frame,
                                                                      service):
    """
    Criteria 1 and 5. No camera node: the launcher still starts the program with the same
    command line (the program waits for a camera itself), at once, and never with --mock, with or
    without SV_SERVICE=1. (It used to wait up to 60 s and exit 1 under the service, and to run
    --mock otherwise.)
    """
    args, done, took = launch(camera_present=False, panel_present=panel_present, service=service, **display)

    assert args is not None, f"the program was not started (exit {done.returncode}): {done.stdout}{done.stderr}"
    assert "--mock" not in args
    assert args == expected(launch.camera_path, launch.panel_path if panel_present else None, frame)
    assert done.returncode == 0
    assert took < LAUNCH_SECONDS, f"the launcher took {took:.1f} s: it must not wait for a camera"
    assert "--mock" not in done.stdout + done.stderr, "the launcher's own output offers mock mode"


def test_t03b_con08_no_camera_says_so_in_the_log(launch):
    """The journal tells a reader why there is no vision yet."""
    _, done, _ = launch(camera_present=False, service="1")

    assert launch.camera_path in done.stdout
    assert "no camera" in done.stdout.lower()


def test_t03b_con08_no_launcher_path_can_start_mock_mode():
    """Criterion 5, source check: --mock appears on no line of the launcher except comments."""
    lines = LAUNCHER.read_text().splitlines()
    code = [ln for ln in lines if not ln.lstrip().startswith("#")]
    assert [ln for ln in code if "mock" in ln.lower()] == [], \
        "a non-comment line of the launcher mentions mock mode"
    assert [ln for ln in code if re.search(r"\bexec\b", ln) and "$RUN" not in ln] == [], \
        "the launcher execs something other than run.sh"


def test_t03b_con08_launcher_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(LAUNCHER)], capture_output=True, text=True).returncode == 0


def test_t03b_con08_launcher_default_run_target_is_unchanged():
    """Without SV_RUN_SH the launcher runs scripts/run.sh next to it, as it always did."""
    text = LAUNCHER.read_text()
    assert re.search(r'^RUN="(\$\{SV_RUN_SH:-)?\$REPO_ROOT/scripts/run\.sh\}?"$', text, re.M), \
        "RUN no longer defaults to $REPO_ROOT/scripts/run.sh"


def test_t03b_con08_service_unit_no_longer_promises_a_wait_or_a_hard_fail():
    """The unit's comment said 'Wait for the camera and hard-fail instead of mock mode'."""
    text = SERVICE_UNIT.read_text()
    assert "hard-fail" not in text and "Wait for the camera" not in text
    assert "Environment=SV_SERVICE=1" in text, "comments only: the unit's settings must not change"
