"""Second Vision control-panel breadboard wiring diagram.

EDIT THE CONSTANTS BELOW to match your actual board, then re-run:
    python3 local/hardware/gen_wiring.py
"""

# ============================================================
#  MEASURE YOUR BOARD, THEN EDIT THESE
# ------------------------------------------------------------
PINS    = 38   # count the pins on ONE side: 19 -> use 38, 15 -> use 30
TOP_ROW = 12   # breadboard row holding the ESP32's TOPMOST pin pair
LEFT_FREE, RIGHT_FREE = "a", "j"   # the free hole column beside each pin row
# ============================================================

W, H = 1360, 950
XOFF = 175

def y(r):                       # terminal strip: 30 rows, 0.1" pitch
    return 100 + (r - 1) * 20

def ry(i):                      # power rail: 25 holes in 5 groups of 5
    g, h = divmod(i, 5)         # groups are separated by a wider gap, and the
    return 110 + g * 120 + h * 20   # rail does NOT line up with row numbers

COL = {c: x + XOFF for c, x in zip("abcde", (110, 130, 150, 170, 190))}
COL.update({c: x + XOFF for c, x in zip("fghij", (250, 270, 290, 310, 330))})
RAIL_NEG_L, RAIL_POS_L = 40 + XOFF, 62 + XOFF
RAIL_POS_R, RAIL_NEG_R = 380 + XOFF, 402 + XOFF
BOARD_X = 20 + XOFF

BLUE, RED, BLACK = "#2563eb", "#dc2626", "#111827"
GREEN, YELLOW, TEAL, VIOLET = "#16a34a", "#ca8a04", "#0d9488", "#7c3aed"

PINOUT = {
 38: (["3V3","EN","GPIO36","GPIO39","GPIO34","GPIO35","GPIO32","GPIO33","GPIO25",
       "GPIO26","GPIO27","GPIO14","GPIO12","GND","GPIO13","GPIO9","GPIO10","GPIO11","5V"],
      ["GND","GPIO23","GPIO22","GPIO1","GPIO3","GPIO21","GND","GPIO19","GPIO18",
       "GPIO5","GPIO17","GPIO16","GPIO4","GPIO0","GPIO2","GPIO15","GPIO8","GPIO7","GPIO6"]),
 30: (["EN","GPIO36","GPIO39","GPIO34","GPIO35","GPIO32","GPIO33","GPIO25","GPIO26",
       "GPIO27","GPIO14","GPIO12","GPIO13","GND","VIN"],
      ["GPIO23","GPIO22","GPIO1","GPIO3","GPIO21","GPIO19","GPIO18","GPIO5","GPIO17",
       "GPIO16","GPIO4","GPIO2","GPIO15","GND","3V3"]),
}
LEFT, RIGHT = PINOUT[PINS]
USED  = {"GPIO18","GPIO17","GPIO22","GPIO34","3V3"}
AVOID = {"GPIO1","GPIO3","GPIO0","GPIO6","GPIO7","GPIO8","GPIO9","GPIO10","GPIO11"}

def find(name):
    if name in LEFT:
        return TOP_ROW + LEFT.index(name), LEFT_FREE, "L"
    return TOP_ROW + RIGHT.index(name), RIGHT_FREE, "R"

def hole(name):
    r, c, _ = find(name)
    return COL[c], y(r)

def label(name):
    r, c, _ = find(name)
    return f"{c}{r}"

s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
     f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
add = s.append

def txt(x, yy, t, size=10, fill="#111827", anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{yy}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{t}</text>')

def wire(x1, y1, x2, y2, color, width=3.2):
    add(f'<path d="M {x1} {y1} L {x2} {y2}" stroke="{color}" stroke-width="{width}" '
        f'fill="none" stroke-linecap="round" opacity="0.9"/>')
    for cx, cy in ((x1, y1), (x2, y2)):
        add(f'<circle cx="{cx}" cy="{cy}" r="4.2" fill="{color}"/>')

txt(20, 32, "Second Vision — Control Panel Wiring", 19, weight="bold")
txt(20, 52, f"ESP32-WROOM {PINS}-pin · 400-tie breadboard · USB-C facing DOWN · "
            f"top pin pair in row {TOP_ROW}", 11, "#6b7280")

# ---- breadboard ---------------------------------------------------------
add(f'<rect x="{BOARD_X}" y="76" width="405" height="628" rx="6" fill="#f9fafb" '
    f'stroke="#d1d5db" stroke-width="1.5"/>')
add(f'<rect x="{COL["e"]}" y="76" width="60" height="628" fill="#eef2f7"/>')

# rails: printed line is drawn in two halves, because most boards break it
for xr, col in ((RAIL_NEG_L, BLUE), (RAIL_POS_L, RED), (RAIL_POS_R, RED), (RAIL_NEG_R, BLUE)):
    add(f'<line x1="{xr}" y1="100" x2="{xr}" y2="{ry(11)}" stroke="{col}" '
        f'stroke-width="1.2" opacity="0.5"/>')
    add(f'<line x1="{xr}" y1="{ry(13)}" x2="{xr}" y2="690" stroke="{col}" '
        f'stroke-width="1.2" opacity="0.5"/>')
# Shade each group of 5 so the 5x5 structure is unmistakable — the rail is
# NOT a continuous strip of 30 holes lined up with the row numbers.
for pair in ((RAIL_NEG_L, RAIL_POS_L), (RAIL_POS_R, RAIL_NEG_R)):
    x0, x1 = min(pair) - 11, max(pair) + 11
    for g in range(5):
        add(f'<rect x="{x0}" y="{ry(g*5)-10}" width="{x1-x0}" '
            f'height="{ry(g*5+4)-ry(g*5)+20}" rx="7" fill="#e2e8f0" opacity="0.75"/>')
for i in range(25):
    for xr in (RAIL_NEG_L, RAIL_POS_L, RAIL_POS_R, RAIL_NEG_R):
        add(f'<circle cx="{xr}" cy="{ry(i)}" r="2.8" fill="#94a3b8"/>')

for r in range(1, 31):
    for c in "abcdefghij":
        add(f'<circle cx="{COL[c]}" cy="{y(r)}" r="2.6" fill="#cbd5e1"/>')
for c in "abcdefghij":
    txt(COL[c], 92, c, 9, "#9ca3af", "middle")
for r in range(1, 31):
    if r % 2 == 0 or r == 1:
        txt(BOARD_X + 68, y(r) + 3, str(r), 8, "#94a3b8", "middle")
        txt(BOARD_X + 336, y(r) + 3, str(r), 8, "#94a3b8", "middle")
txt(BOARD_X + 202, 718, "power rails: 25 holes in 5 groups of 5 — they do NOT line up with the row numbers",
    8.5, "#6b7280", "middle")
txt(BOARD_X + 202, 732, "every hole in one rail half is the SAME node, so any free hole works",
    8.5, "#6b7280", "middle")

# ---- ESP32 --------------------------------------------------------------
bot = TOP_ROW + len(LEFT) - 1
add(f'<rect x="{COL["b"]-10}" y="{y(TOP_ROW)-11}" width="200" '
    f'height="{y(bot)-y(TOP_ROW)+22}" rx="4" fill="#1f2937" stroke="#0f172a" stroke-width="1.5"/>')
add(f'<rect x="{COL["b"]+38}" y="{y(TOP_ROW)+4}" width="104" height="86" rx="2" fill="#9ca3af"/>')
txt(COL["e"]+30, y(TOP_ROW+3)+6, "ESP-32", 12, "#1f2937", "middle", "bold")
txt(COL["e"]+30, y(bot)+14, "USB-C  ▼", 9, "#9ca3af", "middle")
for names, col, anchor, dx in ((LEFT, "b", "start", 10), (RIGHT, "i", "end", -10)):
    for i, name in enumerate(names):
        r = TOP_ROW + i
        fill = "#fbbf24" if name in USED else ("#7f1d1d" if name in AVOID else "#9ca3af")
        add(f'<circle cx="{COL[col]}" cy="{y(r)}" r="3.4" fill="{fill}"/>')
        t = name + ("   ← pot wiper" if name == "GPIO34" else "")
        txt(COL[col]+dx, y(r)+3, t, 7.5, "#f9fafb" if name in USED else "#9ca3af", anchor)

# ---- tactile buttons ----------------------------------------------------
def button(rt, rb, lab, sub, dim=False):
    """dim=True marks a control that is wired for bench testing but is NOT a
    shipping user feature — see the note in the legend."""
    body, knob = ("#9ca3af", "#cbd5e1") if dim else ("#374151", "#4b5563")
    dash = ' stroke-dasharray="4 3"' if dim else ""
    add(f'<rect x="{COL["e"]-6}" y="{y(rt)-8}" width="72" height="{y(rb)-y(rt)+16}" '
        f'rx="3" fill="{body}" stroke="#111827"{dash}/>')
    add(f'<circle cx="{COL["e"]+30}" cy="{(y(rt)+y(rb))/2}" r="9" fill="{knob}" stroke="#1f2937"/>')
    for rr in (rt, rb):
        for cc in ("e", "f"):
            add(f'<circle cx="{COL[cc]}" cy="{y(rr)}" r="3.2" fill="#fbbf24"/>')
    txt(COL["e"]+30, y(rb)+26, f"{lab} — {sub}", 8.5,
        "#6b7280" if dim else "#111827", "middle", "bold")
button(6, 8, "STATUS", "is it alive?")

# ---- potentiometer ------------------------------------------------------
PX, PY = 105, 330
add(f'<circle cx="{PX}" cy="{PY}" r="46" fill="#78350f" stroke="#451a03" stroke-width="2"/>')
add(f'<circle cx="{PX}" cy="{PY}" r="20" fill="#a8a29e" stroke="#57534e" stroke-width="2"/>')
add(f'<line x1="{PX}" y1="{PY}" x2="{PX}" y2="{PY-34}" stroke="#1c1917" stroke-width="5" stroke-linecap="round"/>')
txt(PX, PY-58, "B10K", 11, "#111827", "middle", "bold")
txt(PX, PY-72, "POTENTIOMETER", 9, "#6b7280", "middle")
for lx, lab in ((-30, "1"), (0, "W"), (30, "3")):
    add(f'<rect x="{PX+lx-4}" y="{PY+44}" width="8" height="16" rx="2" fill="#d6d3d1" stroke="#78716c"/>')
    txt(PX+lx, PY+74, lab, 8.5, "#6b7280", "middle")
txt(PX, PY+138, "W = wiper (middle leg)", 8, "#6b7280", "middle")
txt(PX, PY+152, "outer legs interchangeable —", 8, "#6b7280", "middle")
txt(PX, PY+164, "swap them if it reads backwards", 8, "#6b7280", "middle")

# ---- the two rockers ----------------------------------------------------
# One per capability. Each owns a model AND the output channel that model
# feeds — a model with no route to the user is just heat.
RX = 645
def rocker(y0, name, sub1, sub2, accent):
    add(f'<rect x="{RX}" y="{y0}" width="104" height="52" rx="5" fill="#1f2937"/>')
    add(f'<rect x="{RX}" y="{y0}" width="104" height="6" rx="3" fill="{accent}"/>')
    txt(RX+52, y0+34, name, 10, "#f9fafb", "middle", "bold")
    txt(RX+52, y0+70, sub1, 9, "#111827", "middle", "bold")
    txt(RX+52, y0+84, sub2, 8.5, "#6b7280", "middle")

rocker(300, "DEPTH",  "depth estimation", "+ vibration motors", TEAL)
rocker(460, "DETECT", "object detection", "+ speech (TTS)", GREEN)

# ---- wires --------------------------------------------------------------
v3x, v3y = hole("3V3")
gx, gy = hole("GND")
v3rail = RAIL_POS_L if find("3V3")[2] == "L" else RAIL_POS_R
gndrail = RAIL_NEG_L if find("GND")[2] == "L" else RAIL_NEG_R

wire(v3x, v3y, v3rail, ry(9), RED)                     # 1 3V3 -> + rail
wire(gx, gy, gndrail, ry(20), BLACK)                   # 2 GND -> - rail
wire(RAIL_NEG_L, ry(0), RAIL_NEG_R, ry(0), BLACK)      # 3 join - rails
wire(RAIL_NEG_L, ry(8), RAIL_NEG_L, ry(16), BLACK)     # 4 bridge the split
wire(RAIL_POS_L, ry(8), RAIL_POS_R, ry(8), RED)        # 5 join + rails
wire(COL["b"], y(6), RAIL_NEG_L, ry(4), BLACK)         # 6 STATUS gnd
wire(COL["g"], y(8), *hole("GPIO22"), YELLOW)          # 7 STATUS sig
wire(RX, 316, *hole("GPIO18"), TEAL)                   #   DEPTH signal
wire(RX, 340, RAIL_NEG_R, ry(7), BLACK)                #   DEPTH return
wire(RX, 476, *hole("GPIO17"), GREEN)                  #   DETECT signal
wire(RX, 500, RAIL_NEG_R, ry(13), BLACK)               #   DETECT return
wire(PX-30, PY+60, RAIL_POS_L, ry(13), RED)            #   pot leg 1
wire(PX,    PY+60, *hole("GPIO34"), VIOLET)            #   pot wiper
wire(PX+30, PY+60, RAIL_NEG_L, ry(12), BLACK)          #   pot leg 3

# ---- legend -------------------------------------------------------------
LX = 795
add(f'<rect x="{LX-14}" y="86" width="{W-LX-6}" height="840" rx="6" fill="#f9fafb" stroke="#e5e7eb"/>')
txt(LX, 112, "JUMPER WIRES", 13, weight="bold")
rows = [
 ("M-M", f'{label("3V3")}  (3V3)',   "left + rail",          RED,    "power bus"),
 ("M-M", f'{label("GND")}  (GND)',   "left - rail",          BLACK,  "ground bus"),
 ("M-M", "left - rail",              "right - rail",         BLACK,  "join both ground rails"),
 ("M-M", "left - rail  UPPER half",  "left - rail  LOWER half", BLACK,"bridge the split"),
 ("M-M", "left + rail",              "right + rail",         RED,    "join both power rails"),
 ("M-M", "b6    STATUS gnd",         "left - rail",          BLACK,  "status return"),
 ("M-M", "g8    STATUS sig",         f'{label("GPIO22")}  GPIO22', YELLOW,"liveness check"),
 ("--",  "DEPTH  rocker  signal",    f'{label("GPIO18")}  GPIO18', TEAL,  "depth + motors"),
 ("--",  "DEPTH  rocker  return",    "right - rail",         BLACK,  "switch's own wire"),
 ("--",  "DETECT rocker  signal",    f'{label("GPIO17")}  GPIO17', GREEN, "detection + TTS"),
 ("--",  "DETECT rocker  return",    "right - rail",         BLACK,  "switch's own wire"),
 ("M-F", "POT  outer leg 1",         "left + rail",          RED,    "3V3"),
 ("M-F", "POT  WIPER (middle)",      f'{label("GPIO34")}  GPIO34', VIOLET,"strength signal"),
 ("M-F", "POT  outer leg 3",         "left - rail",          BLACK,  "ground"),
]
yy = 140
for h, x in (("TYPE", 0), ("FROM", 52), ("TO", 215), ("PURPOSE", 380)):
    txt(LX+x, yy, h, 8.5, "#9ca3af", weight="bold")
yy += 8
for t, a, b, col, why in rows:
    yy += 25
    add(f'<line x1="{LX}" y1="{yy-16}" x2="{W-24}" y2="{yy-16}" stroke="#e5e7eb"/>')
    add(f'<rect x="{LX}" y="{yy-9}" width="10" height="10" rx="2" fill="{col}"/>')
    txt(LX+16, yy, t, 8.5, "#6b7280")
    txt(LX+52, yy, a, 10)
    txt(LX+203, yy, "→", 10, "#9ca3af")
    txt(LX+215, yy, b, 10)
    txt(LX+380, yy, why, 8.5, "#6b7280")
yy += 32
txt(LX, yy, "7 male-male  +  3 male-female  +  each rocker's own 2 wires", 10, weight="bold")

yy += 26
add(f'<rect x="{LX}" y="{yy-14}" width="{W-LX-30}" height="158" rx="5" fill="#dbeafe" stroke="#93c5fd"/>')
for i, line in enumerate([
    "WHAT SHIPS TO THE USER",
    "One LATCHING rocker per capability, each owning a model and",
    "the channel it speaks through: DETECT = YOLOv8 + speech,",
    "DEPTH = SC-DepthV3 + motors. Position is readable by touch.",
    "The pair sets the pipeline mode; there is no separate mode",
    "control, so the panel can never show a state that is not",
    "running. Both OFF = both channels gated = a silent device.",
    "STATUS answers \u201cis it alive?\u201d \u2014 silence and stillness are",
    "otherwise indistinguishable from a crash.",
]):
    txt(LX+10, yy+4+i*16, line, 8.5 if i else 10, "#1e3a8a", weight="bold" if i == 0 else "normal")

yy += 174
add(f'<rect x="{LX}" y="{yy-14}" width="{W-LX-30}" height="94" rx="5" fill="#fef3c7" stroke="#fbbf24"/>')
for i, line in enumerate([
    "VERIFY, THEN RE-RUN gen_wiring.py",
    f"• Pins on ONE side: 19 -> PINS=38, 15 -> PINS=30.  Now: {PINS}.",
    f"• Row of the TOPMOST pin pair.  Now: TOP_ROW={TOP_ROW}.",
    "• Switches straddle the channel; wire DIAGONALLY.",
    "• Row 12 is 3V3 (left) AND GND (right) - never bridge it.",
]):
    txt(LX+10, yy+4+i*16, line, 8.5 if i else 10, "#78350f", weight="bold" if i == 0 else "normal")

yy += 110
add(f'<rect x="{LX}" y="{yy-14}" width="{W-LX-30}" height="58" rx="5" fill="#fee2e2" stroke="#fca5a5"/>')
txt(LX+10, yy+4, "NEVER WIRE THESE (dark red pins)", 10, "#991b1b", weight="bold")
txt(LX+10, yy+21, "GPIO1 / GPIO3 - your serial link to the Pi", 9, "#991b1b")
txt(LX+10, yy+37, "GPIO0 - bootloader     GPIO6-11 - SPI flash", 9, "#991b1b")

add('</svg>')
open(__file__.rsplit("/", 1)[0] + "/breadboard_wiring.svg", "w").write("\n".join(s))
print(f"wrote breadboard_wiring.svg  (PINS={PINS}, TOP_ROW={TOP_ROW})")
