#!/usr/bin/env python3
"""Sample the Pi 5's 5 V input rail and throttle flags while something else runs.

Attributes a "Low Voltage" warning to a load: run it in one terminal, then in
another bring loads up one at a time (camera only, app, app + motors ...) and
watch EXT5V_V. The PMIC warns below 4.65 V and the firmware throttles there.

    sudo python3 scripts/power_monitor.py            # print every 200 ms
    sudo python3 scripts/power_monitor.py --csv log.csv --interval 0.1

Columns: time, EXT5V_V (the 5 V arriving at the PMIC), VDD_CORE_A (SoC
current, a proxy for CPU/NPU load), throttled (raw hex), and a decoded note.
Needs root or the `video` group for vcgencmd.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

# Bits from `vcgencmd get_throttled` (Raspberry Pi firmware documentation).
NOW = {0: "UNDERVOLT", 1: "FREQ_CAP", 2: "THROTTLED", 3: "SOFT_TEMP"}
PAST = {16: "undervolt-occurred", 17: "freqcap-occurred", 18: "throttled-occurred", 19: "softtemp-occurred"}

LOW_VOLTAGE_V = 4.65


def vcgencmd(*args: str) -> str:
    """Run one vcgencmd and return its stdout stripped."""
    return subprocess.run(["vcgencmd", *args], capture_output=True, text=True, check=True).stdout.strip()


def read_adc() -> dict[str, float]:
    """Return every PMIC ADC channel as {name: value}."""
    out: dict[str, float] = {}
    for line in vcgencmd("pmic_read_adc").splitlines():
        m = re.match(r"\s*(\S+)\s+(?:volt|current)\(\d+\)=([0-9.]+)[AV]", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def read_throttled() -> int:
    """Return the raw throttled bitfield."""
    return int(vcgencmd("get_throttled").split("=")[1], 16)


def decode(flags: int) -> str:
    """Human-readable throttle flags, current state first."""
    now = [n for b, n in NOW.items() if flags & (1 << b)]
    past = [n for b, n in PAST.items() if flags & (1 << b)]
    return " ".join(now + past) or "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=0.2, help="seconds between samples (default 0.2)")
    ap.add_argument("--csv", help="also append samples to this CSV file")
    ap.add_argument("--duration", type=float, default=0, help="stop after this many seconds (default: run until Ctrl-C)")
    args = ap.parse_args()

    try:
        vcgencmd("get_throttled")
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"vcgencmd not usable ({e}); run with sudo or add yourself to the video group", file=sys.stderr)
        return 1

    csv = open(args.csv, "a") if args.csv else None
    if csv and csv.tell() == 0:
        csv.write("t,ext5v_v,vdd_core_a,throttled,note\n")

    start = time.monotonic()
    vmin = 99.0
    print(f"{'t':>7} {'EXT5V':>6} {'CORE_A':>6} {'throttled':>10}  note")
    try:
        while True:
            t = time.monotonic() - start
            adc = read_adc()
            flags = read_throttled()
            v = adc.get("EXT5V_V", float("nan"))
            core = adc.get("VDD_CORE_A", float("nan"))
            vmin = min(vmin, v)
            note = decode(flags)
            if v < LOW_VOLTAGE_V:
                note = f"** {v:.2f} V < {LOW_VOLTAGE_V} V ** " + note
            print(f"{t:7.1f} {v:6.3f} {core:6.2f} {flags:#10x}  {note}")
            if csv:
                csv.write(f"{t:.2f},{v:.4f},{core:.4f},{flags:#x},{note}\n")
                csv.flush()
            if args.duration and t >= args.duration:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\nlowest EXT5V seen: {vmin:.3f} V   final flags: {decode(read_throttled())}")
        if csv:
            csv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
