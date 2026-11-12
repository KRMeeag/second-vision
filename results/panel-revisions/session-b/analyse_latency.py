#!/usr/bin/env python3
"""Summarise one run's latency log (SV_LATENCY_LOG=1): capture -> motor packet, and capture -> speech.

    python3 -I results/panel-revisions/session-b/analyse_latency.py <run folder>

<run folder> holds latency/{haptic,audio}.csv and clock.txt (core/latency_log.py) and perf-<label>-app.log
(perf_run.py, every line prefixed with a Unix timestamp). As for the frame-rate figures, the first 60 s
after the first [DEPTH] line are left out. Every figure is given twice: all rows, and rows whose frame
was captured outside a camera loss ("Camera lost (error" to "Camera back:", plus 5 s).
Writes <run folder>/latency-analysis.txt and latency-audio-rows.csv. Standard library only.
"""
import bisect
import csv
import re
import statistics
import sys
from pathlib import Path

SKIP = 60.0
RAMP = 5.0
MIN_RTT = 0.001   # a UART reply cannot come back faster than this (s)


def pct(values, p):
    """The p-th percentile by nearest rank (perf_sample.py's method)."""
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))]


def summary(ms):
    if not ms:
        return "no rows"
    return (f"median {statistics.median(ms):.0f} ms, p95 {pct(ms, 95):.0f}, p99 {pct(ms, 99):.0f}, "
            f"max {max(ms):.0f}, min {min(ms):.0f} (n={len(ms)})")


def num(v):
    return float(v) if v not in ("", None) else None


def main() -> int:
    run = Path(sys.argv[1])
    lat = run / "latency"
    m = re.search(r"monotonic ([\d.]+) = unix ([\d.]+)", (lat / "clock.txt").read_text())
    unix_minus_mono = float(m[2]) - float(m[1])
    to_mono = lambda unix: unix - unix_minus_mono

    log = next(run.glob("perf-*-app.log"))
    lines = log.read_text(errors="replace").splitlines()
    first_depth = next(float(l.split(" ", 1)[0]) for l in lines if "[DEPTH] Frame" in l)
    start_mono = to_mono(first_depth) + SKIP
    losses, start = [], None
    for l in lines:
        if "Camera lost (error" in l and start is None:
            start = to_mono(float(l.split(" ", 1)[0]))
        elif "Camera back:" in l and start is not None:
            losses.append((start, to_mono(float(l.split(" ", 1)[0])) + RAMP))
            start = None
    if start is not None:
        losses.append((start, float("inf")))
    clear = lambda t: all(not (a <= t <= b) for a, b in losses)

    out = [f"run: {run.name}", f"latency log: {lat}",
           f"left out: the first {SKIP:.0f} s after the first [DEPTH] line; camera losses in the run: {len(losses)}", ""]

    all_hap = list(csv.DictReader(open(lat / "haptic.csv")))
    # When the worker noticed an ACK. New logs: acks.csv. The first logs (2026-11-12) paired each
    # noticed ACK with the latest packet in an ack_seen_mono column instead; those times are the same
    # observations, so they are read back from there.
    if (lat / "acks.csv").exists():
        acks = sorted(float(r["ack_seen_mono"]) for r in csv.DictReader(open(lat / "acks.csv")))
        ack_source = "acks.csv"
    else:
        acks = sorted({float(r["ack_seen_mono"]) for r in all_hap if r.get("ack_seen_mono")})
        ack_source = "ack_seen_mono column of haptic.csv (first log format)"
    out.append(f"ACKs noticed by the serial worker: {len(acks)} (from {ack_source}); a packet is paired with "
               f"the first one noticed at least {1000 * MIN_RTT:.0f} ms after its write (ACKs carry no sequence number)")
    out.append("")
    hap = [r for r in all_hap if num(r["capture_mono"]) is not None and num(r["capture_mono"]) >= start_mono]
    for r in hap:
        i = bisect.bisect_left(acks, num(r["write_mono"]) + MIN_RTT)
        r["ack_noticed_mono"] = acks[i] if i < len(acks) else None
    for label, rows in (("all rows", hap), ("outside camera losses", [r for r in hap if clear(num(r["capture_mono"]))])):
        c = lambda a, b: [1000 * (num(r[b]) - num(r[a])) for r in rows if num(r[a]) is not None and num(r[b]) is not None]
        out += [f"HAPTIC ({label}): {len(rows)} motor packets written, {sum(r['write_ok'] != 'True' for r in rows)} failed writes",
                f"  capture -> UART write:        {summary(c('capture_mono', 'write_mono'))}",
                f"  capture -> ACK noticed:       {summary(c('capture_mono', 'ack_noticed_mono'))}",
                f"  UART write -> ACK noticed:    {summary(c('write_mono', 'ack_noticed_mono'))}"
                "  (when the worker got round to looking: one pass after the next write, not the board's reply time)",
                f"  capture -> depth callback queued it: {summary(c('capture_mono', 'queued_mono'))}",
                f"  queued -> taken by the serial worker: {summary(c('queued_mono', 'dequeued_mono'))}",
                f"  packets with no ACK noticed afterwards: {sum(1 for r in rows if r['ack_noticed_mono'] is None)}", ""]

    aud = list(csv.DictReader(open(lat / "audio.csv")))
    firsts = [r for r in aud if r["first_announcement"] == "True"]
    out.append(f"AUDIO: {len(aud)} detection announcements started ({len(firsts)} first announcements of a track)")
    for label, rows in (("first announcements", firsts), ("all announcements", aud)):
        a = [1000 * (num(r["speech_start_mono"]) - num(r["first_capture_mono"])) for r in rows
             if num(r["first_capture_mono"]) is not None]
        b = [1000 * (num(r["speech_start_mono"]) - num(r["decision_capture_mono"])) for r in rows
             if num(r["decision_capture_mono"]) is not None]
        out += [f"  {label}: (a) first qualifying frame -> speech start: {summary(a)}",
                f"  {label}: (b) decision frame -> speech start:          {summary(b)}"]
    out.append("  speech start = the espeak-ng process had been started; when sound reached the earphones is not measured")

    with open(run / "latency-audio-rows.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["speech_start_unix", "track_id", "label", "zone", "phrase", "first_announcement",
                    "a_first_frame_to_speech_ms", "b_decision_frame_to_speech_ms"])
        for r in aud:
            fa, fb = num(r["first_capture_mono"]), num(r["decision_capture_mono"])
            s = num(r["speech_start_mono"])
            w.writerow([f"{s + unix_minus_mono:.3f}", r["track_id"], r["label"], r["zone"], r["phrase"],
                        r["first_announcement"], f"{1000 * (s - fa):.0f}" if fa is not None else "",
                        f"{1000 * (s - fb):.0f}" if fb is not None else ""])

    (run / "latency-analysis.txt").write_text("\n".join(out) + "\n")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
