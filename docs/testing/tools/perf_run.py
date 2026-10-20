#!/usr/bin/env python3
"""Run the app the way the boot service does and measure it: one command, one evidence set.

    my_hailo_env/bin/python docs/testing/tools/perf_run.py --duration 1200 --label both-20min
    my_hailo_env/bin/python docs/testing/tools/perf_run.py --duration 1200 --label npu-20min --npu
    my_hailo_env/bin/python docs/testing/tools/perf_run.py --duration 36000 --stop-at-volts 3.5 --wait-for-battery --label rundown

The last is the battery rundown (REQ-PERF-01). It refuses to start while the charger is in (the HAT's power-loss
line, GPIO6), or with --wait-for-battery waits for you to unplug it and starts at once. It ends after two readings in
a row at 3.5 V or under (the gauge's percentage drifts; the vendor script's own shutdown is 3.20 V) or after
--duration, stops the app and writes the same evidence set with the battery and the power source in it. The Pi
STAYS ON afterwards (nothing here shuts it down): plug the charger in, or shut it down yourself.
Add --real-boards to include what the real boards draw from the Pi (and to let the panel set the mode).

What it does, in order:
  1. Checks the device is free: the boot service stopped, no other copy of the app, a camera present.
  2. Writes <stem>-system.txt: kernel, HailoRT version, the files changed and not committed, who holds
     /dev/hailo0, temperature and throttle flags before the run.
  3. Starts scripts/sv-main.sh exactly as the service does (same arguments, headless: no DISPLAY, so
     no preview window), console output saved with a timestamp per line to <stem>-app.log.
  4. When the first [DEPTH] line appears, starts perf_sample.py for --duration seconds.
  5. Takes a screenshot of the Pi's desktop half-way and at the end (grim; needs the desktop running).
  6. Stops the app with Ctrl+C (SIGINT), as the service does, and records how long it took to exit
     and its exit code: a clean stop is part of the result.
  7. Draws <stem>.png with perf_plot.py.

With --npu, `hailortcli monitor` is sampled every 10 s beside the app (perf_npu.py; raw captures,
a CSV and a summary). This only works when hailort_service itself has HAILO_MONITOR=1: the app is its
client and the setting on the app does nothing. .claude/tools/queued_npu_run.sh arranges that for one run.

sudo, read-only, passwordless: `sudo -n vcgencmd get_throttled` and `measure_temp`, and
`sudo -n fuser -v /dev/hailo0` (lists holders; never `-k`: hailort_service holds the chip by design).
Everything is written under --out (default docs/testing/reports/T-15/evidence/).
"""
from __future__ import annotations

import argparse
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
import tty
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
SHOT_ENV = {**os.environ, "XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0"}


def sh(cmd, **kw) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 20), **kw)
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(failed: {exc})"


ACK = bytes([0xAA, 0xFF, 0x01])
PACKET_LENGTHS = {0x01: 6, 0xFE: 3, 0x04: 5}      # motor update, heartbeat, hazard alert


class FakeGlasses:
    """A stand-in for the glasses ESP32 on a pseudo-terminal: reads every packet and ACKs it.

    The real board's motors would buzz for the whole run whenever the camera sees something
    close. The program's serial worker behaves the same either way; only the motors stay quiet.
    """

    def __init__(self):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.count = 0
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        buf = b""
        while not self._stop.is_set():
            if not select.select([self.master], [], [], 0.1)[0]:
                continue
            try:
                buf += os.read(self.master, 4096)
            except OSError:
                time.sleep(0.05)
                continue
            while buf:
                i = buf.find(b"\xAA")
                if i < 0 or len(buf[i:]) < 2:
                    buf = buf[i:] if i >= 0 else b""
                    break
                buf = buf[i:]
                n = PACKET_LENGTHS.get(buf[1])
                if n is None:
                    buf = buf[1:]
                    continue
                if len(buf) < n:
                    break
                buf = buf[n:]
                self.count += 1
                try:
                    os.write(self.master, ACK)
                except OSError:
                    pass

    def close(self):
        self._stop.set()


def service_monitor_setting() -> str:
    """HAILO_MONITOR as the running hailort_service sees it (read-only: `sudo -n cat` of its environment)."""
    pid = sh(["systemctl", "show", "hailort.service", "-p", "MainPID", "--value"])
    env = sh(["sudo", "-n", "cat", f"/proc/{pid}/environ"]) if pid.isdigit() else ""
    found = [v for v in env.split("\0") if v.startswith("HAILO_MONITOR=")]
    return found[0] if found else "HAILO_MONITOR unreadable"


def screenshot(path: Path) -> str:
    try:
        r = subprocess.run(["grim", str(path)], env=SHOT_ENV, capture_output=True, text=True, timeout=30)
        return "ok" if r.returncode == 0 else f"failed: {r.stderr.strip()}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--duration", type=float, default=1200)
    ap.add_argument("--label", default="both")
    ap.add_argument("--out", default=str(REPO / "docs/testing/reports/T-15/evidence"))
    ap.add_argument("--npu", action="store_true", help="sample the NPU with hailortcli monitor (needs HAILO_MONITOR=1 in hailort_service)")
    ap.add_argument("--real-boards", action="store_true",
                    help="use the real glasses board and control panel (default: stand-ins, so the "
                         "motors stay quiet and a panel switch left off cannot turn detection or depth off)")
    ap.add_argument("--stop-at-battery", type=float, metavar="PCT",
                    help="end the run once the gauge's percentage is at or under PCT (it drifts: prefer --stop-at-volts)")
    ap.add_argument("--stop-at-volts", type=float, metavar="V",
                    help="end the run after two readings in a row at or under V cell volts (the rundown; try 3.5)")
    ap.add_argument("--wait-for-battery", action="store_true",
                    help="a rundown: wait (up to 2 h) for the charger to be unplugged, then start at once")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"perf-{args.label}"
    py = sys.executable

    # 1. device free?
    problems = []
    if sh(["systemctl", "is-active", "second-vision.service"]) == "active":
        problems.append("second-vision.service is running: sudo systemctl stop second-vision")
    other = [l for l in sh(["pgrep", "-af", "second_vision/main.py"]).splitlines() if "pgrep" not in l]
    if other:
        problems.append("another copy of the app is running: " + other[0][:100])
    if not Path("/dev/video0").exists():
        problems.append("no /dev/video0: no camera attached")
    rundown = args.stop_at_battery is not None or args.stop_at_volts is not None
    if rundown and not args.wait_for_battery and _ac_power() == "1":
        problems.append("the charger is connected: a battery run says nothing then. Unplug it, or add --wait-for-battery")
    if args.wait_for_battery and not rundown:
        problems.append("--wait-for-battery needs --stop-at-volts (or --stop-at-battery): it is for a rundown")
    if problems:
        print("NOT STARTED:\n  " + "\n  ".join(problems))
        return 1

    if args.wait_for_battery and _ac_power() != "0":
        print("Waiting for the charger to be unplugged (the HAT's power-loss line), up to 2 hours; nothing is running.",
              flush=True)
        if not wait_for_battery():
            print("NOT STARTED: the charger was never unplugged within 2 hours")
            return 1
        print(f"{time.strftime('%H:%M:%S')} the charger is out: on battery, starting.", flush=True)

    # 2. system facts
    dirty = sh(["git", "-C", str(REPO), "status", "--short", "--", "src", "scripts"])
    facts = [f"perf_run {args.label}",
             f"kernel: {sh(['uname', '-srm'])}", f"HailoRT: {sh(['hailortcli', '--version']).splitlines()[0]}",
             "code: the development tree; files changed in src/scripts and not committed:",
             dirty or "  (none)",
             f"model file: {sh(['ls', '-l', str(det_model_file())])}",
             f"holders of /dev/hailo0 (sudo -n fuser -v, read-only): {sh(['sudo', '-n', 'fuser', '-v', '/dev/hailo0'])}",
             f"before: {sh(['sudo', '-n', 'vcgencmd', 'measure_temp'])}, "
             f"{sh(['sudo', '-n', 'vcgencmd', 'get_throttled'])} (0x0 = never throttled since boot)",
             f"run: {args.duration:g} s, headless like the boot service, NPU monitor sampling "
             f"{'on' if args.npu else 'off'}; hailort_service has "
             f"{service_monitor_setting()}; "
             f"boards: "
             f"{'real glasses and panel' if args.real_boards else 'stand-in glasses on a pty (ACKs every packet), no control panel, so mode both'}",
             f"battery at the start (X1202 gauge): {_battery_note()}"
             + (f"; the run ends at {args.stop_at_volts:g} V" if args.stop_at_volts is not None else "")
             + (f"; and at {args.stop_at_battery:g} % by the gauge" if args.stop_at_battery is not None else "")]
    Path(f"{stem}-system.txt").write_text("\n".join(facts) + "\n")
    print("\n".join(facts), flush=True)

    # 3. the app, as the service starts it
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["PYTHONUNBUFFERED"] = "1"
    glasses = None
    if not args.real_boards:
        glasses = FakeGlasses()
        env["SERIAL_PORT"] = glasses.path
        env["CONFIG_PORT"] = "/nonexistent-control-panel"      # sv-main.sh then runs without a panel: mode both
    log = open(f"{stem}-app.log", "w", buffering=1)
    started = threading.Event()

    app = subprocess.Popen([str(REPO / "scripts/sv-main.sh")], cwd=REPO, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, start_new_session=True)

    def pump():
        for line in app.stdout:
            log.write(f"{time.time():.3f} {line}")
            if "[DEPTH] Frame" in line:
                started.set()
    threading.Thread(target=pump, daemon=True).start()

    if not started.wait(120):
        print("NOT MEASURED: no [DEPTH] line in 120 s; see", f"{stem}-app.log")
        os.killpg(app.pid, signal.SIGINT)
        return 1
    print(f"app running (pid {app.pid}); sampling for {args.duration:g} s", flush=True)

    # 4 + 5. sampler, with screenshots
    battery_stop = (["--stop-at-battery", str(args.stop_at_battery)] if args.stop_at_battery is not None else []) \
        + (["--stop-at-volts", str(args.stop_at_volts)] if args.stop_at_volts is not None else [])
    sampler = subprocess.Popen([py, str(TOOLS / "perf_sample.py"), "--duration", str(args.duration), "--live",
                                "--sudo-vcgencmd", "--label", args.label, "--out", str(out),
                                "--app-log", f"{stem}-app.log", *battery_stop], cwd=REPO)
    sampled_from = time.monotonic()
    shots = {}
    npu_thread = None
    if args.npu:
        import perf_npu
        npu_thread = threading.Thread(target=perf_npu.sample_loop, args=(stem, time.monotonic(), args.duration),
                                      daemon=True)
        npu_thread.start()
    # Half-way, unless the sampler ends first (a rundown stops at its battery threshold, not at --duration):
    # then the app is stopped at once instead of running on for the rest of the half.
    try:
        sampler.wait(timeout=args.duration / 2)
    except subprocess.TimeoutExpired:
        shots["mid"] = screenshot(Path(f"{stem}-screen-mid.png"))
        sampler.wait()
    sampled_min = (time.monotonic() - sampled_from) / 60
    if npu_thread:
        npu_thread.join(timeout=30)

    # 6. clean stop, as the service does
    t0 = time.monotonic()
    os.killpg(app.pid, signal.SIGINT)
    try:
        code = app.wait(20)
        stop = f"exited {code} {time.monotonic() - t0:.1f} s after Ctrl+C"
    except subprocess.TimeoutExpired:
        os.killpg(app.pid, signal.SIGKILL)
        stop = "did NOT exit within 20 s of Ctrl+C: killed"
    time.sleep(1)
    log.close()
    shots["end"] = screenshot(Path(f"{stem}-screen-end.png"))
    packets = f"stand-in glasses received {glasses.count} packets\n" if glasses else ""
    if glasses:
        glasses.close()
    after = (f"{packets}after: {sh(['sudo', '-n', 'vcgencmd', 'measure_temp'])}, "
             f"{sh(['sudo', '-n', 'vcgencmd', 'get_throttled'])}")
    with open(f"{stem}.txt", "a") as fh:
        fh.write(f"\nstop: {stop}\n{after}\nscreenshots: {shots}\n")
    print(f"\nstop: {stop}\n{after}\nscreenshots: {shots}")

    if args.npu:
        import perf_npu
        npu_lines = perf_npu.summary(Path(f"{stem}-npu.csv"))
        with open(f"{stem}.txt", "a") as fh:
            fh.write("\n" + "\n".join(npu_lines) + "\n")
        print("\n".join(npu_lines))

    # 7. chart
    subprocess.run([py, str(TOOLS / "perf_plot.py"), str(stem), "--title",
                    f"Second Vision, detection + depth, {sampled_min:.0f} min"])
    return 0


def wait_for_battery(read=None, sleep=time.sleep, clock=time.monotonic, limit: float = 2 * 3600, every: float = 2.0) -> bool:
    """
    Wait until the HAT's power-loss line says the charger is out ("0"). True when it is, False after `limit` seconds
    with the charger still in. A line that cannot be read ("") is not "out": the run would then measure nothing.
    """
    read = read or _ac_power
    deadline = clock() + limit
    while read() != "0":
        if clock() > deadline:
            return False
        sleep(every)
    return True


def det_model_file() -> Path:
    """The detection HEF the program uses by default: app.py's DET_MODEL_VERSION (read from its text, since importing
    app needs GStreamer). Falls back to the old path when the constant is not found, so a missing file shows in the
    evidence instead of the tool failing."""
    m = re.search(r'^DET_MODEL_VERSION = "([^"]+)"$',
                  (REPO / "src/second_vision/pipeline/app.py").read_text(), re.M)
    if not m:
        return REPO / "models/hef_deploy/best.hef"
    return REPO / "models" / m.group(1) / "hef_deploy" / "best.hef"


def _battery_note() -> str:
    """The charge and voltage now, from perf_sample's gauge reader; read-only."""
    import perf_sample
    charge, volts = perf_sample.battery()
    source = {"1": "the charger is IN", "0": "on battery", "": "power source unreadable"}[perf_sample.ac_power()]
    return "unreadable" if charge == "" else f"{charge:.1f} % by the gauge, {volts:.3f} V, {source}"


def _ac_power() -> str:
    """"1" with the charger in, "0" on battery, "" unknown (perf_sample's reader of the HAT's power-loss line)."""
    import perf_sample
    return perf_sample.ac_power()


if __name__ == "__main__":
    sys.exit(main())
