#!/usr/bin/env python3
"""Summarise one in-app run: frame rate and delay per branch, heat on the Pi and on the Hailo chip.

    python3 -I results/panel-revisions/session-a/app-runs/analyse_app_run.py <stem> [<stem> ...]

<stem> is perf_run.py's output path without extension (…/perf-<label>). Reads:
  <stem>-app.log          the app's console, every line prefixed with a Unix timestamp by perf_run.py
  <stem>.csv              perf_sample.py's 10 s samples (Pi temperature, clock, throttle flags, CPU, memory)
  <stem>-hailo-temp.csv   hailo_temp_sampler.py's 10 s samples of the chip's two sensors
Depth figures use perf_sample.py's own functions (the 18 Oct method). Detection figures apply the same
method to the "[DET] Frame N | lat X ms" lines the app prints with SV_LATENCY_LOG=1. As on 18 Oct, the
first 60 s are left out of every frame-rate and delay figure. Writes <stem>-analysis.txt and appends a row
to summary.csv beside the stems. Standard library only.
"""
import csv
import re
import statistics
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[4] / "docs" / "testing" / "tools"
sys.path.insert(0, str(TOOLS))
import perf_sample as ps  # noqa: E402

SKIP = 60.0
DET_FRAME = re.compile(r"^(?P<t>\d+\.\d+) \[DET\] Frame (?P<n>\d+) \| lat (?P<lat>[0-9.]+|n/a)")
HEF_LINE = re.compile(r"Using HEF from path: (\S+)")


def det_windows(lines):
    """Frame rate per window between consecutive [DET] lines (15 detection frames each), and delays."""
    pts = [(float(m["t"]), int(m["n"]), m["lat"]) for m in map(DET_FRAME.match, lines) if m]
    if not pts:
        return [], [], []
    t0 = pts[0][0]
    fps, gaps, lats = [], [], []
    for (ta, fa, _), (tb, fb, lat) in zip(pts, pts[1:]):
        if ta - t0 >= SKIP and tb > ta and fb > fa:
            fps.append((fb - fa) / (tb - ta))
            gaps.append(tb - ta)
            if lat != "n/a":
                lats.append(float(lat))
    return fps, gaps, lats


LOST = re.compile(r"^(?P<t>\d+\.\d+) .*Camera lost \(error")
BACK = re.compile(r"^(?P<t>\d+\.\d+) .*Camera back:")
TTS = re.compile(r"^\d+\.\d+ \[TTS\] ")
RAMP_S = 5.0   # after "Camera back", windows that start within this many seconds are also left out


def camera_losses(lines):
    """(start, end) of each camera loss: from "Camera lost (error ..." to the next "Camera back:"."""
    out, start = [], None
    for line in lines:
        if (m := LOST.match(line)) and start is None:
            start = float(m["t"])
        elif (m := BACK.match(line)) and start is not None:
            out.append((start, float(m["t"])))
            start = None
    if start is not None:
        out.append((start, float("inf")))
    return out


def _clear(ta, tb, losses):
    return all(not (ta < end + RAMP_S and tb > start) for start, end in losses)


def windows_excluding(lines, frame_re, losses):
    """Frame rate per 15-frame window (same method as above) for a frame line, leaving out every window
    that overlaps a camera loss or starts within RAMP_S of the camera coming back."""
    pts = [(float(m["t"]), int(m["n"])) for m in map(frame_re.match, lines) if m]
    if not pts:
        return [], []
    t0 = pts[0][0]
    fps, gaps = [], []
    for (ta, fa), (tb, fb) in zip(pts, pts[1:]):
        if ta - t0 >= SKIP and tb > ta and fb > fa and _clear(ta, tb, losses):
            fps.append((fb - fa) / (tb - ta))
            gaps.append(tb - ta)
    return fps, gaps


def lats_excluding(lines, line_re, losses):
    """Delay readings (ms) from timestamped lines, after SKIP, outside camera losses and their ramp."""
    pts = [(float(m["t"]), m["lat"]) for m in map(line_re.match, lines) if m and m["t"]]
    if not pts:
        return []
    t0 = pts[0][0]
    return [float(lat) for t, lat in pts if t - t0 >= SKIP and lat != "n/a" and _clear(t, t, losses)]


TREND_MIN_RUN_S = 900   # a "last 10 minutes" trend is only reported for runs of 15 minutes or more


def slope_per_10min(points):
    """Least-squares slope of (seconds, value) points, in units per 10 minutes. NaN when the points span
    less than 10 minutes: a slope fitted to a shorter stretch, scaled up to 10 minutes, says nothing."""
    # 570, not 600: samples come every 10 s, so the points inside a 600 s window can span up to 10 s less.
    if len(points) < 3 or points[-1][0] - points[0][0] < 570:
        return float("nan")
    xs, ys = zip(*points)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return 600 * sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else float("nan")


def fmt_trend(slope):
    return "n/a (run under 15 min)" if slope != slope else f"{slope:+.2f} C per 10 min"


def fps_summary(fps, gaps):
    if not fps:
        return "no windows", {}
    d = {"windows": len(fps), "median": statistics.median(fps), "p5": ps.pct(fps, 5), "p1": ps.pct(fps, 1),
         "min": min(fps), "stdev": statistics.pstdev(fps), "below_30": sum(f < 29.5 for f in fps),
         "below_25": sum(f < 25 for f in fps), "longest_s": max(gaps)}
    text = (f"{d['windows']} windows: median {d['median']:.1f}, p5 {d['p5']:.1f}, p1 {d['p1']:.1f}, "
            f"min {d['min']:.1f} FPS, stdev {d['stdev']:.2f}; windows under 29.5 FPS: {d['below_30']}, "
            f"under 25: {d['below_25']}; longest window {d['longest_s']:.2f} s (15 frames at 30 FPS take 0.50 s)")
    return text, d


def lat_summary(lats):
    if not lats:
        return "no readings", {}
    d = {"n": len(lats), "median": statistics.median(lats), "p95": ps.pct(lats, 95), "p99": ps.pct(lats, 99),
         "max": max(lats)}
    return (f"median {d['median']:.0f} ms, p95 {d['p95']:.0f}, p99 {d['p99']:.0f}, max {d['max']:.0f} "
            f"({d['n']} readings)"), d


def analyse(stem: Path) -> dict:
    lines = Path(f"{stem}-app.log").read_text(errors="replace").splitlines()
    hef = next((m.group(1) for m in map(HEF_LINE.search, lines) if m), "not found in the log")

    d_fps, d_gaps = ps.window_fps(lines, SKIP)
    d_lat = [lat for _, _, lat in ps.parse_app_log(lines, skip_seconds=SKIP)]
    t_fps, t_gaps, t_lat = det_windows(lines)
    losses = camera_losses(lines)
    down = sum(min(e, float(lines[-1].split(" ", 1)[0])) - st for st, e in losses)
    tts_count = sum(1 for line in lines if TTS.match(line))
    xd_fps, xd_gaps = windows_excluding(lines, ps.FRAME_LINE, losses)
    xt_fps, xt_gaps = windows_excluding(lines, DET_FRAME, losses)
    xd_lat = lats_excluding(lines, ps.DEPTH_LINE, losses)
    xt_lat = lats_excluding(lines, DET_FRAME, losses)
    last_avg = [m for m in map(ps.DEPTH_LINE.match, lines) if m]
    app_avg = (f"depth {last_avg[-1]['depth']} / det {last_avg[-1]['det']} FPS" if last_avg else "n/a")

    rows = list(csv.DictReader(open(f"{stem}.csv")))
    pi = [(float(r["elapsed_s"]), float(r["temp_c"])) for r in rows if r["temp_c"]]
    end = pi[-1][0] if pi else 0
    pi_last10 = [p for p in pi if p[0] >= end - 600] if end >= TREND_MIN_RUN_S else []
    flags = sorted({r["throttled"] for r in rows})
    below_max = sum(1 for r in rows if r["cpu_mhz"] and float(r["cpu_mhz"]) < float(r["cpu_max_mhz"]))
    cpu = [float(r["app_cpu_pct"]) for r in rows if r["app_cpu_pct"]]
    rss = [float(r["app_rss_mb"]) for r in rows if r["app_rss_mb"]]

    hrows = [r for r in csv.DictReader(open(f"{stem}-hailo-temp.csv")) if r["ts0_c"]]
    notes = [r["note"] for r in csv.DictReader(open(f"{stem}-hailo-temp.csv")) if r["note"]]
    hx = [(float(r["unix_time"]), max(float(r["ts0_c"]), float(r["ts1_c"]))) for r in hrows]
    h_end = hx[-1][0] if hx else 0
    h_last10 = ([(t - h_end, v) for t, v in hx if t >= h_end - 600]
                if hx and h_end - hx[0][0] >= TREND_MIN_RUN_S else [])

    dt, dd = fps_summary(d_fps, d_gaps)
    tt, td = fps_summary(t_fps, t_gaps)
    dl, dld = lat_summary(d_lat)
    tl, tld = lat_summary(t_lat)
    xtt, xtd = fps_summary(xt_fps, xt_gaps)
    xdt, xdd = fps_summary(xd_fps, xd_gaps)
    xtl, xtld = lat_summary(xt_lat)
    xdl, xdld = lat_summary(xd_lat)
    out = [f"run: {Path(stem).name}", f"detection model (from the app's log): {hef}",
           f"app's own averages at the last [DEPTH] line (since start, hide dips): {app_avg}",
           "",
           f"DETECTION branch, frame rate per 15 frames (after the first {SKIP:.0f} s): {tt}",
           f"DETECTION branch, camera-to-detection-callback delay: {tl}",
           f"DEPTH branch, frame rate per 15 frames (after the first {SKIP:.0f} s): {dt}",
           f"DEPTH branch, camera-to-depth-callback delay: {dl}",
           "",
           f"camera losses (\"Camera lost (error ...\" to \"Camera back:\"): {len(losses)}, {down:.1f} s without vision in total; speech lines spoken ([TTS]): {tts_count}",
           f"EXCLUDING windows that overlap a camera loss (or start within {RAMP_S:.0f} s of it ending):",
           f"  DETECTION frame rate: {xtt}",
           f"  DETECTION delay: {xtl}",
           f"  DEPTH frame rate: {xdt}",
           f"  DEPTH delay: {xdl}",
           ""]
    if pi:
        out.append(f"Pi SoC temperature (every 10 s): start {pi[0][1]:.1f}, max {max(v for _, v in pi):.1f}, "
                   f"end {pi[-1][1]:.1f} C; trend over the last 10 min {fmt_trend(slope_per_10min(pi_last10))}")
    out.append(f"Pi throttle flags seen: {', '.join(flags)}; CPU clock below its maximum in {below_max} of {len(rows)} samples")
    if cpu:
        out.append(f"app CPU: mean {statistics.fmean(cpu):.1f} % of one core (min {min(cpu):.1f}, max {max(cpu):.1f}); "
                   f"app memory {rss[0]:.0f} -> {rss[-1]:.0f} MB")
    if hx:
        out.append(f"Hailo-8 chip temperature (hotter of its 2 sensors, every 10 s): start {hx[0][1]:.1f}, "
                   f"max {max(v for _, v in hx):.1f}, end {hx[-1][1]:.1f} C; trend over the last 10 min "
                   f"{fmt_trend(slope_per_10min(h_last10))} ({len(hx)} readings)")
    out += [f"Hailo sampler notes: {n}" for n in notes]
    Path(f"{stem}-analysis.txt").write_text("\n".join(out) + "\n")
    print("\n".join(out) + "\n")

    return {"run": Path(stem).name, "hef": hef,
            **{f"det_fps_{k}": round(v, 3) for k, v in td.items()},
            **{f"det_lat_{k}": round(v, 1) for k, v in tld.items()},
            **{f"depth_fps_{k}": round(v, 3) for k, v in dd.items()},
            **{f"depth_lat_{k}": round(v, 1) for k, v in dld.items()},
            "pi_temp_start": pi[0][1] if pi else "", "pi_temp_max": max(v for _, v in pi) if pi else "",
            "pi_temp_end": pi[-1][1] if pi else "", "pi_temp_trend_last10": round(slope_per_10min(pi_last10), 2),
            "pi_throttle_flags": " ".join(flags), "cpu_below_max_samples": below_max,
            "app_cpu_mean": round(statistics.fmean(cpu), 1) if cpu else "",
            "hailo_temp_start": hx[0][1] if hx else "", "hailo_temp_max": max(v for _, v in hx) if hx else "",
            "hailo_temp_end": hx[-1][1] if hx else "",
            "hailo_temp_trend_last10": round(slope_per_10min(h_last10), 2),
            "camera_losses": len(losses), "camera_down_s": round(down, 1), "tts_lines": tts_count,
            **{f"x_det_fps_{k}": round(v, 3) for k, v in xtd.items()},
            **{f"x_det_lat_{k}": round(v, 1) for k, v in xtld.items()},
            **{f"x_depth_fps_{k}": round(v, 3) for k, v in xdd.items()},
            **{f"x_depth_lat_{k}": round(v, 1) for k, v in xdld.items()}}


def main() -> int:
    for arg in sys.argv[1:]:
        stem = Path(arg)
        row = analyse(stem)
        summary = stem.parent / "summary.csv"
        rows = list(csv.DictReader(open(summary))) if summary.exists() else []
        rows = [r for r in rows if r["run"] != row["run"]] + [row]
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(summary, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
