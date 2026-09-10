#!/usr/bin/env python3
"""
Second Vision — control-panel protocol monitor and validator (PROTOCOL v1).

Runs anywhere pyserial does. No Hailo, no GStreamer, no repo checkout — it is
self-contained inside this folder on purpose, so the panel can be exercised on a
laptop with nothing but a USB cable when the Raspberry Pi is not to hand.

    pip install pyserial
    python3 panel_monitor.py COM7              # Windows
    python3 panel_monitor.py /dev/ttyUSB0      # Linux laptop, USB
    python3 panel_monitor.py /dev/serial0      # on the Pi, over the GPIO link

    --raw       also print every line exactly as received, garbage included
    --baud N    override 9600 (you should not need this)

It does not merely tolerate the protocol, it CHECKS it, because the point is to
tell whether the firmware is right — not to be lenient about it:

  * V:  version present at boot, and equal to 1
  * M:  arrives LAST in a burst (it is the commit marker)
  * S:  keys are known, values are in range and correctly formatted
  * B:alive within the watchdog window (protocol §2.6.5)
  * mode agrees with the two rocker flags that preceded it

Exit with Ctrl-C; a summary of every problem seen is printed on the way out.
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required:  pip install pyserial")

PROTOCOL_VERSION = 1
BAUD = 9600
ALIVE_TIMEOUT = 30.0          # §2.6.5: no B:alive for >30 s is a fault
MAX_LINE = 64

BOOL_KEYS = {"tts_enabled", "vibration_enabled"}
FLOAT_KEYS = {"motor_strength"}
VALID_MODES = {"detection", "depth", "both", "none"}

# Never let an encoding fault kill the monitor mid-session: a Windows console
# defaults to cp1252, which cannot encode the box-drawing and arrow glyphs, and
# the traceback took the summary down with it.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):  # pragma: no cover — very old interpreters
    pass


def _encodable(text):
    """True if this console can actually render `text`."""
    try:
        text.encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


# Prefer the nicer glyphs, fall back to ASCII on a console that cannot encode
# them. `errors="replace"` above stops a crash either way; this stops the output
# turning into a field of question marks on cp1252.
_UNICODE_OK = _encodable("→─✗")
ARROW = "→" if _UNICODE_OK else ">"
RULE  = "──" if _UNICODE_OK else "--"
CROSS = "✗" if _UNICODE_OK else "x"
BULLET = "•" if _UNICODE_OK else "-"

# ANSI colour is meaningless when piped to a file, and legacy consoles print the
# escapes literally.
_COLOR = sys.stdout.isatty()
RESET, BOLD, DIM = ("\033[0m", "\033[1m", "\033[2m") if _COLOR else ("", "", "")
RED, GREEN, YELLOW, CYAN = (
    ("\033[31m", "\033[32m", "\033[33m", "\033[36m") if _COLOR else ("", "", "", ""))


class Monitor:
    def __init__(self, raw=False):
        self.raw = raw
        self.state = {}           # the SystemConfig the Pi would end up with
        self.mode = None
        self.version = None
        self.problems = []
        # Armed at construction, NOT left None until the first beat. A panel
        # that never heartbeats at all is the failure most worth catching — and
        # is exactly what broken B:alive looks like — so the first window has to
        # count from when we opened the port.
        self.last_alive = time.monotonic()
        self.ever_alive = False
        self.pending = []         # S: lines since the last commit marker
        self.counts = {"S": 0, "M": 0, "B": 0, "V": 0, "garbage": 0}

    def problem(self, msg):
        self.problems.append(msg)
        print(f"  {RED}{CROSS} {msg}{RESET}")

    # ---- the four line types ------------------------------------------

    def on_version(self, body):
        self.counts["V"] += 1
        parts = body.split(":")
        if len(parts) != 2 or parts[0] != "panel":
            return self.problem(f"malformed version line: V:{body}")
        try:
            v = int(parts[1])
        except ValueError:
            return self.problem(f"non-numeric version: V:{body}")
        self.version = v
        if v != PROTOCOL_VERSION:
            self.problem(f"protocol version {v}, this monitor speaks "
                         f"{PROTOCOL_VERSION} — expect disagreement")
        else:
            print(f"  {GREEN}panel booted, protocol v{v}{RESET}")

    def on_setting(self, body):
        self.counts["S"] += 1
        parts = body.split(":")
        if len(parts) != 2:
            return self.problem(f"malformed setting: S:{body}")
        key, raw = parts

        if key in BOOL_KEYS:
            if raw not in ("0", "1"):
                return self.problem(f"{key} should be 0 or 1, got {raw!r}")
            value = raw == "1"
        elif key in FLOAT_KEYS:
            # §2.2 says always two decimals — a bare "1" would still parse as a
            # float, so check the format rather than only the value.
            if len(raw.split(".")[-1]) != 2 or "." not in raw:
                self.problem(f"{key} must have exactly 2 decimals, got {raw!r}")
            try:
                value = float(raw)
            except ValueError:
                return self.problem(f"{key} is not a number: {raw!r}")
            if not 0.0 <= value <= 1.0:
                self.problem(f"{key} out of range 0.00-1.00: {value}")
        else:
            # §2.6.2 — a future panel may send keys this version does not know.
            # Not an error; worth showing so a typo does not hide as one.
            print(f"  {YELLOW}? unknown setting {key}={raw} (ignored, per "
                  f"§2.6.2){RESET}")
            return

        prev = self.state.get(key)
        self.state[key] = value
        self.pending.append(key)
        marker = " " if prev == value else f"{CYAN}{ARROW}{RESET}"
        print(f"  {marker} {key} = {value}")

    def on_mode(self, body):
        self.counts["M"] += 1
        if body not in VALID_MODES:
            # §2.6.3 — ignore and hold the last known-good mode.
            return self.problem(f"unknown mode {body!r} (holding {self.mode!r})")

        # M: is the commit marker: whatever preceded it in this burst is current.
        expected = self._mode_from_flags()
        if expected and body != expected:
            self.problem(f"mode {body!r} disagrees with the flags in this burst "
                         f"(tts/vibration imply {expected!r})")

        changed = body != self.mode
        self.mode = body
        self.pending.clear()
        tag = f"{CYAN}{ARROW}{RESET}" if changed else " "
        note = "" if changed else f"  {DIM}(unchanged — no rebuild){RESET}"
        print(f"  {tag} MODE = {BOLD}{body}{RESET}{note}")

    def on_event(self, body):
        self.counts["B"] += 1
        if body == "alive":
            gap = time.monotonic() - self.last_alive if self.ever_alive else None
            self.last_alive = time.monotonic()
            self.ever_alive = True
            g = "" if gap is None else f"  {DIM}(+{gap:.1f}s){RESET}"
            print(f"  {DIM}heartbeat{RESET}{g}")
        elif body == "status":
            print(f"  {BOLD}STATUS pressed{RESET} — Pi would speak the full config")
        else:
            print(f"  {YELLOW}? unknown event B:{body}{RESET}")

    def _mode_from_flags(self):
        """The mode the two rocker flags imply, if we have both."""
        det, dep = self.state.get("tts_enabled"), self.state.get("vibration_enabled")
        if det is None or dep is None:
            return None
        return ("both" if det and dep else
                "detection" if det else
                "depth" if dep else "none")

    # ---- dispatch ------------------------------------------------------

    def feed(self, line):
        if self.raw:
            print(f"{DIM}{line!r}{RESET}")
        if not line:
            return
        if len(line) > MAX_LINE:
            self.problem(f"line exceeds {MAX_LINE} bytes ({len(line)})")

        kind, _, body = line.partition(":")
        if not body:
            # Not a protocol line at all. The ESP32 ROM bootloader emits its log
            # at 115200 on every reset; at 9600 that arrives as mojibake. §2.6.1
            # requires discarding it silently — count it so it is visible, but
            # never treat it as an error.
            self.counts["garbage"] += 1
            return
        handler = {"V": self.on_version, "S": self.on_setting,
                   "M": self.on_mode, "B": self.on_event}.get(kind)
        if handler:
            handler(body)
        else:
            self.counts["garbage"] += 1

    def check_watchdog(self):
        if time.monotonic() - self.last_alive > ALIVE_TIMEOUT:
            what = ("no B:alive for >%.0fs — panel fault (§2.6.5)" % ALIVE_TIMEOUT
                    if self.ever_alive else
                    "no B:alive in the first %.0fs — panel is not heartbeating "
                    "at all (§2.6.5)" % ALIVE_TIMEOUT)
            self.problem(what)
            self.last_alive = time.monotonic()   # report once per window

    def summary(self):
        print(f"\n{BOLD}{RULE} summary {RULE}{RESET}")
        print(f"  protocol version : {self.version}")
        print(f"  final mode       : {self.mode}")
        for k in sorted(self.state):
            print(f"  {k:<17}: {self.state[k]}")
        c = self.counts
        print(f"  lines            : S={c['S']} M={c['M']} B={c['B']} "
              f"V={c['V']}  discarded={c['garbage']}")
        if self.problems:
            print(f"\n  {RED}{len(self.problems)} problem(s):{RESET}")
            for p in self.problems:
                print(f"    {RED}{BULLET}{RESET} {p}")
        else:
            print(f"\n  {GREEN}no protocol violations seen{RESET}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="serial device, e.g. COM7 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--raw", action="store_true",
                    help="also print every line exactly as received")
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=1.0)
    except Exception as exc:
        sys.exit(f"could not open {args.port}: {exc}")

    print(f"{BOLD}listening on {args.port} @ {args.baud}{RESET} — flip a rocker, "
          f"turn the knob, press STATUS.  Ctrl-C to stop.\n")
    mon = Monitor(raw=args.raw)
    try:
        while True:
            raw = port.readline()
            if raw:
                mon.feed(raw.decode(errors="ignore").strip())
            mon.check_watchdog()
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        mon.summary()


if __name__ == "__main__":
    main()
