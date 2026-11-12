#!/usr/bin/env python3
"""Round trip of the glasses board's acknowledgement, measured directly (the app must NOT be running).

    my_hailo_env/bin/python results/panel-revisions/session-b/ack_probe.py --count 200 --out <csv>

Sends the app's own heartbeat packet (serial_worker._pack_heartbeat(): 0xAA 0xFE 0xFE), which keeps the
board's watchdog fed and does not move the motors, and times the reply 0xAA 0xFF <type> with a blocking
reader. The time is from just before write() to the read that completes the ACK, on time.perf_counter():
the Pi's UART transmit, the board's handling and reply, and the Pi's UART receive, all included.
Other bytes the board streams (BMI160 telemetry 0x02, turn events 0x03) are skipped by scanning for the
0xAA 0xFF pair, as serial_worker._check_ack() does. One packet at a time, 50 ms apart.
"""
import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from second_vision.workers.serial_worker import _pack_heartbeat  # noqa: E402

ACK = bytes([0xAA, 0xFF])


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--timeout", type=float, default=0.2, help="seconds to wait for each reply")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    packet = _pack_heartbeat()
    rtts, lost = [], 0
    with serial.Serial(args.port, baudrate=args.baud, timeout=0.001, write_timeout=0.05) as port, \
            open(args.out, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["n", "rtt_ms", "reply_type"])
        time.sleep(0.2)
        port.reset_input_buffer()
        for n in range(args.count):
            port.reset_input_buffer()
            buf = bytearray()
            t0 = time.perf_counter()
            port.write(packet)
            deadline = t0 + args.timeout
            got = None
            while time.perf_counter() < deadline:
                buf += port.read(1)
                i = buf.find(ACK)
                if i >= 0 and len(buf) >= i + 3:
                    got = (time.perf_counter() - t0) * 1000, buf[i + 2]
                    break
            if got is None:
                lost += 1
                w.writerow([n, "", "none within timeout"])
            else:
                rtts.append(got[0])
                w.writerow([n, f"{got[0]:.3f}", f"0x{got[1]:02X}"])
            time.sleep(0.05)
    summary = (f"heartbeats sent: {args.count}; replies: {len(rtts)}; none within {1000 * args.timeout:.0f} ms: {lost}\n"
               + (f"round trip (Pi write -> ACK read): median {statistics.median(rtts):.2f} ms, "
                  f"p95 {pct(rtts, 95):.2f}, p99 {pct(rtts, 99):.2f}, max {max(rtts):.2f}, min {min(rtts):.2f}\n"
                  if rtts else "no replies\n"))
    Path(args.out).with_suffix(".txt").write_text(f"ack_probe {args.port} {args.baud} baud, {time.strftime('%F %T %Z')}\n" + summary)
    print(summary, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
