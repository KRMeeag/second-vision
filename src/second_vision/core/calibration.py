"""
Depth calibration capture + analysis (Second Vision, depth post-processing).

The depth post-processing thresholds (MIN_DEPTH_M, MAX_DEPTH_M, WALL_VARIANCE_MAX)
are in the model's RELATIVE units, not meters, so they are placeholders until
measured on the real device. This module turns that measurement into a repeatable
procedure:

  CAPTURE (runs inside the live pipeline, headless — no video window, so it works
  even while the VNC preview is frozen):
      1) SV_CALIBRATE=1 python -m ...sv_dual_callback_withdepth
      2) place an object at a known distance, then in a SECOND ssh terminal:
             echo "0.5m" > calib_label.txt      # tag the current samples
         move it and retag. Capture ALL of these to resolve every threshold:
             0.5m 1m 2m 3m   — object centred at measured distances
             wall            — camera ~1 m from a blank wall, filling the view
             open            — open corridor, nothing close
             pole            — broomstick/cane at ~1 m (thin object)
             stairs (or curb)— floor visible, real ground break in the strip
         Rows stream to depth_calibration.csv, throttled to the terminal.

  ANALYSE (offline, no hardware):
      python -m ...calibration --analyze depth_calibration.csv
  Groups rows by label and prints per-distance stats plus suggested threshold
  values you can drop into depth_utils.py.

Nothing here runs on the normal (non-calibration) path.
"""
import argparse
import csv
import os
import queue
import threading
import time
from collections import defaultdict

import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_utils import (
    ground_break_stats,
    local_flatness,
    zone_warning_breakdown,
)

# Per-scope stats recorded for every frame. Kept small and fixed so the CSV
# schema is stable and easy to analyse.
#
# IMPORTANT: the per-zone measures come from depth_utils (local_flatness,
# zone_warning_breakdown) rather than being reimplemented here. A previous
# version recorded a zone-GLOBAL std while the detector compared a median
# per-cell std — ~2x apart on a real wall, and on opposite sides of the
# threshold, so calibrating from it would have degraded the detector.
_STAT_NAMES = ("p10", "p50", "std", "min", "max", "flatness", "coverage")
_SCOPES = ("frame", "left", "center", "right")
_GROUND_NAMES = ("max_grad", "median_grad", "signed_break")


def _scope_stats(a: np.ndarray) -> dict:
    """Depth distribution + the exact measures the detectors threshold on."""
    stats = {
        "p10": float(np.percentile(a, 10)),
        "p50": float(np.percentile(a, 50)),
        "std": float(np.std(a)),          # kept for reference; NOT the wall metric
        "min": float(np.min(a)),
        "max": float(np.max(a)),
        # what detect_blank_wall actually compares to WALL_VARIANCE_MAX
        "flatness": local_flatness(a),
    }
    # what the T/N shape split compares to BROAD_COVERAGE_MIN
    stats["coverage"] = float(zone_warning_breakdown(a)["coverage"])
    return stats


def zone_depth_stats(depth_map: np.ndarray) -> dict:
    """
    Flat {scope_stat: value} dict for the whole frame and the L/C/R zones,
    using the same 25/50/25 split as compute_zone_intensities, plus the ground
    strip's row-gradient stats (for the hazard / floor-to-wall thresholds).
    """
    w = depth_map.shape[1]
    regions = {
        "frame": depth_map,
        "left": depth_map[:, :w // 4],
        "center": depth_map[:, w // 4: 3 * w // 4],
        "right": depth_map[:, 3 * w // 4:],
    }
    out = {}
    for scope, region in regions.items():
        for name, val in _scope_stats(region).items():
            out[f"{scope}_{name}"] = val
    for name, val in ground_break_stats(depth_map, depth_map.shape[0]).items():
        out[f"ground_{name}"] = val
    return out


def _columns() -> list:
    cols = ["timestamp", "frame", "label"]
    for scope in _SCOPES:
        for name in _STAT_NAMES:
            cols.append(f"{scope}_{name}")
    cols += [f"ground_{n}" for n in _GROUND_NAMES]
    return cols


class CalibrationLogger:
    """
    Streams per-frame depth stats to a CSV and a throttled terminal line, tagging
    each row with a distance label read live from a small text file (so you can
    change it from another ssh session without touching the running pipeline).

    THREADING: log() runs on the GStreamer streaming thread and must stay near
    free — it only copies the small (12 KB) array onto a queue. The stats math
    (three detectors x four regions) and the CSV/SD-card writes happen in a
    background worker thread; flushes are batched. Doing all of that inline
    starved the video branch and blurred the camera preview (live finding).
    Frames are dropped when the worker is busy — calibration averages over many
    frames, so sampling is fine and the pipeline must never wait.
    """

    def __init__(self, csv_path="depth_calibration.csv", label_file="calib_label.txt",
                 print_every=8, sample_every=2, flush_every=20):
        self.csv_path = csv_path
        self.label_file = label_file
        self.print_every = max(int(print_every), 1)
        self.sample_every = max(int(sample_every), 1)
        self.flush_every = max(int(flush_every), 1)
        self._cols = _columns()
        new_file = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        self._fh = open(csv_path, "a", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=self._cols)
        if new_file:
            self._writer.writeheader()
            self._fh.flush()
        self._rows = 0
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._worker_thread = threading.Thread(
            target=self._worker, name="calib-logger", daemon=True)
        self._worker_thread.start()
        print(f"[CALIB] logging to {csv_path} | set distance with: echo \"0.5m\" > {label_file}")

    def _current_label(self) -> str:
        try:
            with open(self.label_file) as f:
                label = f.readline().strip()
            if label:
                return label
        except OSError:
            pass
        return os.environ.get("SV_CALIB_LABEL", "unlabeled")

    def log(self, small: np.ndarray, frame_count: int) -> None:
        """Streaming-thread side: copy + hand off. Never computes, never blocks."""
        if frame_count % self.sample_every:
            return
        try:
            self._queue.put_nowait((small.copy(), frame_count, time.time()))
        except queue.Full:
            pass  # worker busy (e.g. SD flush) — drop rather than stall the pipeline

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            small, frame_count, ts = item
            try:
                label = self._current_label()
                stats = zone_depth_stats(small)
                self._writer.writerow(
                    {"timestamp": ts, "frame": frame_count, "label": label, **stats})
                self._rows += 1
                if self._rows % self.flush_every == 0:
                    self._fh.flush()
                if self._rows % self.print_every == 0:
                    print(f"[CALIB] frame {frame_count} label={label!r} | "
                          f"p10/p50={stats['frame_p10']:.2f}/{stats['frame_p50']:.2f} "
                          f"flat={stats['center_flatness']:.2f} cov={stats['center_coverage']:.0%} | "
                          f"L/C/R p10={stats['left_p10']:.1f}/{stats['center_p10']:.1f}/{stats['right_p10']:.1f}")
            except Exception as e:  # never let the logger kill the session
                print(f"[CALIB] worker error: {e}")

    def close(self) -> None:
        try:
            self._queue.put(None, timeout=1.0)
            self._worker_thread.join(timeout=5.0)
        except Exception:
            pass
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Offline analysis
# --------------------------------------------------------------------------- #

def _grouped_means(csv_path: str):
    """Return {label: {col: mean}} and the ordered list of labels seen."""
    sums, counts, order = defaultdict(lambda: defaultdict(float)), defaultdict(int), []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            label = row["label"]
            if label not in order:
                order.append(label)
            counts[label] += 1
            for col, val in row.items():
                if col in ("timestamp", "frame", "label"):
                    continue
                try:
                    sums[label][col] += float(val)
                except (TypeError, ValueError):
                    pass
    means = {lbl: {c: sums[lbl][c] / counts[lbl] for c in sums[lbl]} for lbl in order}
    return means, order, counts


def analyze_csv(csv_path: str) -> None:
    means, order, counts = _grouped_means(csv_path)
    if not order:
        print("No rows found in", csv_path)
        return

    print(f"\n=== Calibration summary: {csv_path} ===")
    print(f"{'label':<12}{'n':>6}{'p10':>8}{'p50':>8}{'flatness':>10}{'coverage':>10}"
          f"{'g_max':>8}{'g_med':>8}{'break':>9}")
    for lbl in order:
        m = means[lbl]
        print(f"{lbl:<12}{counts[lbl]:>6}{m['frame_p10']:>8.2f}{m['frame_p50']:>8.2f}"
              f"{m['center_flatness']:>10.2f}{m['center_coverage']:>10.0%}"
              f"{m['ground_max_grad']:>8.2f}{m['ground_median_grad']:>8.2f}"
              f"{m['ground_signed_break']:>9.2f}")
    print("  (flatness/coverage are CENTRE-zone; break: + = drop-off, - = step-up)")

    def pick(*names):
        for n in names:
            for l in order:
                if l.lower() == n or n in l.lower():
                    return l
        return None

    print("\n--- Suggested thresholds (depth_utils.py) ---")

    # MIN_DEPTH_M: nearest reading the model emits -> smallest frame_p10 seen.
    near_lbl = min(order, key=lambda l: means[l]["frame_p10"])
    print(f"MIN_DEPTH_M         ~ {means[near_lbl]['frame_p10']:.1f}"
          f"    (nearest p10, from {near_lbl!r})")

    # MAX_DEPTH_M: safe-zone cutoff -> the p10 of the open / clear scene.
    far_lbl = pick("open", "clear", "far") or max(order, key=lambda l: means[l]["frame_p10"])
    print(f"MAX_DEPTH_M         ~ {means[far_lbl]['frame_p10']:.1f}"
          f"    (safe-zone cutoff, from {far_lbl!r})")

    # WALL_VARIANCE_MAX: between a wall's local flatness and an open scene's.
    wall_lbl, open_lbl = pick("wall"), pick("open", "clear", "far")
    if wall_lbl and open_lbl:
        wf, of = means[wall_lbl]["center_flatness"], means[open_lbl]["center_flatness"]
        print(f"WALL_VARIANCE_MAX   ~ {(wf + of) / 2:.2f}"
              f"    (between wall {wf:.2f} and open {of:.2f})")
    else:
        print("WALL_VARIANCE_MAX   : need a 'wall' label and an 'open' label")

    # BROAD_COVERAGE_MIN: between a thin object's coverage and a wall's.
    pole_lbl = pick("pole", "thin", "stick")
    if pole_lbl and wall_lbl:
        pc, wc = means[pole_lbl]["center_coverage"], means[wall_lbl]["center_coverage"]
        print(f"BROAD_COVERAGE_MIN  ~ {(pc + wc) / 2:.2f}"
              f"    (between pole {pc:.0%} and wall {wc:.0%})")
    else:
        print("BROAD_COVERAGE_MIN  : need a 'pole' label and a 'wall' label")

    # HAZARD_MIN_STEP: above the largest gradient seen on SAFE ground.
    safe_lbls = [l for l in order if l.lower() in ("open", "clear", "far", "wall")]
    if safe_lbls:
        worst_safe = max(means[l]["ground_max_grad"] for l in safe_lbls)
        print(f"HAZARD_MIN_STEP     ~ {worst_safe * 1.5:.2f}"
              f"    (1.5x the worst safe-ground gradient {worst_safe:.2f})")
    else:
        print("HAZARD_MIN_STEP     : need an 'open' or 'wall' label")

    # HAZARD_MAX_JUMP / FLOOR_WALL_MIN_JUMP: from a real ground break.
    haz_lbl = pick("stair", "dropoff", "drop", "curb", "step")
    if haz_lbl:
        g = means[haz_lbl]["ground_max_grad"]
        d = "drop-off" if means[haz_lbl]["ground_signed_break"] > 0 else "step-up"
        print(f"HAZARD_MAX_JUMP     ~ {g:.2f}    (saturate at the {haz_lbl!r} break, a {d})")
        print(f"FLOOR_WALL_MIN_JUMP ~ {max(g * 0.3, 1.0):.2f}"
              f"    (0.3x that break; confirm against a floor+wall capture)")
    else:
        print("HAZARD_MAX_JUMP     : need a 'stairs' or 'curb' label")
        print("FLOOR_WALL_MIN_JUMP : need a 'stairs' or 'curb' label")

    # WALL_CONFIRM_SATURATE: feel decision, informed by the point-blank wall.
    if wall_lbl:
        print(f"WALL_CONFIRM_SATURATE : feel decision — a confirmed wall at "
              f"p10 {means[wall_lbl]['frame_p10']:.1f} should read max buzz; "
              f"agree the cut-in distance with the haptics owner")

    print("\nNote: starting points from the captured means — sanity-check against "
          "the per-label table above, then re-run the unit tests.\n")


def main():
    ap = argparse.ArgumentParser(description="Depth calibration analysis")
    ap.add_argument("--analyze", metavar="CSV", required=True, help="calibration CSV to summarise")
    args = ap.parse_args()
    analyze_csv(args.analyze)


if __name__ == "__main__":
    main()