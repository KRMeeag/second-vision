"""Figure for the paper: the cell voltage during the battery rundown, and nothing else.

    my_hailo_env/bin/python docs/testing/reports/T-15/evidence/perf-rundown-voltage-figure.py

Reads perf-rundown.csv (the run only: 21:09:04 to 00:47:29 on 2026-10-19/20, on battery in every sample) and writes
perf-rundown-voltage-figure.png and .svg beside it. No plotting library is installed here, so it draws with cv2 and
writes the SVG by hand from the same scales; the SVG is the one to put in a paper (it stays sharp at any size).
"""
import csv
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
rows = list(csv.DictReader(open(HERE / "perf-rundown.csv")))
hours = [float(r["elapsed_s"]) / 3600 for r in rows]
volts = [float(r["battery_v"]) for r in rows]
STOP_V = 3.5
X_MAX, Y_MIN, Y_MAX = 3.75, 3.40, 4.15           # the axes: a little past the end of the run, tidy volt marks

W, H = 2000, 1150
ML, MR, MT, MB = 190, 90, 150, 170                 # margins: left, right, top, bottom
PX0, PX1, PY0, PY1 = ML, W - MR, MT, H - MB        # the plot box
sx = lambda h: PX0 + (PX1 - PX0) * h / X_MAX
sy = lambda v: PY1 - (PY1 - PY0) * (v - Y_MIN) / (Y_MAX - Y_MIN)

BLUE, RED, GREY, FG, GRID = (190, 85, 30), (40, 40, 200), (110, 110, 110), (35, 35, 35), (226, 226, 226)   # BGR
FONT = cv2.FONT_HERSHEY_SIMPLEX
y_ticks = [round(3.4 + 0.1 * i, 1) for i in range(8)]          # 3.4 .. 4.1
x_ticks = [0.5 * i for i in range(8)]                           # 0 .. 3.5 hours
TITLE = "Cell voltage during the battery rundown"
SUB = "Detection and depth at full load, on battery throughout (Raspberry Pi 5 + Hailo-8, X1202 UPS hat)"
YLAB, XLAB = "Cell voltage (V)", "Time on battery (hours)"
END = (hours[-1], volts[-1]); START = (hours[0], volts[0])
STOP_NOTE = f"stop threshold {STOP_V} V (two readings in a row)"

# ---------------------------------------------------------------- PNG
img = np.full((H, W, 3), 255, np.uint8)
def text(s, xy, scale, colour=FG, thick=2, centre=False, right=False):
    (tw, th), _ = cv2.getTextSize(s, FONT, scale, thick)
    x, y = xy
    if centre: x -= tw // 2
    if right: x -= tw
    cv2.putText(img, s, (int(x), int(y)), FONT, scale, colour, thick, cv2.LINE_AA)

text(TITLE, (ML, 70), 1.35, thick=3)
text(SUB, (ML, 112), 0.78, GREY, 2)
for v in y_ticks:
    cv2.line(img, (PX0, int(sy(v))), (PX1, int(sy(v))), GRID, 2)
    text(f"{v:.1f}", (PX0 - 16, sy(v) + 10), 0.9, right=True)
for h in x_ticks:
    cv2.line(img, (int(sx(h)), PY0), (int(sx(h)), PY1), GRID, 2)
    text(f"{h:g}", (sx(h), PY1 + 44), 0.9, centre=True)
cv2.rectangle(img, (PX0, PY0), (PX1, PY1), FG, 2)
text(XLAB, ((PX0 + PX1) / 2, H - 50), 1.0, centre=True)
# the y label, rotated
lab = np.full((60, 420, 3), 255, np.uint8)
cv2.putText(lab, YLAB, (6, 42), FONT, 1.0, FG, 2, cv2.LINE_AA)
rot = cv2.rotate(lab, cv2.ROTATE_90_COUNTERCLOCKWISE)
yy = int((PY0 + PY1) / 2 - rot.shape[0] / 2)
img[yy:yy + rot.shape[0], 40:40 + rot.shape[1]] = np.minimum(img[yy:yy + rot.shape[0], 40:40 + rot.shape[1]], rot)

cv2.line(img, (PX0, int(sy(STOP_V))), (PX1, int(sy(STOP_V))), RED, 3, cv2.LINE_AA)
text(STOP_NOTE, (PX1 - 14, sy(STOP_V) + 36), 0.85, RED, 2, right=True)
pts = np.array([[sx(h), sy(v)] for h, v in zip(hours, volts)], np.int32)
cv2.polylines(img, [pts], False, BLUE, 4, cv2.LINE_AA)
for (h, v), side in ((START, "start"), (END, "end")):
    cv2.circle(img, (int(sx(h)), int(sy(v))), 11, BLUE, -1, cv2.LINE_AA)
text(f"start: {START[1]:.2f} V", (sx(START[0]) + 22, sy(START[1]) - 22), 0.95)
text(f"end: {END[1]:.2f} V after {END[0]:.2f} h", (sx(END[0]) + 10, sy(END[1]) + 92), 0.95, right=True)
cv2.imwrite(str(HERE / "perf-rundown-voltage-figure.png"), img)

# ---------------------------------------------------------------- SVG (the same scales)
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif" fill="#232323">',
       f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
       f'<text x="{ML}" y="70" font-size="52" font-weight="bold">{esc(TITLE)}</text>',
       f'<text x="{ML}" y="112" font-size="30" fill="#6e6e6e">{esc(SUB)}</text>']
for v in y_ticks:
    svg.append(f'<line x1="{PX0}" y1="{sy(v):.1f}" x2="{PX1}" y2="{sy(v):.1f}" stroke="#e2e2e2" stroke-width="2"/>')
    svg.append(f'<text x="{PX0 - 16}" y="{sy(v) + 11:.1f}" font-size="34" text-anchor="end">{v:.1f}</text>')
for h in x_ticks:
    svg.append(f'<line x1="{sx(h):.1f}" y1="{PY0}" x2="{sx(h):.1f}" y2="{PY1}" stroke="#e2e2e2" stroke-width="2"/>')
    svg.append(f'<text x="{sx(h):.1f}" y="{PY1 + 48}" font-size="34" text-anchor="middle">{h:g}</text>')
svg += [f'<rect x="{PX0}" y="{PY0}" width="{PX1 - PX0}" height="{PY1 - PY0}" fill="none" stroke="#232323" stroke-width="2"/>',
        f'<text x="{(PX0 + PX1) / 2:.0f}" y="{H - 48}" font-size="38" text-anchor="middle">{esc(XLAB)}</text>',
        f'<text transform="translate(78 {(PY0 + PY1) / 2:.0f}) rotate(-90)" font-size="38" text-anchor="middle">{esc(YLAB)}</text>',
        f'<line x1="{PX0}" y1="{sy(STOP_V):.1f}" x2="{PX1}" y2="{sy(STOP_V):.1f}" stroke="#c82828" stroke-width="3"/>',
        f'<text x="{PX1 - 14}" y="{sy(STOP_V) + 38:.1f}" font-size="32" fill="#c82828" text-anchor="end">{esc(STOP_NOTE)}</text>',
        '<polyline fill="none" stroke="#1e55be" stroke-width="4" stroke-linejoin="round" points="'
        + " ".join(f"{sx(h):.1f},{sy(v):.1f}" for h, v in zip(hours, volts)) + '"/>']
for h, v in (START, END):
    svg.append(f'<circle cx="{sx(h):.1f}" cy="{sy(v):.1f}" r="11" fill="#1e55be"/>')
svg += [f'<text x="{sx(START[0]) + 22:.1f}" y="{sy(START[1]) - 20:.1f}" font-size="36">start: {START[1]:.2f} V</text>',
        f'<text x="{sx(END[0]) + 10:.1f}" y="{sy(END[1]) + 96:.1f}" font-size="36" text-anchor="end">end: {END[1]:.2f} V after {END[0]:.2f} h</text>',
        "</svg>"]
(HERE / "perf-rundown-voltage-figure.svg").write_text("\n".join(svg) + "\n")
print(f"written: perf-rundown-voltage-figure.png and .svg ({len(rows)} samples, 0 to {hours[-1]:.2f} h, {START[1]:.3f} V to {END[1]:.3f} V)")
