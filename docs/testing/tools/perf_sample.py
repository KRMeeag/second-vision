#!/usr/bin/env python3
"""Sample the Pi's load, memory, temperature and throttling while the app runs.

    python3 docs/testing/tools/perf_sample.py --duration 600 --label stability \\
        --match Parallel-Depth-Detection --app-log /tmp/app.log
    python3 docs/testing/tools/perf_sample.py --self-check

Start it, then start the app (or the other way round). It never touches the camera, the Hailo chip
or the app: it only reads /proc, /sys and, optionally, a log file the app writes. Safe to run
beside anything.

Every --interval seconds (10 by default) it records:
  - temperature (/sys/class/thermal), CPU clock against its maximum (a clock below the maximum
    under load means throttling), the undervoltage alarm and the fan speed (hwmon);
  - per-core CPU load and the system's memory use (psutil);
  - the app's own CPU and resident memory, summed over its child processes (psutil);
  - `vcgencmd get_throttled`, with --sudo-vcgencmd (it needs root on this Pi; the only sudo used).
With --app-log it also reads the app's own "[DEPTH] ... depth X FPS / det Y FPS | lat Z ms" lines
(callbacks.py), which is where the frame rate and camera-to-callback delay already come from.

The battery: the X1202 UPS HAT's gauge (I2C bus 1, address 0x36, the registers its vendor script
~/x120x/bat.py reads) gives the charge in % and the cell voltage; two register reads per sample, no root.
Whether the charger is in comes from the HAT's own power-loss line (GPIO6, read as an input, the way its vendor
script ~/x120x/pld-trixie.py does): `ac_power` is 1 with the adapter in, 0 on battery. The summary says how many
samples were on battery; a run with the charger in says nothing about battery life. The gauge's PERCENTAGE drifts
(2026-10-19: 85 % at a full-cell 4.185 V), and the vendor script shuts down on VOLTAGE (3.20 V), so the rundown stops
on voltage: --stop-at-volts V ends the sampling after two readings in a row at or under V, while the Pi still has
power (the cut-off is near 3.2 V, and nothing here shuts the Pi down cleanly). --stop-at-battery PCT does the same on
the percentage, for what it is worth.

It writes <out>/perf-<label>.csv and a .txt summary. The summary applies the stability
rule of REQ-DTA-09 (docs/testing/REQUIREMENTS.md) when the run is long enough: ignoring the first
minute, memory at the end at most 5 % above memory at minute 1, and average CPU over the last 3
minutes within 10 percentage points of the first 3.

Not covered here: NPU load. perf_npu.py samples it beside perf_run.py --npu; it needs HAILO_MONITOR=1 on
hailort_service, which owns the chip (docs/testing/reports/T-15/measurement-2.md).
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    sys.exit("psutil is missing. Use my_hailo_env/bin/python (it has psutil 7.0.0).")

THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")
CPUFREQ = Path("/sys/devices/system/cpu/cpu0/cpufreq")
DEPTH_LINE = re.compile(r"^(?:(?P<t>\d+\.\d+) )?.*?depth (?P<depth>[0-9.]+) FPS / det (?P<det>[0-9.]+) FPS \| lat (?P<lat>[0-9.]+) ms")


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def hwmon(name: str, leaf: str) -> str | None:
    """Value of `leaf` in the hwmon device called `name`, or None."""
    for d in Path("/sys/class/hwmon").glob("hwmon*"):
        if _read(d / "name") == name:
            return _read(d / leaf)
    return None


APP_NAMES = ("second_vision/main.py", "Parallel-Depth-Detection")      # the app renames its own process


def find_app(match: str) -> psutil.Process | None:
    """The newest non-shell process whose command line or name contains `match`.

    The app renames itself (Parallel-Depth-Detection-V4), so its command line no longer says
    main.py: both the command line and the name are searched. This script, its parents (the shell
    that started it) and anything mentioning perf_sample are left out, so the match text in our own
    command line is never mistaken for the app.
    """
    skip = {psutil.Process().pid, *(p.pid for p in psutil.Process().parents())}
    found = []
    for p in psutil.process_iter(["pid", "cmdline", "name", "create_time"]):
        cmd = p.info["cmdline"] or []
        if (p.info["pid"] in skip or not cmd or "perf_sample" in " ".join(cmd)
                or Path(cmd[0]).name in ("bash", "sh", "zsh", "dash", "timeout")):
            continue
        if match in " ".join(cmd) or match in (p.info["name"] or ""):
            found.append(p)
    return max(found, key=lambda p: p.info["create_time"]) if found else None


def tree(proc: psutil.Process) -> list[psutil.Process]:
    """The process and all its children."""
    try:
        return [proc, *proc.children(recursive=True)]
    except psutil.Error:
        return []


GAUGE_BUS, GAUGE_ADDR = 1, 0x36          # the X1202's battery gauge
GAUGE_VCELL, GAUGE_SOC = 0x02, 0x04      # cell voltage (1.25 mV / 16 per step), charge (1/256 % per step)


def gauge_word(raw: int) -> int:
    """A gauge register as a number: the gauge sends the high byte first, SMBus reads the low byte first."""
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


def battery() -> tuple:
    """(charge %, cell volts) from the X1202's gauge, or ("", "") when it cannot be read. Read-only."""
    try:
        import smbus2
        with smbus2.SMBus(GAUGE_BUS) as bus:
            volts = gauge_word(bus.read_word_data(GAUGE_ADDR, GAUGE_VCELL)) * 1.25 / 1000 / 16
            charge = gauge_word(bus.read_word_data(GAUGE_ADDR, GAUGE_SOC)) / 256
        return round(charge, 2), round(volts, 3)
    except Exception:                       # no smbus2, no HAT, a bus error: the run goes on without it
        return "", ""


def ac_power() -> str:
    """The HAT's power-loss line: "1" with the adapter in, "0" on battery, "" when it cannot be read. Read-only."""
    try:
        import gpiod
        with gpiod.request_lines("/dev/gpiochip0", consumer="sv-perf-sample",
                                 config={6: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)}) as req:
            return "1" if req.get_values()[0] == gpiod.line.Value.ACTIVE else "0"
    except Exception:                       # no gpiod, no HAT, the line busy: the run goes on without it
        return ""


def battery_stop(row: dict, threshold, volts=None) -> bool:
    """
    True when this sample is at or under --stop-at-battery (percent) or --stop-at-volts (cell voltage). An unread
    value never stops. A sample with the charger in never stops either: the cell voltage is high then.
    """
    if row.get("ac_power") == "1":
        return False
    if threshold is not None and row.get("battery_pct", "") != "" and row["battery_pct"] <= threshold:
        return True
    return volts is not None and row.get("battery_v", "") != "" and row["battery_v"] <= volts


def battery_lines(rows: list[dict]) -> list[str]:
    """The battery part of the summary: the charge and voltage over the run, and the drain rate."""
    pts = [(r["elapsed_s"], r["battery_pct"], r["battery_v"]) for r in rows if r.get("battery_pct", "") != ""]
    if not pts:
        return ["battery (X1202 gauge): no readings"]
    (t0, p0, v0), (t1, p1, v1) = pts[0], pts[-1]
    hours = (t1 - t0) / 3600
    out = [f"battery (X1202 gauge): {p0:.1f} % -> {p1:.1f} %, {v0:.3f} V -> {v1:.3f} V over {hours * 60:.0f} min "
           f"({len(pts)} readings; the percentage drifts, the voltage is the measure)"]
    source = [r.get("ac_power", "") for r in rows if r.get("ac_power", "") in ("0", "1")]
    if source:
        on_battery = source.count("0")
        out.append(f"power source (the HAT's power-loss line): on battery in {on_battery} of {len(source)} samples"
                   + ("" if on_battery == len(source) else
                      "; THE CHARGER WAS IN for the rest: this is not a clean battery run"))
        if on_battery == 0:
            return out + ["battery: on the charger throughout: this run says nothing about battery life"]
    if not source and p1 > p0 + 0.5:
        out.append("battery: the charge ROSE, so the charger was connected: this run says nothing about battery life")
    elif p0 - p1 >= 1.0 and hours > 0:
        rate = (p0 - p1) / hours
        out.append(f"battery drain by the gauge's percentage: {rate:.1f} % per hour; at that rate 100 % to 5 % takes about "
                   f"{95 / rate:.1f} h (rough: the percentage drifts and a Li-ion cell does not drain evenly; a rundown to "
                   "the stop voltage is the measurement)")
    else:
        out.append("battery drain: under 1 % over the run, too little to estimate a rate from")
    return out


def throttled_flags(use_sudo: bool = False) -> str:
    """`vcgencmd get_throttled`, which this Pi only allows for root (`sudo -n`, read-only)."""
    cmd = (["sudo", "-n"] if use_sudo else []) + ["vcgencmd", "get_throttled"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip().split("=")[-1] if out.returncode == 0 and out.stdout else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def parse_app_log(lines, skip_seconds: float = 0.0) -> list[tuple[float, float, float]]:
    """(depth FPS, det FPS, latency ms) for each [DEPTH] line that carries all three.

    Lines written by perf_run.py start with a Unix timestamp; with `skip_seconds` the first
    seconds after the first line (the frame rate is still ramping up) are left out.
    """
    out, t0 = [], None
    for line in lines:
        m = DEPTH_LINE.match(line)
        if not m:
            continue
        if m["t"]:
            t0 = t0 if t0 is not None else float(m["t"])
            if float(m["t"]) - t0 < skip_seconds:
                continue
        out.append((float(m["depth"]), float(m["det"]), float(m["lat"])))
    return out


FRAME_LINE = re.compile(r"^(?P<t>\d+\.\d+) \[DEPTH\] Frame (?P<n>\d+) ")


def window_fps(lines, skip_seconds: float = 60.0) -> tuple[list[float], list[float]]:
    """Frame rate per window between consecutive timestamped [DEPTH] lines, and the window lengths.

    The app's own "depth X FPS" is the average since the branch started, so it hides short dips.
    Each [DEPTH] line is printed every 15 frames; 15 frames over the seconds between two lines is the
    rate in that window. Needs the timestamps perf_run.py adds to every line.
    """
    pts = [(float(m["t"]), int(m["n"])) for m in map(FRAME_LINE.match, lines) if m]
    if not pts:
        return [], []
    fps, gaps = [], []
    for (ta, fa), (tb, fb) in zip(pts, pts[1:]):
        if ta - pts[0][0] >= skip_seconds and tb > ta and fb > fa:
            fps.append((fb - fa) / (tb - ta))
            gaps.append(tb - ta)
    return fps, gaps


def pct(values: list[float], p: float) -> float:
    """The p-th percentile (0 to 100) by nearest rank."""
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))]


def stability(rows: list[dict], interval: float) -> list[str]:
    """REQ-DTA-09: judged over the app's samples; needs more than 7 minutes of them."""
    app = [r for r in rows if r["app_rss_mb"] != ""]
    if len(app) * interval < 7 * 60:
        return ["stability (REQ-DTA-09): not judged, the run is shorter than 7 minutes"]
    skip = int(60 / interval)
    body = app[skip:]
    block = int(180 / interval)
    mem0, mem1 = body[0]["app_rss_mb"], body[-1]["app_rss_mb"]
    cpu0 = statistics.mean(r["app_cpu_pct"] for r in body[:block])
    cpu1 = statistics.mean(r["app_cpu_pct"] for r in body[-block:])
    mem_ok = mem1 <= mem0 * 1.05
    cpu_ok = abs(cpu1 - cpu0) <= 10
    return [f"stability (REQ-DTA-09): memory {mem0:.0f} -> {mem1:.0f} MB ({(mem1 / mem0 - 1) * 100:+.1f} %, "
            f"limit +5 %): {'PASS' if mem_ok else 'FAIL'}",
            f"stability (REQ-DTA-09): app CPU first 3 min {cpu0:.0f} %, last 3 min {cpu1:.0f} % "
            f"(limit 10 points apart): {'PASS' if cpu_ok else 'FAIL'}"]


def summary(rows: list[dict], frames: list[tuple[float, float, float]], interval: float,
            windows: tuple[list[float], list[float]] | None = None) -> list[str]:
    """Plain lines describing the run; `frames` are the app log's steady-state lines."""
    def line(label, key, unit):
        vals = [r[key] for r in rows if r[key] != ""]
        return (f"{label}: min {min(vals):.1f}, mean {statistics.mean(vals):.1f}, max {max(vals):.1f} {unit}"
                if vals else f"{label}: no data")
    out = [line("temperature", "temp_c", "C"), line("CPU clock", "cpu_mhz", "MHz"),
           line("system memory used", "mem_used_mb", "MB"), line("app CPU", "app_cpu_pct", "% of one core"),
           line("app resident memory", "app_rss_mb", "MB"), line("busiest core", "core_max_pct", "%")]
    dips = [r for r in rows if r["cpu_mhz"] != "" and r["cpu_mhz"] < r["cpu_max_mhz"] * 0.98]
    if dips:
        out.append(f"CPU clock below its maximum in {len(dips)} of {len(rows)} samples (lowest "
                   f"{min(r['cpu_mhz'] for r in dips):.0f} MHz, at {min(r['temp_c'] for r in dips):.0f} to "
                   f"{max(r['temp_c'] for r in dips):.0f} C): short dips; thermal throttling is judged by the flags below")
    else:
        out.append("CPU clock stayed at its maximum in every sample")
    flags = sorted({r["throttled"] for r in rows})
    out.append(f"throttle flags seen (vcgencmd get_throttled): {', '.join(flags)} "
               "(0x0 means no under-voltage, frequency cap or throttling now, and none since boot)")
    out.append(f"undervoltage alarm raised in {sum(1 for r in rows if r['undervolt'] == '1')} samples")
    if frames:
        d, t, lat = zip(*frames)
        out += [f"depth FPS, the app's own counter (an average since start, so it hides dips): median "
                f"{statistics.median(d):.1f}, min {min(d):.1f}",
                f"detection FPS, the app's own counter (same): median {statistics.median(t):.1f}, min {min(t):.1f}",
                f"camera-to-callback delay: median {statistics.median(lat):.0f} ms, "
                f"p95 {pct(list(lat), 95):.0f} ms, p99 {pct(list(lat), 99):.0f} ms, max {max(lat):.0f} ms "
                f"({len(lat)} lines)"]
    else:
        out.append("FPS and delay: no app log lines read (pass --app-log)")
    if windows and windows[0]:
        w, g = windows
        out.append(f"frame rate per 15-frame window (after the first minute, {len(w)} windows): median "
                   f"{statistics.median(w):.1f}, 5th percentile {pct(w, 5):.1f}, 1st percentile {pct(w, 1):.1f}, "
                   f"min {min(w):.1f} FPS; longest window {max(g):.2f} s (15 frames take 0.50 s at 30 FPS); "
                   f"windows over 1 s: {sum(1 for x in g if x > 1.0)}")
    return out + battery_lines(rows) + stability(rows, interval)


def sample(proc: psutil.Process | None, started: float, use_sudo: bool = False) -> dict:
    cores = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    temp = _read(THERMAL)
    cur, top = _read(CPUFREQ / "scaling_cur_freq"), _read(CPUFREQ / "cpuinfo_max_freq")
    row = {"elapsed_s": round(time.monotonic() - started, 1),
           "temp_c": int(temp) / 1000 if temp else "",
           "cpu_mhz": int(cur) / 1000 if cur else "", "cpu_max_mhz": int(top) / 1000 if top else "",
           "undervolt": hwmon("rpi_volt", "in0_lcrit_alarm") or "",
           "fan_rpm": hwmon("pwmfan", "fan1_input") or "",
           "core_max_pct": max(cores), "cores_pct": " ".join(f"{c:.0f}" for c in cores),
           "mem_used_mb": (mem.total - mem.available) / 2**20,
           "app_cpu_pct": "", "app_rss_mb": "", "throttled": throttled_flags(use_sudo)}
    row["battery_pct"], row["battery_v"] = battery()
    row["ac_power"] = ac_power()
    if proc is not None:
        procs, cpu, rss = tree(proc), 0.0, 0.0
        for p in procs:
            try:
                cpu += p.cpu_percent(None)
                rss += p.memory_info().rss
            except psutil.Error:
                pass
        if procs:
            row["app_cpu_pct"], row["app_rss_mb"] = cpu, rss / 2**20
    return row


def self_check() -> int:
    log = ["[DEPTH] Frame 15 | L=1 | depth 28.9 FPS / det 29.5 FPS | lat 112 ms", "noise",
           "[DEPTH] Frame 30 | depth 30.0 FPS / det 0.0 FPS | lat 98 ms"]
    got = parse_app_log(log)
    assert got == [(28.9, 29.5, 112.0), (30.0, 0.0, 98.0)], got
    wl = [f"{100 + i * 0.5:.3f} [DEPTH] Frame {15 * (i + 1)} | x" for i in range(5)]
    wl[4] = "102.500 [DEPTH] Frame 75 | x"
    f, g = window_fps(wl, 0.0)
    assert [round(x) for x in f] == [30, 30, 30, 15] and max(g) == 1.0, (f, g)
    stamped = ["100.0 [DEPTH] depth 10.0 FPS / det 10.0 FPS | lat 90 ms",
               "170.5 [DEPTH] depth 29.0 FPS / det 29.0 FPS | lat 100 ms"]
    assert parse_app_log(stamped, skip_seconds=60) == [(29.0, 29.0, 100.0)]
    assert pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10 and pct([5], 95) == 5
    rows = [{"elapsed_s": 0.0, "temp_c": 50.0, "cpu_mhz": 2400.0, "cpu_max_mhz": 2400.0, "undervolt": "0",
             "fan_rpm": "", "core_max_pct": 40.0, "cores_pct": "", "mem_used_mb": 900.0,
             "app_cpu_pct": 100.0, "app_rss_mb": 500.0 + i * 0.01, "throttled": "unavailable"} for i in range(80)]
    text = "\n".join(stability(rows, 10.0))
    assert "PASS" in text and "FAIL" not in text, text
    rows[-1]["app_rss_mb"] = 900.0
    assert "FAIL" in "\n".join(stability(rows, 10.0))
    assert "not judged" in stability(rows[:10], 10.0)[0]
    assert gauge_word(0x00D1) == 0xD100 and abs(gauge_word(0x00D1) * 1.25 / 1000 / 16 - 4.18) < 1e-9
    assert round(gauge_word(0x3A63) / 256, 1) == 99.2                      # the high byte is the whole percent
    drain = [{"elapsed_s": i * 10.0, "battery_pct": 90.0 - i * 10 / 360, "battery_v": 4.0} for i in range(361)]
    text = "\n".join(battery_lines(drain))
    assert "90.0 % -> 80.0 %" in text and "10.0 % per hour" in text and "about 9.5 h" in text, text
    assert "ROSE" in "\n".join(battery_lines([dict(drain[0], battery_pct=80.0), dict(drain[-1], battery_pct=90.0)]))
    assert "too little" in "\n".join(battery_lines(drain[:3]))
    assert battery_lines([dict(drain[0], battery_pct="")]) == ["battery (X1202 gauge): no readings"]
    assert battery_stop({"battery_pct": 5.0}, 5) and not battery_stop({"battery_pct": 5.1}, 5)
    assert not battery_stop({"battery_pct": ""}, 5) and not battery_stop({"battery_pct": 1.0}, None)
    assert battery_stop({"battery_v": 3.45, "ac_power": "0"}, None, 3.5) and not battery_stop({"battery_v": 3.55}, None, 3.5)
    assert battery_stop({"battery_v": 3.5}, None, 3.5), "at the threshold counts"
    assert not battery_stop({"battery_v": 3.4, "ac_power": "1"}, None, 3.5), "never stop on the charger"
    assert not battery_stop({"battery_v": ""}, None, 3.5) and not battery_stop({"battery_v": 3.0}, None, None)
    mixed = [dict(r, ac_power="1" if i < 100 else "0") for i, r in enumerate(drain)]
    assert "on battery in 261 of 361" in "\n".join(battery_lines(mixed)) and "CHARGER WAS IN" in "\n".join(battery_lines(mixed))
    clean = [dict(r, ac_power="0") for r in drain]
    assert "on battery in 361 of 361" in "\n".join(battery_lines(clean)) and "CHARGER" not in "\n".join(battery_lines(clean))
    plugged = [dict(r, ac_power="1") for r in drain]
    assert "says nothing about battery life" in "\n".join(battery_lines(plugged))
    print("self-check ok")
    return 0


def resummarize(stem: Path, interval: float) -> int:
    """Rewrite STEM.txt's summary from STEM.csv (and STEM-app.log), keeping its header and stop lines."""
    NUM = ("elapsed_s", "temp_c", "cpu_mhz", "cpu_max_mhz", "core_max_pct", "mem_used_mb", "app_cpu_pct",
           "app_rss_mb", "battery_pct", "battery_v")
    rows = []
    for r in csv.DictReader(open(f"{stem}.csv")):
        rows.append({k: (float(v) if k in NUM and v != "" else v) for k, v in r.items()})
    old = Path(f"{stem}.txt").read_text().splitlines()
    head = old[:old.index("")] if "" in old else old[:7]
    tail = [l for l in old if l.startswith(("stop:", "stand-in", "after:", "screenshots:"))]
    log = Path(f"{stem}-app.log")
    text = log.read_text(errors="replace").splitlines() if log.exists() else []
    frames, windows = parse_app_log(text, skip_seconds=60), window_fps(text)
    m = re.search(r"samples: \d+ every ([0-9.]+) s", "\n".join(head))
    lines = head + [""] + summary(rows, frames, float(m[1]) if m else interval, windows) + ([""] + tail if tail else [])
    Path(f"{stem}.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--duration", type=float, default=600, help="seconds to sample (default 600)")
    ap.add_argument("--interval", type=float, default=10, help="seconds between samples (default 10)")
    ap.add_argument("--match", default="Parallel-Depth-Detection", help="text in the app's process name or command line")
    ap.add_argument("--app-log", help="file the app's console output is written to")
    ap.add_argument("--label", default="run", help="short name for the output files")
    ap.add_argument("--out", default=None, help="folder for the outputs (default: ./perf-logs)")
    ap.add_argument("--from-csv", metavar="STEM", help="rebuild STEM.txt's summary from STEM.csv and exit")
    ap.add_argument("--live", action="store_true", help="print each sample as it is taken")
    ap.add_argument("--sudo-vcgencmd", action="store_true",
                    help="read `vcgencmd get_throttled` with `sudo -n` (read-only; needs passwordless sudo)")
    ap.add_argument("--stop-at-battery", type=float, metavar="PCT",
                    help="end the sampling once the gauge's percentage is at or under PCT (it drifts: prefer --stop-at-volts)")
    ap.add_argument("--stop-at-volts", type=float, metavar="V",
                    help="end the sampling after two readings in a row at or under V cell volts, on battery (a rundown "
                         "that stops in time; the vendor script's own shutdown is 3.20 V)")
    ap.add_argument("--self-check", action="store_true", help="test the script's own parsing and exit")
    args = ap.parse_args()
    if args.self_check:
        return self_check()

    if args.from_csv:
        return resummarize(Path(args.from_csv), args.interval)
    out = Path(args.out or "perf-logs")
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"perf-{args.label}"
    proc = None
    rows: list[dict] = []
    psutil.cpu_percent(percpu=True)                       # first call only primes the counters
    run_start = time.monotonic()
    t_end = run_start + args.duration
    ended, low_streak = "", 0
    try:
        while time.monotonic() < t_end:
            if proc is None or not proc.is_running():
                proc = find_app(args.match)
                if proc is not None:
                    for p in tree(proc):
                        p.cpu_percent(None)               # prime
            time.sleep(min(args.interval, max(0.0, t_end - time.monotonic())))
            row = sample(proc if proc and proc.is_running() else None, run_start, args.sudo_vcgencmd)
            rows.append(row)
            if args.live:
                print(f"{row['elapsed_s']:6.0f} s  {row['temp_c']:5.1f} C  {row['cpu_mhz']:6.0f}/{row['cpu_max_mhz']:.0f} MHz  "
                      f"cores {row['cores_pct']:<15}  mem {row['mem_used_mb']:6.0f} MB  "
                      f"app cpu {row['app_cpu_pct'] if row['app_cpu_pct'] == '' else format(row['app_cpu_pct'], '5.0f')}%  "
                      f"app rss {row['app_rss_mb'] if row['app_rss_mb'] == '' else format(row['app_rss_mb'], '5.0f')} MB  "
                      f"throttled {row['throttled']}  "
                      f"battery {row['battery_pct'] if row['battery_pct'] == '' else format(row['battery_pct'], '5.1f')} % "
                      f"{row['battery_v'] if row['battery_v'] == '' else format(row['battery_v'], '.3f')} V "
                      f"{ {'1': 'CHARGER IN', '0': 'on battery', '': '?'}[row['ac_power']] }",
                      flush=True)
            low_streak = low_streak + 1 if battery_stop(row, args.stop_at_battery, args.stop_at_volts) else 0
            if low_streak >= 2:                         # two in a row: one noisy reading must not end a run
                ended = (f"ended at the battery threshold: {row['battery_v']:.3f} V ({row['battery_pct']:.1f} % by the "
                         f"gauge) after {row['elapsed_s'] / 3600:.2f} h on this load "
                         f"(--stop-at-volts {args.stop_at_volts} --stop-at-battery {args.stop_at_battery})")
                print(ended, flush=True)
                break
    except KeyboardInterrupt:
        print("stopped early; writing what was sampled")

    frames, windows = [], None
    if args.app_log and Path(args.app_log).exists():
        lines = Path(args.app_log).read_text(errors="replace").splitlines()
        frames = parse_app_log(lines, skip_seconds=60)
        windows = window_fps(lines)
    if rows:
        with open(f"{stem}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    env = _read(Path(f"/proc/{proc.pid}/environ")) if proc else None
    lines = [f"perf_sample {args.label}", f"command: {' '.join(sys.argv)}",
             f"samples: {len(rows)} every {args.interval:g} s; app found: {'yes' if proc else 'NO'}",
             f"HAILO_MONITOR set in the app: {'yes' if env and 'HAILO_MONITOR=1' in env else 'no / unreadable'}",
             f"vcgencmd get_throttled: {rows[-1]['throttled'] if rows else 'n/a'}"]
    lines += [ended, ""] if ended else [""]
    lines += summary(rows, frames, args.interval, windows) if rows else ["no samples"]
    Path(f"{stem}.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwritten: {stem}.csv, {stem}.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
