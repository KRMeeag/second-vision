"""
TC-024-003, device level: the USB camera is removed while the program runs (REQ-CON-08, its
"removed while running" half). Task T-03a. Written by the Tester (tests/uat/).

These tests cover ONLY the removal half of TC-024-003. The case's alert half (vibration pulses and
"Critical Error: Camera cannot be reached" every 10 s, REQ-CON-07) is task T-03c and is NOT built:
a PASS line for TC-024-003 from this file says nothing about the alerts.

Workbook TC-024-003, verbatim (docs/testing/uat-snapshot.yaml, Test Cases!A263:G263):
    steps:    1. Power on Raspberry Pi 5
              2. Disconnect USB Camera
    input:    Initially have the USB camera connected, then simulate a disconnection
    expected: There should be a quick set of vibration pulse alongside a "Critical Error: Camera
              cannot be reached" audio error. This should be repeated every 10 seconds

              In terms of the pipeline, the system should catch the no devices exception and allow
              the device to proceed in mock mode temporarily. Device should also listen for cameras
              that may pop up in the list of devices to check if everything is okay.
The "mock mode" sentence is overruled by D-13 (REQ-CON-08: "It must never fall back to the testing
mock mode"); these tests assert the opposite. Deviations: DEVIATIONS.md DV-001.

Level `device` (opt-in). Boot service stopped, camera attached, passwordless sudo for the tool:
    my_hailo_env/bin/python -m pytest tests/uat/test_tc024_003_camera_removed.py -v -rA --run-device

What is real: the program, launched as the boot service launches it (scripts/run.sh --input
/dev/video0 --width 640 --height 480 --serial-port ... --config-port ..., without SV_SERVICE=1,
no DISPLAY); the camera; the Hailo chip; the unplug, simulated by .claude/tools/usb_sim.py through
the conftest `camera_unplug` fixture (the kernel's USB `authorized` switch: the camera's driver lets
go and its /dev/video* nodes vanish, as on a pulled cable).
What is a stand-in: in every test but `..._real_ports`, the glasses ESP32 and the control panel are
pseudo-terminals. The fake glasses board ACKs every packet and records it, so the motor commands on
the wire can be read; the fake panel sends `M:<mode>` lines, so a mode switch needs no person. The
`..._real_ports` test uses /dev/serial0 and /dev/ttyAMA3, as the service does.

usb_sim.py defect designed around (brief, "Testing notes"): every `detach --for N` leaves a timer
that re-attaches whichever detach is current after N s. So: --for 20 at most, an explicit attach,
no new detach while any earlier timer is still pending (checked with pgrep, not guessed), and the
camera is asserted still absent before a test relies on it.

App output of each run goes to $SV_UAT_APP_LOG_DIR (default: pytest's tmp_path), one file per run.
"""

from __future__ import annotations

import os
import random
import re
import select
import signal
import subprocess
import threading
import time
import tty
from pathlib import Path

import pytest

pytestmark = pytest.mark.device

REPO = Path(__file__).resolve().parents[2]
# SV_UAT_RUN_SH points the tests at another checkout's run.sh (a scratch copy of the baseline or of an earlier
# attempt), only to compare numbers such as memory growth; the default is this worktree's launcher.
RUN_SH = Path(os.environ.get("SV_UAT_RUN_SH") or REPO / "scripts" / "run.sh")
CAMERA_NODE = "/dev/video0"                    # what the boot launcher passes
REAL_GLASSES_PORT = "/dev/serial0"
REAL_PANEL_PORT = "/dev/ttyAMA3"

DETACH_SECONDS = 20                            # usb_sim --for; the brief: 20 or less
SHORT_DETACH_SECONDS = 6                       # a detach whose own timer is the replug
TIMER_PATTERN = "usb_sim.py attach camera --from-timer"
START_TIMEOUT = 90                             # program start to first depth frame (HEF load)
LOSS_TIMEOUT = 20                              # unplug to "looking for a camera"
RECOVERY_TIMEOUT = 45                          # replug to "Camera back"
STOP_BOUND_SECONDS = 10.0                      # the unit's TimeoutStopSec=10
FORCE_EXIT_SECONDS = 5.0                       # app.py SHUTDOWN_FORCE_EXIT_SECONDS

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FRAME = re.compile(r"\[DEPTH\] Frame (\d+)")
LOSS_SEEN = "Camera lost ("
LOSS_DONE = "Camera lost: vision pipeline stopped and Hailo device released"
CAMERA_BACK = "Camera back: vision pipeline running on"
NEVER = (                                      # none of these may appear in a run
    "Traceback",
    "Segmentation fault",
    "MOCK",
    "[MOCK]",
    "HAILO_OUT_OF_PHYSICAL_DEVICES",
    "not enough free devices",
    "Failed to start new pipeline",
    "Exception during pipeline rebuild",
    "Exiting with error",
    "stopped acknowledging",
    "forcing exit",
    "CRITICAL",                                # a GStreamer CRITICAL is a finding (brief-2, 1a)
    "Fatal Python error",
)
MOTOR_ZERO = bytes([0xAA, 0x01, 0, 0, 0, 0x01])
HEARTBEAT = bytes([0xAA, 0xFE, 0xFE])
ACK = bytes([0xAA, 0xFF, 0x01])
PACKET_LENGTHS = {0x01: 6, 0xFE: 3, 0x04: 5}


# ============================================================
# Stand-ins on pseudo-terminals
# ============================================================
class FakeGlasses:
    """The glasses ESP32 on a pty: records every packet (time, bytes) and ACKs each one."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.packets: list[tuple[float, bytes]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="fake-glasses")
        self._thread.start()

    def _run(self):
        buf = b""
        while not self._stop.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if not ready:
                continue
            try:
                buf += os.read(self.master, 4096)
            except OSError:
                time.sleep(0.05)
                continue
            while buf:
                start = buf.find(b"\xAA")
                if start < 0:
                    buf = b""
                    break
                buf = buf[start:]
                if len(buf) < 2:
                    break
                length = PACKET_LENGTHS.get(buf[1])
                if length is None:
                    buf = buf[1:]
                    continue
                if len(buf) < length:
                    break
                packet, buf = buf[:length], buf[length:]
                self.packets.append((time.monotonic(), packet))
                try:
                    os.write(self.master, ACK)
                except OSError:
                    pass

    def motors_between(self, t0: float, t1: float = float("inf")) -> list[bytes]:
        return [p for t, p in list(self.packets) if t0 <= t < t1 and p[1] == 0x01]

    def heartbeats_between(self, t0: float, t1: float = float("inf")) -> list[bytes]:
        return [p for t, p in list(self.packets) if t0 <= t < t1 and p == HEARTBEAT]

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


class FakePanel:
    """The control panel on a pty: sends text lines (M:<mode>, B:alive) as the real panel does."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)

    def send(self, line: str):
        os.write(self.master, (line + "\n").encode())

    def close(self):
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


# ============================================================
# The program under test
# ============================================================
class AppRun:
    """scripts/run.sh as the boot launcher calls it, output captured line by line with times."""

    def __init__(self, log_path: Path, serial_port: str, config_port: str):
        self.log_path = log_path
        self.cmd = [str(RUN_SH), "--input", CAMERA_NODE, "--width", "640", "--height", "480",
                    "--serial-port", serial_port, "--config-port", config_port]
        env = dict(os.environ)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SV_SERVICE"):
            env.pop(name, None)
        env["PYTHONUNBUFFERED"] = "1"
        self.lines: list[tuple[float, str]] = []
        self._log = open(log_path, "w", buffering=1)
        self._log.write(f"# command: {' '.join(self.cmd)}\n# started: {time.strftime('%F %T %Z')}\n")
        self.started_at = time.monotonic()
        # run.sh exec's python, so this PID is the program; its own session, so anything it
        # leaves behind can be found by session id.
        self.proc = subprocess.Popen(self.cmd, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.pid = self.proc.pid
        self._reader = threading.Thread(target=self._read, daemon=True, name="app-output")
        self._reader.start()

    def _read(self):
        for raw in self.proc.stdout:
            line = ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip("\n"))
            now = time.monotonic()
            self.lines.append((now, line))
            try:
                self._log.write(f"{now - self.started_at:9.3f} {line}\n")
            except ValueError:
                pass

    def mark(self, text: str):
        """A tester's note in the app log, with the time, so the log reads as a timeline."""
        now = time.monotonic()
        try:
            self._log.write(f"{now - self.started_at:9.3f} ### TESTER: {text}\n")
        except ValueError:
            pass
        return now

    # ---- reading the output ----
    def index(self) -> int:
        return len(self.lines)

    def find(self, needle: str, start: int = 0):
        for i, (t, line) in enumerate(list(self.lines)[start:], start):
            if needle in line:
                return i, t, line
        return None

    def wait_for(self, needle: str, timeout: float, start: int = 0, interval: float = 0.2):
        limit = time.monotonic() + timeout
        while True:
            hit = self.find(needle, start)
            if hit is not None:
                return hit
            if time.monotonic() >= limit or self.proc.poll() is not None:
                return self.find(needle, start)
            time.sleep(interval)

    def last_of(self, needles: tuple[str, ...], start: int = 0):
        """The last output line at or after `start` containing any of `needles`, or None."""
        hit = None
        for i, (t, line) in enumerate(list(self.lines)[start:], start):
            if any(n in line for n in needles):
                hit = (i, t, line)
        return hit

    def frames_after(self, start: int) -> list[int]:
        return [int(m.group(1)) for _, line in list(self.lines)[start:]
                for m in [FRAME.search(line)] if m]

    def wait_frames(self, start: int, count: int, timeout: float) -> bool:
        limit = time.monotonic() + timeout
        while time.monotonic() < limit and self.proc.poll() is None:
            if len(self.frames_after(start)) >= count:
                return True
            time.sleep(0.3)
        return len(self.frames_after(start)) >= count

    def forbidden(self, start: int = 0) -> list[str]:
        return [line for _, line in list(self.lines)[start:] if any(n in line for n in NEVER)]

    def text(self, start: int = 0) -> str:
        return "\n".join(line for _, line in list(self.lines)[start:])

    def alive(self) -> bool:
        return self.proc.poll() is None

    def open_video_nodes(self) -> list[str]:
        """The /dev/video* nodes the program has open now (its own /proc, no root needed)."""
        found = set()
        fd_dir = Path(f"/proc/{self.pid}/fd")
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("/dev/video"):
                    found.add(target)
        except OSError:
            pass
        return sorted(found)

    # ---- stopping ----
    def stop(self, signum: int, timeout: float = 15.0) -> tuple[int | None, float]:
        """Send `signum` to the program's PID; (exit code or None, seconds to exit)."""
        sent = self.mark(f"sending {signal.Signals(signum).name} to PID {self.pid}")
        try:
            os.kill(self.pid, signum)
        except ProcessLookupError:
            pass
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = None
        return code, time.monotonic() - sent

    def leftovers(self) -> list[str]:
        """Processes still in the program's session (espeak, udevadm, display ...), as 'pid cmd'."""
        out = subprocess.run(["ps", "-eo", "pid=,sid=,args="], capture_output=True, text=True).stdout
        left = []
        for row in out.splitlines():
            parts = row.split(None, 2)
            if len(parts) >= 2 and parts[1] == str(self.pid) and parts[0] != str(self.pid):
                left.append(f"{parts[0]} {parts[2] if len(parts) > 2 else ''}")
        return left

    def close(self):
        """Always: stop the program by its PID if it is still running (SIGINT, then SIGKILL)."""
        if self._log.closed:
            return
        if self.proc.poll() is None:
            self.stop(signal.SIGINT, timeout=15)
        if self.proc.poll() is None:
            self.mark("still running 15 s after SIGINT: SIGKILL to its PID")
            os.kill(self.pid, signal.SIGKILL)
            self.proc.wait(timeout=10)
        self._reader.join(timeout=5)
        self._log.write(f"# exit code: {self.proc.returncode}; finished {time.strftime('%F %T %Z')}\n")
        self._log.close()


# ============================================================
# The simulated unplug, with the tool's stale timer designed around
# ============================================================
def pending_timers() -> list[str]:
    out = subprocess.run(["pgrep", "-af", TIMER_PATTERN], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if "pgrep" not in line]


def wait_no_pending_timer(limit: float = DETACH_SECONDS + 10, interval: float = 0.5) -> None:
    deadline = time.monotonic() + limit
    while pending_timers():
        if time.monotonic() > deadline:
            pytest.fail(f"a usb_sim re-attach timer is still pending after {limit} s: {pending_timers()}",
                        pytrace=False)
        time.sleep(interval)


def unplug(camera_unplug, app: AppRun) -> float:
    """Detach the camera (no earlier timer pending); assert its nodes are gone. Returns the time."""
    wait_no_pending_timer()
    assert os.path.exists(CAMERA_NODE) or camera_unplug.nodes_present(), "no camera to unplug"
    t = app.mark("usb_sim detach camera --for %d" % DETACH_SECONDS)
    camera_unplug.detach(DETACH_SECONDS)
    assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
    app.mark("camera nodes gone")
    return t


def replug(camera_unplug, app: AppRun) -> float:
    """Attach the camera explicitly, after asserting it was still away; wait for its nodes."""
    assert not camera_unplug.nodes_present(), \
        "the camera came back before the test re-attached it (a stale usb_sim timer?)"
    t = app.mark("usb_sim attach camera")
    result = camera_unplug.attach()
    assert result.returncode == 0, f"attach failed: {result.stdout} {result.stderr}"
    assert camera_unplug.wait_for_nodes(True, 15), "the camera's video nodes did not come back"
    app.mark(f"camera nodes back: {camera_unplug.nodes}")
    return t


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def app_log_dir(tmp_path) -> Path:
    where = os.environ.get("SV_UAT_APP_LOG_DIR")
    path = Path(where) if where else tmp_path
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def launch(request, app_log_dir):
    """Start the program (with fake or real ports); always stopped by PID at the end."""
    runs, stand_ins = [], []

    def start(real_ports: bool = False):
        if real_ports:
            glasses, panel = None, None
            serial_port, config_port = REAL_GLASSES_PORT, REAL_PANEL_PORT
        else:
            glasses, panel = FakeGlasses(), FakePanel()
            stand_ins.extend([glasses, panel])
            serial_port, config_port = glasses.path, panel.path
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
        stamp = time.strftime("%Y-%m-%d-%H%M%S")
        app = AppRun(app_log_dir / f"{stamp}-app-{name}.log", serial_port, config_port)
        runs.append(app)
        print(f"app log: {app.log_path}")
        return app, glasses, panel

    yield start
    for app in runs:
        app.close()
    for stand_in in stand_ins:
        stand_in.close()


def started(app: AppRun) -> int:
    """Wait for the first depth frames; returns the output index after them."""
    assert app.wait_frames(0, 3, START_TIMEOUT), \
        f"the program did not start streaming (alive={app.alive()}):\n{app.text()[-3000:]}"
    return app.index()


def switch_mode(app: AppRun, panel: FakePanel, mode: str, timeout: float = 30) -> int:
    """A panel switch to `mode`; waits until the pipeline for it is running. Returns the index before."""
    start = app.index()
    app.mark(f"panel sends M:{mode}")
    panel.send(f"M:{mode}")
    assert app.wait_for(f"[CONFIG] Mode: {mode}", 5, start), "the panel line was not read"
    assert app.wait_for(f"Pipeline rebuilt — mode={mode}", timeout, start), \
        f"the pipeline was not rebuilt for {mode}:\n{app.text(start)[-3000:]}"
    time.sleep(3)                                  # let it run a little
    return start


def assert_loss_handled(app: AppRun, since: int) -> tuple[float, float]:
    """The loss is seen and the dead pipeline is torn down; the program is still running."""
    seen = app.wait_for(LOSS_SEEN, LOSS_TIMEOUT, since)
    assert seen, f"no camera loss logged (alive={app.alive()}):\n{app.text(since)[-3000:]}"
    done = app.wait_for(LOSS_DONE, LOSS_TIMEOUT, since)
    assert done, f"the dead pipeline was not torn down:\n{app.text(since)[-3000:]}"
    assert app.alive(), f"the program ended after the unplug:\n{app.text(since)[-3000:]}"
    assert app.find("Shutdown initiated", since) is None, "the unplug led to a shutdown"
    return seen[1], done[1]


def assert_clean(app: AppRun) -> None:
    bad = app.forbidden()
    assert bad == [], f"forbidden lines in the program's output: {bad[:10]}"


# ============================================================
# The tests
# ============================================================
@pytest.mark.timeout(300)
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_tc024_003_con08_device_unplug_replug_in_each_mode(camera_unplug, launch, mode):
    """
    Removal half of TC-024-003 only (alert half: T-03c). Workbook expected, in part: "Device should
    also listen for cameras that may pop up in the list of devices to check if everything is okay."
    REQ-CON-08: the program does not crash; the other parts keep running; it keeps checking for a
    camera; when one appears the vision pipeline starts on it. Never mock mode.

    Real camera and Hailo, fake glasses board and fake panel. In `mode`: unplug, the program stays
    up, the motors get one zero command and only heartbeats follow, the camera is looked for;
    replug, the pipeline restarts in `mode` on the camera and the Hailo chip (depth frames again,
    after a switch to depth for the modes without a depth branch); Ctrl+C then exits 0.
    """
    app, glasses, panel = launch()
    started(app)
    if mode != "both":
        switch_mode(app, panel, mode)
    before = app.index()
    assert app.open_video_nodes(), "the program does not hold the camera before the unplug"

    t_unplug = unplug(camera_unplug, app)
    t_seen, t_done = assert_loss_handled(app, before)
    time.sleep(8)                                  # hold the camera away
    assert app.alive()
    assert app.open_video_nodes() == [], f"a dead camera node is still open: {app.open_video_nodes()}"
    away_motors = glasses.motors_between(t_seen, time.monotonic())
    assert MOTOR_ZERO in away_motors, f"no zero motor command after the loss: {away_motors[-5:]}"
    after_zero = away_motors[away_motors.index(MOTOR_ZERO):]
    assert all(p == MOTOR_ZERO for p in after_zero), f"non-zero motor command after the stop: {after_zero}"
    assert len(glasses.heartbeats_between(t_done, time.monotonic())) >= 5, "heartbeat stopped during the loss"

    back_from = app.index()
    replug(camera_unplug, app)
    back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
    assert back, f"the pipeline never restarted:\n{app.text(back_from)[-4000:]}"
    assert f"(mode={mode})" in back[2], f"restarted in the wrong mode: {back[2]}"
    time.sleep(3)
    assert app.open_video_nodes(), "the restarted pipeline does not hold a camera node"
    if mode in ("both", "depth"):
        assert app.wait_frames(back[0], 3, 20), "no depth frames after the restart"
    else:
        switched = switch_mode(app, panel, "depth")
        assert app.wait_frames(switched, 3, 20), "no depth frames after the restart and a switch to depth"
    assert glasses.motors_between(back[1]), "the motors were not driven again after the restart"

    code, took = app.stop(signal.SIGINT)
    print(f"mode={mode}: loss seen {t_seen - t_unplug:.2f} s after detach, torn down "
          f"{t_done - t_unplug:.2f} s after; camera back {back[1] - t_unplug:.1f} s after detach; "
          f"Ctrl+C exit {code} in {took:.2f} s")
    assert code == 0, f"exit code {code}"
    assert took < STOP_BOUND_SECONDS
    assert app.leftovers() == []
    assert "Exiting successfully" in app.text()
    assert_clean(app)


@pytest.mark.timeout(240)
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_tc024_003_con08_device_clean_stop_while_camera_missing(camera_unplug, launch, signum):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression ("Ctrl+C ... stops both threads after
    their current loop, with no zombie processes"). With the camera unplugged and the program
    looking for it, Ctrl+C (SIGINT, also what the boot service sends) or SIGTERM ends it: exit 0,
    within the bounds (SHUTDOWN_FORCE_EXIT_SECONDS 5 s, TimeoutStopSec=10), motors at zero, nothing
    left running, the Hailo chip and the camera free.
    """
    app, glasses, panel = launch()
    started(app)
    before = app.index()
    unplug(camera_unplug, app)
    assert_loss_handled(app, before)
    time.sleep(5)
    assert not camera_unplug.nodes_present(), "the camera came back before the stop (stale timer?)"

    code, took = app.stop(signum)
    print(f"{signal.Signals(signum).name} while the camera is missing: exit {code} in {took:.2f} s")
    assert code == 0, f"exit code {code}:\n{app.text(before)[-3000:]}"
    assert took < FORCE_EXIT_SECONDS, f"took {took:.2f} s (force-exit timer is {FORCE_EXIT_SECONDS} s)"
    assert "Exiting successfully" in app.text() and "[MAIN] Done." in app.text()
    motors = glasses.motors_between(0)
    assert motors and motors[-1] == MOTOR_ZERO, f"the last motor command is not zero: {motors[-3:]}"
    assert app.leftovers() == [], f"left running: {app.leftovers()}"
    holders = subprocess.run(["fuser", "/dev/hailo0"], capture_output=True, text=True)
    assert str(app.pid) not in holders.stdout
    assert_clean(app)


@pytest.mark.timeout(480)
def test_tc024_003_con08_device_three_unplugs_in_a_row(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. REQ-CON-08 under repetition: unplug and replug three times in
    one run. Each loss: the program stays up, one zero motor command, recovery on the camera and
    the Hailo chip (depth frames again); never a shutdown, never mock mode.
    """
    app, glasses, panel = launch()
    started(app)
    timings = []
    for round_ in range(3):
        before = app.index()
        t_unplug = unplug(camera_unplug, app)
        t_seen, t_done = assert_loss_handled(app, before)
        time.sleep(5)
        zeros = [p for p in glasses.motors_between(t_seen, time.monotonic()) if p == MOTOR_ZERO]
        assert zeros, f"round {round_}: no zero motor command"
        back_from = app.index()
        replug(camera_unplug, app)
        back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
        assert back, f"round {round_}: never restarted:\n{app.text(back_from)[-4000:]}"
        assert app.wait_frames(back[0], 3, 20), f"round {round_}: no depth frames after the restart"
        timings.append((round(t_done - t_unplug, 2), round(back[1] - t_unplug, 1)))
    print(f"(torn down s, back s) after each detach: {timings}")
    assert app.find("Shutdown initiated") is None
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(300)
@pytest.mark.parametrize("gap", [0.0, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6],
                         ids=["at-once", "after-0.8s", "after-1.0s", "after-1.1s", "after-1.2s",
                              "after-1.3s", "after-1.4s", "after-1.5s", "after-1.6s"])
def test_tc024_003_con08_device_unplug_while_mode_switch_pending(camera_unplug, launch, gap):
    """
    Removal half of TC-024-003 only. Brief criterion 2: a panel mode switch during the loss "neither
    ends the program nor tries to start a pipeline on the missing camera". Here the switch comes
    first and the unplug `gap` seconds later, while the rebuild for it is still pending or running
    (the window the Developer named as R4 residual). Expected: the program stays up, and when the
    camera returns the pipeline runs in the newly selected mode. Measured on this Pi (2026-10-17):
    the rebuild's teardown ends ~1.16 s after the panel line and the new pipeline opens the camera
    ~0.35 s later, so gaps 1.1-1.4 s aim at the moment between the node check and the open.
    """
    app, glasses, panel = launch()
    started(app)
    wait_no_pending_timer()
    before = app.index()
    app.mark(f"panel sends M:depth, unplug follows after {gap} s")
    panel.send("M:depth")
    if gap:
        time.sleep(gap)
    unplug(camera_unplug, app)
    time.sleep(10)
    assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(before)[-4000:]}"
    assert app.find("Shutdown initiated", before) is None
    assert app.find(LOSS_DONE, before) or app.find("waiting for a camera instead", before), \
        f"neither a camera loss nor a wait was logged:\n{app.text(before)[-4000:]}"
    back_from = app.index()
    replug(camera_unplug, app)
    back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
    assert back, f"never restarted:\n{app.text(back_from)[-4000:]}"
    assert "(mode=depth)" in back[2], back[2]
    assert app.wait_frames(back[0], 3, 20)
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS
    assert_clean(app)


@pytest.mark.timeout(300)
def test_tc024_003_con08_device_real_ports(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. As the boot service runs it: the real glasses ESP32 on
    /dev/serial0 and the real panel on /dev/ttyAMA3. REQ-CON-08 "every other part keeps running:
    the glasses and panel links, speech": during the loss the glasses link raises no "ESP32 stopped
    acknowledging" and the panel link stays open; the camera returns and vision restarts.
    Whether the board answers at all is recorded (the "UART Connection to ESP32 is WORKING" line);
    a board that never answered makes the heartbeat part of this test say so, not pass.
    The real panel sends its whole state (switches, knobs, `M:<mode>`) every 10 s, so the mode may
    change to whatever its switch says, before or during the loss; the restart must use that mode.
    """
    app, _, _ = launch(real_ports=True)
    started(app)
    board_answered = app.find("UART Connection to ESP32 is WORKING") is not None
    panel_open = app.find("Connected to control panel on /dev/ttyAMA3") is not None
    before = app.index()
    t_unplug = unplug(camera_unplug, app)
    assert_loss_handled(app, before)
    time.sleep(12)                                 # longer than the panel's 10 s state burst
    assert app.alive()
    assert app.find("stopped acknowledging", before) is None
    panel_during = [line for _, line in app.lines[before:] if line.startswith("[CONFIG]")]
    back_from = app.index()
    replug(camera_unplug, app)
    back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
    assert back, f"never restarted:\n{app.text(back_from)[-4000:]}"
    modes = re.findall(r"\[CONFIG\] Mode: (\w+)", app.text())
    expected_mode = modes[-1] if modes else "both"
    assert f"(mode={expected_mode})" in back[2], (expected_mode, back[2])
    time.sleep(3)
    if expected_mode in ("both", "depth"):
        assert app.wait_frames(back[0], 3, 20), "no depth frames after the restart"
    assert app.open_video_nodes(), "the restarted pipeline holds no camera node"
    code, took = app.stop(signal.SIGINT)
    print(f"real ports: board answered={board_answered}, panel port open={panel_open}, "
          f"panel lines during the loss={panel_during}, restarted in mode={expected_mode}, "
          f"camera back {back[1] - t_unplug:.1f} s after detach, exit {code} in {took:.2f} s")
    assert code == 0 and took < STOP_BOUND_SECONDS
    assert app.leftovers() == []
    assert_clean(app)
    assert board_answered, ("the glasses ESP32 never acknowledged on /dev/serial0, so this run "
                            "cannot show its heartbeat survives the loss (everything else passed)")


@pytest.mark.timeout(300)
def test_tc024_003_con08_device_camera_returns_on_another_node(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. Brief criterion 4: "A camera that comes back under a different
    /dev/video* node counts as 'a camera appears'", as when it is plugged into another port and the
    kernel numbers it anew. Made without a person: while the camera is away the test holds its old
    nodes open, which keeps their numbers taken, so the kernel gives the returning camera new ones.
    Expected: vision restarts on the new node, in the same process. Afterwards the camera is
    detached and attached once more with nothing held, so it is back on /dev/video0, which the boot
    launcher needs.
    """
    app, glasses, panel = launch()
    held = []
    try:
        started(app)
        old_nodes = [n for n in camera_unplug.nodes if os.path.exists(n)]
        held = [os.open(n, os.O_RDONLY | os.O_NONBLOCK) for n in old_nodes]
        app.mark(f"tester holds {old_nodes} open so their numbers stay taken")
        before = app.index()
        unplug(camera_unplug, app)
        assert_loss_handled(app, before)
        time.sleep(5)
        back_from = app.index()
        replug(camera_unplug, app)
        new_nodes = list(camera_unplug.nodes)
        assert not set(new_nodes) & set(old_nodes), \
            f"the camera came back on the same nodes {new_nodes}; new numbers could not be forced"
        back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
        assert back, f"never restarted on the new nodes {new_nodes}:\n{app.text(back_from)[-4000:]}"
        assert any(f"running on {n} " in back[2] for n in new_nodes), back[2]
        assert app.wait_frames(back[0], 3, 20), "no depth frames from the camera on its new node"
        code, took = app.stop(signal.SIGINT)
        print(f"old nodes {old_nodes}, new nodes {new_nodes}; {back[2]}; exit {code} in {took:.2f} s")
        assert code == 0 and took < STOP_BOUND_SECONDS
        assert_clean(app)
    finally:
        for fd in held:
            os.close(fd)
        app.close()
        if not os.path.exists(CAMERA_NODE):        # renumber it: detach and attach with nothing held
            wait_no_pending_timer()
            camera_unplug.detach(DETACH_SECONDS)
            camera_unplug.wait_for_nodes(False, 5)
            camera_unplug.attach()
            camera_unplug.wait_for_nodes(True, 15)
    assert os.path.exists(CAMERA_NODE), "the camera is not back on /dev/video0 after the test"


@pytest.mark.timeout(300)
def test_tc024_003_con08_device_mode_switch_during_loss_is_announced_and_used(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. REQ-CON-08 "every other part keeps running: the glasses and
    panel links, speech"; brief criterion 2: while the camera is missing, a panel mode switch is
    still announced ("<mode> mode") and recorded, and neither ends the program nor tries to start a
    pipeline on the missing camera. When the camera returns, the pipeline runs in that mode.
    Real camera and Hailo; fake panel and glasses board; the real speech worker (espeak).
    """
    app, glasses, panel = launch()
    started(app)
    before = app.index()
    unplug(camera_unplug, app)
    t_seen, t_done = assert_loss_handled(app, before)
    time.sleep(3)
    during = app.index()
    app.mark("panel sends M:detection while the camera is missing")
    panel.send("M:detection")
    assert app.wait_for("[TTS] 'detection mode'", 10, during), \
        f"the mode switch was not spoken:\n{app.text(during)[-3000:]}"
    assert app.wait_for("Mode detection recorded while the camera is missing", 5, during)
    time.sleep(5)
    assert app.alive() and app.find("Shutdown initiated", before) is None
    for attempt in ("Generated Pipeline string", "Camera found on", "Pipeline rebuilt"):
        assert app.find(attempt, during) is None, f"a pipeline was started on the missing camera: {attempt}"
    assert len(glasses.heartbeats_between(t_done, time.monotonic())) >= 5, "heartbeat stopped"
    back_from = app.index()
    replug(camera_unplug, app)
    back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
    assert back, f"never restarted:\n{app.text(back_from)[-4000:]}"
    assert "(mode=detection)" in back[2], back[2]
    # Let the restarted pipeline settle before the stop, as the other tests do. A Ctrl+C within
    # ~0.2 s of ANY pipeline start segfaults about 3 times in 5, on the baseline code too
    # (test-report-1.md, evidence/2026-10-17-tester-stop-right-after-mode-switch-no-loss*.log);
    # that older defect is not what this test checks.
    time.sleep(3)
    assert app.alive()
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS
    assert_clean(app)


RESTART_FOUND = "Camera found on"
WAITING_AGAIN = (LOSS_DONE, "still looking for a camera")


@pytest.mark.timeout(420)
def test_tc024_003_con08_device_camera_drops_again_while_restarting(camera_unplug, launch):
    """
    Removal half of TC-024-003 only (alert half: T-03c). REQ-CON-08 with a loose connector: "the
    program does not crash ... when a camera appears, the vision pipeline starts on it", when the
    camera comes back and drops out again while the program is restarting vision on it (brief R4:
    "Report what happens if the camera vanishes again mid-rebuild"). Never mock mode.

    Three bounces in one run. Each: the camera is detached with `--for 6`, so the tool's own timer
    brings it back and no timer is left pending; once the program logs "Camera found on", the
    camera is detached again after `delay` s: 1.0 (frames flow), 0.3 (the Hailo networks are
    still being configured), 0.0 (the pipeline is being built and started; last, because on
    2026-10-17 this one crashed the program, see test-report-1.md). Expected each time: the
    program stays up (no shutdown, no exit), ends up waiting for a camera again, holds no dead
    camera node, and the last motor command on the wire is zero; then the camera is attached for
    good and depth frames come back. Real camera and Hailo; fake glasses board and panel.
    """
    app, glasses, panel = launch()
    started(app)
    outcomes = []
    for delay in (1.0, 0.3, 0.0):
        before = app.index()
        wait_no_pending_timer()
        app.mark(f"usb_sim detach camera --for {SHORT_DETACH_SECONDS} (the tool's timer is the replug)")
        camera_unplug.detach(SHORT_DETACH_SECONDS)
        assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
        assert_loss_handled(app, before)
        found = app.wait_for(RESTART_FOUND, SHORT_DETACH_SECONDS + 15, before, interval=0.02)
        assert found, f"the camera's return was not found:\n{app.text(before)[-3000:]}"
        if delay:
            time.sleep(delay)
        wait_no_pending_timer(limit=10, interval=0.05)  # the --for timer has finished its attach
        lag = time.monotonic() - found[1]
        t_bounce = app.mark(f"bounce: usb_sim detach camera --for {DETACH_SECONDS}, "
                            f"{lag:.2f} s after '{RESTART_FOUND}'")
        camera_unplug.detach(DETACH_SECONDS)
        assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
        time.sleep(8)
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(before)[-4000:]}"
        assert app.find("Shutdown initiated", before) is None, "the bounce led to a shutdown"
        last = app.last_of(WAITING_AGAIN + (CAMERA_BACK,), found[0])
        assert last and CAMERA_BACK not in last[2], \
            f"not waiting for a camera after the bounce:\n{app.text(found[0])[-3000:]}"
        assert app.open_video_nodes() == [], f"a dead camera node is still open: {app.open_video_nodes()}"
        motors = glasses.motors_between(0)
        assert motors and motors[-1] == MOTOR_ZERO, f"the last motor command is not zero: {motors[-3:]}"
        back_first = app.find(CAMERA_BACK, found[0])
        if back_first is not None and back_first[1] < t_bounce:
            outcome = "pipeline had started, then a new camera loss"
        elif app.find("did not start on", found[0]) is not None:
            outcome = "start failed, back to waiting"
        else:
            outcome = f"other: last state line {last[2]!r}"
        frames = len(app.frames_after(found[0]))

        back_from = app.index()
        replug(camera_unplug, app)
        back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
        assert back, f"never restarted after the bounce:\n{app.text(back_from)[-4000:]}"
        assert app.wait_frames(back[0], 3, 20), "no depth frames after the final replug"
        outcomes.append(f"bounce {lag:.2f} s after found: {outcome}; depth frame lines before "
                        f"the bounce's teardown: {frames}")
    print("\n".join(outcomes))
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(240)
@pytest.mark.parametrize("signum,when", [
    (signal.SIGINT, "during-teardown"), (signal.SIGTERM, "during-teardown"),
    (signal.SIGINT, "during-restart"), (signal.SIGTERM, "during-restart"),
], ids=["sigint-during-teardown", "sigterm-during-teardown",
        "sigint-during-restart", "sigterm-during-restart"])
def test_tc024_003_con08_device_stop_mid_transition(camera_unplug, launch, signum, when):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression ("Ctrl+C while detecting and speaking
    stops both threads after their current loop, with no zombie processes"); brief criterion 6.
    The stop lands while the program is in the middle of a camera transition, not idly waiting:
    during the teardown of the dead pipeline (0.3 s after the loss is logged; the teardown takes
    ~1.2 s), or as soon as the program logs "Camera found on" for a camera that came back.
    Expected: exit 0 within the bounds (SHUTDOWN_FORCE_EXIT_SECONDS 5 s, TimeoutStopSec=10), the
    last motor command on the wire zero, nothing left running.
    """
    app, glasses, panel = launch()
    started(app)
    before = app.index()
    unplug(camera_unplug, app)
    if when == "during-teardown":
        seen = app.wait_for(LOSS_SEEN, LOSS_TIMEOUT, before, interval=0.02)
        assert seen, f"no camera loss logged:\n{app.text(before)[-3000:]}"
        time.sleep(0.3)
        assert app.find(LOSS_DONE, before) is None, \
            "the teardown had already finished, so the stop would not land in it"
    else:
        assert_loss_handled(app, before)
        time.sleep(3)
        back_from = app.index()
        replug(camera_unplug, app)
        found = app.wait_for(RESTART_FOUND, RECOVERY_TIMEOUT, back_from, interval=0.02)
        assert found, f"the camera's return was not found:\n{app.text(back_from)[-3000:]}"
    stop_at = app.index()
    code, took = app.stop(signum)
    after_stop = app.text(stop_at)
    landed = ("teardown finished after the signal" if LOSS_DONE in after_stop
              else "camera started after the signal" if CAMERA_BACK in after_stop
              else "no transition line after the signal")
    print(f"{signal.Signals(signum).name} {when}: exit {code} in {took:.2f} s; {landed}")
    assert code == 0, f"exit code {code}:\n{app.text(before)[-3000:]}"
    assert took < FORCE_EXIT_SECONDS, f"took {took:.2f} s (force-exit timer is {FORCE_EXIT_SECONDS} s)"
    assert "[MAIN] Done." in app.text()
    motors = glasses.motors_between(0)
    assert motors and motors[-1] == MOTOR_ZERO, f"the last motor command is not zero: {motors[-3:]}"
    assert app.leftovers() == [], f"left running: {app.leftovers()}"
    assert_clean(app)


# ============================================================
# Attempt 2 (Tester): the windows brief-2 names, covered more finely
# ============================================================
class CameraHolder:
    """
    The tester's own gst-launch process that streams from a camera node and throws the frames
    away: while it runs nobody else can stream from the node (UVC allows one streaming client),
    so a camera that is "found" cannot be used. Stopped by its PID.
    """

    def __init__(self, node: str):
        self.proc = subprocess.Popen(
            ["gst-launch-1.0", "-q", "v4l2src", f"device={node}", "!", "fakesink", "sync=false"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def lose_and_wait_for_found(camera_unplug, app: AppRun, before: int):
    """
    Detach with the tool's own timer as the replug (`--for SHORT_DETACH_SECONDS`), wait for the
    loss to be handled, then for "Camera found on" (polled every 20 ms). Returns that log entry.
    """
    wait_no_pending_timer()
    app.mark(f"usb_sim detach camera --for {SHORT_DETACH_SECONDS} (the tool's timer is the replug)")
    camera_unplug.detach(SHORT_DETACH_SECONDS)
    assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
    assert_loss_handled(app, before)
    found = app.wait_for(RESTART_FOUND, SHORT_DETACH_SECONDS + 15, before, interval=0.02)
    assert found, f"the camera's return was not found:\n{app.text(before)[-3000:]}"
    return found


def proc_stats(pid: int) -> tuple[int, int]:
    """(VmRSS in MB, thread count) of the program, from its /proc status; (-1, -1) if unreadable."""
    try:
        text = Path(f"/proc/{pid}/status").read_text()
        rss = int(re.search(r"VmRSS:\s+(\d+) kB", text).group(1)) // 1024
        threads = int(re.search(r"Threads:\s+(\d+)", text).group(1))
        return rss, threads
    except (OSError, AttributeError, ValueError):
        return -1, -1


@pytest.mark.timeout(1500)
def test_tc024_003_con08_device_bounce_sweep(camera_unplug, launch):
    """
    Removal half of TC-024-003 only (alert half: T-03c). Brief-2 criterion 1a: "the camera returns
    and drops again 0.0, 0.3 and 1.0 s after 'Camera found on'" and the F2 window (a Hailo
    pipeline lost within ~0.6 s of its start). The bounce test above takes three offsets; this one
    sweeps twelve, 0.0 to 1.5 s, in one run. Each: detach (the tool's timer replugs), "Camera found
    on", wait `delay`, detach again. Expected each time: the program stays up and ends up waiting
    for a camera (or running vision on it), no shutdown, no segfault, no GStreamer CRITICAL, no
    dead camera node held open, the last motor command zero; then the camera is attached for good
    and depth frames come back. The row printed for each offset says how long after "Camera found
    on" the loss was logged and what the program did (start failed, or pipeline started then lost).
    """
    app, glasses, panel = launch()
    started(app)
    time.sleep(3)
    stats_before = proc_stats(app.pid)
    rows = []
    for delay in (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5):
        before = app.index()
        found = lose_and_wait_for_found(camera_unplug, app, before)
        if delay:
            time.sleep(delay)
        wait_no_pending_timer(limit=10, interval=0.05)
        t_bounce = app.mark(f"bounce: usb_sim detach camera --for {DETACH_SECONDS}, "
                            f"{time.monotonic() - found[1]:.2f} s after '{RESTART_FOUND}'")
        camera_unplug.detach(DETACH_SECONDS)
        assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
        time.sleep(8)
        assert app.alive(), f"offset {delay}: the program ended (exit {app.proc.returncode}):\n" \
                            f"{app.text(before)[-4000:]}"
        assert app.find("Shutdown initiated", before) is None, f"offset {delay}: a shutdown"
        last = app.last_of(WAITING_AGAIN + (CAMERA_BACK,), found[0])
        assert last and CAMERA_BACK not in last[2], \
            f"offset {delay}: not waiting for a camera after the bounce:\n{app.text(found[0])[-3000:]}"
        assert app.open_video_nodes() == [], f"offset {delay}: a dead camera node is open"
        motors = glasses.motors_between(0)
        assert motors and motors[-1] == MOTOR_ZERO, f"offset {delay}: last motor command {motors[-3:]}"
        assert_clean(app)                           # includes CRITICAL
        lost = app.find(LOSS_SEEN, found[0])
        back_first = app.find(CAMERA_BACK, found[0])
        if back_first is not None and back_first[1] < t_bounce:
            what = "pipeline started, then lost"
        elif app.find("did not start on", found[0]) is not None:
            what = "start failed, back to waiting"
        else:
            what = "loss handled before the start finished"
        rows.append(f"offset {delay:4.2f} s: loss logged "
                    f"{(lost[1] - found[1]) if lost else float('nan'):5.2f} s after 'Camera found on'; {what}")

        back_from = app.index()
        replug(camera_unplug, app)
        back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
        assert back, f"offset {delay}: never restarted after the bounce:\n{app.text(back_from)[-4000:]}"
        assert app.wait_frames(back[0], 3, 20), f"offset {delay}: no depth frames after the final replug"
    time.sleep(3)
    stats_after = proc_stats(app.pid)
    print("\n".join(rows))
    print(f"program after 12 camera restarts: RSS {stats_before[0]} -> {stats_after[0]} MB, "
          f"threads {stats_before[1]} -> {stats_after[1]}")
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)
    assert stats_after[1] <= stats_before[1] + 8, f"threads grew: {stats_before} -> {stats_after}"


@pytest.mark.timeout(900)
def test_tc024_003_con08_device_mode_switch_while_camera_restarting(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. Brief-2 criterion 1c reaches the other way round too: a panel
    mode switch that arrives while the camera's restart is under way ("Camera found on" logged, the
    pipeline not yet running). The switch is recorded (the restart is not interrupted), announced
    ("<mode> mode"), and the program ends up running in the NEW mode; it neither ends nor crashes.
    Four switches, 0.0, 0.1, 0.3 and 0.8 s after "Camera found on", to depth, both, depth, both.
    """
    app, glasses, panel = launch()
    started(app)
    for target, delay in (("depth", 0.0), ("both", 0.1), ("depth", 0.3), ("both", 0.8)):
        before = app.index()
        found = lose_and_wait_for_found(camera_unplug, app, before)
        if delay:
            time.sleep(delay)
        sent = app.index()
        app.mark(f"panel sends M:{target}, {delay} s after '{RESTART_FOUND}'")
        panel.send(f"M:{target}")
        assert app.wait_for(f"[CONFIG] Mode: {target}", 5, sent), "the panel line was not read"
        assert app.wait_for(f"[TTS] '{target} mode'", 10, sent), \
            f"the switch to {target} was not announced:\n{app.text(sent)[-3000:]}"

        deadline = time.monotonic() + 40
        running = None
        while time.monotonic() < deadline:
            assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(before)[-4000:]}"
            last = app.last_of(("Pipeline rebuilt — mode=",), found[0])
            if last and f"mode={target}" in last[2] and app.wait_frames(last[0], 3, 1):
                running = last
                break
            time.sleep(0.5)
        assert running, (f"not running in {target} after the switch:\n{app.text(found[0])[-4000:]}")
        assert app.find("Shutdown initiated", before) is None
        assert_clean(app)
        time.sleep(3)                               # let the camera pass the first seconds
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(240)
@pytest.mark.parametrize("delay", [0.0, 0.1, 0.2, 0.4, 0.8], ids=lambda d: f"after-{d}s")
def test_tc024_003_con08_device_stop_right_after_camera_found(camera_unplug, launch, delay):
    """
    Removal half of TC-024-003 only; REQ-DTA-08 regression, brief criterion 6. The stop (Ctrl+C)
    arrives `delay` s after "Camera found on", i.e. while the vision pipeline is being started on
    the returned camera, or in its first second. (O1, the older defect, was a segfault for a stop
    within ~0.15 s of a pipeline start; brief-2 does not ask for it to be fixed, so this records
    what happens on the new start path.) Expected: exit 0 within the force-exit bound, last motor
    command zero, nothing left running, no CRITICAL.
    """
    app, glasses, panel = launch()
    started(app)
    before = app.index()
    unplug(camera_unplug, app)
    assert_loss_handled(app, before)
    time.sleep(3)
    back_from = app.index()
    replug(camera_unplug, app)
    found = app.wait_for(RESTART_FOUND, RECOVERY_TIMEOUT, back_from, interval=0.02)
    assert found, f"the camera's return was not found:\n{app.text(back_from)[-3000:]}"
    if delay:
        time.sleep(delay)
    stop_at = app.index()
    code, took = app.stop(signal.SIGINT)
    landed = ("camera started after the signal" if CAMERA_BACK in app.text(stop_at)
              else "no start line after the signal")
    print(f"SIGINT {delay} s after '{RESTART_FOUND}': exit {code} in {took:.2f} s; {landed}")
    assert code == 0, f"exit code {code}:\n{app.text(back_from)[-3000:]}"
    assert took < FORCE_EXIT_SECONDS, f"took {took:.2f} s"
    assert "[MAIN] Done." in app.text()
    motors = glasses.motors_between(0)
    assert motors and motors[-1] == MOTOR_ZERO, f"the last motor command is not zero: {motors[-3:]}"
    assert app.leftovers() == [], f"left running: {app.leftovers()}"
    assert_clean(app)


@pytest.mark.timeout(300)
def test_tc024_003_con08_device_camera_found_but_held_by_another_process(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. Brief-2 criterion 1b: "A camera is found but cannot be opened
    (any reason): back to 'waiting', with the clear log line, no crash, no exit." Here the reason
    is real and needs no unplug timing: right after the replug a process of the tester's own
    streams from /dev/video0 (UVC allows one streaming client), so the program's pipeline cannot
    stream from it. The program keeps trying for 20 s (every attempt starts a Hailo pipeline and
    loses it again within the first second: the F2 window, repeated); then the holder is stopped
    and vision must come back with depth frames. Expected: same process, no shutdown, no
    segfault, no CRITICAL, no thread/process left. Inconclusive (skipped) if the program got the
    camera before the holder did.
    """
    app, glasses, panel = launch()
    started(app)
    before = app.index()
    unplug(camera_unplug, app)
    assert_loss_handled(app, before)
    time.sleep(2)
    back_from = app.index()
    replug(camera_unplug, app)
    holder = CameraHolder(CAMERA_NODE)
    try:
        time.sleep(1.5)
        if not holder.alive() or app.find(CAMERA_BACK, back_from) is not None \
                and app.find("Camera lost (", back_from) is None:
            pytest.skip("inconclusive: the program got the camera before the holder did")
        time.sleep(20)
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(back_from)[-4000:]}"
        assert app.find("Shutdown initiated", before) is None
        assert_clean(app)
        attempts = [line for _, line in app.lines[back_from:] if RESTART_FOUND in line]
        lost = [line for _, line in app.lines[back_from:] if LOSS_SEEN in line]
        print(f"while held: {len(attempts)} starts on a camera, {len(lost)} losses")
        assert attempts, "the program never tried the returned camera"
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
    assert back, f"vision did not come back after the holder stopped:\n{app.text(freed)[-4000:]}"
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(300)
@pytest.mark.parametrize("delay", [0.0, 0.2, 0.4, 0.6, 0.8], ids=lambda d: f"after-{d}s")
def test_tc024_003_con08_device_unplug_during_the_programs_first_start(camera_unplug, launch, delay):
    """
    Removal half of TC-024-003 only. The program's FIRST pipeline (at boot) is started by the
    library's run(), not by the camera-restart path the Developer changed, so the Hailo elements are
    not fed their first buffer by the app. This asks what a camera lost in the first second of that
    pipeline does: the detach command is sent `delay` s after the program logs "[MAIN] Starting
    pipeline..." (the command itself needs ~0.2 s before the nodes vanish). The first depth frames
    come ~1.1 s after that line (earlier logs). Expected, as for any other loss: the program stays up
    and waits for a camera, no segfault, no CRITICAL; the camera then returns and vision restarts.
    Not required by the brief's 1a wording (it speaks of a camera that "returns and drops again"),
    recorded because REQ-CON-08 says "removed while running" and the boot pipeline is the one that
    runs most.
    """
    app, glasses, panel = launch()
    starting = app.wait_for("[MAIN] Starting pipeline", START_TIMEOUT, 0, interval=0.01)
    assert starting, f"the program never started its pipeline:\n{app.text()[-3000:]}"
    if delay:
        time.sleep(delay)
    wait_no_pending_timer()
    t_cmd = app.mark(f"usb_sim detach camera --for {DETACH_SECONDS}, {delay} s after '[MAIN] Starting pipeline'")
    camera_unplug.detach(DETACH_SECONDS)
    assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
    time.sleep(10)
    first_frames = len(app.frames_after(0))
    assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text()[-4000:]}"
    assert app.find("Shutdown initiated") is None, f"a shutdown:\n{app.text()[-3000:]}"
    seen = app.find(LOSS_SEEN)
    print(f"detach command {t_cmd - starting[1]:.2f} s after 'Starting pipeline'; depth frame lines before: "
          f"{first_frames}; loss logged: {seen[2][:90] if seen else None}")
    assert seen, f"no camera loss was logged:\n{app.text()[-3000:]}"
    assert app.find(LOSS_DONE), f"the dead pipeline was not torn down:\n{app.text()[-3000:]}"
    assert_clean(app)
    back_from = app.index()
    replug(camera_unplug, app)
    back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
    assert back, f"never restarted:\n{app.text(back_from)[-4000:]}"
    assert app.wait_frames(back[0], 3, 20), "no depth frames after the replug"
    time.sleep(2)
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(1500)
@pytest.mark.parametrize("kind", ["camera-restart", "mode-switch"])
def test_tc024_003_con08_device_memory_over_repeated_restarts(camera_unplug, launch, kind):
    """
    Removal half of TC-024-003 only. Not a pass/fail on memory: it records the program's resident
    memory and thread count after each of 15 restarts of the vision pipeline, so a leak per restart
    (a loose connector can restart vision many times a day) can be seen and compared with the
    baseline's mode switches (`SV_UAT_RUN_SH` points the run at another checkout's run.sh). The
    restarts are camera losses and returns ("camera-restart") or panel mode switches between
    both and depth ("mode-switch"). Asserts only that each restart works, nothing is forbidden in
    the output, and the thread count does not grow by more than 8.
    """
    app, glasses, panel = launch()
    started(app)
    time.sleep(5)
    series = [proc_stats(app.pid)]
    for i in range(15):
        if kind == "camera-restart":
            before = app.index()
            unplug(camera_unplug, app)
            assert_loss_handled(app, before)
            time.sleep(2)
            back_from = app.index()
            replug(camera_unplug, app)
            back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
            assert back, f"restart {i}: never restarted:\n{app.text(back_from)[-3000:]}"
            assert app.wait_frames(back[0], 3, 20), f"restart {i}: no depth frames"
        else:
            switch_mode(app, panel, "depth" if i % 2 == 0 else "both")
        time.sleep(4)
        series.append(proc_stats(app.pid))
    rss = [r for r, _ in series]
    thr = [t for _, t in series]
    per_restart = (rss[-1] - rss[3]) / (len(rss) - 4)
    print(f"{kind}: RSS MB after each restart (first = before any): {rss}")
    print(f"{kind}: threads: {thr}")
    print(f"{kind}: RSS growth restarts 3..15: {rss[-1] - rss[3]} MB, {per_restart:.1f} MB per restart")
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert_clean(app)
    assert thr[-1] <= thr[0] + 8, f"threads grew: {thr}"


@pytest.mark.timeout(1200)
@pytest.mark.parametrize("seed", [11, 12, 13])
def test_tc024_003_con08_device_loose_connector_random_flaps(camera_unplug, launch, seed):
    """
    Removal half of TC-024-003 only (alert half: T-03c). REQ-CON-08, "the program does not crash;
    every other part keeps running; ... when a camera appears, the vision pipeline starts on it",
    for a loose connector that drops and returns many times in one run. 25 flaps in ONE process
    (so every start after the first runs in a process where earlier Hailo pipelines were torn
    down, the condition under which attempt 1 crashed). Each flap: the camera is detached for 1, 2
    or 3 s (the tool's own timer is the replug), then either (a) it stays for 0 to 2.6 s, or (b)
    once the program logs "Camera found on" it stays for 0 to 0.7 s, before the next drop. The
    choices come from random.Random(seed), so a failure can be repeated. After each flap: the
    program is still the same process, no shutdown, no forbidden line (incl. CRITICAL). At the end
    the camera stays: vision restarts on it and depth frames come back, no dead camera node is
    held, Ctrl+C exits 0. Real camera and Hailo; fake glasses board and panel.
    """
    rng = random.Random(seed)
    app, glasses, panel = launch()
    started(app)
    kinds = {"free": 0, "after-found": 0}
    for flap in range(25):
        away = rng.choice([1, 1, 2, 3])
        mode = "after-found" if rng.random() < 0.5 else "free"
        up = rng.uniform(0.0, 2.6) if mode == "free" else rng.uniform(0.0, 0.7)
        kinds[mode] += 1
        before = app.index()
        wait_no_pending_timer()
        app.mark(f"flap {flap}: usb_sim detach camera --for {away}; then {mode} {up:.2f} s")
        camera_unplug.detach(away)
        assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
        assert camera_unplug.wait_for_nodes(True, away + 15), "the tool did not bring the camera back"
        if mode == "after-found":
            found = app.wait_for(RESTART_FOUND, away + 25, before, interval=0.02)
            assert found, f"flap {flap}: the camera's return was not found:\n{app.text(before)[-3000:]}"
        time.sleep(up)
        assert app.alive(), f"flap {flap}: the program ended (exit {app.proc.returncode}):\n{app.text()[-4000:]}"
        assert app.find("Shutdown initiated") is None, f"flap {flap}: a shutdown:\n{app.text(before)[-3000:]}"
        assert_clean(app)
    wait_no_pending_timer()
    time.sleep(3)
    if not camera_unplug.nodes_present():
        replug(camera_unplug, app)
    final_from = app.index()
    state = app.last_of(WAITING_AGAIN + (CAMERA_BACK, LOSS_SEEN), 0)
    if not (state and CAMERA_BACK in state[2]):       # not running yet: it must restart on the camera
        back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, final_from)
        assert back, f"vision never restarted after the last flap:\n{app.text(final_from)[-4000:]}"
    frames_from = app.index()
    assert app.wait_frames(frames_from, 3, 30), f"no depth frames after the last flap:\n{app.text(frames_from)[-3000:]}"
    assert app.alive()
    held = app.open_video_nodes()
    assert all(os.path.exists(node) for node in held), f"a dead camera node is held: {held}"
    counts = {needle: sum(1 for _, line in list(app.lines) if needle in line)
              for needle in (RESTART_FOUND, "did not start on", CAMERA_BACK, LOSS_SEEN, "waiting for a camera instead")}
    print(f"seed {seed}: 25 flaps ({kinds}); log lines: {counts}")
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(240)
@pytest.mark.parametrize("what,delay", [
    ("sigint", 0.05), ("sigint", 0.15), ("sigint", 0.4),
    ("mode-switch", 0.05), ("mode-switch", 0.15), ("mode-switch", 0.4),
    ("rebuild-then-sigint", 0.05), ("rebuild-then-sigint", 0.15),
], ids=lambda v: str(v))
def test_tc024_003_con08_device_boot_pipeline_stopped_or_switched_before_its_first_frame(
        camera_unplug, launch, what, delay):
    """
    NOT a TC-024-003 requirement: an observation for the board (T-11 / "O1": brief-2 says Ctrl+C
    within about 0.15 s of any pipeline start segfaulted 3 times in 5, baseline too). The program's
    FIRST pipeline is started by the library's run(), so its Hailo elements are not primed by the
    app; this asks what happens if it is taken down before it has seen a frame, by a stop or by a
    panel mode switch (the first rebuild is not delayed: `_last_rebuild_at` starts at 0). The
    first depth frames come about 1.1 s after "[MAIN] Starting pipeline". Expected: the program
    exits 0 (stop) or rebuilds for the new mode and keeps running (mode switch); no segfault, no
    CRITICAL. Real camera and Hailo; fake glasses board and panel. Run it N times with a shell
    loop to get a rate; compare with a baseline checkout through SV_UAT_RUN_SH.
    """
    app, glasses, panel = launch()
    starting = app.wait_for("[MAIN] Starting pipeline", START_TIMEOUT, 0, interval=0.005)
    assert starting, f"the program never started its pipeline:\n{app.text()[-3000:]}"
    time.sleep(delay)
    if what == "sigint":
        code, took = app.stop(signal.SIGINT)
        print(f"SIGINT {delay} s after 'Starting pipeline': exit {code} in {took:.2f} s")
        assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
        assert app.leftovers() == []
        assert_clean(app)
        return
    start = app.index()
    if what == "rebuild-then-sigint":
        # The O1 case: the program's SECOND pipeline (built by a panel mode switch) is stopped
        # `delay` s after "Pipeline rebuilt" (here the delay counts from the rebuild, not from the start).
        panel.send("M:depth")
        rebuilt = app.wait_for("Pipeline rebuilt — mode=depth", 40, start, interval=0.005)
        assert rebuilt, f"no rebuild (alive={app.alive()}, exit {app.proc.poll()}):\n{app.text(start)[-3000:]}"
        time.sleep(delay)
        code, took = app.stop(signal.SIGINT)
        print(f"SIGINT {delay} s after 'Pipeline rebuilt': exit {code} in {took:.2f} s")
        assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
        assert app.leftovers() == []
        assert_clean(app)
        return
    app.mark(f"panel sends M:depth, {delay} s after 'Starting pipeline'")
    panel.send("M:depth")
    rebuilt = app.wait_for("Pipeline rebuilt — mode=depth", 40, start)
    assert rebuilt, f"no rebuild (alive={app.alive()}, exit {app.proc.poll()}):\n{app.text(start)[-3000:]}"
    assert app.wait_frames(rebuilt[0], 3, 30), "no depth frames after the mode switch"
    assert app.alive()
    time.sleep(2)
    code, took = app.stop(signal.SIGINT)
    print(f"mode switch {delay} s after 'Starting pipeline': rebuilt, exit {code} in {took:.2f} s")
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


def cpu_ticks(pid: int) -> int:
    """utime + stime of the whole program (all threads), in clock ticks; -1 if unreadable."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return -1


@pytest.mark.timeout(600)
def test_tc024_003_con08_device_long_absence_keeps_looking_and_says_so_once_a_minute(camera_unplug, launch):
    """
    Removal half of TC-024-003 only. REQ-CON-08: "the device keeps checking the USB ports for a
    camera". decision-1 Q5 asked for one INFO line about every 60 s while no camera is found
    ("Still no USB camera after N s"), which so far only a gst test (shortened period) covers. The
    camera is detached for 135 s (the tool's own timer is the replug). Expected: the program stays
    up the whole time; the line appears at about 60 s and 120 s after the wait began, and not
    between; the idle wait costs little CPU (measured over 20 s, under 50 % of one core); then the
    camera returns and vision restarts on it, depth frames come back, Ctrl+C exits 0.
    """
    app, glasses, panel = launch()
    started(app)
    before = app.index()
    wait_no_pending_timer()
    app.mark("usb_sim detach camera --for 135 (the tool's timer is the replug)")
    camera_unplug.detach(135)
    assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
    done = app.wait_for(LOSS_DONE, LOSS_TIMEOUT, before)
    assert done, f"the dead pipeline was not torn down:\n{app.text(before)[-3000:]}"
    t_wait = done[1]
    time.sleep(25)
    ticks0, t0 = cpu_ticks(app.pid), time.monotonic()
    time.sleep(20)
    ticks1, t1 = cpu_ticks(app.pid), time.monotonic()
    hertz = os.sysconf("SC_CLK_TCK")
    cpu = (ticks1 - ticks0) / hertz / (t1 - t0) * 100 if ticks0 >= 0 and ticks1 >= 0 else -1
    # up to 115 s after the wait began, then through the replug at 135 s
    deadline = t_wait + 128
    while time.monotonic() < deadline:
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(before)[-4000:]}"
        time.sleep(1)
    lines = [(t - t_wait, line) for t, line in list(app.lines)[before:] if "Still no USB camera after" in line]
    print(f"CPU while waiting: {cpu:.1f} % of one core (20 s window)")
    print("'Still no USB camera' lines (seconds after the wait began): "
          + "; ".join(f"{t:.1f} s: {line[-70:]}" for t, line in lines))
    assert app.find("Shutdown initiated", before) is None
    assert 0 <= cpu < 50, f"waiting for a camera used {cpu:.1f} % of one core"
    assert len(lines) == 2, f"expected two 'Still no USB camera' lines by 128 s, got {len(lines)}"
    assert 55 <= lines[0][0] <= 66 and 115 <= lines[1][0] <= 126, [t for t, _ in lines]
    back_from = app.index()
    wait_no_pending_timer(limit=40)
    assert camera_unplug.wait_for_nodes(True, 15), "the tool did not bring the camera back"
    back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, back_from)
    assert back, f"never restarted after the long absence:\n{app.text(back_from)[-4000:]}"
    assert app.wait_frames(back[0], 3, 20), "no depth frames after the long absence"
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)
