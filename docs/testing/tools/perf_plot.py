#!/usr/bin/env python3
"""Draw a perf_sample run as one PNG, for a report or a slide.

    my_hailo_env/bin/python docs/testing/tools/perf_plot.py <stem> [--title TEXT]

<stem> is the path perf_sample.py wrote without its extension, e.g.
docs/testing/reports/T-15/evidence/2026-10-18-perf-both-20min. Reads <stem>.csv and, if present,
<stem>.txt (for the header) and <stem>-app.log (timestamped console lines from perf_run.py).
Writes <stem>.png. Uses only cv2 and numpy (already in my_hailo_env), no matplotlib.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import cv2
import numpy as np

W, H0 = 1500, 1800
BG, FG, GRID = (255, 255, 255), (40, 40, 40), (225, 225, 225)
COLORS = [(200, 80, 30), (30, 140, 40), (30, 30, 200), (150, 60, 150)]
FONT = cv2.FONT_HERSHEY_SIMPLEX
APP_LINE = re.compile(r"^(?P<t>\d+\.\d+) \[DEPTH\] Frame (?P<f>\d+) .*depth (?P<d>[0-9.]+) FPS / det (?P<n>[0-9.]+) FPS \| lat (?P<l>[0-9.]+) ms")


def nice(lo: float, hi: float) -> tuple[float, float]:
    """A tidy axis range that contains [lo, hi]."""
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def panel(img, box, title, series, xmax, ylabel, ylim=None):
    """Draw one line chart. series: [(label, [(x_seconds, y)], colour)]."""
    x0, y0, x1, y1 = box
    cv2.rectangle(img, (x0, y0), (x1, y1), FG, 1)
    cv2.putText(img, title, (x0, y0 - 12), FONT, 0.7, FG, 2, cv2.LINE_AA)
    pts = [p for _, s, _ in series for p in s]
    if not pts:
        cv2.putText(img, "no data", (x0 + 20, (y0 + y1) // 2), FONT, 0.7, (120, 120, 120), 1, cv2.LINE_AA)
        return
    lo, hi = ylim or nice(min(p[1] for p in pts), max(p[1] for p in pts))
    for i in range(6):
        v = lo + (hi - lo) * i / 5
        y = int(y1 - (y1 - y0) * i / 5)
        cv2.line(img, (x0, y), (x1, y), GRID, 1)
        cv2.putText(img, f"{v:.0f}" if hi - lo > 8 else f"{v:.1f}", (x0 - 62, y + 5), FONT, 0.5, FG, 1, cv2.LINE_AA)
    for i in range(7):
        t = xmax * i / 6
        x = int(x0 + (x1 - x0) * i / 6)
        cv2.line(img, (x, y1), (x, y1 + 5), FG, 1)
        cv2.putText(img, f"{t / 60:.0f} min", (x - 22, y1 + 22), FONT, 0.5, FG, 1, cv2.LINE_AA)
    cv2.putText(img, ylabel, (x0 + 8, y0 + 20), FONT, 0.5, (90, 90, 90), 1, cv2.LINE_AA)
    for k, (label, s, colour) in enumerate(series):
        xy = np.array([[x0 + (x1 - x0) * t / xmax, y1 - (y1 - y0) * (v - lo) / (hi - lo)] for t, v in s], np.int32)
        if len(xy) > 1:
            cv2.polylines(img, [xy], False, colour, 2, cv2.LINE_AA)
    lx = x1 - 20 - sum(60 + 9 * len(label) for label, _, _ in series)       # legend on the title line
    for label, _, colour in series:
        cv2.line(img, (lx, y0 - 17), (lx + 26, y0 - 17), colour, 4)
        cv2.putText(img, label, (lx + 34, y0 - 12), FONT, 0.5, FG, 1, cv2.LINE_AA)
        lx += 60 + 9 * len(label)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    stem = Path(sys.argv[1])
    title = sys.argv[sys.argv.index("--title") + 1] if "--title" in sys.argv else stem.name
    rows = list(csv.DictReader(open(f"{stem}.csv")))
    n = len(rows)
    step = 10.0
    if Path(f"{stem}.txt").exists():
        m = re.search(r"samples: \d+ every ([0-9.]+) s", Path(f"{stem}.txt").read_text())
        step = float(m.group(1)) if m else step
    t = [i * step for i in range(n)]

    def col(name, scale=1.0):
        return [(t[i], float(r[name]) * scale) for i, r in enumerate(rows) if r[name] not in ("", None)]

    frames = []
    app_log = Path(f"{stem}-app.log")
    if app_log.exists():
        for line in app_log.read_text(errors="replace").splitlines():
            m = APP_LINE.match(line)
            if m:
                frames.append((float(m["t"]), float(m["d"]), float(m["n"]), float(m["l"]), int(m["f"])))
    t0 = frames[0][0] if frames else 0.0
    window = [(b[0] - t0, (b[4] - a[4]) / (b[0] - a[0])) for a, b in zip(frames, frames[1:])
              if b[0] > a[0] and b[4] > a[4]]
    xmax = max(t[-1] if t else 1, frames[-1][0] - t0 if frames else 1)

    npu = []
    if Path(f"{stem}-npu.csv").exists():
        for r in csv.DictReader(open(f"{stem}-npu.csv")):
            if r["device_util_pct"] != "":
                npu.append((float(r["elapsed_s"]), float(r["device_util_pct"])))
    charge = col("battery_pct") if rows and "battery_pct" in rows[0] else []      # runs before 2026-10-19 have none
    H = H0 + (280 if npu else 0) + (280 if charge else 0)
    img = np.full((H, W, 3), BG, np.uint8)
    cv2.putText(img, title, (40, 38), FONT, 1.0, FG, 2, cv2.LINE_AA)
    head = [l for l in Path(f"{stem}.txt").read_text().splitlines() if l.startswith("samples:")][:1] \
        if Path(f"{stem}.txt").exists() else []
    cv2.putText(img, " ".join(head).split(";")[0][:140], (40, 66), FONT, 0.5, (90, 90, 90), 1, cv2.LINE_AA)
    box = lambda k: (110, 120 + k * 280, W - 40, 120 + k * 280 + 200)  # noqa: E731
    top = float(rows[0]["cpu_max_mhz"] or 2400)
    panel(img, box(0), "SoC temperature (the Pi throttles at about 80 C)", [("temperature C", col("temp_c"), COLORS[2])],
          xmax, "C", (50, 90))
    panel(img, box(1), "CPU clock (maximum %.0f MHz)" % top, [("CPU clock MHz", col("cpu_mhz"), COLORS[0])],
          xmax, "MHz", (1400, top + 100))
    panel(img, box(2), "CPU load", [("busiest core %", col("core_max_pct"), COLORS[0]),
          ("app, % of one core", col("app_cpu_pct"), COLORS[1])], xmax, "%", (0, 100))
    panel(img, box(3), "Memory used by the app", [("app resident MB", col("app_rss_mb"), COLORS[1])], xmax, "MB")
    panel(img, box(4), "Frame rate: each 15 frames (the app's own average since start in green)",
          [("per 15 frames", window, COLORS[0]), ("app's average (depth)", [(a - t0, d) for a, d, _, _, _ in frames], COLORS[1])],
          xmax, "frames per second", (0, 40))
    panel(img, box(5), "Camera-to-callback delay", [("ms", [(a - t0, l) for a, _, _, l, _ in frames], COLORS[2])],
          xmax, "milliseconds", (0, 200))
    if npu:
        panel(img, box(6), "NPU (Hailo-8) utilisation, from hailortcli monitor",
              [("NPU %", npu, COLORS[3])], xmax, "%", (0, 100))
    if charge:
        panel(img, box(7 if npu else 6), "Battery charge (X1202 gauge; a rising line means the charger was in)",
              [("charge %", charge, COLORS[1])], xmax, "%", (0, 100))
    cv2.imwrite(f"{stem}.png", img)
    print(f"written: {stem}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
