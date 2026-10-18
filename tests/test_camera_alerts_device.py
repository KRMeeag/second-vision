"""
T-03c1, device level: the camera's status in speech, on the real program (REQ-CON-07 spoken half,
REQ-CON-09; UAT cases TC-024-002, TC-024-003, TC-024-004). Written by the Developer; the Tester's own
versions are in tests/uat/.

What is real: the program, launched as the boot service launches it (scripts/run.sh --input /dev/video0
--width 640 --height 480 --serial-port ... --config-port ..., no SV_SERVICE, no DISPLAY); the camera;
the Hailo chip; the real tts_worker (its mailbox, pacing and espeak-ng subprocess protocol); the real
alert scheduler and worker; the unplug, simulated by .claude/tools/usb_sim.py through the conftest
`camera_unplug` fixture (the kernel's USB `authorized` switch: the camera's driver lets go and its
/dev/video* nodes vanish, as on a pulled cable).
What is a stand-in: `espeak-ng` itself. A script first on PATH records when each phrase starts and ends
(CLOCK_MONOTONIC, the clock of this process too) and runs as long as the real sentence does (measured
with the real espeak-ng at the program's speed 160: 2.75 s and 2.02 s). No sound. Whether a person
HEARS it through earphones is the user-observed step in docs/testing/MANUAL-TESTS.md. The glasses
board and the control panel are pseudo-terminals (the board ACKs every packet; the panel sends the
`M:<mode>` lines).

What the checks allow for (see "Reading the program's own record" below; made-up histories in
tests/test_camera_alerts_checks.py show they still catch every fault): the checks run on every camera
episode the program logs, not only the ones the test caused, because on this Pi the camera has dropped on
its own with no unplug in progress; and a camera sentence cut by an urgent object warning (the alert
scheduler's rule, until D-16 changes it) is not counted as said, must have been cut by another announcement,
and is said again by the scheduler.

usb_sim.py defect designed around (as in tests/uat/test_tc024_003_camera_removed.py): every
`detach --for N` leaves a timer that re-attaches whichever detach is current after N s. So most
detaches here use the tool's own timer as the replug, and no new detach starts while an earlier timer
is still pending (checked with pgrep).

Opt-in, boot service stopped, camera attached, passwordless sudo for the tool:
    my_hailo_env/bin/python -m pytest tests/test_camera_alerts_device.py -v -s --run-device
Output of each run goes to $SV_UAT_APP_LOG_DIR (default: pytest's tmp_path): one app log and one
speech log per run.
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import statistics
import subprocess
import sys
import threading
import time
import tty
from pathlib import Path

import pytest

pytestmark = pytest.mark.device

REPO = Path(__file__).resolve().parents[1]
RUN_SH = Path(os.environ.get("SV_UAT_RUN_SH") or REPO / "scripts" / "run.sh")
CAMERA_NODE = "/dev/video0"

MISSING = "Critical Error: Camera cannot be reached"
BACK = "Camera has been reconnected"
SPEECH_SECONDS = {MISSING: 2.75, BACK: 2.02}          # measured with the real espeak-ng -s 160
OTHER_SPEECH_SECONDS = 1.0

TIMER_PATTERN = "usb_sim.py attach camera --from-timer"
START_TIMEOUT = 90                                   # program start to first depth frame (HEF load)
RECOVERY_TIMEOUT = 60                                # replug to "Camera back"
STOP_BOUND_SECONDS = 10.0                            # the unit's TimeoutStopSec=10
T03B_STOP_ENVELOPE = (0.36, 1.12)                    # T-03b decision-1: 30 of 30 exit 0 in this time

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FRAME = re.compile(r"\[DEPTH\] Frame (\d+)")
LOSS_SEEN = "Camera lost ("
LOSS_DONE = "Camera lost: vision pipeline stopped and Hailo device released"
CAMERA_BACK = "Camera back: vision pipeline running on"
ALERT_CLEARED = "[ALERT] cleared 'camera-missing'"
NO_CAMERA_AT_START = "No camera at start-up"
CONFIRM_SECONDS = 1.0                                # app.CAMERA_BACK_CONFIRM_SECONDS: a return is said this long after the pipeline runs
FAILED_START_WINDOW = 1.5                            # a camera lost this soon after "Camera back" never came back (held by another process)
MAIN_LOOP = "[MAIN] Starting pipeline"
NEVER = ("Traceback", "Segmentation fault", "MOCK", "[MOCK]", "HAILO_OUT_OF_PHYSICAL_DEVICES",
         "not enough free devices", "Failed to start new pipeline", "Exception during pipeline rebuild",
         "Exiting with error", "stopped acknowledging", "forcing exit", "CRITICAL", "Fatal Python error",
         "Could not raise the missing-camera alert", "Could not announce that the camera is back")
ACK = bytes([0xAA, 0xFF, 0x01])
PACKET_LENGTHS = {0x01: 6, 0xFE: 3, 0x04: 5}

STUB = """#!{python} -S
import json, os, signal, sys, time
text = sys.argv[-1]
cfg = json.loads(os.environ.get("ESPEAK_STUB_CONFIG", "{{}}"))
duration = cfg.get("texts", {{}}).get(text, cfg.get("default", 0.3))
log = os.open(os.environ["ESPEAK_STUB_LOG"], os.O_WRONLY | os.O_APPEND | os.O_CREAT)
def rec(ev, **kw):
    os.write(log, (json.dumps(dict(ev=ev, text=text, t=time.monotonic(), pid=os.getpid(), **kw)) + "\\n").encode())
def on_term(signum, frame):
    rec("end", cut=True)
    os._exit(143)
signal.signal(signal.SIGTERM, on_term)
rec("start", argv=sys.argv[1:])
time.sleep(duration)
rec("end", cut=False)
"""


# ============================================================
# Stand-ins
# ============================================================
class FakeGlasses:
    """The glasses ESP32 on a pty: ACKs every packet and counts them."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.packets = 0
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
                buf = buf[length:]
                self.packets += 1
                try:
                    os.write(self.master, ACK)
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


class FakePanel:
    """The control panel on a pty: sends text lines (M:<mode>) as the real panel does."""

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


class SpeechLog:
    """What the stub espeak-ng recorded: one dict per utterance (text, start, end, cut)."""

    def __init__(self, path: Path):
        self.path = path

    def utterances(self) -> list[dict]:
        events = []
        try:
            for line in self.path.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass                       # a line being written
        except OSError:
            return []
        begun, out = {}, []
        for event in events:
            if event["ev"] == "start":
                begun[event["pid"]] = event
                out.append({"text": event["text"], "start": event["t"], "end": None, "cut": False,
                            "pid": event["pid"]})
            elif event["pid"] in begun:
                for utt in reversed(out):
                    if utt["pid"] == event["pid"]:
                        utt["end"], utt["cut"] = event["t"], event.get("cut", False)
                        break
        return out

    def sentences(self) -> list[dict]:
        """The camera's two sentences that were not cut (one still being said counts)."""
        return [u for u in self.utterances() if u["text"] in (MISSING, BACK) and not u["cut"]]

    def count(self, text: str, since: float = 0.0) -> int:
        """How many times `text` was started since the monotonic time `since`, not counting a cut one."""
        return sum(1 for u in self.utterances() if u["text"] == text and u["start"] >= since and not u["cut"])


class AppRun:
    """scripts/run.sh as the boot launcher calls it, output captured line by line with times."""

    def __init__(self, log_path: Path, serial_port: str, config_port: str, env_extra: dict):
        self.log_path = log_path
        self.cmd = [str(RUN_SH), "--input", CAMERA_NODE, "--width", "640", "--height", "480",
                    "--serial-port", serial_port, "--config-port", config_port]
        env = dict(os.environ)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SV_SERVICE"):
            env.pop(name, None)
        env.update(env_extra)
        env["PYTHONUNBUFFERED"] = "1"
        self.lines: list[tuple[float, str]] = []
        self._log = open(log_path, "w", buffering=1)
        self._log.write(f"# command: {' '.join(self.cmd)}\n# started: {time.strftime('%F %T %Z')}\n")
        self.started_at = time.monotonic()
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

    def mark(self, text: str) -> float:
        now = time.monotonic()
        try:
            self._log.write(f"{now - self.started_at:9.3f} ### TEST: {text}\n")
        except ValueError:
            pass
        return now

    def index(self) -> int:
        return len(self.lines)

    def find(self, needle: str, start: int = 0):
        for i, (t, line) in enumerate(list(self.lines)[start:], start):
            if needle in line:
                return i, t, line
        return None

    def wait_for(self, needle: str, timeout: float, start: int = 0, interval: float = 0.05):
        limit = time.monotonic() + timeout
        while True:
            hit = self.find(needle, start)
            if hit is not None:
                return hit
            if time.monotonic() >= limit or self.proc.poll() is not None:
                return self.find(needle, start)
            time.sleep(interval)

    def count(self, needle: str, start: int = 0) -> int:
        return sum(1 for _, line in list(self.lines)[start:] if needle in line)

    def frames_after(self, start: int) -> list[int]:
        return [int(m.group(1)) for _, line in list(self.lines)[start:] for m in [FRAME.search(line)] if m]

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

    def events(self) -> list[tuple[float, str]]:
        """
        The program's own record of the camera, in order: (monotonic time, kind) with kind 'wait' (it began
        without a camera), 'lost' (a camera loss was noticed), 'back' (the vision pipeline runs on a camera)
        or 'cleared' (the missing-camera alert was withdrawn). This is what the program did, whatever the
        test did: a camera that dropped on its own shows up here too.
        """
        kinds = ((NO_CAMERA_AT_START, "wait"), (LOSS_SEEN, "lost"), (CAMERA_BACK, "back"), (ALERT_CLEARED, "cleared"))
        found = []
        for t, line in list(self.lines):
            for needle, kind in kinds:
                if needle in line:
                    found.append((t, kind))
                    break
        return found

    def stop(self, signum: int, timeout: float = 15.0) -> tuple[int | None, float]:
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
        """Processes still in the program's session, as 'pid cmd'; a stub espeak-ng finishing its sentence is not one."""
        out = subprocess.run(["ps", "-eo", "pid=,sid=,args="], capture_output=True, text=True).stdout
        left = []
        for row in out.splitlines():
            parts = row.split(None, 2)
            if len(parts) >= 2 and parts[1] == str(self.pid) and parts[0] != str(self.pid):
                cmd = parts[2] if len(parts) > 2 else ""
                if "espeak-ng" not in cmd:
                    left.append(f"{parts[0]} {cmd}")
        return left

    def close(self):
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


def wait_no_pending_timer(limit: float = 120.0, interval: float = 0.5) -> None:
    deadline = time.monotonic() + limit
    while pending_timers():
        if time.monotonic() > deadline:
            pytest.fail(f"a usb_sim re-attach timer is still pending after {limit} s: {pending_timers()}",
                        pytrace=False)
        time.sleep(interval)


def unplug_for(camera_unplug, app: AppRun | None, seconds: int, check_gone: bool = True) -> float:
    """Detach the camera for `seconds` (the tool's timer is the replug); returns the time of the command."""
    wait_no_pending_timer()
    assert os.path.exists(CAMERA_NODE) or camera_unplug.nodes_present(), "no camera to unplug"
    t = time.monotonic()
    if app is not None:
        app.mark(f"usb_sim detach camera --for {seconds}")
    camera_unplug.detach(seconds)
    if check_gone:                       # not for a 1 s bounce: the nodes may be back before the first look
        assert camera_unplug.wait_for_nodes(False, 5), "the camera's video nodes did not go away"
        if app is not None:
            app.mark("camera nodes gone")
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
def launch(request, app_log_dir, tmp_path, monkeypatch):
    """Start the program on a stub espeak-ng; always stopped by PID at the end. Returns (app, panel, speech)."""
    runs, stand_ins = [], []
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "espeak-ng"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o755)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)

    def start():
        stamp = time.strftime("%Y-%m-%d-%H%M%S") + f"-{len(runs)}"
        speech_path = app_log_dir / f"{stamp}-speech-{name}.log"
        glasses, panel = FakeGlasses(), FakePanel()
        stand_ins.extend([glasses, panel])
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "ESPEAK_STUB_LOG": str(speech_path),
            "ESPEAK_STUB_CONFIG": json.dumps({"texts": SPEECH_SECONDS, "default": OTHER_SPEECH_SECONDS}),
        }
        app = AppRun(app_log_dir / f"{stamp}-app-{name}.log", glasses.path, panel.path, env)
        runs.append(app)
        print(f"app log: {app.log_path}\nspeech log: {speech_path}")
        return app, panel, SpeechLog(speech_path)

    yield start
    for app in runs:
        app.close()
    for stand_in in stand_ins:
        stand_in.close()


# ============================================================
# Steps and checks
# ============================================================
def started(app: AppRun) -> int:
    assert app.wait_frames(0, 3, START_TIMEOUT), \
        f"the program did not start streaming (alive={app.alive()}):\n{app.text()[-3000:]}"
    return app.index()


def switch_mode(app: AppRun, panel: FakePanel, mode: str, timeout: float = 30) -> int:
    start = app.index()
    app.mark(f"panel sends M:{mode}")
    panel.send(f"M:{mode}")
    assert app.wait_for(f"[CONFIG] Mode: {mode}", 5, start), "the panel line was not read"
    assert app.wait_for(f"Pipeline rebuilt — mode={mode}", timeout, start), \
        f"the pipeline was not rebuilt for {mode}:\n{app.text(start)[-3000:]}"
    time.sleep(3)
    return start


def assert_clean(app: AppRun) -> None:
    bad = app.forbidden()
    assert bad == [], f"forbidden lines in the program's output: {bad[:10]}"


def collapse(sentences: list[dict]) -> list[str]:
    """['M','M','B',...] -> the texts with consecutive repeats of the missing sentence merged."""
    out: list[str] = []
    for utt in sentences:
        if not (utt["text"] == MISSING and out and out[-1] == MISSING):
            out.append(utt["text"])
    return out


def wait_back_spoken(speech: SpeechLog, since: float, total: int, timeout: float = 12.0) -> None:
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if speech.count(BACK, since) >= total:
            return
        time.sleep(0.1)
    pytest.fail(f"'{BACK}' was not said (count {speech.count(BACK, since)}, wanted {total})", pytrace=False)


# ---- Reading the program's own record and the speech log ----------------------------------------
# Two things on the device are not under the test's control, and a check that ignored them would fail
# for reasons that are not the feature's:
#  * an urgent object warning ("person center") may cut a camera sentence that is being said: that is the
#    alert scheduler's documented rule (core/alerts.py "Not lost"; D-16 will change it later). The
#    scheduler says the sentence again as soon as the channel is free. So a camera sentence that was cut
#    is not counted as said; it must have been cut by another announcement (never by the camera code),
#    and all the time the channel was busy with other speech is allowed for in the timing checks;
#  * the camera may drop on its own, with no unplug in progress (seen again on 2026-10-18; the Lead's
#    brief says to report it). The program's own log shows every loss and return, planned or not, so the
#    checks run on EVERY episode the program logged, and the test says how many were not its own.
def build_episodes(events: list[tuple[float, str]]) -> list[dict]:
    """
    Group the program's camera events (AppRun.events) into episodes: from a loss (or from the start-up
    wait) to the return that held. A "back" followed by a loss within FAILED_START_WINDOW s was a start
    that failed at once (the camera held by another process, or gone again): the episode goes on.
    Each episode: {'loss', 'back', 'cleared', 'startup'}; 'back' is None while it is still open.
    """
    episodes: list[dict] = []
    open_ep, last_closed = None, None
    for i, (t, kind) in enumerate(events):
        if kind in ("wait", "lost"):
            if open_ep is None:
                open_ep = {"loss": t, "back": None, "cleared": None, "startup": kind == "wait"}
                episodes.append(open_ep)
        elif kind == "back" and open_ep is not None:
            following = next((u for u, k in events[i + 1:] if k in ("lost", "wait")), None)
            if following is not None and following - t < FAILED_START_WINDOW:
                continue
            open_ep["back"] = t
            last_closed, open_ep = open_ep, None
        elif kind == "cleared" and last_closed is not None and last_closed["cleared"] is None:
            last_closed["cleared"] = t
    return episodes


def busy_seconds(utts: list[dict], start: float, end: float, exclude: tuple = ()) -> float:
    """Seconds of [start, end] in which some utterance, other than the `exclude` ones (by pid), was being said."""
    skip = {u["pid"] for u in exclude}
    now = time.monotonic()
    spans = sorted((max(u["start"], start), min(u["end"] if u["end"] is not None else now, end))
                   for u in utts if u["pid"] not in skip)
    total, edge = 0.0, start
    for s, e in spans:
        s = max(s, edge)
        if e > s:
            total += e - s
            edge = e
    return total


def check_episode(utts: list[dict], ep: dict, until: float, label: str, min_repeats: int = 1) -> dict:
    """
    One loss and its return, from the speech log (all utterances, cut ones included) and the episode the
    program logged. `until`: where the next episode begins (or now); nothing of this episode may be said
    after the return and before it.
      * the first missing sentence begins within 2 s of the loss being noticed, plus any time the channel
        was busy with other speech;
      * then every 10 s from the START of each (9 s at the least; later only by the time other speech took);
      * nothing more of it starts once the alert was cleared (and, unless a sentence was cut and is owed,
        nothing after the pipeline runs);
      * "reconnected" is said exactly once (one that ran to its end), not before CONFIRM_SECONDS after the
        pipeline runs and then without delay but for other speech;
      * a camera sentence that was cut was cut by another announcement.
    """
    loss_t, back_t, cleared_t = ep["loss"], ep["back"], ep["cleared"]
    assert back_t is not None, f"{label}: the camera never came back"
    assert cleared_t is not None, f"{label}: the missing-camera alert was not cleared after the return"
    camera = (MISSING, BACK)
    window_end = cleared_t + 0.15
    missing = [u for u in utts if u["text"] == MISSING and loss_t - 0.5 <= u["start"] <= window_end]
    done = [u for u in missing if not u["cut"]]
    assert done, f"{label}: the missing sentence was not said: {utts}"
    cut_missing = [u for u in missing if u["cut"]]

    first = missing[0]["start"] - loss_t
    first_wait = busy_seconds(utts, loss_t, missing[0]["start"], exclude=(missing[0],))
    assert first - first_wait <= 2.0, \
        f"{label}: the first missing sentence came {first:.2f} s after the loss was noticed ({first_wait:.2f} s of it busy)"
    expected = max(min_repeats, int((back_t - loss_t - 1.5) // 10) + 1) - (1 if cut_missing else 0)
    assert len(done) >= expected, \
        f"{label}: {len(done)} missing sentences in {back_t - loss_t:.1f} s, wanted {expected}: {utts}"
    gaps = [b["start"] - a["start"] for a, b in zip(done, done[1:])]
    for a, b in zip(done, done[1:]):
        gap = b["start"] - a["start"]
        extra = busy_seconds(utts, a["start"] + 10.0, b["start"], exclude=(a, b))
        assert gap >= 9.0 and gap - 10.0 <= extra + 1.0, \
            f"{label}: repeat gap {gap:.2f} s (start to start; {extra:.2f} s of other speech): {gaps}"

    stray = [u for u in utts if u["text"] == MISSING and window_end < u["start"] <= until]
    assert not stray, f"{label}: the missing sentence went on after the alert was cleared: {stray}"
    if not cut_missing:
        late = [u for u in missing if u["start"] > back_t + 0.3]
        assert not late, f"{label}: the missing sentence was said after the pipeline ran: {late}"

    backs = [u for u in utts if u["text"] == BACK and back_t - 0.3 <= u["start"] <= until]
    done_backs = [u for u in backs if not u["cut"]]
    assert len(done_backs) == 1, f"{label}: {len(done_backs)} reconnected sentences that ran to their end: {backs}"
    delay = backs[0]["start"] - back_t
    assert delay >= CONFIRM_SECONDS - 0.15, \
        f"{label}: 'reconnected' came {delay:.2f} s after the pipeline ran, before the return was confirmed"
    confirm_t = back_t + CONFIRM_SECONDS
    waited = busy_seconds(utts, back_t, backs[0]["start"], exclude=tuple(backs))
    late_by = backs[0]["start"] - confirm_t - busy_seconds(utts, confirm_t, backs[0]["start"], exclude=tuple(backs))
    assert late_by <= 0.6, \
        f"{label}: 'reconnected' came {delay:.2f} s after the pipeline ran, {late_by:.2f} s later than the confirmation " \
        f"and the other speech explain ({waited:.2f} s busy)"

    cuts = [u for u in utts if u["text"] in camera and u["cut"] and loss_t - 0.5 <= u["start"] <= until]
    for u in cuts:
        follower = next((o for o in utts if o is not u and u["end"] - 0.05 <= o["start"] <= u["end"] + 0.6), None)
        assert follower is not None and follower["text"] not in camera, \
            f"{label}: '{u['text']}' was cut by something that is not another announcement: {follower}"
    return {"first": first, "repeats": len(done), "gaps": gaps, "delay": delay, "waited": waited,
            "cuts": len(cuts), "startup": ep["startup"]}


def check_run(app: AppRun, speech: SpeechLog, planned: int, label: str, min_repeats: dict | None = None) -> list[dict]:
    """
    Check every camera episode the program logged. `planned`: the episodes the test caused (the program
    logs at least that many). Prints one line per episode and says how many were not the test's own.
    The sentences, collapsed, are exactly [missing..., reconnected] once per episode.
    """
    now = time.monotonic()
    episodes = build_episodes(app.events())
    assert len(episodes) >= planned, \
        f"{label}: the program logged {len(episodes)} camera episodes, the test caused {planned}: {episodes}"
    utts = speech.utterances()
    results = []
    for i, ep in enumerate(episodes):
        until = episodes[i + 1]["loss"] - 0.3 if i + 1 < len(episodes) else now
        results.append(check_episode(utts, ep, until, f"{label}, episode {i + 1}", (min_repeats or {}).get(i, 1)))
        print(describe(f"{label}, episode {i + 1}", results[-1]))
    sequence = collapse(speech.sentences())
    assert sequence == [MISSING, BACK] * len(episodes), \
        f"{label}: sentences {[u['text'] for u in speech.sentences()]} for {len(episodes)} episodes"
    assert speech.count(BACK) == len(episodes)
    cuts = sum(r["cuts"] for r in results)
    print(f"{label}: {len(episodes)} episodes, {planned} caused by the test, {len(episodes) - planned} not (the camera "
          f"dropped with no unplug in progress); {cuts} camera sentences cut by object announcements and said again")
    return results


def describe(label: str, ep: dict) -> str:
    gaps = ", ".join(f"{g:.1f}" for g in ep["gaps"]) or "-"
    return (f"{label}: first sentence {ep['first']:+.2f} s after the loss, {ep['repeats']} sentences "
            f"(gaps {gaps} s), 'reconnected' {ep['delay']:.2f} s after the pipeline ran "
            f"(waited {ep['waited']:.2f} s for other speech), {ep['cuts']} cut by object announcements")


def settle(app: AppRun, speech: SpeechLog, label: str, timeout: float = 120.0, quiet: float = 4.0) -> None:
    """
    Wait until the program is on a camera with nothing owed: its last camera event is a return that held
    (a pipeline running, the alert cleared, 'reconnected' said, `quiet` s without a new loss). A camera
    that dropped on its own in the meantime is waited out here; check_run() checks it with the others.
    """
    deadline = time.monotonic() + timeout
    while True:
        events = app.events()
        episodes = build_episodes(events)
        if not episodes:
            return
        last = episodes[-1]
        now = time.monotonic()
        said = speech.count(BACK) >= len(episodes)
        if last["back"] is not None and last["cleared"] is not None and said and now - last["back"] >= quiet:
            return
        if now > deadline:
            pytest.fail(f"{label}: the program did not settle on a camera within {timeout:.0f} s: {last}, "
                        f"'reconnected' said {speech.count(BACK)} of {len(episodes)} times", pytrace=False)
        time.sleep(0.2)


def one_cycle(app: AppRun, panel: FakePanel, camera_unplug, speech: SpeechLog, mode: str,
              away: int, label: str, current_mode: str) -> None:
    """
    Settle on the camera, switch to `mode` if needed, unplug for `away` s, wait for the return and for
    'reconnected'. What was said is checked afterwards, for every episode, by check_run().
    """
    settle(app, speech, label)
    if mode != current_mode:
        switch_mode(app, panel, mode)
    time.sleep(3)
    before = app.index()
    unplug_for(camera_unplug, app, away)
    seen = app.wait_for(LOSS_SEEN, 20, before)
    assert seen, f"{label}: no camera loss logged:\n{app.text(before)[-3000:]}"
    back = app.wait_for(CAMERA_BACK, away + RECOVERY_TIMEOUT, seen[0])
    assert back, f"{label}: the pipeline never restarted:\n{app.text(seen[0])[-4000:]}"
    assert f"(mode={mode})" in back[2], f"{label}: restarted in the wrong mode: {back[2]}"
    wait_back_spoken(speech, seen[1], 1)
    if mode in ("both", "depth"):
        frames = app.wait_frames(back[0], 3, 25)
        dropped = app.find(LOSS_SEEN, back[0] + 1) is not None
        assert frames or dropped, f"{label}: no depth frames"       # a camera that dropped again has none


# ============================================================
# The tests
# ============================================================
@pytest.mark.timeout(420)
def test_t03c1_device_absent_at_start_up_says_it_every_10_s_for_over_60_s_then_reconnects(camera_unplug, launch):
    """
    Criterion 1 and the first half of 3 (REQ-CON-07, TC-024-002, then REQ-CON-09 / TC-024-004): the
    camera is detached before the program starts and comes back 85 s later (the tool's timer). The
    program says the missing sentence at once and every 10 s for as long as no camera is there (here
    about 75 s: at least six sentences), then, when the vision pipeline runs on the camera, nothing
    more for the alert and "Camera has been reconnected" once.
    """
    t_detach = unplug_for(camera_unplug, None, 85)
    app, panel, speech = launch()
    waiting = app.wait_for(NO_CAMERA_AT_START, 60)
    assert waiting, f"the program did not start without a camera:\n{app.text()[-3000:]}"
    main_loop = app.find(MAIN_LOOP)
    assert main_loop, "no '[MAIN] Starting pipeline' line"
    back = app.wait_for(CAMERA_BACK, 85 + RECOVERY_TIMEOUT, waiting[0])
    assert back, f"the pipeline never started when the camera returned:\n{app.text()[-4000:]}"
    wait_back_spoken(speech, waiting[1], 1)
    time.sleep(12)                                   # more than one cadence: nothing may be said now
    settle(app, speech, "absent at start-up")
    results = check_run(app, speech, 1, "absent at start-up", min_repeats={0: 6})
    absent_seconds = back[1] - main_loop[1]
    first = min(u["start"] for u in speech.sentences())
    print(f"first sentence {first - main_loop[1]:+.2f} s after '{MAIN_LOOP}'; absent for {absent_seconds:.0f} s "
          f"of the main loop; camera detached {back[1] - t_detach:.0f} s before the pipeline ran")
    assert build_episodes(app.events())[0]["startup"]
    assert results[0]["startup"] and first - main_loop[1] <= 2.0
    assert absent_seconds >= 60
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(420)
@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_t03c1_device_lost_while_running_in_each_mode(camera_unplug, launch, mode):
    """
    Criterion 2, 3, 6 (REQ-CON-07, REQ-CON-09; TC-024-003, TC-024-004), per mode. With the camera present
    and the mode switched, nothing is said. The camera is detached for 24 s (the tool's timer is the
    replug): the missing sentence begins within 2 s of the loss being noticed (the log line) and repeats
    every 10 s; when the pipeline runs again nothing more for it and "reconnected" once; then silence.
    """
    app, panel, speech = launch()
    started(app)
    if mode != "both":
        switch_mode(app, panel, mode)
    time.sleep(4)
    assert app.find(LOSS_SEEN) is None and speech.sentences() == [], "a camera sentence was said with the camera present"
    one_cycle(app, panel, camera_unplug, speech, mode, 24, f"mode {mode}", mode)
    time.sleep(10)                                   # one more cadence of silence
    settle(app, speech, f"mode {mode}")
    results = check_run(app, speech, 1, f"mode {mode}")
    assert results[0]["repeats"] >= 2
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(1500)
def test_t03c1_device_ten_loss_and_return_cycles_in_mixed_modes(camera_unplug, launch):
    """
    Criterion 5 and "fixed means repeated": ten loss-and-return cycles in one run, modes mixed (both,
    depth, detection), 14 s away each. In none of them is the sentence missing, doubled or out of order:
    'missing' then 'reconnected' once per episode the program logged (ten caused by the test, plus any
    the camera caused by dropping on its own), each checked on its own.
    """
    modes = ["both", "depth", "detection", "both", "detection", "depth", "both", "depth", "detection", "both"]
    app, panel, speech = launch()
    started(app)
    current = "both"
    for i, mode in enumerate(modes):
        one_cycle(app, panel, camera_unplug, speech, mode, 14, f"cycle {i + 1} ({mode})", current)
        current = mode
    time.sleep(12)                                   # one more cadence of silence at the end
    settle(app, speech, "ten cycles")
    results = check_run(app, speech, 10, "ten cycles")
    firsts = [r["first"] for r in results]
    delays = [r["delay"] for r in results]
    print(f"first sentence after the loss: min {min(firsts):.2f} max {max(firsts):.2f} s; "
          f"'reconnected' after the pipeline ran: min {min(delays):.2f} median {statistics.median(delays):.2f} "
          f"max {max(delays):.2f} s")
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(900)
def test_t03c1_device_start_up_without_a_camera_then_two_cycles(camera_unplug, launch):
    """Criterion 5, second form: absent at start-up, back; then two more losses and returns; each gives both sentences once."""
    unplug_for(camera_unplug, None, 40)
    app, panel, speech = launch()
    waiting = app.wait_for(NO_CAMERA_AT_START, 60)
    assert waiting, f"the program did not start without a camera:\n{app.text()[-3000:]}"
    back = app.wait_for(CAMERA_BACK, 40 + RECOVERY_TIMEOUT, waiting[0])
    assert back, f"the pipeline never started:\n{app.text()[-4000:]}"
    wait_back_spoken(speech, waiting[1], 1)
    dropped = app.find(LOSS_SEEN, back[0] + 1) is not None
    assert app.wait_frames(back[0], 3, 30) or dropped, "no depth frames after the first start"
    current = "both"
    for i, mode in enumerate(["both", "depth"]):
        one_cycle(app, panel, camera_unplug, speech, mode, 14, f"cycle {i + 1} ({mode})", current)
        current = mode
    time.sleep(12)
    settle(app, speech, "start-up and two cycles")
    check_run(app, speech, 3, "start-up and two cycles", min_repeats={0: 3})
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(1500)
def test_t03c1_device_ten_bounces_gone_for_one_second(camera_unplug, launch):
    """
    "Fixed means repeated", the bounce case: the camera is detached for 1 s (the tool's timer is the
    replug), ten times in one run, in the modes both/depth/detection. A bounce either goes unnoticed (no
    "Camera lost" line: nothing is said) or is a loss: the missing sentence first (it is not suppressed
    because the camera is back quickly), then "reconnected" once, never doubled or reversed, and the
    sentence counts equal the program's own count of losses and returns.
    """
    app, panel, speech = launch()
    started(app)
    current, noticed, quiet_bounces = "both", 0, 0
    for i in range(10):
        mode = ("both", "depth", "detection")[i % 3]
        settle(app, speech, f"bounce {i + 1}")
        if mode != current:
            switch_mode(app, panel, mode)
            current = mode
        time.sleep(3)
        before, episodes_before = app.index(), len(build_episodes(app.events()))
        unplug_for(camera_unplug, app, 1, check_gone=False)
        wait_no_pending_timer(limit=15)
        seen = app.wait_for(LOSS_SEEN, 12, before)
        if seen is None:
            time.sleep(4)
            assert len(build_episodes(app.events())) == episodes_before, f"bounce {i + 1}: a loss with no loss line"
            quiet_bounces += 1
            print(f"bounce {i + 1} ({mode}): not noticed by the program, no loss, nothing said")
            continue
        noticed += 1
        back = app.wait_for(CAMERA_BACK, RECOVERY_TIMEOUT, seen[0])
        assert back, f"bounce {i + 1}: the pipeline never restarted:\n{app.text(seen[0])[-4000:]}"
        wait_back_spoken(speech, seen[1], 1)
        if mode in ("both", "depth"):
            frames = app.wait_frames(back[0], 3, 25)
            assert frames or app.find(LOSS_SEEN, back[0] + 1) is not None, f"bounce {i + 1}: no depth frames"
    time.sleep(12)
    settle(app, speech, "ten bounces")
    results = check_run(app, speech, noticed, "ten bounces")
    print(f"{noticed} of 10 bounces were noticed ({quiet_bounces} not); the program logged {len(results)} "
          f"episodes, and the sentence counts equal that number")
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(300)
def test_t03c1_device_a_camera_never_lost_says_neither_sentence_across_mode_switches(launch):
    """
    Criterion 6: a program that starts with a camera and never loses it, switched through every mode, says
    neither sentence. (If the camera drops on its own meanwhile, the program rightly says the missing
    sentence from that moment: then nothing may have been said BEFORE the first loss, and the episodes
    the drop caused are checked like any other. The test says so in its output.)
    """
    app, panel, speech = launch()
    started(app)
    for mode in ("depth", "detection", "none", "both", "detection", "depth", "both"):
        switch_mode(app, panel, mode)
    time.sleep(35)                                   # three cadences of 10 s
    assert app.alive()
    assert app.find(NO_CAMERA_AT_START) is None
    first_loss = next((t for t, kind in app.events() if kind in ("lost", "wait")), None)
    said = [u for u in speech.sentences() if first_loss is None or u["start"] < first_loss - 0.5]
    assert said == [], f"a camera sentence was said while the camera was there: {said}"
    heard = sorted({u["text"] for u in speech.utterances()})
    print(f"said in the whole run: {heard}")
    if first_loss is None:
        assert speech.sentences() == []
    else:
        print("NOTE: the camera dropped on its own during this run (no unplug in progress); the program's "
              "reaction to it is checked below")
        settle(app, speech, "never lost")
        check_run(app, speech, 0, "never lost")
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


class CameraHolder:
    """A gst-launch process that streams from a camera node and throws the frames away (UVC allows one client)."""

    def __init__(self, node: str):
        self.proc = subprocess.Popen(
            ["gst-launch-1.0", "-q", "v4l2src", f"device={node}", "!", "fakesink", "sync=false"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def output(self) -> str:
        """What it printed, once it has ended (an error line when it could not stream)."""
        try:
            return self.proc.stdout.read().decode("utf-8", errors="replace").strip()[:400] if not self.alive() else ""
        except (OSError, ValueError):
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def hold_camera_when_it_returns(node: str, timeout: float = 30.0, attempts: int = 6) -> tuple[CameraHolder | None, str]:
    """
    Start a CameraHolder the moment `node` exists again, and return it once it is streaming (still alive
    0.8 s after it started: a gst-launch that cannot open or stream the camera ends at once). A node that
    just appeared may not be usable for a moment, so a holder that ended is started again, `attempts`
    times at most. (None, why) if none stayed up: either the program streamed first or gst-launch could
    not run, and `why` has its last output.
    """
    deadline = time.monotonic() + timeout
    why = f"{node} did not come back within {timeout:.0f} s"
    tried = 0
    while time.monotonic() < deadline and tried < attempts:
        if not os.path.exists(node):
            time.sleep(0.02)
            continue
        holder = CameraHolder(node)
        tried += 1
        time.sleep(0.8)
        if holder.alive():
            return holder, ""
        why = f"holder attempt {tried} ended at once: {holder.output() or '(no output)'}"
        holder.stop()
    return None, why


@pytest.mark.timeout(400)
def test_t03c1_device_a_camera_found_but_held_by_another_process_keeps_the_alert_and_never_says_reconnected(
        camera_unplug, launch):
    """
    Criterion 4 (T-03b's case: the camera is back, its node is there, but it cannot be used): a process of
    this test starts streaming from the camera the moment its node returns, so the program's pipeline reaches
    PLAYING and the camera fails a few milliseconds later ("Device '/dev/video0' is busy": a camera loss on
    the bus), and the program tries again every few seconds. While the camera is held: the missing sentence
    goes on every 10 s from its own last utterance (not once per attempt), the program never gets a frame,
    and "reconnected" is not said. When the holder stops, vision runs and "reconnected" is said once.

    History: attempt 1 of this task skipped this case as 'inconclusive' because the program logged
    "Camera back" (its start was reported running); that line is printed even when the camera then fails,
    so the holder had won and the case was conclusive, and it showed the repeats restarting at every attempt
    (see dev-report-1.md). The skip is now only for a holder that could not stay up.
    """
    app, panel, speech = launch()
    started(app)
    before = app.index()
    unplug_for(camera_unplug, app, 6)
    seen = app.wait_for(LOSS_SEEN, 20, before)
    assert seen, f"no camera loss logged:\n{app.text(before)[-3000:]}"
    holder, why = hold_camera_when_it_returns(CAMERA_NODE)
    if holder is None:
        pytest.skip(f"inconclusive: the camera could not be held ({why})")
    try:
        held_idx = app.index()
        app.mark("the holder is streaming from the camera")
        time.sleep(28)                               # three cadences while the camera cannot be used
        assert holder.alive(), f"the holder ended during the hold: {holder.output()}"
        assert app.alive(), f"the program ended (exit {app.proc.returncode}):\n{app.text(seen[0])[-3000:]}"
        attempts = app.count("Camera found on", held_idx)
        failures = app.count(LOSS_SEEN, held_idx)
        busy = app.count("is busy", held_idx)
        frames = app.frames_after(held_idx)
        utts = speech.utterances()
        missing = [u for u in speech.sentences() if u["text"] == MISSING and u["start"] >= seen[1] - 0.5]
        gaps = [b["start"] - a["start"] for a, b in zip(missing, missing[1:])]
        print(f"while held (28 s): {attempts} starts on the camera, {failures} camera failures ({busy} 'is busy'), "
              f"{len(frames)} frames, {len(missing)} missing sentences (gaps {[round(g, 1) for g in gaps]}), "
              f"{speech.count(BACK)} reconnected")
        assert attempts >= 3, "the program did not keep trying the returned camera"
        assert failures >= 2 and not frames, "the camera was not held (the program got frames) or was not tried"
        assert not any(u["text"] == BACK for u in utts), "'reconnected' was said while the camera could not be used"
        assert len(missing) >= 3, f"the alert did not go on: {missing}"
        for a, b in zip(missing, missing[1:]):       # 10 s from the start of each, but for other speech in between
            extra = busy_seconds(utts, a["start"] + 10.0, b["start"], exclude=(a, b))
            assert b["start"] - a["start"] >= 9.0 and b["start"] - a["start"] - 10.0 <= extra + 1.0, \
                f"the missing sentence is not on its 10 s cadence while the program retries: gaps {gaps}"
    finally:
        holder.stop()
    freed = app.index()
    assert app.wait_frames(freed, 3, RECOVERY_TIMEOUT), \
        f"vision did not come back after the holder stopped:\n{app.text(freed)[-4000:]}"
    wait_back_spoken(speech, seen[1], 1)
    time.sleep(12)
    settle(app, speech, "held camera")
    check_run(app, speech, 1, "held camera", min_repeats={0: 3})
    code, took = app.stop(signal.SIGINT)
    assert code == 0 and took < STOP_BOUND_SECONDS, (code, took)
    assert app.leftovers() == []
    assert_clean(app)


@pytest.mark.timeout(900)
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_t03c1_device_stop_while_the_alert_is_raised_after_a_loss(camera_unplug, launch, signum):
    """
    Criterion 7: Ctrl+C (or SIGTERM, what the boot service sends) while the missing-camera alert is
    raised after a loss at run time. Five trials per signal, stopped 0.3 to 8 s after the first sentence
    began (inside the sentence, between sentences, with the teardown and the watcher at different
    points). Exit 0 each time, in the time T-03b measured (0.36 to 1.12 s) or close to it, nothing left.
    """
    offsets = [0.3, 1.0, 2.0, 4.0, 8.0]
    times = []
    for trial, offset in enumerate(offsets):
        app, panel, speech = launch()
        started(app)
        before = app.index()
        unplug_for(camera_unplug, app, 20)
        seen = app.wait_for(LOSS_SEEN, 20, before)
        assert seen, f"trial {trial}: no camera loss logged"
        limit = time.monotonic() + 8
        while speech.count(MISSING) < 1 and time.monotonic() < limit:
            time.sleep(0.02)
        first = [u for u in speech.sentences() if u["text"] == MISSING]
        assert first, f"trial {trial}: the alert was never said"
        time.sleep(max(0.0, first[0]["start"] + offset - time.monotonic()))
        speaking = speech.count(MISSING) >= 1
        code, took = app.stop(signum)
        times.append(took)
        print(f"{signal.Signals(signum).name} trial {trial + 1}: {offset:.1f} s after the first sentence "
              f"began (alert raised, speaking={speaking}): exit {code} in {took:.2f} s")
        assert code == 0, f"trial {trial}: exit code {code}\n{app.text()[-2500:]}"
        assert took < STOP_BOUND_SECONDS
        assert app.leftovers() == [], f"trial {trial}: left running: {app.leftovers()}"
        assert_clean(app)
        wait_no_pending_timer()
        assert camera_unplug.wait_for_nodes(True, 20)
        app.close()
    print(f"{signal.Signals(signum).name}: exit times {min(times):.2f} to {max(times):.2f} s over "
          f"{len(times)} trials (T-03b: {T03B_STOP_ENVELOPE[0]} to {T03B_STOP_ENVELOPE[1]} s)")


@pytest.mark.timeout(900)
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_t03c1_device_stop_while_the_alert_is_raised_from_start_up(camera_unplug, launch, signum):
    """Criterion 7, the start-up form: no camera at launch, the alert is raised from the first second; stopped 0.3 to 8 s after its first sentence."""
    offsets = [0.3, 1.0, 2.0, 4.0, 8.0]
    times = []
    for trial, offset in enumerate(offsets):
        unplug_for(camera_unplug, None, 22)
        app, panel, speech = launch()
        assert app.wait_for(NO_CAMERA_AT_START, 60), f"trial {trial}: not started without a camera"
        limit = time.monotonic() + 10
        while speech.count(MISSING) < 1 and time.monotonic() < limit:
            time.sleep(0.02)
        first = [u for u in speech.sentences() if u["text"] == MISSING]
        assert first, f"trial {trial}: the alert was never said"
        time.sleep(max(0.0, first[0]["start"] + offset - time.monotonic()))
        code, took = app.stop(signum)
        times.append(took)
        print(f"{signal.Signals(signum).name} trial {trial + 1}: {offset:.1f} s after the first sentence began: "
              f"exit {code} in {took:.2f} s")
        assert code == 0, f"trial {trial}: exit code {code}\n{app.text()[-2500:]}"
        assert took < STOP_BOUND_SECONDS
        assert app.leftovers() == [], f"trial {trial}: left running: {app.leftovers()}"
        assert_clean(app)
        app.close()
        wait_no_pending_timer()
        assert camera_unplug.wait_for_nodes(True, 20)
    print(f"{signal.Signals(signum).name}: exit times {min(times):.2f} to {max(times):.2f} s over "
          f"{len(times)} trials (T-03b: {T03B_STOP_ENVELOPE[0]} to {T03B_STOP_ENVELOPE[1]} s)")
