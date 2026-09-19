"""ESP32 control panel -> Raspberry Pi 5 UART link, and header pin allocation.

The breadboard diagram (gen_wiring.py) stops at the ESP32. This one covers the
single wire that carries the panel's output to the Pi, and which header pins are
already claimed by other parts of the project. Re-run after any change:

    python3 docs/gen_pi_link.py
"""

import os

W, H = 1240, 1010
s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
     f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
add = s.append

def txt(x, y, t, size=10, fill="#111827", anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{t}</text>')

def wire(pts, color, width=4.5):
    d = " L ".join(f"{x} {y}" for x, y in pts)
    add(f'<path d="M {d}" stroke="{color}" stroke-width="{width}" fill="none" '
        f'stroke-linecap="round" stroke-linejoin="round"/>')
    for cx, cy in (pts[0], pts[-1]):
        add(f'<circle cx="{cx}" cy="{cy}" r="5.5" fill="{color}"/>')

GREEN, BLACK, RED, AMBER, GREY = "#16a34a", "#111827", "#dc2626", "#d97706", "#6b7280"

txt(24, 36, "Second Vision — control panel → Raspberry Pi 5", 20, weight="bold")
txt(24, 58, "ONE wire. The panel only transmits, and ground is already common "
            "through the X1202.", 11, "#6b7280")

# ---------------- ESP32 + X1202 ----------------
EX, EY, EW, EH = 60, 96, 180, 180
add(f'<rect x="{EX}" y="{EY}" width="{EW}" height="{EH}" rx="6" fill="#1f2937"/>')
add(f'<rect x="{EX+38}" y="{EY+16}" width="104" height="60" rx="3" fill="#9ca3af"/>')
txt(EX+90, EY+52, "ESP-32", 13, "#1f2937", "middle", "bold")
txt(EX+90, EY+96, "control panel", 9, "#cbd5e1", "middle")

tx = (EX + EW, EY + 132)
add(f'<circle cx="{tx[0]}" cy="{tx[1]}" r="7.5" fill="{GREEN}"/>')
txt(tx[0]-14, tx[1]+3, "GPIO23", 10, "#f9fafb", "end", "bold")
txt(tx[0]-14, tx[1]+17, "UART2 TX — j13", 8, "#9ca3af", "end")

vin, gnd = (EX + 58, EY + EH), (EX + 122, EY + EH)
for (px, py), lab, col in ((vin, "5V", RED), (gnd, "GND", BLACK)):
    add(f'<circle cx="{px}" cy="{py}" r="7" fill="{col}" stroke="#ffffff" stroke-width="2"/>')
    txt(px, py-13, lab, 8.5, "#f9fafb", "middle", "bold")
txt(vin[0], vin[1]-26, "(VIN on 30-pin)", 6.5, "#9ca3af", "middle")

XY = EY + EH + 92
add(f'<rect x="{EX}" y="{XY}" width="{EW}" height="74" rx="6" fill="#334155"/>')
txt(EX+EW/2, XY+30, "X1202 UPS", 12, "#f9fafb", "middle", "bold")
txt(EX+EW/2, XY+46, "XH2.54 5V out", 8.5, "#cbd5e1", "middle")
txt(EX+EW/2, XY+60, "\u2192 protoboard rails", 8, "#9ca3af", "middle")
txt(EX+EW/2, XY+92, "powers the Pi → this GND", 8, "#6b7280", "middle")
txt(EX+EW/2, XY+104, "IS the Pi's GND", 8, "#6b7280", "middle", "bold")
for (px, lab, col) in ((EX+58, "5V", RED), (EX+122, "GND", BLACK)):
    add(f'<circle cx="{px}" cy="{XY}" r="7" fill="{col}" stroke="#ffffff" stroke-width="2"/>')
    txt(px, XY+22, lab, 8.5, "#f9fafb", "middle", "bold")
wire([(EX+58, XY), (vin[0], vin[1])], RED)
wire([(EX+122, XY), (gnd[0], gnd[1])], BLACK)

# ---------------- the 40-pin header ----------------
# All 40 are drawn because the panel's pin is 21 — counting from pin 1 is the
# only reliable way to find it, and the map doubles as the project's pin
# allocation so two people cannot claim the same pin.
HX, HY, PITCH = 138, 560, 54
EVEN_Y = HY + 46

CLAIMED = {
    21: (GREEN, "GPIO9",  "PANEL"),
    8:  (AMBER, "GPIO14", "motor"),
    10: (AMBER, "GPIO15", "motor"),
    6:  (AMBER, "GND",    "motor"),
    3:  (GREY,  "GPIO2",  "I²C"),
    5:  (GREY,  "GPIO3",  "I²C"),
}

add(f'<rect x="{HX-30}" y="{HY-24}" width="{PITCH*19+60}" height="{EVEN_Y-HY+48}" '
    f'rx="8" fill="#111827"/>')
txt(HX-30, HY-64, "Pi 5 40-PIN HEADER — who owns what", 12, "#111827", weight="bold")

hole = {}
for i in range(20):
    x = HX + i * PITCH
    for pin, y in ((2*i+1, HY), (2*i+2, EVEN_Y)):
        info = CLAIMED.get(pin)
        col = info[0] if info else "#4b5563"
        ring = ' stroke="#ffffff" stroke-width="2.5"' if info else ' stroke="#1f2937"'
        if pin == 1:
            add(f'<rect x="{x-11}" y="{y-11}" width="22" height="22" rx="2" '
                f'fill="{col}" stroke="#ffffff" stroke-width="2"/>')
        else:
            add(f'<circle cx="{x}" cy="{y}" r="11" fill="{col}"{ring}/>')
        txt(x, y+4, str(pin), 9.5, "#ffffff", "middle", "bold")
        if info:
            _, name, owner = info
            ly = HY - 34 if y == HY else EVEN_Y + 42
            txt(x, ly, name, 7.5, col, "middle", "bold")
            txt(x, ly + (-11 if y == HY else 11), owner, 7, col, "middle")
        hole[pin] = (x, y)

txt(HX-30, EVEN_Y+82, "PIN 1 is the only SQUARE pad — find it, then count.",
    9.5, "#991b1b", weight="bold")

# ---------------- the one wire ----------------
px, py = hole[21]
wire([tx, (tx[0]+70, tx[1]), (tx[0]+70, HY-110), (px, HY-110), (px, py)], GREEN)

# ---------------- table ----------------
TY = 742
txt(24, TY, "THE WIRES", 13, weight="bold")
rows = [
 ("X1202  5V",   "ESP32 5V pin",           RED,   "38-pin board. Labelled VIN on 30-pin boards"),
 ("X1202  GND",  "ESP32 GND",              BLACK, "also the Pi's ground: same node"),
 ("ESP32  GPIO23", "Pi PIN 21  (GPIO9 · uart3-pi5 RX)", GREEN,
  "panel data → Pi.  NOT pin 10 — that is the motor link"),
]
yy = TY + 22
for h, x in (("FROM", 40), ("TO", 250), ("why", 560)):
    txt(x, yy, h, 9, "#9ca3af", weight="bold")
yy += 4
for a, b, col, why in rows:
    yy += 28
    add(f'<line x1="24" y1="{yy-18}" x2="{W-24}" y2="{yy-18}" stroke="#e5e7eb"/>')
    add(f'<rect x="24" y="{yy-10}" width="11" height="11" rx="2" fill="{col}"/>')
    txt(40, yy, a, 10.5, weight="bold")
    txt(250, yy, b, 10.5, weight="bold")
    txt(560, yy, why, 9, "#6b7280")

# ---------------- notes ----------------
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"/>')
    txt(x+13, y+21, title, 10, tc, weight="bold")
    for i, l in enumerate(lines):
        txt(x+13, y+40+i*14, l, 8.4, tc)

BY, BH = 866, 116
box(24, BY, 388, BH, "#fee2e2", "#fca5a5", "THIS KILLS THE BOARD", [
    "• CHECK POLARITY with a meter before connecting the",
    "   XH2.54. Reversed 5 V and GND destroys the ESP32.",
    "• 5 V to the ESP32's 5V pin ONLY. Never its 3V3 pin (that",
    "   bypasses the regulator), never a Pi GPIO (3.3 V only).",
    "• TWO separate protoboard rails: 5V in, and 3V3 out of the",
    "   ESP32 feeding the pot. NEVER join them.",
], "#991b1b")

box(428, BY, 388, BH, "#dbeafe", "#93c5fd", "WHY GPIO23, NOT GPIO1", [
    "GPIO1 is UART0 TX — the ESP32's USB serial. The ROM",
    "bootloader dumps its 115200 log there on EVERY reset,",
    "which would land straight in the Pi's parser.",
    "",
    "GPIO23 is UART2: the Pi never sees it, and USB stays free",
    "for flashing and monitoring at the same time.",
], "#1e3a8a")

box(832, BY, 384, BH, "#fef3c7", "#fbbf24", "WHY NOT PINS 8/10", [
    "The primary UART (/dev/serial0) is taken by the",
    "motor-controller ESP32-C3 on pins 6/8/10.",
    "",
    "The panel gets uart3-pi5 instead — add",
    "dtoverlay=uart3-pi5 to config.txt, then reboot.",
    "GPIO9 is also SPI0 MISO: free only while SPI stays off.",
], "#78350f")

add('</svg>')

# Path and encoding both matter here, and both have broken on Windows before.
# Keep os.path and the explicit encoding; do not "simplify" them away.
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pi_link_wiring.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote pi_link_wiring.svg")
