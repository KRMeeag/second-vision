"""Where to put the multimeter probes to check the ESP32's supply.

Pin data comes from gen_wiring.py so the counting cannot drift.

    python3 docs/gen_measure_points.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(HERE, "gen_wiring.py"), encoding="utf-8").read()
_ns = {}
exec(_src[_src.index("PINOUT = {"):_src.index("LEFT, RIGHT =")], _ns)
LEFT, _ = _ns["PINOUT"][38]

W, H = 1260, 830
RED, BLACK, GREY, GREEN, AMBER = "#dc2626", "#111827", "#9ca3af", "#16a34a", "#d97706"
s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
     f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
add = s.append

def txt(x, y, t, size=10, fill="#111827", anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{t}</text>')

txt(24, 38, "Measuring the ESP32's supply — probe placement", 20, weight="bold")
txt(24, 60, "Circuit POWERED: X1202 connected, USB disconnected, red LED lit. "
            "Voltage is measured live.", 11, "#6b7280")

# ---------------- the meter ----------------
MX, MY, MW, MH = 60, 96, 300, 470
add(f'<rect x="{MX}" y="{MY}" width="{MW}" height="{MH}" rx="14" fill="#1f2937"/>')
add(f'<rect x="{MX+28}" y="{MY+26}" width="{MW-56}" height="72" rx="5" fill="#cbd5e1"/>')
txt(MX+MW/2, MY+80, "5.02", 34, "#111827", "middle", "bold")
txt(MX+MW-42, MY+52, "V⎓", 12, "#475569", "middle")

# dial
DCX, DCY, DR = MX + MW/2, MY + 232, 84
add(f'<circle cx="{DCX}" cy="{DCY}" r="{DR}" fill="#374151" stroke="#111827" stroke-width="3"/>')
add(f'<circle cx="{DCX}" cy="{DCY}" r="30" fill="#4b5563"/>')
# pointer to V-DC (up-left)
add(f'<line x1="{DCX}" y1="{DCY}" x2="{DCX-46}" y2="{DCY-58}" stroke="#f9fafb" '
    f'stroke-width="7" stroke-linecap="round"/>')
for lbl, dx, dy, col, w in (("V⏜  20", -74, -74, GREEN, "bold"),
                            ("V~", 58, -70, GREY, "normal"),
                            ("A", 84, 14, RED, "bold"),
                            ("Ω", 4, 96, GREY, "normal")):
    txt(DCX+dx, DCY+dy, lbl, 12 if col != GREY else 11, col, "middle", w)
txt(DCX, DCY+DR+34, "set here: DC volts, 20 V range", 9.5, GREEN, "middle", "bold")

# sockets
SY = MY + MH - 44
for lbl, dx, col in (("10A", -84, RED), ("VΩmA", 0, RED), ("COM", 84, BLACK)):
    cx = DCX + dx
    add(f'<circle cx="{cx}" cy="{SY}" r="17" fill="{col}" stroke="#111827" stroke-width="2"/>')
    txt(cx, SY+40, lbl, 9, "#f9fafb" if col == BLACK else col, "middle", "bold")
add(f'<line x1="{DCX-84-22}" y1="{SY-22}" x2="{DCX-84+22}" y2="{SY+22}" '
    f'stroke="#ef4444" stroke-width="4"/>')
txt(DCX-84, SY-30, "NOT this one", 8, "#ef4444", "middle", "bold")

# ---------------- the ESP32 left column ----------------
BX, BY, BW = 800, 110, 150
PITCH = 25
BH = PITCH * (len(LEFT) - 1) + 40
add(f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" rx="6" fill="#1f2937"/>')
txt(BX+BW/2, BY+BH-12, "USB-C ▼", 9, "#9ca3af", "middle")
txt(BX+BW/2, BY-14, "ESP32 — LEFT column", 11, "#111827", "middle", "bold")

hole = {}
for i, name in enumerate(LEFT):
    y = BY + 22 + i * PITCH
    up = len(LEFT) - i          # position counting up from the USB-C end
    if name == "5V":
        col, lab = RED, f"5V   ({up} up)  ← RED probe"
    elif name == "GND" :
        col, lab = BLACK, f"GND  ({up} up)  ← BLACK probe"
    else:
        col, lab = "#4b5563", f"{name}"
    add(f'<circle cx="{BX-8}" cy="{y}" r="{6 if col=="#4b5563" else 8}" fill="{col}"'
        + ('' if col == "#4b5563" else ' stroke="#ffffff" stroke-width="2"') + '/>')
    strong = col != "#4b5563"
    txt(BX+BW+14, y+4, lab, 10 if strong else 8.5,
        col if strong else "#9ca3af", "start", "bold" if strong else "normal")
    hole[name] = (BX-8, y)

# probe leads
def lead(a, b, col):
    ax, ay = a; bx, by = b
    mid = 520
    add(f'<path d="M {ax} {ay} C {ax+60} {ay}, {mid} {ay}, {mid} {by} '
        f'L {bx-40} {by} L {bx} {by}" '
        f'stroke="{col}" stroke-width="4" fill="none" stroke-linecap="round" '
        f'stroke-linejoin="round"/>')
    add(f'<circle cx="{bx}" cy="{by}" r="4" fill="{col}"/>')
lead((DCX, SY), hole["5V"], RED)
lead((DCX+84, SY), hole["GND"], BLACK)

# ---------------- notes ----------------
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"/>')
    txt(x+13, y+21, title, 10.5, tc, weight="bold")
    for i, l in enumerate(lines):
        txt(x+13, y+41+i*15, l, 8.8, tc)

box(400, 96, 360, 104, "#fee2e2", "#fca5a5", "CHECK THE DIAL FIRST", [
    "If the dial is on A (amps) and the probes touch 5 V",
    "and GND, the meter IS a short circuit — the same",
    "fault that dropped the Pi earlier.",
    "",
    "Confirm it reads V⎓ before anything touches a pin.",
], "#991b1b")

box(400, 214, 360, 104, "#dbeafe", "#93c5fd", "REACHING THE PINS", [
    "Those pins already have jumpers on them.",
    "",
    "On a protoboard every hole in the same ROW is the",
    "same electrical point — so probe an empty hole in",
    "the 5V row and one in the GND row instead.",
], "#1e3a8a")

box(400, 332, 360, 104, "#f0fdf4", "#86efac", "THEN CHECK 3V3", [
    "Leave BLACK where it is. Move RED to 3V3 — the TOP",
    "pin of this column, 19 up from the USB-C end.",
    "+3.2 to +3.4 V = regulator working.",
    "Well below that = the chip is browning out.",
], "#14532d")

# reading table
TY = 668
txt(400, TY, "WHAT THE NUMBER MEANS  (red on 5V, black on GND)", 12, weight="bold")
rows = [("4.8 – 5.2 V", GREEN,  "supply is good — the fault is elsewhere"),
        ("3.5 – 4.5 V", AMBER,  "SAGGING under load — the loose XH2.54 contact"),
        ("0.0 – 0.5 V", RED,    "no contact at all, despite the lit LED"),
        ("negative",    GREY,   "probes reversed — swap them, harmless")]
yy = TY + 16
for val, col, meaning in rows:
    yy += 30
    add(f'<line x1="400" y1="{yy-20}" x2="{W-40}" y2="{yy-20}" stroke="#e5e7eb"/>')
    add(f'<rect x="400" y="{yy-11}" width="12" height="12" rx="2" fill="{col}"/>')
    txt(422, yy, val, 11, col, "start", "bold")
    txt(540, yy, meaning, 9.5, "#6b7280")

add('</svg>')
OUT = os.path.join(HERE, "measure_points.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote measure_points.svg")
