"""Standalone-boot fix: the two additions an ESP32 DevKit needs off USB.

Pin data is read from gen_wiring.py so the counting cannot drift.

    python3 docs/gen_standalone_fix.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(HERE, "gen_wiring.py"), encoding="utf-8").read()
_ns = {}
exec(_src[_src.index("PINOUT = {"):_src.index("LEFT, RIGHT =")], _ns)
LEFT, RIGHT = _ns["PINOUT"][38]

VIOLET, GREEN, RED, BLACK, GREY = "#7c3aed", "#16a34a", "#dc2626", "#111827", "#9ca3af"
W, H = 1300, 900
s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
     f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
add = s.append

def txt(x, y, t, size=10, fill="#111827", anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{t}</text>')

txt(24, 38, "Making the ESP32 boot without USB", 20, weight="bold")
txt(24, 60, "The CP2102 drives EN and IO0 on this DevKit. Unplug USB and it is "
            "unpowered — so the board never boots on its own.", 11, "#6b7280")

# ---- board ----
BX, BY, BW, PITCH = 470, 110, 200, 34
BH = PITCH * (len(LEFT) - 1) + 44
add(f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" rx="6" fill="#1f2937"/>')
add(f'<rect x="{BX+48}" y="{BY+18}" width="104" height="70" rx="3" fill="#9ca3af"/>')
txt(BX+BW/2, BY+60, "ESP-32", 13, "#1f2937", "middle", "bold")
txt(BX+BW/2, BY+BH-12, "USB-C ▼  (unplugged)", 9, "#9ca3af", "middle")

HOT = {"3V3": VIOLET, "EN": GREEN, "GPIO0": VIOLET, "5V": RED, "GND": BLACK}
hole = {}
for names, side in ((LEFT, "L"), (RIGHT, "R")):
    for i, name in enumerate(names):
        y = BY + 30 + i * PITCH
        px = BX - 9 if side == "L" else BX + BW + 9
        col = HOT.get(name, "#4b5563")
        hot = name in HOT
        add(f'<circle cx="{px}" cy="{y}" r="{7 if hot else 5}" fill="{col}"'
            + (' stroke="#ffffff" stroke-width="2"' if hot else '') + '/>')
        lx = px - 13 if side == "L" else px + 13
        txt(lx, y + 4, name, 10 if hot else 8,
            col if hot else "#9ca3af", "end" if side == "L" else "start",
            "bold" if hot else "normal")
        # first occurrence wins for GND; keep each side separately
        hole.setdefault((name, side), (px, y))

L3V3 = hole[("3V3", "L")]
LEN_ = hole[("EN", "L")]
LGND = hole[("GND", "L")]
RG0  = hole[("GPIO0", "R")]

# ---- fix 1: 10k from GPIO0 to 3V3 ----
def resistor(a, b, col, label):
    ax, ay = a; bx, by = b
    midx = (ax + bx) / 2
    add(f'<path d="M {ax} {ay} L {ax+60} {ay} L {ax+60} {(ay+by)/2} '
        f'L {bx-60} {(ay+by)/2} L {bx-60} {by} L {bx} {by}" stroke="{col}" '
        f'stroke-width="3.5" fill="none" stroke-linejoin="round"/>')
    add(f'<rect x="{midx-26}" y="{(ay+by)/2-9}" width="52" height="18" rx="3" '
        f'fill="#fef3c7" stroke="{col}" stroke-width="2"/>')
    txt(midx, (ay+by)/2 + 4, label, 8.5, "#78350f", "middle", "bold")

# route GPIO0 (right) around the bottom to 3V3 (top-left)
gx, gy = RG0; tx_, ty_ = L3V3
add(f'<path d="M {gx} {gy} L {gx+110} {gy} L {gx+110} {BY+BH+58} '
    f'L {tx_-150} {BY+BH+58} L {tx_-150} {ty_} L {tx_} {ty_}" '
    f'stroke="{VIOLET}" stroke-width="3.5" fill="none" stroke-linejoin="round"/>')
add(f'<rect x="{gx+84}" y="{BY+BH+49}" width="56" height="18" rx="3" '
    f'fill="#f5f3ff" stroke="{VIOLET}" stroke-width="2"/>')
txt(gx+112, BY+BH+62, "10 kΩ", 8.5, VIOLET, "middle", "bold")
for p in (RG0, L3V3):
    add(f'<circle cx="{p[0]}" cy="{p[1]}" r="4" fill="{VIOLET}"/>')

# ---- fix 2: capacitor EN -> GND ----
ex, ey = LEN_; nx, ny = LGND
capx = ex - 190
add(f'<path d="M {ex} {ey} L {capx} {ey} M {nx} {ny} L {capx} {ny} '
    f'M {capx} {ey} L {capx} {ey+34} M {capx} {ny} L {capx} {ny-34}" '
    f'stroke="{GREEN}" stroke-width="3.5" fill="none"/>')
cy_ = (ey + ny) / 2
add(f'<line x1="{capx-22}" y1="{cy_-8}" x2="{capx+22}" y2="{cy_-8}" stroke="{GREEN}" stroke-width="4"/>')
add(f'<line x1="{capx-22}" y1="{cy_+8}" x2="{capx+22}" y2="{cy_+8}" stroke="{GREEN}" stroke-width="4"/>')
txt(capx, cy_ - 20, "1–10 µF", 9.5, GREEN, "middle", "bold")
txt(capx, cy_ + 30, "stripe → GND", 8, GREEN, "middle")

# ---- notes ----
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"/>')
    txt(x+13, y+21, title, 10.5, tc, weight="bold")
    for i, l in enumerate(lines):
        txt(x+13, y+41+i*15, l, 8.8, tc)

box(880, 110, 396, 172, "#f5f3ff", "#c4b5fd", "1 — GPIO0 to 3V3 via 10 kΩ", [
    "GPIO0 selects boot mode. Held LOW at reset the chip",
    "enters DOWNLOAD mode: it waits for a flash command",
    "that never comes and never runs your firmware.",
    "",
    "With USB unplugged the CP2102 is unpowered, and the",
    "auto-reset transistors can hold IO0 low. This resistor",
    "keeps it high so the board boots normally.",
    "Use 10 kΩ, NOT a bare wire — a hard tie fights the",
    "auto-reset circuit next time you flash over USB.",
], "#4c1d95")

box(880, 298, 396, 140, "#f0fdf4", "#86efac", "2 — 1–10 µF from EN to GND", [
    "EN is the reset line. On USB the CP2102 pulses it, so",
    "the chip always starts cleanly. On any other supply it",
    "relies on a pull-up alone, and a slow-rising rail can",
    "leave the ESP32 half-started.",
    "",
    "This capacitor stretches the power-on reset.",
    "Striped (negative) leg to GND.",
], "#14532d")

box(880, 454, 396, 126, "#fef3c7", "#fbbf24", "IF THE SUPPLY ALSO SAGS", [
    "Add 100–470 µF across 5V and GND, close to the board.",
    "",
    "Only needed if the rail cannot hold the boot surge —",
    "the X1202 aux output could not; the Pi's 5V header,",
    "with real current behind it, should not need this.",
], "#78350f")

box(880, 596, 396, 158, "#fee2e2", "#fca5a5", "FINDING THE PINS", [
    "GPIO0   right column, 6th up from the USB-C end.",
    "        Neighbours: GPIO4 above, GPIO2 below.",
    "3V3     top pin of the LEFT column. Neighbour: EN.",
    "EN      second from the top, left column.",
    "GND     left column, 6th up from the USB-C end.",
    "",
    "Read the silkscreen. Do not count — 3V3 and 5V are",
    "end pins of the SAME column and counting has gone",
    "wrong before.",
], "#991b1b")

add('</svg>')
OUT = os.path.join(HERE, "standalone_fix.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote standalone_fix.svg")
