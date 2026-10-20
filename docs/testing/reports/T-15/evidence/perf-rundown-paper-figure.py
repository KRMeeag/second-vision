"""The paper's figure for the battery rundown: cell voltage over time (with the stop, the camera drops and the idle
hand reads after it), frame rate per 15 frames, and SoC temperature. Reuses perf_plot's drawing; changes no tool."""
import csv, re, sys
from pathlib import Path
import cv2, numpy as np

REPO = Path(sys.argv[1]); sys.path.insert(0, str(REPO / "docs/testing/tools"))
import perf_plot as pp

ev = REPO / "docs/testing/reports/T-15/evidence"
rows = list(csv.DictReader(open(ev / "perf-rundown.csv")))
t = [float(r["elapsed_s"]) for r in rows]
volts = [(a, float(r["battery_v"])) for a, r in zip(t, rows)]
temp = [(a, float(r["temp_c"])) for a, r in zip(t, rows)]
frames, t0 = [], None
for line in open(ev / "perf-rundown-app.log", errors="replace"):
    m = pp.APP_LINE.match(line)
    if m:
        frames.append((float(m["t"]), int(m["f"])))
# the frame counter restarts when vision restarts (the camera drop): skip windows that cross a restart
t0 = frames[0][0]
fps = [(b[0] - t0, (b[1] - a[1]) / (b[0] - a[0])) for a, b in zip(frames, frames[1:]) if b[1] > a[1] and b[0] > a[0]]

XMAX = 4.7 * 3600
W, H = 1500, 1000
img = np.full((H, W, 3), pp.BG, np.uint8)
cv2.putText(img, "Battery rundown: detection + depth at full load, on battery (2026-10-19 21:09 to 2026-10-20 00:47)",
            (40, 38), pp.FONT, 0.8, pp.FG, 2, cv2.LINE_AA)
cv2.putText(img, "3.64 h from 4.05 V to the 3.5 V stop; the program then stopped, the Pi stayed on at idle",
            (40, 66), pp.FONT, 0.55, (90, 90, 90), 1, cv2.LINE_AA)
box = lambda k: (110, 130 + k * 300, W - 95, 130 + k * 300 + 220)

# 1. voltage
b0 = box(0); x0, y0, x1, y1 = b0; lo, hi = 3.3, 4.2
pp.panel(img, b0, "Cell voltage (X1202 fuel gauge, read every 10 s)", [("under full load", volts, pp.COLORS[0])], XMAX, "volts", (lo, hi))
X = lambda s: int(x0 + (x1 - x0) * s / XMAX); Y = lambda v: int(y1 - (y1 - y0) * (v - lo) / (hi - lo))
cv2.line(img, (x0, Y(3.5)), (x1, Y(3.5)), (30, 30, 200), 1, cv2.LINE_AA)
cv2.putText(img, "3.5 V: the stop I chose (the HAT cuts at about 3.2 V)", (x0 + 8, Y(3.5) + 18), pp.FONT, 0.5, (30, 30, 200), 1, cv2.LINE_AA)
for minute, label in ((90.9, "camera drops twice"), (187.5, "cover and flash")):
    xx = X(minute * 60); cv2.line(img, (xx, y0), (xx, y1), (150, 150, 150), 1, cv2.LINE_AA)
    cv2.putText(img, label, (xx + 5, y0 + 34), pp.FONT, 0.5, (90, 90, 90), 1, cv2.LINE_AA)
xs = X(13101.5); cv2.line(img, (xs, y0), (xs, y1), (30, 30, 200), 1, cv2.LINE_AA)
cv2.putText(img, "program stopped 00:47", (xs - 215, y0 + 78), pp.FONT, 0.5, (30, 30, 200), 1, cv2.LINE_AA)
start = 21 * 3600 + 9 * 60 + 4                         # 21:09:04, the app's first line; the clock is read from it
idle = [("01:26", 3.461), ("01:28", 3.454), ("01:36", 3.434), ("01:43", 3.430)]
for hhmm, v in idle:
    h, m = map(int, hhmm.split(":")); s = (h + 24) * 3600 + m * 60 - start
    cv2.circle(img, (X(s), Y(v)), 6, (30, 140, 40), 2, cv2.LINE_AA)
cv2.putText(img, "idle, hand reads", (X(4.1 * 3600), Y(3.54)), pp.FONT, 0.5, (30, 140, 40), 1, cv2.LINE_AA)

# 2. frame rate
pp.panel(img, box(1), "Frame rate, each 15 frames (windows across a vision restart left out)",
         [("per 15 frames", [p for p in fps if p[1] < 40], pp.COLORS[1])], XMAX, "frames per second", (0, 40))
# 3. temperature
pp.panel(img, box(2), "SoC temperature (the Pi throttles at about 80 C; the flags stayed 0x0 throughout)",
         [("temperature C", temp, pp.COLORS[2])], XMAX, "C", (50, 90))
out = ev / "perf-rundown-paper-figure.png"
cv2.imwrite(str(out), img); print("written", out)
