#!/usr/bin/env python3
"""Log the Hailo-8 chip temperature every --interval seconds while the app runs.

    my_hailo_env/bin/python results/panel-revisions/session-a/app-runs/hailo_temp_sampler.py \
        --duration 1800 --out <stem>-hailo-temp.csv [--delay 30]

Opens the device for a firmware control read (the call `hailortcli fw-control identify` also makes)
and closes it again for every sample, so no handle is held while hailort_service runs the networks.
Reads both on-die sensors (ts0, ts1). get_throttling_state() only says whether the chip's thermal
protection is ENABLED, not whether it is acting, so it is recorded once, at the start.
A failed read is written as a row with the error, never raised.
"""
import argparse
import csv
import sys
import time

from hailo_platform import Device


def read_once(device_id):
    with Device(device_id) as dev:
        t = dev.control.get_chip_temperature()
        return t.ts0_temperature, t.ts1_temperature


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before the first sample")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    time.sleep(args.delay)
    device_id = Device.scan()[0]
    try:
        with Device(device_id) as dev:
            protection = dev.control.get_throttling_state()
    except Exception as exc:  # recorded, not fatal
        protection = f"unreadable: {exc!r}"
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["unix_time", "ts0_c", "ts1_c", "note"])
        w.writerow([f"{time.time():.3f}", "", "", f"thermal protection enabled: {protection}"])
        fh.flush()
        start = time.monotonic()
        while time.monotonic() - start < args.duration:
            tick = time.monotonic()
            try:
                ts0, ts1 = read_once(device_id)
                w.writerow([f"{time.time():.3f}", f"{ts0:.2f}", f"{ts1:.2f}", ""])
            except Exception as exc:
                w.writerow([f"{time.time():.3f}", "", "", f"read failed: {exc!r}"])
            fh.flush()
            time.sleep(max(0.0, args.interval - (time.monotonic() - tick)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
