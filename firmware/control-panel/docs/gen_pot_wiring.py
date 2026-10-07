"""Potentiometer + ground distribution for the Second Vision control panel.

Answers one question with a picture: the pot has three legs, so does it need
three ESP32 pins? No — it takes ONE (GPIO34). Its other two legs go into the
3V3 and GND RAILS, which one ESP32 pin each feeds for every device at once.

Conceptual wiring, not hole-accurate: breadboard_wiring.svg is the hole map.
Pin data is read out of gen_wiring.py, so the drawings cannot drift.

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
TOP_ROW = 12  # breadboard row of the top pin pair, as in gen_wiring.py

RED, BLACK, VIOLET, GREEN = "#dc2626", "#111827", "#7c3aed", "#16a34a"
TEAL, YELLOW, BLUE, GREY = "#0d9488", "#ca8a04", "#2563eb", "#9ca3af"
DANGER, ORANGE = "#7f1d1d", "#ea580c"

AVOID = {"GPIO0", "GPIO1", "GPIO3", "GPIO6", "GPIO7", "GPIO8", "GPIO9",
         "GPIO10", "GPIO11", "GPIO12", "EN"}
CAREFUL = {"GPIO2", "GPIO5", "GPIO14", "GPIO15"}
USED = {"3V3": RED, "5V": RED, "GPIO34": VIOLET, "GPIO17": GREEN,
        "GPIO18": TEAL, "GPIO22": YELLOW, "GPIO23": BLUE}

W, H = 1500, 1010
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
    connect' — without it a wire drawn over a rail reads as tied to it.
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
txt(24, 36, "Control panel — the potentiometer, and where every ground goes", 20, weight="bold")
txt(24, 58, "ESP32-WROOM-32 38-pin DevKit, USB at the bottom. Conceptual: the hole-by-hole "
            "map is breadboard_wiring.svg (wiper a16 · 3V3 a12 · GND a25).", 11, "#6b7280")

# ---- geometry ---------------------------------------------------------------
BX0, BX1 = 560, 780                      # board body; pin dots sit on its edges
def py(i):
    return 150 + i * 36
PLUS_X, MINUS_X, RMINUS_X = 130, 90, 1010   # 3V3 rail, left GND rail, right GND rail
RAIL_TOP, RAIL_BOT, JOIN_Y = 118, 960, 960

# ---- rails (drawn first, so wires sit on top) ------------------------------
for x, col, bot in ((PLUS_X, RED, 860), (MINUS_X, BLACK, RAIL_BOT), (RMINUS_X, BLACK, RAIL_BOT)):
    add(f'<rect x="{x - 6}" y="{RAIL_TOP}" width="12" height="{bot - RAIL_TOP}" rx="6" '
        f'fill="{col}" opacity="0.13"/>')
    add(f'<line x1="{x}" y1="{RAIL_TOP}" x2="{x}" y2="{bot}" stroke="{col}" stroke-width="3"/>')
txt(PLUS_X, 109, "3V3 rail +", 10, RED, "middle", "bold")
txt(MINUS_X, 92, "GND rail −", 10, BLACK, "middle", "bold")
txt(RMINUS_X, 104, "GND rail −", 10, BLACK, "middle", "bold")
path([(MINUS_X, JOIN_Y), (RMINUS_X, JOIN_Y)], BLACK)
txt((MINUS_X + RMINUS_X) / 2, JOIN_Y - 8,
    "the two GND rails are joined by one jumper — every hole on both is the same ground",
    10, "#374151", "middle")

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


def hole(side, name):
    names = LEFT if side == "L" else RIGHT
    return f'{"a" if side == "L" else "j"}{TOP_ROW + names.index(name)}'


Y = {n: py(LEFT.index(n)) for n in ("3V3", "GPIO34", "5V")}
Y_GND_L = py(LEFT.index("GND"))
YR = {n: py(i) for i, n in enumerate(RIGHT)}
RGND = [py(i) for i, n in enumerate(RIGHT) if n == "GND"]

# ---- left side: power in, the pot ---------------------------------------------
# 3V3 pin -> 3V3 rail (feeds the pot's top leg)
path([(BX0, Y["3V3"]), (PLUS_X, Y["3V3"])], RED)
dot(PLUS_X, Y["3V3"], RED)
tag(BX0 - 8, Y["3V3"] - 8, f"3V3 pin ({hole('L', '3V3')}) → feeds the 3V3 rail", RED, "end")

# the ONE left GND pin -> GND rail
path([(BX0, Y_GND_L), (MINUS_X, Y_GND_L)], BLACK, hops=(PLUS_X,))
dot(MINUS_X, Y_GND_L, BLACK)
tag(BX0 - 8, Y_GND_L - 8, f"GND pin ({hole('L', 'GND')}) → feeds the GND rail — the only GND pin used", BLACK, "end")

# the pot: body below, legs pointing up
PX, PY_, PR = 320, 440, 42
LEG_Y = PY_ - PR - 26
add(f'<circle cx="{PX}" cy="{PY_}" r="{PR}" fill="#78350f" stroke="#451a03" stroke-width="2"/>')
add(f'<circle cx="{PX}" cy="{PY_}" r="17" fill="#a8a29e" stroke="#57534e" stroke-width="2"/>')
add(f'<line x1="{PX}" y1="{PY_}" x2="{PX + 12}" y2="{PY_ - 12}" stroke="#1c1917" stroke-width="4" stroke-linecap="round"/>')
for dx in (-30, 0, 30):
    add(f'<rect x="{PX + dx - 4}" y="{LEG_Y}" width="8" height="{PY_ - PR - LEG_Y + 6}" rx="2" '
        f'fill="#d6d3d1" stroke="#78716c"/>')
txt(PX, PY_ + PR + 20, "B10K POTENTIOMETER", 11, BLACK, "middle", "bold")
txt(PX, PY_ + PR + 36, "1  ·  W  ·  3", 10, "#6b7280", "middle")
txt(PX, PY_ + PR + 52, "outer legs interchangeable — swap", 9, "#6b7280", "middle")
txt(PX, PY_ + PR + 64, "them if clockwise turns it DOWN", 9, "#6b7280", "middle")

# wiper -> GPIO34 (the pot's ONLY ESP32 pin)
path([(PX, LEG_Y), (PX, Y["GPIO34"]), (BX0, Y["GPIO34"])], VIOLET, width=4)
tag(BX0 - 8, Y["GPIO34"] - 8, f"WIPER → GPIO34 ({hole('L', 'GPIO34')}) — its only ESP32 pin", VIOLET, "end")
# leg 1 -> 3V3 rail
path([(PX - 30, LEG_Y), (PX - 30, 345), (PLUS_X, 345)], RED)
dot(PLUS_X, 345, RED)
tag(PLUS_X + 10, 338, "leg 1 → any hole on the 3V3 rail", RED)
# leg 3 -> GND rail (around the pot, over the 3V3 rail)
path([(PX + 30, LEG_Y), (PX + 30, LEG_Y - 14), (410, LEG_Y - 14), (410, 572), (MINUS_X, 572)],
     BLACK, hops=(PLUS_X,))
dot(MINUS_X, 572, BLACK)
tag(MINUS_X + 52, 565, "leg 3 → any hole on the GND rail", BLACK)

# X1202 power in
XB = (170, 868, 230, 62)  # x, y, w, h
add(f'<rect x="{XB[0]}" y="{XB[1]}" width="{XB[2]}" height="{XB[3]}" rx="6" fill="#334155"/>')
txt(XB[0] + XB[2] / 2, XB[1] + 26, "X1202 UPS", 12, "#f8fafc", "middle", "bold")
txt(XB[0] + XB[2] / 2, XB[1] + 44, "5 V out · its GND is the Pi's GND", 9, "#cbd5e1", "middle")
x5, xg = XB[0] + 180, XB[0] + 50
path([(BX0, Y["5V"]), (x5, Y["5V"]), (x5, XB[1])], RED)
tag(BX0 - 8, Y["5V"] - 8, "5V pin ← X1202 5 V (power IN)", RED, "end")
path([(xg, XB[1]), (xg, 845), (MINUS_X, 845)], BLACK, hops=(PLUS_X,))
dot(MINUS_X, 845, BLACK)
tag(MINUS_X + 52, 838, "X1202 GND → GND rail", BLACK)

# ---- right side: link, button, rockers -------------------------------------------
PI = (1180, YR["GPIO23"] - 26, 290, 52)
add(f'<rect x="{PI[0]}" y="{PI[1]}" width="{PI[2]}" height="{PI[3]}" rx="6" fill="#14532d"/>')
txt(PI[0] + PI[2] / 2, PI[1] + 22, "Raspberry Pi 5 — header pin 21", 11, "#f0fdf4", "middle", "bold")
txt(PI[0] + PI[2] / 2, PI[1] + 38, "GPIO9 · uart3-pi5 RX · /dev/ttyAMA3", 9, "#bbf7d0", "middle")
path([(BX1, YR["GPIO23"]), (PI[0], YR["GPIO23"])], BLUE, hops=(RMINUS_X,))
tag(BX1 + 10, YR["GPIO23"] - 8, f"GPIO23 ({hole('R', 'GPIO23')}) → Pi, panel data", BLUE)


def device(x, y, w, h, title, sub, fill, accent):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}"/>')
    add(f'<rect x="{x}" y="{y}" width="{w}" height="5" rx="2" fill="{accent}"/>')
    txt(x + w / 2, y + h / 2 + 4, title, 10, "#f9fafb", "middle", "bold")
    txt(x + w / 2, y + h + 14, sub, 9, "#374151", "middle")


DX, DW = 880, 92
for name, label, sub, col in (("GPIO22", "STATUS", "momentary button", YELLOW),
                              ("GPIO18", "DEPTH", "rocker · motors", TEAL),
                              ("GPIO17", "DETECT", "rocker · speech", GREEN)):
    y = YR[name]
    device(DX, y - 22, DW, 44, label, sub, "#1f2937", col)
    path([(BX1, y), (DX, y)], col)
    tag(BX1 + 10, y - 8, f"{name} ({hole('R', name)})", col)
    path([(DX + DW, y + 10), (RMINUS_X, y + 10)], BLACK)
    dot(RMINUS_X, y + 10, BLACK)

for y in RGND:
    tag(BX1 + 10, y - 7, "GND — spare (same node)", "#6b7280")

# ---- explanation panel -------------------------------------------------------------
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}"/>')
    txt(x + 14, y + 23, title, 11.5, tc, weight="bold")
    for i, line in enumerate(lines):
        txt(x + 14, y + 44 + i * 16, line, 9.6, tc)


BXP, BWP = 1060, 420
box(BXP, 262, BWP, 168, "#ede9fe", "#c4b5fd", "THE POT USES ONE ESP32 PIN, NOT THREE", [
    "wiper (middle leg)  →  GPIO34      ← the only ESP32 pin",
    "leg 1               →  3V3 RAIL    ← a rail hole, not a pin",
    "leg 3               →  GND RAIL    ← a rail hole, not a pin",
    "",
    "A rail is a long strip of holes that are ALL one connection.",
    "One ESP32 pin feeds it, and any number of wires can plug into",
    "its free holes.",
], "#4c1d95")
box(BXP, 446, BWP, 170, "#f3f4f6", "#d1d5db", "GND PINS: 1 OF 3 USED", [
    f"left GND ({hole('L', 'GND')})  → GND rail — the one that is wired",
    f"right GND ({hole('R', 'GND')}, j{TOP_ROW + RIGHT.index('GND', 1)})  → spare, nothing on them",
    "",
    "All three are the SAME ground inside the board. Five ground",
    "wires — X1202, pot, STATUS, DEPTH, DETECT — share the rail,",
    "so adding the pot needs no new GND pin.",
], BLACK)
box(BXP, 632, BWP, 132, "#fef3c7", "#fbbf24", "ESP32 PINS IN USE: 8 OF 38", [
    "5V · 3V3 · GND · GPIO17 · GPIO18 · GPIO22 · GPIO23 · GPIO34",
    "",
    "Free and safe if you need more:",
    "GPIO4 · 13 · 16 · 19 · 21 · 25 · 26 · 27 · 32 · 33",
    "Analog-only spares: GPIO35 · 36 · 39",
], "#78350f")
box(BXP, 780, BWP, 150, "#fee2e2", "#fca5a5", "DON'T", [
    "• Pot leg on 5V — GPIO34 tolerates 3.6 V at most.",
    "• Move the WIPER when it reads backwards — swap legs 1 and 3.",
    "• Row 12: 3V3 (left) faces GND (right). Never bridge it.",
    "• Wire the pot to EN, GPIO0 or GPIO12 — reset / boot pins.",
    "• Flash the `bench` build — it compiles the pot out.",
    "  Use `pio run -e panel -t upload`.",
], "#991b1b")

# legend for the pin dots
LX, LY = 24, 985
for i, (col, label) in enumerate(((RED, "power"), (BLACK, "GND"), (VIOLET, "pot"),
                                  (GREY, "free"), (ORANGE, "boot-sensitive"),
                                  (DANGER, "never wire"))):
    dot(LX + i * 120 + 6, LY - 4, col, 6)
    txt(LX + i * 120 + 18, LY, label, 10, "#374151")

add('</svg>')

# os.path and the explicit encoding are both deliberate — the folder is copied to
# Windows, where rsplit("/") paths and the cp1252 default broke gen_wiring.py.
OUT = os.path.join(HERE, "pot_wiring.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote pot_wiring.svg")
