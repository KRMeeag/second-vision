#!/usr/bin/env python3
"""Side-by-side table of a B3 run against the 18 Oct run, from their saved files only.

    python3 -I results/panel-revisions/session-b/compare_b3.py <run folder> [<run folder> ...]

18 Oct: docs/testing/reports/T-15/evidence/perf-both-20min.{txt,csv} and -app.log (the 14-class model).
Each run folder: perf-<label>.txt/.csv/-app.log (perf_run.py + perf_sample.py), -analysis.txt
(analyse_app_run.py), latency-analysis.txt (analyse_latency.py). The figures are recomputed with
perf_sample.py's own functions for every column, the 18 Oct one included, so the method is identical.
Also lists every dip below 25 FPS (consecutive 15-frame windows) with the minute it started, for the
brief's question about the roughly 6-minute dips. Writes b3-comparison.md beside this script.
"""
import csv
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WT = HERE.parents[2]
sys.path.insert(0, str(WT / "docs" / "testing" / "tools"))
import perf_sample as ps  # noqa: E402

OCT18 = WT / "docs" / "testing" / "reports" / "T-15" / "evidence" / "perf-both-20min"


def figures(stem: Path) -> dict:
    lines = Path(f"{stem}-app.log").read_text(errors="replace").splitlines()
    rows = list(csv.DictReader(open(f"{stem}.csv")))
    frames = [(float(m["t"]), int(m["n"])) for m in map(ps.FRAME_LINE.match, lines) if m]
    t_first, t_last = frames[0][0], frames[-1][0]
    win, gaps = ps.window_fps(lines, 60.0)
    lat = [l for _, _, l in ps.parse_app_log(lines, skip_seconds=60)]
    cpu = [float(r["app_cpu_pct"]) for r in rows if r["app_cpu_pct"]]
    core = [float(r["core_max_pct"]) for r in rows if r["core_max_pct"]]
    temp = [float(r["temp_c"]) for r in rows if r["temp_c"]]
    rss = [(float(r["elapsed_s"]), float(r["app_rss_mb"])) for r in rows if r["app_rss_mb"]]
    m1 = [v for t, v in rss if 50 <= t <= 70] or [rss[0][1]]
    txt = Path(f"{stem}.txt").read_text(errors="replace")
    flags = sorted({r["throttled"] for r in rows})
    vc = re.findall(r"temp=([\d.]+)'C", Path(f"{stem}-system.txt").read_text() + txt)

    # dips: runs of consecutive windows under 25 FPS, after the first minute
    pts = [(t, n) for t, n in frames if t - t_first >= 60]
    dips, cur = [], None
    for (ta, fa), (tb, fb) in zip(pts, pts[1:]):
        if tb <= ta or fb <= fa:
            continue
        low = (fb - fa) / (tb - ta) < 25
        if low and cur is None:
            cur = [ta, tb]
        elif low:
            cur[1] = tb
        elif cur is not None:
            dips.append(cur)
            cur = None
    if cur is not None:
        dips.append(cur)
    merged = []                     # dips under 30 s apart are one episode
    for a, b in dips:
        if merged and a - merged[-1][1] < 30:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    dips = merged

    first3 = [float(r["app_cpu_pct"]) for r in rows if r["app_cpu_pct"] and 60 < float(r["elapsed_s"]) <= 240]
    end = float(rows[-1]["elapsed_s"])
    last3 = [float(r["app_cpu_pct"]) for r in rows if r["app_cpu_pct"] and float(r["elapsed_s"]) > end - 180]
    return {
        "avg_fps": (frames[-1][1] - frames[0][1]) / (t_last - t_first),
        "frames": frames[-1][1] - frames[0][1], "secs": t_last - t_first,
        "win": (statistics.median(win), ps.pct(win, 5), ps.pct(win, 1), min(win), len(win)),
        "lat": (statistics.median(lat), ps.pct(lat, 95), ps.pct(lat, 99), max(lat), len(lat)),
        "cpu": statistics.fmean(cpu), "core": (statistics.fmean(core), max(core)),
        "mem": (statistics.median(m1), rss[-1][1]),
        "temp": (temp[0], min(temp), max(temp)), "flags": flags, "vc": vc,
        "cpu3": (statistics.fmean(first3) if first3 else float("nan"), statistics.fmean(last3)),
        "dips": [((a - t_first) / 60, b - a) for a, b in dips],
    }


def row_text(f):
    w, l = f["win"], f["lat"]
    mem0, mem1 = f["mem"]
    return {
        "Average FPS (depth frames / seconds, whole sampled run)": f"{f['avg_fps']:.1f} ({f['frames']} frames in {f['secs']:.0f} s)",
        "15-frame windows after minute 1: median / p5 / p1 / min": f"{w[0]:.1f} / {w[1]:.1f} / {w[2]:.1f} / {w[3]:.1f} ({w[4]} windows)",
        "Capture → depth callback: median / p95 / p99 / max (ms)": f"{l[0]:.0f} / {l[1]:.0f} / {l[2]:.0f} / {l[3]:.0f} ({l[4]} lines)",
        "App CPU, average (% of one core)": f"{f['cpu']:.0f} %",
        "Busiest core, average / max": f"{f['core'][0]:.0f} % / {f['core'][1]:.0f} %",
        "App memory, minute 1 → end": f"{mem0:.0f} → {mem1:.0f} MB ({100 * (mem1 - mem0) / mem0:+.1f} %)",
        "App CPU, first 3 min after minute 1 / last 3 min": f"{f['cpu3'][0]:.0f} % / {f['cpu3'][1]:.0f} %",
        "SoC temperature first sample / min / max (every 10 s)": f"{f['temp'][0]:.1f} / {f['temp'][1]:.1f} / {f['temp'][2]:.1f} °C",
        "vcgencmd measure_temp before / after": " / ".join(f"{v} °C" for v in f["vc"][:2]) or "not recorded",
        "Throttle flags (every 10 s)": ", ".join(f["flags"]),
        "Dip episodes below 25 FPS (start minute, length; dips <30 s apart merged)": "; ".join(f"{m:.1f} min, {s:.1f} s" for m, s in f["dips"]) or "none",
    }


def main() -> int:
    runs = [Path(a) for a in sys.argv[1:]]
    cols = [("18 Oct (14-class)", row_text(figures(OCT18)))]
    for r in runs:
        stem = next(r.glob("perf-*-app.log")).with_name(next(r.glob("perf-*-app.log")).name[:-len("-app.log")])
        cols.append((r.name, row_text(figures(stem))))
    out = ["| Measure | " + " | ".join(c for c, _ in cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for key in cols[0][1]:
        out.append(f"| {key} | " + " | ".join(c[key] for _, c in cols) + " |")
    text = "\n".join(out) + "\n"
    (HERE / "b3-comparison.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
