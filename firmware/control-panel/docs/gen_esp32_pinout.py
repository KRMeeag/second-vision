"""Complete ESP32-WROOM-32 (38-pin DevKit) pinout for the Second Vision panel.

Every pin, what it is for, and what must never be wired. The pin data is read
out of gen_wiring.py rather than copied, so the two drawings cannot drift.

    python3 docs/gen_esp32_pinout.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Single source of truth: pull PINOUT straight from the breadboard generator.
_src = open(os.path.join(HERE, "gen_wiring.py"), encoding="utf-8").read()
_ns = {}
exec(_src[_src.index("PINOUT = {"):_src.index("LEFT, RIGHT =")], _ns)
LEFT, RIGHT = _ns["PINOUT"][38]
assert len(LEFT) == len(RIGHT) == 19, "38-pin board = 19 per side"

# role -> (colour, label). Anything unlisted is a free GPIO.
POWER, GND, DANGER = "#dc2626", "#111827", "#7f1d1d"
DETECT, DEPTH, STATUS, POT, LINK = "#16a34a", "#0d9488", "#ca8a04", "#7c3aed", "#2563eb"

ROLE = {
    "5V":     (POWER,  "POWER IN  ← X1202 5 V"),
    "3V3":    (POWER,  "3.3 V OUT → pot only"),
    "GPIO23": (LINK,   "→ Pi pin 21  (UART2 TX)"),
    "GPIO17": (DETECT, "DETECT rocker → GND"),
    "GPIO18": (DEPTH,  "DEPTH rocker → GND"),
    "GPIO22": (STATUS, "STATUS button → GND"),
    "GPIO34": (POT,    "pot wiper (input only)"),
}
AVOID = {
    "GPIO0":  "bootstrap — held low = flash mode",
    "GPIO1":  "UART0 TX — the USB serial",
    "GPIO3":  "UART0 RX — the USB serial",
    "GPIO6":  "SPI flash", "GPIO7": "SPI flash", "GPIO8": "SPI flash",
    "GPIO9":  "SPI flash", "GPIO10": "SPI flash", "GPIO11": "SPI flash",
}
INPUT_ONLY = {"GPIO34", "GPIO35", "GPIO36", "GPIO39"}

W, H = 1300, 1000
s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
     f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
add = s.append

def txt(x, y, t, size=10, fill="#111827", anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{t}</text>')

txt(24, 38, "ESP32-WROOM-32 — 38-pin DevKit — Second Vision control panel",
    20, weight="bold")
txt(24, 60, "Board oriented with the USB-C connector at the BOTTOM, as it sits "
            "on the breadboard.", 11, "#6b7280")

BX, BY, BW = 400, 108, 250          # board body
PITCH = 40
rows = len(LEFT)
BH = PITCH * (rows - 1) + 46

add(f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" rx="6" fill="#1f2937"/>')
add(f'<rect x="{BX+56}" y="{BY+18}" width="138" height="96" rx="3" fill="#9ca3af"/>')
txt(BX+BW/2, BY+72, "ESP-32", 15, "#1f2937", "middle", "bold")
txt(BX+BW/2, BY+BH-14, "USB-C  ▼", 10, "#9ca3af", "middle")

def draw(names, side):
    for i, name in enumerate(names):
        y = BY + 30 + i * PITCH
        role = ROLE.get(name)
        if name == "GND":
            col, note = GND, "ground"
        elif role:
            col, note = role
        elif name in AVOID:
            col, note = DANGER, AVOID[name]
        else:
            col, note = "#9ca3af", "free" + (" · input only" if name in INPUT_ONLY else "")

        px = BX - 10 if side == "L" else BX + BW + 10
        add(f'<circle cx="{px}" cy="{y}" r="6" fill="{col}"/>')
        lx = px - 14 if side == "L" else px + 14
        anchor = "end" if side == "L" else "start"
        strong = bool(role) or name in AVOID or name == "GND"
        txt(lx, y - 1, name, 11, col if strong else "#6b7280", anchor,
            "bold" if strong else "normal")
        txt(lx, y + 12, note, 8, col if strong else "#9ca3af", anchor)

draw(LEFT, "L")
draw(RIGHT, "R")

# ---- legend ----
def box(x, y, w, h, fill, stroke, title, lines, tc):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"/>')
    txt(x+13, y+21, title, 10.5, tc, weight="bold")
    for i, l in enumerate(lines):
        txt(x+13, y+41+i*15, l, 8.6, tc)

LY = BY
box(880, LY, 396, 200, "#f9fafb", "#e5e7eb", "WHAT IS WIRED", [
    "5V      ← X1202 5 V, via the protoboard 5V rail",
    "GND     ← X1202 GND, via the GND rail",
    "3V3     → the potentiometer ONLY. This is an OUTPUT:",
    "          the ESP32's own regulator. Never feed 5 V in.",
    "GPIO17  DETECT rocker → GND   (INPUT_PULLUP, active low)",
    "GPIO18  DEPTH rocker  → GND   (INPUT_PULLUP, active low)",
    "GPIO22  STATUS button → GND   (momentary)",
    "GPIO34  pot wiper. Input only, no internal pull-up,",
    "          on ADC1 so it survives WiFi being enabled.",
    "GPIO23  → Pi header pin 21. UART2 TX, panel → Pi.",
], "#111827")

box(880, LY+216, 396, 132, "#fee2e2", "#fca5a5", "NEVER WIRE THESE", [
    "GPIO6-11   SPI flash. Touching these bricks the boot.",
    "GPIO0      bootstrap: held low at reset = flash mode.",
    "GPIO1/3    UART0 — the USB serial. The ROM bootloader",
    "           dumps its 115200 log here on every reset,",
    "           which is why the Pi link is GPIO23 instead.",
], "#991b1b")

box(880, LY+364, 396, 148, "#fef3c7", "#fbbf24", "PIN-CHOICE SAFETY", [
    "GPIO17 and GPIO18 have plain GPIOs either side, so a",
    "jumper one hole off reads wrong instead of shorting.",
    "",
    "GPIO23 takes the single soldered link wire and no switch:",
    "it sits one row from the GND pin, on a row carrying 3V3",
    "on the opposite side. A switch leg can bridge two rows;",
    "one wire cannot.",
], "#78350f")

box(880, LY+528, 396, 132, "#dbeafe", "#93c5fd", "INPUT-ONLY PINS", [
    "GPIO34 / 35 / 36 / 39 are INPUT ONLY and have no",
    "internal pull-up or pull-down.",
    "",
    "Correct for the pot, which drives the pin itself. Wrong",
    "for a switch, which needs a pull-up to define its",
    "released state — a switch here would float.",
], "#1e3a8a")

add('</svg>')

OUT = os.path.join(HERE, "esp32_pinout.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote esp32_pinout.svg")
