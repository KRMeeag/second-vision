"""ESP32 -> Raspberry Pi 5 link diagram (power from a Geekworm X1202 UPS).

The breadboard diagram (gen_wiring.py) stops at the ESP32. This one covers how
the panel reaches the Pi. Re-run after any change:

    python3 docs/gen_pi_link.py
"""

W, H = 1240, 840
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

GREEN, BLACK, RED = "#16a34a", "#111827", "#dc2626"

txt(24, 36, "Second Vision — ESP32 → Raspberry Pi 5 link", 20, weight="bold")
txt(24, 58, "Power from the X1202's XH2.54 header. Only ONE wire touches the Pi's GPIO header.",
    11, "#6b7280")

# ---------------- Pi 40-pin header ----------------
# Odd pins 1..39 in one row, even 2..40 in the other; pin 2 sits beside pin 1.
HX, HY, PITCH = 700, 190, 74
ODD_ROW  = ["3V3", "GPIO2", "GPIO3", "GPIO4", "GND"]
EVEN_ROW = [("5V", None), ("5V", None), ("GND", None),
            ("GPIO14 TXD", None), ("GPIO15 RXD", GREEN)]
EVEN_Y   = HY + 58

add(f'<rect x="{HX-38}" y="{HY-30}" width="{PITCH*4+76}" height="{EVEN_Y-HY+50}" '
    f'rx="8" fill="#111827"/>')
txt(HX-38, HY-44, "Pi 40-PIN HEADER   (first 10 pins)", 11, "#111827", weight="bold")

hole = {}
for i in range(5):
    x = HX + i * PITCH
    p_odd, p_even = 2*i + 1, 2*i + 2
    r = 2 if p_odd == 1 else 12          # pin 1 is square — the landmark
    add(f'<rect x="{x-12}" y="{HY-12}" width="24" height="24" rx="{r}" '
        f'fill="#4b5563" stroke="#1f2937"/>')
    txt(x, HY+5, str(p_odd), 10.5, "#e5e7eb", "middle", "bold")
    txt(x, HY-19, ODD_ROW[i], 8.5, "#cbd5e1", "middle")
    name, col = EVEN_ROW[i]
    ring = ' stroke="#ffffff" stroke-width="2.5"' if col else ' stroke="#1f2937"'
    add(f'<circle cx="{x}" cy="{EVEN_Y}" r="12" fill="{col or "#4b5563"}"{ring}/>')
    txt(x, EVEN_Y+5, str(p_even), 10.5, "#ffffff", "middle", "bold")
    txt(x, EVEN_Y-22, name, 8, "#ffffff" if col else "#cbd5e1", "middle",
        "bold" if col else "normal")
    hole[p_even] = (x, EVEN_Y)
txt(HX + PITCH*4 + 52, HY + 5,     "…", 20, "#9ca3af", "middle")
txt(HX + PITCH*4 + 52, EVEN_Y + 5, "…", 20, "#9ca3af", "middle")

NX, NY = 330, 92
add(f'<rect x="{NX}" y="{NY}" width="316" height="80" rx="6" fill="#fee2e2" stroke="#fca5a5"/>')
txt(NX+13, NY+21, "FIND PIN 1 BEFORE YOU COUNT", 9.5, "#991b1b", weight="bold")
txt(NX+13, NY+38, "It is the only SQUARE pad on the header.", 8.6, "#991b1b")
txt(NX+13, NY+53, "Nearest the USB-C power connector.", 8.6, "#991b1b")
txt(NX+13, NY+68, "Pin 2 sits beside pin 1, in the other row.", 8.6, "#991b1b")
add(f'<path d="M {NX+316} {NY+40} L {HX-52} {HY-6}" stroke="#fca5a5" '
    f'stroke-width="2" fill="none" stroke-dasharray="4 3"/>')

# ---------------- ESP32 ----------------
EX, EY, EW, EH = 110, 150, 190, 210
add(f'<rect x="{EX}" y="{EY}" width="{EW}" height="{EH}" rx="6" fill="#1f2937"/>')
add(f'<rect x="{EX+43}" y="{EY+18}" width="104" height="70" rx="3" fill="#9ca3af"/>')
txt(EX+95, EY+59, "ESP-32", 13, "#1f2937", "middle", "bold")

tx = (EX + EW, EY + 150)
add(f'<circle cx="{tx[0]}" cy="{tx[1]}" r="7.5" fill="{GREEN}"/>')
txt(tx[0]-14, tx[1]+3, "TX0 / GPIO1", 10, "#f9fafb", "end", "bold")
txt(tx[0]-14, tx[1]+17, "j15 on the breadboard", 8, "#9ca3af", "end")

vin = (EX + 62,  EY + EH)
gnd = (EX + 132, EY + EH)
for (px, py), lab, col in ((vin, "VIN", RED), (gnd, "GND", BLACK)):
    add(f'<circle cx="{px}" cy="{py}" r="7.5" fill="{col}" stroke="#ffffff" stroke-width="2"/>')
    txt(px, py-14, lab, 9, "#f9fafb", "middle", "bold")

# ---------------- X1202 ----------------
XX, XY, XW, XH_ = 110, 470, 190, 86
add(f'<rect x="{XX}" y="{XY}" width="{XW}" height="{XH_}" rx="6" fill="#334155"/>')
txt(XX+XW/2, XY+40, "X1202 UPS", 12, "#f9fafb", "middle", "bold")
txt(XX+XW/2, XY+58, "XH2.54 5V output", 8.5, "#cbd5e1", "middle")
txt(XX+XW/2, XY+104, "powers the Pi → this GND IS the Pi's GND", 8.5, "#6b7280", "middle")
x5v  = (XX + 62,  XY)
xgnd = (XX + 132, XY)
for (px, py), lab, col in ((x5v, "5V", RED), (xgnd, "GND", BLACK)):
    add(f'<circle cx="{px}" cy="{py}" r="7.5" fill="{col}" stroke="#ffffff" stroke-width="2"/>')
    txt(px, py+22, lab, 9, "#f9fafb", "middle", "bold")

# ---------------- wires ----------------
wire([x5v,  vin],  RED)                                    # power pair, straight up
wire([xgnd, gnd],  BLACK)
wire([tx, (460, tx[1]), (460, 420), (hole[10][0], 420),    # the only Pi-header wire
      hole[10]], GREEN)

# ---------------- table ----------------
TY = 600
txt(24, TY, "THE WIRES", 13, weight="bold")
rows = [
 ("X1202  5V",   "ESP32 VIN",  RED,   "power in — never into a Pi GPIO"),
 ("X1202  GND",  "ESP32 GND",  BLACK, "also the Pi's ground: same node, shared reference"),
 ("ESP32  TX0 / GPIO1", "Pi pin 10  (GPIO15 · RXD)", GREEN, "panel data → Pi"),
]
yy = TY + 22
for h, x in (("FROM", 40), ("TO", 250), ("why", 520)):
    txt(x, yy, h, 9, "#9ca3af", weight="bold")
yy += 4
for a, b, col, why in rows:
    yy += 28
    add(f'<line x1="24" y1="{yy-18}" x2="{W-24}" y2="{yy-18}" stroke="#e5e7eb"/>')
    add(f'<rect x="24" y="{yy-10}" width="11" height="11" rx="2" fill="{col}"/>')
    txt(40, yy, a, 10.5, weight="bold")
    txt(250, yy, b, 10.5, weight="bold")
    txt(520, yy, why, 9, "#6b7280")

# ---------------- notes ----------------
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"/>')
    txt(x+13, y+21, title, 10, tc, weight="bold")
    for i, l in enumerate(lines):
        txt(x+13, y+40+i*14, l, 8.4, tc)

BY, BH = 720, 106
box(24, BY, 388, BH, "#fee2e2", "#fca5a5", "THIS KILLS PINS", [
    "• The X1202's 5V goes to ESP32 VIN and NOWHERE else.",
    "   Pi GPIO is 3.3 V and not 5 V tolerant.",
    "• Never into the ESP32's 3V3 pin either — that bypasses",
    "   its regulator and puts 5 V on the module.",
    "• Pin 8 is the Pi's TXD: land there and you get silence.",
], "#991b1b")

box(428, BY, 388, BH, "#dbeafe", "#93c5fd", "WHY ONE WIRE TO THE Pi", [
    "The X1202 feeds the Pi, so its GND and the Pi's GND are",
    "electrically one node. Grounding the ESP32 at the X1202",
    "already references it to the Pi — no separate ground wire",
    "to pin 6 is needed. Adding one is harmless (same node) but",
    "buys nothing at 9600 baud over this distance.",
], "#1e3a8a")

box(832, BY, 384, BH, "#fef3c7", "#fbbf24", "WHEN FLASHING OVER USB", [
    "Unplug the X1202 5V from VIN first. Devkits tolerate USB",
    "and VIN together via a diode, but powering from both is",
    "not worth the risk for the sake of one connector.",
    "",
    "The signal wire can stay connected throughout.",
], "#78350f")

add('</svg>')
open(__file__.rsplit("/", 1)[0] + "/pi_link_wiring.svg", "w").write("\n".join(s))
print("wrote pi_link_wiring.svg")
