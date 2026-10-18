"""
TC-024-002, REQ-CON-08 half only: the USB camera is absent at start-up. Task T-03b. Written by the
Tester (tests/uat/).

These tests cover ONLY the "absent at start-up" half of REQ-CON-08. The case's alert half (the
vibration pulses and the spoken "Critical Error: Camera cannot be reached" every 10 s, REQ-CON-07)
is task T-03c and is NOT built: a PASS line for TC-024-002 from this file says nothing about it.

Workbook TC-024-002, verbatim (docs/testing/uat-snapshot.yaml, Test Cases!A262:G262):
    steps:    1. Power on Raspberry Pi 5
    input:    USB Camera is not Connected
    expected: There should be a quick set of vibration pulse alongside a "Critical Error: Camera cannot be
              reached" audio error. This should be repeated every 10 seconds

              In terms of the pipeline, the system should catch the no devices exception and allow the
              device to proceed in mock mode temporarily. Device should also listen for cameras that may
              pop up in the list of devices to check if everything is okay.
The workbook's own wording "proceed in mock mode temporarily" is overruled by D-13 (REQ-CON-08: "It
must never fall back to the testing mock mode"); these tests assert the opposite.

Level: `unit` tests (no marker) run anywhere; `device` tests (opt-in) need the boot service stopped,
the camera attached, passwordless sudo for the tool:
    my_hailo_env/bin/python -m pytest tests/uat/test_tc024_002_camera_absent_at_start.py -v -rA --run-device

What is real in the device tests: the launcher scripts/sv-main.sh (as the boot service runs it,
with SV_SERVICE=1, no DISPLAY, and its INPUT default /dev/video0), scripts/run.sh, the program, the
camera, the Hailo chip, and the simulated unplug (.claude/tools/usb_sim.py through the conftest
`camera_unplug` fixture). Stand-ins: the glasses ESP32 and the control panel are pseudo-terminals
(the harness of tests/uat/test_tc024_003_camera_removed.py: FakeGlasses ACKs and records, FakePanel
sends `M:<mode>`).

"Camera appears at T s" below means: the `attach camera` command is issued T s after the launch;
the camera's nodes come back about 1.5 s later (the kernel enumerating it, as for a plugged cable).

usb_sim.py defect designed around: every `detach --for N` leaves a timer that re-attaches whichever
detach is current after N s. So: no new detach while an earlier timer is pending (checked with
pgrep), --for just above the planned attach time, and the camera asserted gone before relying on it.
"""

from __future__ import annotations

import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_tc024_003_camera_removed import (  # the T-03a harness, used as it is
    AppRun, FakeGlasses, FakePanel, REPO, CAMERA_NODE, MOTOR_ZERO, CAMERA_BACK, RECOVERY_TIMEOUT,
    STOP_BOUND_SECONDS, START_TIMEOUT, ANSI, NEVER, cpu_ticks, wait_no_pending_timer, assert_clean,
)

device = pytest.mark.device
LAUNCHER = REPO / "scripts" / "sv-main.sh"
MODES = ["both", "depth", "detection", "none"]
NO_CAMERA_LINE = "No camera at start-up"
FOUND = "Camera found on"
STILL = "Still no USB camera after"
PANEL_UP = "Connected to control panel"
PIPELINE_STRING = "Generated Pipeline string"
STOP_ABOUT_ONE_SECOND = 2.0       # the brief says "within about 1 s"; measured 0.3 to 1.0 s


# ============================================================
# The program, started the way the boot service starts it
# ============================================================
class LauncherRun(AppRun):
    """scripts/sv-main.sh with SERIAL_PORT / CONFIG_PORT pointed at the stand-ins; INPUT left at its default."""

    def __init__(self, log_path: Path, serial_port: str, config_port: str, *, service: str | None = "1",
                 run_sh: str | None = None, extra_env: dict | None = None):
        self.log_path = log_path
        self.cmd = [str(LAUNCHER)]
        env = dict(os.environ)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SV_SERVICE", "INPUT", "SV_RUN_SH"):
            env.pop(name, None)
        if service is not None:
            env["SV_SERVICE"] = service
        if run_sh:
            env["SV_RUN_SH"] = run_sh
        env.update({"SERIAL_PORT": serial_port, "CONFIG_PORT": config_port, "PYTHONUNBUFFERED": "1"})
        env.update(extra_env or {})
        self.lines: list[tuple[float, str]] = []
        self._log = open(log_path, "w", buffering=1)
        self._log.write(f"# command: {' '.join(self.cmd)} (SV_SERVICE={service} SV_RUN_SH={run_sh})\n"
                        f"# started: {time.strftime('%F %T %Z')}\n")
        self.started_at = time.monotonic()
        # sv-main.sh exec's run.sh which exec's python: this PID is the program; own session.
        self.proc = subprocess.Popen(self.cmd, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.pid = self.proc.pid
        import threading
        self._reader = threading.Thread(target=self._read, daemon=True, name="app-output")
        self._reader.start()

    def since_launch(self, t: float) -> float:
        return t - self.started_at

    def cmdline(self) -> list[str]:
        try:
            return Path(f"/proc/{self.pid}/cmdline").read_bytes().decode().split("\0")
        except OSError:
            return []

    def open_paths(self, prefix: str) -> list[str]:
        found = set()
        try:
            for fd in Path(f"/proc/{self.pid}/fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith(prefix):
                    found.add(target)
        except OSError:
            pass
        return sorted(found)

    def count(self, needle: str, start: int = 0) -> int:
        return sum(1 for _, line in list(self.lines)[start:] if needle in line)


@pytest.fixture
def uat_log_dir(tmp_path) -> Path:
    where = os.environ.get("SV_UAT_APP_LOG_DIR")
    path = Path(where) if where else tmp_path
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def sv(request, uat_log_dir):
    """sv(service="1", run_sh=None) -> (app, glasses, panel); everything stopped by PID at the end."""
    runs, stand_ins = [], []

    def start(service: str | None = "1", run_sh: str | None = None, tag: str = "", real_ports: bool = False):
        if real_ports:
            glasses = panel = None
            ports = ("/dev/serial0", "/dev/ttyAMA3")        # the launcher's own defaults
        else:
            glasses, panel = FakeGlasses(), FakePanel()
            stand_ins.extend([glasses, panel])
            ports = (glasses.path, panel.path)
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name) + (f"-{tag}" if tag else "")
        stamp = time.strftime("%H%M%S")
        app = LauncherRun(uat_log_dir / f"{time.strftime('%Y-%m-%d')}-{stamp}-app-{name}.log",
                          ports[0], ports[1], service=service, run_sh=run_sh)
        runs.append(app)
        return app, glasses, panel

    yield start
    for app in runs:
        app.close()
    for stand_in in stand_ins:
        stand_in.close()


def start_without_camera(camera_unplug, sv, hold: int, **kw):
    """Detach the camera (no stale timer pending; the tool's own timer is the fallback replug after
    `hold` s), assert its nodes are gone, then start the program through the boot launcher."""
    wait_no_pending_timer(limit=max(hold, 30) + 15)
    assert camera_unplug.nodes_present() or os.path.exists(CAMERA_NODE), "no camera attached to begin with"
    camera_unplug.detach(hold)
    assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
    return sv(**kw)


def await_waiting(app: LauncherRun, timeout: float = 20):
    hit = app.wait_for(NO_CAMERA_LINE, timeout)
    assert hit, f"the program did not say it started without a camera (alive={app.alive()}):\n{app.text()[-3000:]}"
    return hit


def await_panel(app: LauncherRun, timeout: float = 20):
    assert app.wait_for(PANEL_UP, timeout), f"panel reader not up:\n{app.text()[-2000:]}"


def appear_at(camera_unplug, app: LauncherRun, delay: float) -> float:
    """Issue `attach camera` `delay` s after the launch; waits for the nodes; returns the log index before."""
    pause = app.started_at + delay - time.monotonic()
    if pause > 0:
        time.sleep(pause)
    assert not camera_unplug.nodes_present(), \
        "the camera came back before the test attached it (a stale usb_sim timer?)"
    index = app.index()
    app.mark(f"usb_sim attach camera (planned {delay} s after the launch)")
    result = camera_unplug.attach()
    assert result.returncode == 0, f"attach failed: {result.stdout} {result.stderr}"
    assert camera_unplug.wait_for_nodes(True, 15), "the camera's video nodes did not come back"
    app.mark(f"camera nodes back: {camera_unplug.nodes}")
    return index


def await_vision(app: LauncherRun, since: int, mode: str, timeout: float = RECOVERY_TIMEOUT):
    """The camera is found once and vision runs in `mode`; returns the 'Camera back' hit."""
    found = app.wait_for(FOUND, 20, since)
    assert found, f"the camera was not found:\n{app.text(since)[-3000:]}"
    assert f"(mode={mode})" in found[2], found[2]
    back = app.wait_for(CAMERA_BACK, timeout, since)
    assert back, f"vision did not start:\n{app.text(since)[-3500:]}"
    assert f"(mode={mode})" in back[2], back[2]
    if mode in ("both", "depth"):
        assert app.wait_frames(back[0], 3, 25), f"no depth frames in mode {mode}:\n{app.text(back[0])[-2500:]}"
    else:
        time.sleep(3)
        assert app.alive()
    assert app.open_paths("/dev/video"), "the program has no camera node open although vision runs"
    if mode != "none":
        assert app.open_paths("/dev/hailo"), "no Hailo device open in a Hailo mode"
    return back


def finish(app: LauncherRun, signum=signal.SIGINT, bound: float = STOP_BOUND_SECONDS) -> float:
    code, took = app.stop(signum)
    assert code == 0, f"exit code {code} after {signal.Signals(signum).name}:\n{app.text()[-3000:]}"
    assert took < bound, f"took {took:.2f} s"
    assert app.leftovers() == [], f"left running: {app.leftovers()}"
    assert_clean(app)
    return took


def send_mode(app: LauncherRun, panel: FakePanel, mode: str):
    start = app.index()
    app.mark(f"panel sends M:{mode}")
    panel.send(f"M:{mode}")
    assert app.wait_for(f"[CONFIG] Mode: {mode}", 8, start), f"the panel line was not read:\n{app.text(start)[-1500:]}"


# ============================================================
# unit level (no marker): the launcher, main(), no hardware
# ============================================================
STUB = '#!/bin/bash\nprintf \'%s\\n\' "$@" > "$STUB_ARGS_FILE"\nexit ${STUB_EXIT:-0}\n'


def run_launcher(tmp_path, *, camera: bool, panel: bool, display=None, wayland=None, service=None,
                 stub_exit=0, input_name="video0"):
    stub = tmp_path / "stub-run.sh"
    stub.write_text(STUB)
    stub.chmod(0o755)
    args_file = tmp_path / "args.txt"
    if args_file.exists():
        args_file.unlink()
    cam, pan = tmp_path / input_name, tmp_path / "ttyAMA3"
    for path, present in ((cam, camera), (pan, panel)):
        if present:
            path.touch()
        elif path.exists():
            path.unlink()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "SV_RUN_SH": str(stub),
           "STUB_ARGS_FILE": str(args_file), "STUB_EXIT": str(stub_exit), "SERIAL_PORT": "/dev/test-serial",
           "CONFIG_PORT": str(pan), "INPUT": str(cam)}
    for key, value in (("DISPLAY", display), ("WAYLAND_DISPLAY", wayland), ("SV_SERVICE", service)):
        if value is not None:
            env[key] = value
    began = time.monotonic()
    done = subprocess.run([str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=30, cwd=tmp_path)
    args = args_file.read_text().splitlines() if args_file.exists() else None
    return args, done, time.monotonic() - began, str(cam), str(pan)


def baseline_command_line(cam, pan, frame, with_panel):
    """Written from the brief (7) and the baseline launcher's two exec lines, not from the new script."""
    line = ["--input", cam, "--width", "640", "--height", "480"] + (["--use-frame"] if frame else [])
    line += ["--serial-port", "/dev/test-serial"]
    return line + (["--config-port", pan] if with_panel else [])


@pytest.mark.parametrize("service", [None, "0", "1"], ids=["no-service", "service0", "service1"])
@pytest.mark.parametrize("camera", [True, False], ids=["camera", "no-camera"])
@pytest.mark.parametrize("panel", [True, False], ids=["panel", "no-panel"])
@pytest.mark.parametrize("env_display", [{}, {"display": ":0"}, {"wayland": "wayland-0"},
                                         {"display": ":0", "wayland": "wayland-0"}],
                         ids=["headless", "DISPLAY", "WAYLAND", "both-displays"])
def test_tc024_002_con08_launcher_never_mock_and_same_command_line(tmp_path, service, camera, panel, env_display):
    """
    Criteria 5 (launcher) and 7. Whatever the camera, panel port, display and SV_SERVICE: the program is
    started (the stub is reached, exit status passed through), with the baseline's command line, and
    `--mock` is never among the arguments or in the launcher's own output.
    """
    args, done, took, cam, pan = run_launcher(tmp_path, camera=camera, panel=panel, service=service, **env_display)
    frame = bool(env_display)
    assert done.returncode == 0, done.stderr
    assert args == baseline_command_line(cam, pan, frame, panel), args
    assert all("mock" not in a.lower() for a in args)
    assert "mock" not in (done.stdout + done.stderr).lower().replace("mock mode is never", "")
    assert took < 5, f"the launcher took {took:.1f} s (the old one waited up to 60 s for a camera)"
    if not camera:
        assert "no camera" in done.stdout.lower()


def test_tc024_002_con08_launcher_passes_the_programs_exit_status_through(tmp_path):
    """exec: the launcher's exit status is the program's (systemd Restart=always sees the real one)."""
    for code in (0, 1, 7):
        args, done, _, _, _ = run_launcher(tmp_path, camera=False, panel=True, service="1", stub_exit=code)
        assert done.returncode == code


def test_tc024_002_con08_launcher_source_has_no_mock_on_any_executed_line():
    """Criterion 5, source check: no line that is not a comment names mock; both exec lines run $RUN."""
    text = LAUNCHER.read_text()
    code = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not [l for l in code if "mock" in l.lower()], [l for l in code if "mock" in l.lower()]
    execs = [l for l in code if re.match(r"\s*exec\b", l)]
    assert len(execs) == 2 and all(l.split()[1] == '"$RUN"' for l in execs), execs
    assert not [l for l in code if re.search(r"\b(sleep|exit)\b", l)], "the launcher must neither wait nor exit"
    assert subprocess.run(["bash", "-n", str(LAUNCHER)]).returncode == 0
    assert 'RUN="${SV_RUN_SH:-$REPO_ROOT/scripts/run.sh}"' in text


def test_tc024_002_con08_service_unit_has_no_stale_wait_or_hard_fail_comment():
    unit = (REPO / "scripts" / "second-vision.service").read_text()
    assert "hard-fail" not in unit and "Wait for the camera" not in unit
    for key in ("Restart=always", "ExecStart=@REPO@/scripts/sv-main.sh", "Environment=SV_SERVICE=1"):
        assert key in unit


def main_py(*args, hailo_missing=True, timeout=30):
    """main.py run for real in a child; hailo_apps made un-importable (None in sys.modules) if asked."""
    code = ("import runpy, sys\n"
            + ("sys.modules['hailo_apps'] = None\n" if hailo_missing else "")
            + f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
            f"sys.argv = ['main.py'] + {list(args)!r}\n"
            f"runpy.run_path({str(REPO / 'src' / 'second_vision' / 'main.py')!r}, run_name='__main__')\n")
    return subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)


def test_tc024_002_con08_main_refuses_without_hailo_apps_and_without_mock():
    """
    Criterion 5 (main.py): hailo_apps cannot be imported and --mock was not given: a clear message that
    names what is missing and how to ask for mock mode, exit non-zero, and NO worker started: the glasses
    board (a pty) receives no byte, no mock banner, no pipeline.
    """
    glasses, panel = FakeGlasses(), FakePanel()
    try:
        proc = main_py("--input", "/dev/video0", "--serial-port", glasses.path, "--config-port", panel.path)
        out, err = proc.communicate(timeout=30)
        time.sleep(1.0)
        assert proc.returncode not in (0, None), (proc.returncode, out, err)
        assert "hailo_apps" in err and "--mock" in err and "D-13" in err, err
        assert "MOCK" not in out and "Running in" not in out and "Starting" not in out, out
        assert glasses.packets == [], f"a serial worker was started: {glasses.packets[:3]}"
    finally:
        glasses.close()
        panel.close()


def test_tc024_002_con08_main_with_mock_still_runs_mock_mode_and_stops_with_ctrl_c():
    """Criterion 5: `--mock` behaves as before (mock banner, the glasses link runs, SIGINT exits 0)."""
    glasses, panel = FakeGlasses(), FakePanel()
    try:
        proc = main_py("--mock", "--serial-port", glasses.path, "--config-port", panel.path)
        time.sleep(5)
        assert proc.poll() is None, proc.communicate()
        os.kill(proc.pid, signal.SIGINT)
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 0, (proc.returncode, out[-1500:], err[-1500:])
        assert "Running in MOCK mode" in out, out[-1500:]
        assert glasses.packets, "the glasses link did not run in mock mode"
        assert "[MOCK] Detection generator started" in out
    finally:
        glasses.close()
        panel.close()


# ============================================================
# device level: the criteria of the brief
# ============================================================
@device
@pytest.mark.timeout(480)
def test_tc024_002_con08_device_no_camera_120s_stays_up_everything_else_runs(camera_unplug, sv):
    """
    Criteria 1, 2 and 5. REQ-CON-08: started at boot with no camera: the program does not crash; the glasses
    link, the panel and speech keep running; it keeps checking; never mock. 125 s with no camera: same PID,
    alive, no forbidden line (Traceback, CRITICAL, MOCK, segfault ...), "Still no USB camera after N s" about
    once a minute (two by 125 s), heartbeats to the glasses all the time, only zero-motor commands (no
    vibration), a panel mode switch recorded and announced, no Hailo device and no camera node open (no
    pipeline exists), CPU low. Then the camera appears and vision starts in the mode set meanwhile (depth).
    """
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=140)
    pid = app.pid
    wait_hit = await_waiting(app)
    t_wait = wait_hit[1]
    await_panel(app)
    assert app.count(NO_CAMERA_LINE) == 1
    assert app.open_paths("/dev/video") == [] and app.open_paths("/dev/hailo") == [], \
        (app.open_paths("/dev/video"), app.open_paths("/dev/hailo"))
    assert app.find(PIPELINE_STRING) is None, "a pipeline string was generated although there is no camera"
    time.sleep(8)
    send_mode(app, panel, "depth")
    assert app.wait_for("depth mode", 8), "the mode switch was not announced"
    assert app.wait_for("recorded while the camera is missing", 5), "the switch was not recorded as pending"
    time.sleep(10)
    ticks0, c0 = cpu_ticks(app.pid), time.monotonic()
    time.sleep(20)
    ticks1, c1 = cpu_ticks(app.pid), time.monotonic()
    cpu = (ticks1 - ticks0) / os.sysconf("SC_CLK_TCK") / (c1 - c0) * 100
    deadline = app.started_at + 124
    while time.monotonic() < deadline:
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text()[-4000:]}"
        time.sleep(1)
    assert app.pid == pid and app.proc.poll() is None
    stills = [(t - t_wait, line) for t, line in app.lines if STILL in line]
    heart = len(glasses.heartbeats_between(0))
    motors = glasses.motors_between(0)
    print(f"CPU while waiting {cpu:.1f} % of one core; 'Still no USB camera' at {[round(t, 1) for t, _ in stills]} s; "
          f"{heart} heartbeats in 124 s; motor packets {len(motors)} (non-zero: {sum(m != MOTOR_ZERO for m in motors)})")
    assert len(stills) == 2 and 55 <= stills[0][0] <= 66 and 115 <= stills[1][0] <= 126, stills
    assert heart >= 100, f"only {heart} heartbeats in 124 s"
    assert app.count("UART Connection to ESP32 is WORKING") >= 1 or app.find("acknowledg") is None
    assert motors and all(m == MOTOR_ZERO for m in motors), "the motors received a vibration while there is no camera"
    assert 0 <= cpu < 50
    assert app.open_paths("/dev/video") == [] and app.open_paths("/dev/hailo") == []
    assert app.find(PIPELINE_STRING) is None and app.find(FOUND) is None
    assert_clean(app)
    since = appear_at(camera_unplug, app, 126)
    await_vision(app, since, "depth")
    assert app.count(FOUND) == 1 and app.count(PIPELINE_STRING) == 1
    finish(app, signal.SIGTERM)


@device
@pytest.mark.timeout(420)
@pytest.mark.parametrize("delay", [1, 10, 90], ids=["at-1s", "at-10s", "at-90s"])
@pytest.mark.parametrize("mode", MODES)
def test_tc024_002_con08_device_camera_appears_later_vision_starts_in_the_current_mode(camera_unplug, sv, delay, mode):
    """
    Criterion 3. Camera plugged in at `delay` s after the start, on the same node, with the panel set to
    `mode` while waiting: vision starts, once, in `mode`; the other parts stay up; clean stop (alternately
    SIGINT/SIGTERM) exit 0.
    """
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=delay + 10)
    await_waiting(app)
    await_panel(app)
    if mode != "both":
        send_mode(app, panel, mode)
    since = appear_at(camera_unplug, app, delay)
    back = await_vision(app, since, mode)
    print(f"mode {mode}, attach at {delay} s: {back[2][-90:]}")
    assert app.count(FOUND) == 1 and app.count(PIPELINE_STRING) == 1 and app.find("Camera lost (") is None
    assert app.open_paths("/dev/video") != []
    finish(app, signal.SIGINT if delay == 10 else signal.SIGTERM)


@device
@pytest.mark.timeout(900)
def test_tc024_002_con08_device_count_25_fresh_starts_camera_appears_0p5_to_5s(camera_unplug, sv):
    """
    Criteria 3 and 6 (brief, "Fixed means repeated", counts doubled and a bit): 25 fresh-process starts,
    no camera, the camera appearing 0.5 to 5 s later at random (seeded), modes cycled, stop alternately
    SIGINT and SIGTERM after vision runs. Every one: vision running in the right mode, exit 0, no forbidden line.
    """
    rng = random.Random(20261017)
    records, failures = [], []
    for i in range(25):
        mode, delay = MODES[i % 4], round(rng.uniform(0.5, 5.0), 2)
        signum = signal.SIGINT if i % 2 == 0 else signal.SIGTERM
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=9, tag=f"{i:02d}")
        record = {"run": i, "mode": mode, "attach_at": delay, "signal": signal.Signals(signum).name}
        try:
            await_waiting(app)
            await_panel(app)
            if mode != "both":
                send_mode(app, panel, mode)
            since = appear_at(camera_unplug, app, delay)
            back = await_vision(app, since, mode)
            record["found_after_attach_s"] = round(app.wait_for(FOUND, 1, since)[1] - app.lines[since][0], 2)
            record["took_s"] = round(finish(app, signum), 2)
            record["ok"] = True
        except AssertionError as exc:
            record["ok"] = False
            record["why"] = str(exc)[:300]
            failures.append(record)
            app.close()
        records.append(record)
        print(json.dumps(record))
    print(f"SUMMARY: {sum(r['ok'] for r in records)} of {len(records)} ok")
    assert not failures, failures


@device
@pytest.mark.timeout(1500)
def test_tc024_002_con08_device_race_sweep_camera_appearing_around_the_launch(camera_unplug, sv):
    """
    Observation beyond the brief (R3): the camera's nodes appear just before, during or just after the
    constructor's look at /dev/video0 (about 0.5 s after launch). Before the look: the library's boot path
    (a camera that is present at start, T-11's question); after it: the waiting path. The attach command is
    issued at offsets -1.0 to +0.8 s from the launch (the nodes follow 0.2 s later); each of the 2 x 19 runs
    must end alive with vision running and exit 0 on Ctrl+C, no forbidden line. The split between the two
    paths is reported, not judged.
    """
    offsets = [-1.0, -0.5, -0.2, 0.0, 0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0, 1.5]
    rows = []
    for rep in range(2):
        for off in offsets:
            wait_no_pending_timer(limit=40)
            camera_unplug.detach(9)
            assert camera_unplug.wait_for_nodes(False, 5)
            if off < 0:                                   # attach first; the program is started |off| s later
                t_attach = time.monotonic()
                camera_unplug.attach()
                time.sleep(max(0.0, -off - (time.monotonic() - t_attach)))
            app, glasses, panel = sv(tag=f"{off:+.2f}")
            if off >= 0:
                pause = app.started_at + off - time.monotonic()
                if pause > 0:
                    time.sleep(pause)
                camera_unplug.attach()
            row = {"offset": off, "rep": rep}
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and app.alive():
                    if app.find(CAMERA_BACK) or len(app.frames_after(0)) >= 3:
                        break
                    time.sleep(0.25)
                row["path"] = "waiting" if app.find(NO_CAMERA_LINE) else "library-boot-path"
                row["vision"] = bool(app.find(CAMERA_BACK) or len(app.frames_after(0)) >= 3)
                row["alive"] = app.alive()
                time.sleep(1)
                code, took = app.stop(signal.SIGINT)
                row["exit"] = code
                row["forbidden"] = app.forbidden()[:3]
                row["crash"] = (code != 0) or bool(row["forbidden"])
            except AssertionError as exc:
                row["crash"] = True
                row["why"] = str(exc)[:200]
            app.close()
            rows.append(row)
            print(json.dumps(row))
    bad = [r for r in rows if r.get("crash") or not r.get("vision")]
    paths = {p: sum(1 for r in rows if r.get("path") == p) for p in ("waiting", "library-boot-path")}
    print(f"SUMMARY: {len(rows) - len(bad)} of {len(rows)} ended with vision and exit 0; paths {paths}")
    assert not bad, bad


@device
@pytest.mark.timeout(300)
def test_tc024_002_con08_device_camera_appears_on_another_node(camera_unplug, sv):
    """
    Criterion 3, "or another node": the old nodes are held open (as in T-03a's test) while the camera is
    away, so the returning camera gets new numbers. Vision starts on the new node, in the same process.
    Afterwards the camera is renumbered back to /dev/video0 for the launcher.
    """
    wait_no_pending_timer()
    old = [n for n in camera_unplug.nodes if os.path.exists(n)] or [CAMERA_NODE]
    held = [os.open(n, os.O_RDONLY | os.O_NONBLOCK) for n in old if os.path.exists(n)]
    try:
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=20)
        await_waiting(app)
        since = appear_at(camera_unplug, app, 4)
        new_nodes = list(camera_unplug.nodes)
        assert not set(new_nodes) & set(old), f"the camera came back on the same nodes {new_nodes}"
        back = await_vision(app, since, "both")
        assert any(f"running on {n} " in back[2] for n in new_nodes), (back[2], new_nodes)
        print(f"old nodes {old}, new nodes {new_nodes}; {back[2][-100:]}")
        finish(app)
    finally:
        for fd in held:
            os.close(fd)
        if not os.path.exists(CAMERA_NODE):
            wait_no_pending_timer()
            camera_unplug.detach(20)
            camera_unplug.wait_for_nodes(False, 5)
            camera_unplug.attach()
            camera_unplug.wait_for_nodes(True, 15)
    assert os.path.exists(CAMERA_NODE), "the camera is not back on /dev/video0 after the test"


@device
@pytest.mark.timeout(300)
@pytest.mark.parametrize("after", [0.0, 1.0, 1.6, 2.0, 2.4, 3.0, 4.0],
                         ids=["with-attach", "1.0s", "1.6s", "2.0s", "2.4s", "3.0s", "4.0s"])
def test_tc024_002_con08_device_mode_switch_around_the_moment_the_camera_appears(camera_unplug, sv, after):
    """
    Criterion 2/3: a panel mode switch (detection) `after` s after the attach command, i.e. before, during
    and after the start of the first pipeline (the nodes are back ~1.5 s after the attach; the pipeline
    start takes several seconds). The program stays up and ends with vision in the LAST mode set;
    then a switch to depth runs frames (the program is still functional).
    """
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=14)
    await_waiting(app)
    await_panel(app)
    since = appear_at(camera_unplug, app, 2)
    time.sleep(max(0.0, after - 1.6))                      # appear_at already waited for the nodes (~1.5 s)
    send_mode(app, panel, "detection")
    deadline = time.monotonic() + 60
    final = None
    while time.monotonic() < deadline:
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(since)[-4000:]}"
        hits = [line for _, line in list(app.lines)[since:] if "Camera back" in line or "Pipeline rebuilt" in line]
        if hits and "mode=detection" in hits[-1]:
            final = hits[-1]
            break
        time.sleep(0.5)
    assert final, f"vision did not end in detection:\n{app.text(since)[-4000:]}"
    time.sleep(3)
    assert app.alive() and app.find("Shutdown initiated", since) is None
    send_mode(app, panel, "depth")
    assert app.wait_for("Pipeline rebuilt — mode=depth", 40, since), app.text(since)[-3000:]
    assert app.wait_frames(app.index(), 3, 25)
    finish(app)


@device
@pytest.mark.timeout(300)
def test_tc024_002_con08_device_ten_rapid_mode_switches_while_waiting_last_one_wins(camera_unplug, sv):
    """Criterion 2/3: ten panel switches inside two seconds while waiting; the camera then appears: one start, in the last mode."""
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=20)
    await_waiting(app)
    await_panel(app)
    sequence = ["depth", "detection", "none", "both", "depth", "none", "detection", "depth", "both", "detection"]
    for mode in sequence:
        panel.send(f"M:{mode}")
        time.sleep(0.2)
    time.sleep(1)
    assert app.alive()
    assert app.count("[CONFIG] Mode:") >= len(sequence) - 1
    since = appear_at(camera_unplug, app, 6)
    await_vision(app, since, "detection")
    assert app.count(PIPELINE_STRING) == 1
    finish(app)


@device
@pytest.mark.timeout(900)
def test_tc024_002_con08_device_camera_drops_again_just_after_it_appears_8_runs(camera_unplug, sv):
    """
    Criterion 4: the camera appears and is gone again 0.1 to 0.7 s after "Camera found" (re-attached by the
    tool 6 s later), in 8 fresh starts. Every run ends in 'running' (frames again) and exit 0, never a crash or an exit.
    """
    rng = random.Random(4)
    records = []
    for i in range(8):
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=3, tag=f"{i}")   # the tool's timer is the first appearance
        record = {"run": i}
        try:
            await_waiting(app)
            found = app.wait_for(FOUND, 30)
            assert found, f"never found:\n{app.text()[-2500:]}"
            time.sleep(rng.uniform(0.1, 0.7))
            wait_no_pending_timer(limit=20)
            since = app.index()
            app.mark("detach camera --for 6 just after Camera found")
            camera_unplug.detach(6)
            assert camera_unplug.wait_for_nodes(False, 5)
            record["after_found_s"] = round(time.monotonic() - found[1], 2)
            time.sleep(4)
            assert app.alive(), f"the program ended after the camera dropped (exit {app.proc.returncode}):\n{app.text(since)[-3000:]}"
            assert wait_for_nodes_back(camera_unplug)
            assert app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT + 15, since) or app.wait_for(CAMERA_BACK, 1, found[0]), \
                f"never running again:\n{app.text(since)[-3500:]}"
            time.sleep(2)
            assert app.wait_frames(app.index() - 1, 3, 25) or app.wait_frames(found[0], 3, 5), "no frames"
            record["state"] = "running"
            record["took_s"] = round(finish(app), 2)
            record["ok"] = True
        except AssertionError as exc:
            record.update(ok=False, why=str(exc)[:300])
            app.close()
        records.append(record)
        print(json.dumps(record))
    print(f"SUMMARY: {sum(r['ok'] for r in records)} of {len(records)} ok")
    assert all(r["ok"] for r in records), records


def wait_for_nodes_back(camera_unplug, timeout: float = 20) -> bool:
    return camera_unplug.wait_for_nodes(True, timeout)


@device
@pytest.mark.timeout(900)
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_tc024_002_con08_device_stop_while_waiting_and_right_after_the_camera_appears_15_runs(camera_unplug, sv, signum):
    """
    Criterion 6 with Amendment A: stops from the first [MAIN] line (or 0.7 s after launch if later). Each
    signal: 8 fresh starts with no camera stopped 0.7 to 3 s after launch (while waiting), then 7 fresh
    starts stopped 0.0 to 1.5 s after "Camera found" (right after the camera appears). Every one: exit 0 within
    about 1 s (<= 2 s allowed), no forbidden line, no leftovers, the Hailo chip and the camera free.
    """
    rng = random.Random(int(signum) * 11)
    records = []
    for i in range(15):
        waiting_stop = i < 8
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=10, tag=f"{i:02d}")
        record = {"run": i, "phase": "waiting" if waiting_stop else "after-found"}
        try:
            if waiting_stop:
                main_line = app.wait_for("[MAIN]", 10)
                earliest = 0.7 if main_line is None else max(0.7, app.since_launch(main_line[1]) + 0.05)
                at = max(earliest, rng.uniform(0.7, 3.0))
                pause = app.started_at + at - time.monotonic()
                if pause > 0:
                    time.sleep(pause)
                record["stop_at_s"] = round(at, 2)
            else:
                await_waiting(app)
                since = appear_at(camera_unplug, app, 1)
                found = app.wait_for(FOUND, 20, since)
                assert found, app.text(since)[-2000:]
                gap = rng.uniform(0.0, 1.5)
                time.sleep(gap)
                record["stop_after_found_s"] = round(gap, 2)
            code, took = app.stop(signum)
            record.update(exit=code, took_s=round(took, 2))
            assert code == 0 and took <= STOP_ABOUT_ONE_SECOND + (2.0 if not waiting_stop else 0.0), record
            assert app.leftovers() == [], app.leftovers()
            assert_clean(app)
            assert str(app.pid) not in subprocess.run(["fuser", "/dev/hailo0"], capture_output=True, text=True).stdout
            record["ok"] = True
        except AssertionError as exc:
            record.update(ok=False, why=str(exc)[:300])
            app.close()
        records.append(record)
        print(json.dumps(record))
    print(f"SUMMARY: {sum(r['ok'] for r in records)} of {len(records)} ok")
    assert all(r["ok"] for r in records), [r for r in records if not r["ok"]]


@device
@pytest.mark.timeout(900)
@pytest.mark.parametrize("which", ["new", "baseline"])
def test_tc024_002_con08_device_observation_stops_before_the_first_main_line_20_runs(camera_unplug, sv, which):
    """
    OBSERVATION, not pass/fail (Amendment A): SIGINT/SIGTERM alternately 0.1 to 0.7 s after launch with no
    camera, before the program has installed its signal handlers. Reports the exit codes and how many
    signals were lost (the program kept running). `baseline` runs the baseline tree's run.sh through the new
    launcher (set SV_UAT_BASELINE_RUN_SH to the scratch copy's scripts/run.sh), `new` this tree. Leftover child
    processes 1.5 s after the stop are listed, not asserted.
    """
    run_sh = None
    if which == "baseline":
        run_sh = os.environ.get("SV_UAT_BASELINE_RUN_SH")
        if not run_sh:
            pytest.skip("SV_UAT_BASELINE_RUN_SH (a scratch copy of the baseline run.sh) is not set")
    rng = random.Random(99)
    rows = []
    for i in range(20):
        signum = signal.SIGINT if i % 2 == 0 else signal.SIGTERM
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=9, run_sh=run_sh, tag=f"{which}{i:02d}")
        at = round(rng.uniform(0.1, 0.7), 2)
        pause = app.started_at + at - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        main_seen = app.find("[MAIN]") is not None
        app.mark(f"early {signal.Signals(signum).name} at {at} s, [MAIN] line seen: {main_seen}")
        try:
            os.kill(app.pid, signum)
        except ProcessLookupError:
            pass
        try:
            code = app.proc.wait(timeout=7)
            lost = False
        except subprocess.TimeoutExpired:
            code, lost = None, True
        rows.append({"which": which, "signal": signal.Signals(signum).name, "at_s": at, "main_line_seen": main_seen,
                     "exit": code, "lost": lost})
        app.close()
        time.sleep(1.5)                              # a child that was mid-run (hailortcli ...) may still be finishing
        rows[-1]["leftovers_after_1.5s"] = app.leftovers()
        print(json.dumps(rows[-1]))
    clean = sum(r["exit"] == 0 for r in rows)
    print(f"OBSERVATION {which}: {clean} of {len(rows)} exit 0; lost {sum(r['lost'] for r in rows)}; "
          f"killed by signal {sum(1 for r in rows if r['exit'] is not None and r['exit'] < 0)}; "
          f"other non-zero {sum(1 for r in rows if r['exit'] not in (None, 0) and r['exit'] > 0)}")


@device
@pytest.mark.timeout(400)
def test_tc024_002_con08_device_plain_start_with_camera_present_through_the_launcher_3_runs(sv, tmp_path):
    """
    Criterion 7: camera attached at start: the launcher's command line is the old one (recorded by a wrapper
    that hands the arguments on to the real scripts/run.sh), vision starts through the library path as before
    (frames flow, no 'No camera at start-up', no 'Camera found', one pipeline string), a panel switch rebuilds,
    exit 0.
    """
    assert os.path.exists(CAMERA_NODE), "camera not attached"
    for i, modes in enumerate([("detection", "depth"), ("none", "both"), ("depth", "detection")]):
        record = tmp_path / f"args-{i}.txt"
        wrapper = tmp_path / f"record-run-{i}.sh"
        wrapper.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{record}"\nexec "{REPO / "scripts" / "run.sh"}" "$@"\n')
        wrapper.chmod(0o755)
        app, glasses, panel = sv(tag=f"{i}", run_sh=str(wrapper))
        try:
            assert app.wait_frames(0, 3, START_TIMEOUT), f"no frames:\n{app.text()[-3000:]}"
            args = record.read_text().splitlines()
            assert args == ["--input", "/dev/video0", "--width", "640", "--height", "480",
                            "--serial-port", glasses.path, "--config-port", panel.path], args
            assert app.find(NO_CAMERA_LINE) is None and app.find(FOUND) is None
            assert app.count(PIPELINE_STRING) == 1
            await_panel(app)
            for mode in modes:
                start = app.index()
                panel.send(f"M:{mode}")
                assert app.wait_for(f"Pipeline rebuilt — mode={mode}", 40, start), app.text(start)[-2500:]
                time.sleep(2)
            finish(app, signal.SIGINT if i % 2 == 0 else signal.SIGTERM)
        finally:
            app.close()
@device
@pytest.mark.timeout(240)
def test_tc024_002_con08_device_real_ports_no_camera_then_camera(camera_unplug, sv):
    """
    Criterion 2 on the REAL boards, once: /dev/serial0 (glasses ESP32) and /dev/ttyAMA3 (panel), the launcher's
    own defaults. No camera for 25 s: the real board ACKs ("UART Connection to ESP32 is WORKING"), no
    "stopped acknowledging", the panel link is up; then the camera appears and vision starts. Whether the real
    motors stay still while waiting is for a person (MANUAL-TESTS.md).
    """
    app, _, _ = start_without_camera(camera_unplug, sv, hold=40, real_ports=True)
    await_waiting(app)
    assert app.wait_for("UART Connection to ESP32 is WORKING", 15), app.text()[-2500:]
    assert app.wait_for(PANEL_UP, 10)
    time.sleep(25)
    assert app.alive() and app.find("stopped acknowledging") is None and app.find(PIPELINE_STRING) is None
    modes = [line.split("Mode:")[1].strip() for _, line in app.lines if "[CONFIG] Mode:" in line]
    mode = modes[-1] if modes else "both"        # the real panel's switches decide, as in service
    print(f"the real panel's mode at the time: {mode} (panel lines: {modes})")
    since = appear_at(camera_unplug, app, 28)
    await_vision(app, since, mode)
    finish(app)


@device
@pytest.mark.timeout(300)
def test_tc024_002_con08_device_camera_appears_but_cannot_be_opened_then_can(camera_unplug, sv):
    """
    Criterion 4: a camera appears that cannot be opened (a process of the Tester's streams from the node,
    UVC allows one client) from a start-up that began without a camera. For 15 s the program tries and falls
    back to waiting, never a crash or an exit; when the holder stops, vision starts (depth frames).
    """
    from test_tc024_003_camera_removed import CameraHolder
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=15)
    await_waiting(app)
    since = appear_at(camera_unplug, app, 1)
    holder = CameraHolder(CAMERA_NODE)
    try:
        time.sleep(1.2)
        if not holder.alive() or app.find(CAMERA_BACK, since) is not None:
            pytest.skip("inconclusive: the program got the camera before the holder did")
        time.sleep(15)
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(since)[-4000:]}"
        assert app.find("Shutdown initiated", since) is None
        assert_clean(app)
        attempts = app.count(FOUND, since)
        print(f"while held: {attempts} starts on the camera, {app.count('Camera lost (', since)} losses")
        assert attempts >= 1, "the program never tried the camera that appeared"
    finally:
        holder.stop()
    freed = app.index()
    back = None
    deadline = time.monotonic() + RECOVERY_TIMEOUT
    while time.monotonic() < deadline:
        back = app.wait_for(CAMERA_BACK, 5, freed)
        if back and app.wait_frames(back[0], 3, 10):
            break
        back = None
    assert back, f"vision did not start after the holder stopped:\n{app.text(freed)[-4000:]}"
    finish(app)


@device
@pytest.mark.timeout(1200)
def test_tc024_002_con08_device_camera_gone_again_sweep_0_to_2p5s_after_nodes_return_20_runs(camera_unplug, sv):
    """
    Criterion 4, finer than the 'drops again' runs above: in 20 fresh starts the camera's first appearance is
    the usb_sim timer (`detach --for 3`, no stale timer afterwards); the moment its nodes exist again a second
    detach (`--for 6`) is issued 0 to 2.5 s later at random (seeded), i.e. before the program's look, during
    'Camera found' / the pipeline's start, and just after it runs. Every run ends alive, back in 'running'
    with depth frames once the tool re-attaches, and exits 0 on Ctrl+C; none shows a forbidden line.
    """
    rng = random.Random(31)
    records = []
    for i in range(20):
        offset = round(rng.uniform(0.0, 2.5), 2)
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=3, tag=f"{i:02d}")
        record = {"run": i, "offset_after_nodes_s": offset}
        try:
            await_waiting(app)
            limit = time.monotonic() + 15
            while not os.path.exists(CAMERA_NODE) and time.monotonic() < limit:
                time.sleep(0.02)
            assert os.path.exists(CAMERA_NODE), "the tool did not bring the camera back"
            t_nodes = time.monotonic()
            time.sleep(offset)
            wait_no_pending_timer(limit=10)
            since = app.index()
            app.mark(f"detach camera --for 6, {offset} s after the nodes returned")
            camera_unplug.detach(6)
            assert camera_unplug.wait_for_nodes(False, 5), "nodes did not go away"
            found = app.find(FOUND, 0)
            record["found_before_detach_command_returned"] = found is not None
            time.sleep(4)
            assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text()[-3500:]}"
            assert camera_unplug.wait_for_nodes(True, 20)
            assert app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT + 15, since), f"never running again:\n{app.text()[-3500:]}"
            back_index = app.find(CAMERA_BACK, since)[0]
            assert app.wait_frames(back_index, 3, 25), "no frames after the camera returned"
            record["lost_logged"] = app.count("Camera lost (") + app.count("did not start on")
            record["took_s"] = round(finish(app), 2)
            record["ok"] = True
        except AssertionError as exc:
            record.update(ok=False, why=str(exc)[:300])
            app.close()
        records.append(record)
        print(json.dumps(record))
    print(f"SUMMARY: {sum(r['ok'] for r in records)} of {len(records)} ok")
    assert all(r["ok"] for r in records), [r for r in records if not r["ok"]]


def mapped_hailo_libs(pid: int) -> list[str]:
    """Hailo-related shared objects mapped into the process (read from its own /proc, no root needed)."""
    try:
        text = Path(f"/proc/{pid}/maps").read_text()
    except OSError:
        return []
    return sorted({line.split("/")[-1] for line in text.splitlines() if "hailo" in line.lower() and ".so" in line})


@device
@pytest.mark.timeout(200)
def test_tc024_002_con08_device_waiting_process_holds_no_hailo_pipeline_or_device(camera_unplug, sv):
    """
    Dev-report claim (design): with no camera node at launch NO Hailo pipeline is built ("a hailonet-free
    waiting start"). Observed from outside the process: while waiting no /dev/hailo* and no /dev/video* is
    open and no pipeline string was generated; the shared objects the process has mapped are listed before
    and after vision starts (printed; the plugin is expected to appear only after).
    """
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=20)
    await_waiting(app)
    time.sleep(6)
    waiting_libs = mapped_hailo_libs(app.pid)
    assert app.open_paths("/dev/hailo") == [] and app.open_paths("/dev/video") == []
    assert app.find(PIPELINE_STRING) is None
    since = appear_at(camera_unplug, app, 8)
    await_vision(app, since, "both")
    running_libs = mapped_hailo_libs(app.pid)
    print(f"hailo shared objects mapped while waiting: {waiting_libs}")
    print(f"hailo shared objects mapped once vision runs: {running_libs}")
    assert "libgsthailo.so" not in waiting_libs or "libgsthailo.so" in running_libs
    finish(app)


def proc_resources(pid: int) -> dict:
    """Threads, open files and resident memory (kB) of the process, from its own /proc."""
    status = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            status[key] = value.strip()
        fds = len(list(Path(f"/proc/{pid}/fd").iterdir()))
    except OSError:
        return {}
    return {"threads": int(status["Threads"]), "fds": fds, "rss_kb": int(status["VmRSS"].split()[0])}


@device
@pytest.mark.timeout(300)
def test_tc024_002_con08_device_waiting_for_a_camera_leaks_nothing_over_105s(camera_unplug, sv):
    """
    REQ-CON-08 'keeps checking the USB ports': the look every 2 s must not leak. Threads, open files and resident
    memory are read at 15 s and at 120 s of waiting (about 52 further looks): threads equal, open files within
    2, memory growth under 10 MB; the program's children are only the short-lived ones of a look.
    """
    app, glasses, panel = start_without_camera(camera_unplug, sv, hold=140)
    await_waiting(app)
    time.sleep(14)
    early = proc_resources(app.pid)
    time.sleep(105)
    late = proc_resources(app.pid)
    print(f"at 15 s: {early}; at 120 s: {late}; children now: {app.leftovers()}")
    assert app.alive() and early and late
    assert late["threads"] == early["threads"], (early, late)
    assert abs(late["fds"] - early["fds"]) <= 2, (early, late)
    assert late["rss_kb"] - early["rss_kb"] < 10_000, (early, late)
    assert len(app.leftovers()) <= 1, app.leftovers()
    since = appear_at(camera_unplug, app, 122)
    await_vision(app, since, "both")
    finish(app)


@device
@pytest.mark.timeout(900)
@pytest.mark.parametrize("which", ["new", "baseline"])
def test_tc024_002_con08_device_25_fresh_starts_with_no_camera_8s_each_alive_and_clean(camera_unplug, sv, which):
    """
    "What happens today" (brief, point 3) against this tree: 25 fresh-process starts with the camera absent, each
    left 8 s, then Ctrl+C. `new` (this tree): every run alive at 8 s, exit 0, no forbidden line (asserted).
    `baseline` (SV_UAT_BASELINE_RUN_SH, a scratch copy of the baseline's run.sh; observation only): how many
    runs crash (SIGSEGV is exit -11) or end by themselves before 8 s.
    """
    run_sh = None
    if which == "baseline":
        run_sh = os.environ.get("SV_UAT_BASELINE_RUN_SH")
        if not run_sh:
            pytest.skip("SV_UAT_BASELINE_RUN_SH (a scratch copy of the baseline run.sh) is not set")
    rows = []
    for i in range(25):
        app, glasses, panel = start_without_camera(camera_unplug, sv, hold=10, run_sh=run_sh, tag=f"{which}{i:02d}")
        time.sleep(8)
        alive = app.alive()
        code = app.proc.poll()
        if alive:
            code, _ = app.stop(signal.SIGINT)
        row = {"which": which, "run": i, "alive_at_8s": alive, "exit": code,
               "segfault": code == -11 or any("Segmentation fault" in l for _, l in app.lines),
               "forbidden": app.forbidden()[:2]}
        rows.append(row)
        print(json.dumps(row))
        app.close()
    bad = [r for r in rows if not r["alive_at_8s"] or r["exit"] != 0 or r["forbidden"]]
    print(f"OBSERVATION {which}: {len(rows) - len(bad)} of {len(rows)} alive at 8 s and exit 0 and clean; "
          f"segfaults {sum(r['segfault'] for r in rows)}")
    if which == "new":
        assert not bad, bad
