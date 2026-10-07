"""The control panel as built on the protoboard: two knobs, two rockers, grounds.

Answers the questions people actually ask while soldering:
  * each knob has three legs — does it need three ESP32 pins?  No: ONE each
    (STRENGTH -> GPIO34, VOLUME -> GPIO33). The other two legs are 3V3 and GND,
    shared by both knobs.
  * there are only three GND pins and lots of grounds — how?  One GND pin feeds
    the protoboard's GND strip, and every ground lands on the strip.
  * why GPIO33 for the volume knob, and what would hurt the board?

Conceptual — what connects to what, not where each hole is. Pin data is read out
of gen_wiring.py, so the drawings cannot drift.

    python3 docs/gen_pot_wiring.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Single source of truth: pull PINOUT straight from the breadboard generator.
_src = open(os.path.join(HERE, "gen_wiring.py"), encoding="utf-8").read()
_ns = {}
exec(_src[_src.index("PINOUT = {"):_src.index("LEFT, RIGHT =")], _ns)
LEFT, RIGHT = _ns["PINOUT"][38]
assert len(LEFT) == len(RIGHT) == 19, "38-pin board = 19 per side"

RED, BLACK, VIOLET, PINK, GREEN = "#dc2626", "#111827", "#7c3aed", "#db2777", "#16a34a"
TEAL, BLUE, GREY = "#0d9488", "#2563eb", "#9ca3af"
DANGER, ORANGE = "#7f1d1d", "#ea580c"

AVOID = {"GPIO0", "GPIO1", "GPIO3", "GPIO6", "GPIO7", "GPIO8", "GPIO9",
         "GPIO10", "GPIO11", "GPIO12", "EN"}
CAREFUL = {"GPIO2", "GPIO5", "GPIO14", "GPIO15"}
USED = {"3V3": RED, "5V": RED, "GPIO34": VIOLET, "GPIO33": PINK,
        "GPIO17": GREEN, "GPIO18": TEAL, "GPIO23": BLUE}

W, H = 1500, 1050
s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
     f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
add = s.append


def txt(x, y, t, size=10, fill=BLACK, anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{t}</text>')


def dot(x, y, col, r=5):
    add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{col}" stroke="#ffffff" stroke-width="1.5"/>')


def path(points, col, hops=(), width=3.2):
    """Polyline through `points`; horizontal segments jump over x in `hops`.

    A hop is a small arc, the schematic convention for 'crosses, does NOT
    connect' — without it a wire drawn over another reads as tied to it.
    """
    d = f"M {points[0][0]} {points[0][1]}"
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if y0 == y1:
            step = 1 if x1 > x0 else -1
            for hx in sorted((h for h in hops if min(x0, x1) < h < max(x0, x1)),
                             key=lambda h: step * h):
                d += f" L {hx - 7 * step} {y0} A 7 7 0 0 {1 if step > 0 else 0} {hx + 7 * step} {y0}"
        d += f" L {x1} {y1}"
    add(f'<path d="{d}" stroke="{col}" stroke-width="{width}" fill="none" '
        f'stroke-linecap="round" stroke-linejoin="round"/>')


def tag(x, y, t, col, anchor="start"):
    txt(x, y, t, 9, col, anchor, "bold")


# ---- title ----------------------------------------------------------------
txt(24, 36, "Control panel as built — two knobs, two rockers, one ground strip", 20, weight="bold")
txt(24, 58, "ESP32-WROOM-32 38-pin DevKit, USB at the bottom, on the protoboard. Conceptual: "
            "what connects to what, not where each hole is. The STATUS button is gone.",
    11, "#6b7280")

# ---- geometry ---------------------------------------------------------------
BX0, BX1 = 560, 780                      # board body; pin dots sit on its edges
def py(i):
    return 150 + i * 36
YL = {n: py(i) for i, n in enumerate(LEFT)}
YR = {n: py(i) for i, n in enumerate(RIGHT)}
Y_GND_L = YL["GND"]
RGND = [py(i) for i, n in enumerate(RIGHT) if n == "GND"]
STRIP_Y, STRIP_X0, STRIP_X1 = 975, 120, 1010
V3_X, GNDV_X, BGND_X = 360, 380, 470        # 3V3 link, knobs' GND link, board GND drop

# ---- the GND strip (drawn first, so wires sit on top) -------------------------
add(f'<rect x="{STRIP_X0}" y="{STRIP_Y - 7}" width="{STRIP_X1 - STRIP_X0}" height="14" rx="7" '
    f'fill="{BLACK}" opacity="0.13"/>')
add(f'<line x1="{STRIP_X0}" y1="{STRIP_Y}" x2="{STRIP_X1}" y2="{STRIP_Y}" stroke="{BLACK}" stroke-width="4"/>')
txt((STRIP_X0 + STRIP_X1) / 2, STRIP_Y + 24,
    "PROTOBOARD GND STRIP — every ground lands here; all of it is one connection",
    10.5, BLACK, "middle", "bold")

# ---- the board ---------------------------------------------------------------
add(f'<rect x="{BX0}" y="100" width="{BX1 - BX0}" height="{py(18) - 100 + 32}" rx="7" fill="#1f2937"/>')
txt((BX0 + BX1) / 2, 124, "ESP32 DevKit · 38-pin", 11, "#e5e7eb", "middle", "bold")
txt((BX0 + BX1) / 2, py(18) + 26, "USB ▼", 10, GREY, "middle")


def pin_colour(name):
    if name == "GND":
        return BLACK
    if name in USED:
        return USED[name]
    if name in AVOID:
        return DANGER
    if name in CAREFUL:
        return ORANGE
    return GREY


for side, names in (("L", LEFT), ("R", RIGHT)):
    for i, name in enumerate(names):
        y = py(i)
        x = BX0 if side == "L" else BX1
        dot(x, y, pin_colour(name), 6)
        if name == "GND":            # light ring so black GND dots show on the board
            add(f'<circle cx="{x}" cy="{y}" r="6" fill="none" stroke="#f9fafb" stroke-width="1.5"/>')
        strong = name in USED or name == "GND"
        fill = "#f9fafb" if strong else ("#fca5a5" if name in AVOID else
                                         "#fdba74" if name in CAREFUL else "#9ca3af")
        lx = x + 14 if side == "L" else x - 14
        txt(lx, y + 4, name, 11, fill, "start" if side == "L" else "end",
            "bold" if strong else "normal")

# ---- the two knobs: legs point right — 3V3 on top, wiper middle, GND bottom ----
def knob(cy, title, sub, wiper_col):
    cx, r = 250, 40
    add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#78350f" stroke="#451a03" stroke-width="2"/>')
    add(f'<circle cx="{cx}" cy="{cy}" r="16" fill="#a8a29e" stroke="#57534e" stroke-width="2"/>')
    add(f'<line x1="{cx}" y1="{cy}" x2="{cx + 11}" y2="{cy - 11}" stroke="#1c1917" '
        f'stroke-width="4" stroke-linecap="round"/>')
    for dy, col in ((-24, RED), (0, wiper_col), (24, BLACK)):
        add(f'<rect x="{cx + r - 4}" y="{cy + dy - 4}" width="{334 - (cx + r - 4)}" height="8" '
            f'rx="2" fill="#d6d3d1" stroke="{col}" stroke-width="1.5"/>')
    txt(cx - r - 12, cy - 4, title, 11.5, BLACK, "end", "bold")
    txt(cx - r - 12, cy + 12, sub, 9, "#6b7280", "end")
    return cy - 24, cy, cy + 24          # 3V3 leg, wiper, GND leg


S3, SW, SG = knob(YL["GPIO34"], "STRENGTH knob", "B10K · motors", VIOLET)
V3, VW, VG = knob(YL["GPIO33"], "VOLUME knob", "B10K · speech", PINK)
assert SW == YL["GPIO34"] and VW == YL["GPIO33"]

# 3V3: ONE wire from the ESP32 3V3 pin, linked across both knobs' top legs
path([(BX0, YL["3V3"]), (V3_X, YL["3V3"]), (V3_X, V3), (334, V3)], RED)
path([(V3_X, S3), (334, S3)], RED)
dot(V3_X, S3, RED)
tag(BX0 - 8, YL["3V3"] - 8, "3V3 pin → both knobs' TOP legs — one wire, linked at the knobs", RED, "end")

# wipers: one ESP32 pin each
path([(334, SW), (BX0, SW)], VIOLET, hops=(V3_X,), width=4)
tag(BX0 - 8, SW - 8, "STRENGTH wiper → GPIO34", VIOLET, "end")
path([(334, VW), (BX0, VW)], PINK, hops=(GNDV_X,), width=4)
tag(BX0 - 8, VW - 8, "VOLUME wiper → GPIO33", PINK, "end")

# GND: both knobs' bottom legs linked, one wire down to the strip
path([(334, SG), (GNDV_X, SG), (GNDV_X, STRIP_Y)], BLACK)
path([(334, VG), (GNDV_X, VG)], BLACK)
dot(GNDV_X, VG, BLACK)
dot(GNDV_X, STRIP_Y, BLACK)
tag(GNDV_X + 8, VG + 22, "both bottom legs → GND strip", BLACK)

# the ESP32's own ground: ONE GND pin feeds the strip
path([(BX0, Y_GND_L), (BGND_X, Y_GND_L), (BGND_X, STRIP_Y)], BLACK)
dot(BGND_X, STRIP_Y, BLACK)
tag(BX0 - 8, Y_GND_L - 8, "GND pin → strip", BLACK, "end")

# X1202 power in
XB = (150, 862, 210, 60)  # x, y, w, h
add(f'<rect x="{XB[0]}" y="{XB[1]}" width="{XB[2]}" height="{XB[3]}" rx="6" fill="#334155"/>')
txt(XB[0] + XB[2] / 2, XB[1] + 25, "X1202 UPS", 12, "#f8fafc", "middle", "bold")
txt(XB[0] + XB[2] / 2, XB[1] + 43, "5 V → ESP32 5V pin only · GND = Pi GND", 9, "#cbd5e1", "middle")
x5, xg = XB[0] + 160, XB[0] + 50
path([(BX0, YL["5V"]), (x5, YL["5V"]), (x5, XB[1])], RED, hops=(BGND_X, GNDV_X))
tag(BX0 - 8, YL["5V"] - 8, "5V ← X1202", RED, "end")
path([(xg, XB[1] + XB[3]), (xg, STRIP_Y)], BLACK)
dot(xg, STRIP_Y, BLACK)

# ---- right side: link, rockers, freed pin --------------------------------------
PI = (1180, YR["GPIO23"] - 26, 290, 52)
add(f'<rect x="{PI[0]}" y="{PI[1]}" width="{PI[2]}" height="{PI[3]}" rx="6" fill="#14532d"/>')
txt(PI[0] + PI[2] / 2, PI[1] + 22, "Raspberry Pi 5 — header pin 21", 11, "#f0fdf4", "middle", "bold")
txt(PI[0] + PI[2] / 2, PI[1] + 38, "GPIO9 · uart3-pi5 RX · /dev/ttyAMA3", 9, "#bbf7d0", "middle")
path([(BX1, YR["GPIO23"]), (PI[0], YR["GPIO23"])], BLUE)
tag(BX1 + 10, YR["GPIO23"] - 8, "GPIO23 → Pi, panel data", BLUE)

tag(BX1 + 10, YR["GPIO22"] - 7, "free — STATUS removed (no ADC: can't take a knob)", "#6b7280")
for y in RGND:
    tag(BX1 + 10, y - 7, "GND — spare (same node)", "#6b7280")


def device(x, y, w, h, title, sub, accent):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="#1f2937"/>')
    add(f'<rect x="{x}" y="{y}" width="{w}" height="5" rx="2" fill="{accent}"/>')
    txt(x + w / 2, y + h / 2 + 4, title, 10, "#f9fafb", "middle", "bold")
    txt(x + w / 2, y + h + 14, sub, 9, "#374151", "middle")


DX, DW, RET_X = 880, 92, 1010
for name, label, sub, col in (("GPIO18", "DEPTH", "rocker · motors on/off", TEAL),
                              ("GPIO17", "DETECT", "rocker · speech on/off", GREEN)):
    y = YR[name]
    device(DX, y - 22, DW, 44, label, sub, col)
    path([(BX1, y), (DX, y)], col)
    tag(BX1 + 10, y - 8, name, col)
    path([(DX + DW, y + 10), (RET_X, y + 10)], BLACK)
    dot(RET_X, y + 10, BLACK)
path([(RET_X, YR["GPIO18"] + 10), (RET_X, STRIP_Y)], BLACK)
dot(RET_X, STRIP_Y, BLACK)
tag(RET_X - 8, YR["GPIO17"] + 74, "rocker returns → GND strip", BLACK, "end")

# ---- explanation panel -------------------------------------------------------------
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}"/>')
    txt(x + 14, y + 23, title, 11.5, tc, weight="bold")
    for i, line in enumerate(lines):
        txt(x + 14, y + 44 + i * 16, line, 9.6, tc)


BXP, BWP = 1060, 420
box(BXP, 252, BWP, 154, "#fce7f3", "#f9a8d4", "WHY GPIO33 FOR THE VOLUME KNOB", [
    "ADC1 — it can read a voltage. GPIO22 (old STATUS) can't.",
    "Not a boot pin — no knob position can stop the board booting.",
    "Neighbours GPIO32 and GPIO25 are unused, so a solder bridge",
    "  can't join it to the strength wiper (GPIO35 could) or to EN.",
    "Two spare pins (GPIO35, GPIO32) separate the two wipers.",
    "Read only — never set it as an OUTPUT.",
], "#831843")
box(BXP, 420, BWP, 154, "#f3f4f6", "#d1d5db", "BOTH KNOBS: ONE 3V3 WIRE, ONE GND WIRE", [
    "top legs linked      → one wire to the ESP32 3V3 pin",
    "middle legs (wipers) → GPIO34 and GPIO33, NEVER a power pin",
    "bottom legs linked   → protoboard GND strip",
    "Only ONE ESP32 GND pin is used; it feeds the strip.",
    "Two 10 kΩ knobs across 3.3 V draw 0.66 mA in total — nothing.",
    "Clockwise turns it DOWN? Swap that knob's outer legs.",
], BLACK)
box(BXP, 588, BWP, 154, "#fef3c7", "#fbbf24", "BEFORE POWER — 30-SECOND METER CHECK", [
    "3V3 pin ↔ GND: about 5 kΩ (two knobs in parallel).",
    "  A BEEP is a short — find it before powering.",
    "Turn both knobs to BOTH ends: 3V3 ↔ GND still no beep.",
    "  (A beep at an end = a wiper on a power pin.)",
    "5V pin ↔ 3V3 pin: no beep.   GPIO34 ↔ GPIO33: no beep.",
    "Then power on, and touch the board: warm = unplug.",
], "#78350f")
box(BXP, 756, BWP, 154, "#fee2e2", "#fca5a5", "FLASH THE BUILD THAT MATCHES THE SOLDERING", [
    "both knobs soldered      → pio run -e panel -t upload",
    "volume knob not in yet   → pio run -e panel_no_volume -t upload",
    "An unwired knob pin floats: ~54 junk lines a second.",
    "",
    "Never put a knob on 5V, EN, GPIO0, GPIO2, GPIO12 or GPIO15,",
    "or two wipers on neighbouring pins.",
], "#991b1b")

# legend for the pin dots
LX, LY = 24, 1032
for i, (col, label) in enumerate(((RED, "power"), (BLACK, "GND"), (VIOLET, "strength wiper"),
                                  (PINK, "volume wiper"), (GREY, "free"),
                                  (ORANGE, "boot-sensitive"), (DANGER, "never wire"))):
    dot(LX + i * 150 + 6, LY - 4, col, 6)
    txt(LX + i * 150 + 18, LY, label, 10, "#374151")

add('</svg>')

# os.path and the explicit encoding are both deliberate — the folder is copied to
# Windows, where rsplit("/") paths and the cp1252 default broke gen_wiring.py.
OUT = os.path.join(HERE, "pot_wiring.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote pot_wiring.svg")
