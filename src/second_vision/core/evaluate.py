"""
Depth accuracy evaluation — turn a captured corpus into defensible numbers.

    PYTHONPATH=src python3 -m second_vision.core.evaluate depth_corpus/ --calib-env room1 --warn-m 1.5

Reads the manifest that core/capture.py writes (SV_CAPTURE=1), replays the
EXACT device chain on every saved frame — crop_border -> downsample ->
DepthPostProcessor -> HapticMapper, the same functions, the same constants —
and scores the result against the laser-measured distances encoded in the
labels (`board_1.0m_C_room2`, see capture.parse_label). No hardware needed:
the whole thing is NumPy on .npy files, so it re-runs in seconds every time a
threshold in depth_utils.py moves. That is the point: every placeholder there
becomes a before/after number instead of a guess.

Four accuracies, kept apart because the wearer experiences them differently
and the literature only defines metrics for the first:

  A. MODEL depth accuracy    — per-point distance error after scale alignment
                               (delta1/2/3, AbsRel, RMSE, RMSElog), scored on the
                               target's ROI median ("what the model says") AND on
                               the zone's nearest sub-cell after floor
                               suppression ("what the device uses").
  B. OBSTACLE detection      — warn iff the target is within --warn-m of the
                               lens: precision / recall / F1, open-scene
                               false-positive rate, intensity-vs-distance curve.
  C. SPATIAL (L/C/R)         — does the strongest warning land in the true
                               zone? 3x3 confusion at perception level and after
                               the haptics policy (centre priority etc.).
  D. ORDINAL                 — Spearman rho between raw model output and true
                               distance, with NO fitting at all: the property
                               the proximity curve actually rests on.

SCALE. The model emits RELATIVE depth (depth_utils.py header): a fixed ruler is
needed before A means anything. We fit 1/d_true = s * (1/d_pred) + t by least
squares on the CALIBRATION captures (--calib-env), freeze it, and score the
rest. Inverse-depth space because the head is a disparity head, and because a
depth-space fit over-weights the far field the motors never respond to. The fit
is also reported per environment: if s wanders between rooms, no fixed
MIN_DEPTH_M / MAX_DEPTH_M can be right, and that is a first-class finding.

Per-image median scaling (the SC-Depth paper protocol) is deliberately NOT
offered: with one measured distance per capture it would zero the error by
construction. The fixed-scale number is the one the device lives with.

Frames within a capture are correlated, so every confidence interval here is a
bootstrap over CAPTURES, never over frames. Report n (captures) with every
number; the tables print it.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from second_vision.core.capture import parse_label
from second_vision.core.depth_utils import (
    DepthPostProcessor,
    FLOOR_FILL,
    MAX_DEPTH_M,
    MIN_DEPTH_M,
    NEAR_PERCENTILE,
    SUBGRID_SHAPE,
    ZONE_NAMES,
    _zone_bounds,
    crop_border,
    downsample_depth,
    floor_profile,
    suppress_floor,
)
from second_vision.core.haptics import DEADBAND, HapticMapper

# The device caps the depth branch at 20 FPS (app.DEPTH_MAX_FPS); the haptics
# pulse clock is replayed at that rate so cycle holds and rise interrupts
# behave as they do live.
REPLAY_FPS = 20.0
# Target ROI, as a fraction of the target zone: the central band of columns and
# the middle band of rows. The capture protocol keeps the target centred in its
# zone precisely so this convention can stand in for a drawn ROI (no RGB is
# saved). A board or a person at 0.5-3 m fills far more than this.
ROI_COL_FRACTION = 0.5
ROI_ROW_FRACTION = 0.5
DELTA_THRESHOLDS = (1.25, 1.25 ** 2, 1.25 ** 3)
BOOTSTRAP_N = 1000


@dataclass
class Capture:
    """One labelled placement: its ground truth and every replayed frame."""
    label: str
    target: str
    distance_m: Optional[float]
    zone: Optional[str]
    env: str
    files: List[str] = field(default_factory=list)
    # Filled by replay(); one entry per frame.
    roi_depth: np.ndarray = None            # model units, ROI median (model level)
    device_depth: np.ndarray = None         # model units, nearest sub-cell p10 after floor erase
    perception: Dict[str, np.ndarray] = None   # DepthPostProcessor output, 0-255 per zone
    driven: Dict[str, np.ndarray] = None       # HapticMapper driven level, 0..4 per zone
    floor_erased: np.ndarray = None         # fraction of the grid the floor model erased

    @property
    def is_open(self) -> bool:
        return self.target.lower() == "open" or self.distance_m is None

    @property
    def n_frames(self) -> int:
        return len(self.files)


# --------------------------------------------------------------------------- #
# Corpus loading
# --------------------------------------------------------------------------- #

def _float_or_none(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load_corpus(corpus_dir: str) -> List[Capture]:
    """
    Group the manifest's rows into Captures, one per label, frames in the
    order they were shot. Ground truth comes from the GT columns when present
    and from the label otherwise, so manifests written before those columns
    existed still score.
    """
    manifest = os.path.join(corpus_dir, "manifest.csv")
    if not os.path.exists(manifest):
        raise FileNotFoundError(f"no manifest.csv in {corpus_dir}")
    rows_by_label: Dict[str, list] = defaultdict(list)
    with open(manifest, newline="") as fh:
        for row in csv.DictReader(fh):
            rows_by_label[row["label"]].append(row)

    captures = []
    for label, rows in rows_by_label.items():
        rows.sort(key=lambda r: int(float(r.get("frame") or 0)))
        first = rows[0]
        parsed = parse_label(label)
        target = (first.get("target") or parsed["target"] or "").strip()
        cap = Capture(
            label=label,
            target=target,
            distance_m=_float_or_none(first.get("distance_m")) or parsed["distance_m"],
            zone=(first.get("zone") or parsed["zone"] or None),
            env=(first.get("env") or parsed["env"] or ""),
            files=[os.path.join(corpus_dir, r["file"]) for r in rows],
        )
        if cap.zone not in ZONE_NAMES:
            cap.zone = None
        captures.append(cap)
    return captures


# --------------------------------------------------------------------------- #
# Replay — the device chain, function for function
# --------------------------------------------------------------------------- #

def roi_slice(zone_slice: np.ndarray) -> np.ndarray:
    """The central ROI_COL_FRACTION x ROI_ROW_FRACTION block of a zone slice."""
    h, w = zone_slice.shape
    r0 = int(h * (1 - ROI_ROW_FRACTION) / 2)
    c0 = int(w * (1 - ROI_COL_FRACTION) / 2)
    r1 = max(r0 + 1, h - r0)
    c1 = max(c0 + 1, w - c0)
    return zone_slice[r0:r1, c0:c1]


def zone_nearest_depth(obstacle_slice: np.ndarray,
                       grid_shape: Tuple[int, int] = SUBGRID_SHAPE) -> float:
    """
    The depth (model units) the device grades a zone on: the NEAR_PERCENTILE
    of its nearest sub-cell, on the floor-erased slice — exactly the quantity
    subgrid_proximity() pushes through the curve, before the curve.
    """
    rows, cols = grid_shape
    h, w = obstacle_slice.shape
    if h < rows or w < cols:
        return float(np.percentile(obstacle_slice, NEAR_PERCENTILE))
    nearest = np.inf
    for band in np.array_split(obstacle_slice, rows, axis=0):
        for cell in np.array_split(band, cols, axis=1):
            if cell.size:
                nearest = min(nearest, float(np.percentile(cell, NEAR_PERCENTILE)))
    return nearest


def replay(cap: Capture, zone_for_open: str = "center") -> Capture:
    """
    Run every frame of a capture through the device chain and record what each
    stage said. Fresh DepthPostProcessor / HapticMapper per capture: their EMA,
    hysteresis and pulse state must start clean, as they do live after a mode
    switch, or the first capture's scene would leak into the next.
    """
    zone = cap.zone or zone_for_open
    x0, x1 = None, None
    processor = DepthPostProcessor()
    mapper = HapticMapper()
    roi, dev, erased = [], [], []
    perc = {z: [] for z in ZONE_NAMES}
    drv = {z: [] for z in ZONE_NAMES}

    for i, path in enumerate(cap.files):
        raw = np.load(path).astype(np.float32)
        cropped = crop_border(raw)
        small = downsample_depth(cropped)
        if x0 is None:
            x0, x1 = _zone_bounds(cropped.shape[1])[zone]
        gx0, gx1 = _zone_bounds(small.shape[1])[zone]

        # Model level: the target's own pixels, at native resolution.
        roi.append(float(np.median(roi_slice(cropped[:, x0:x1]))))

        # Device level: same floor model the processor fits, same erase.
        floor = floor_profile(small)
        obstacles = suppress_floor(small, floor)
        dev.append(zone_nearest_depth(obstacles[:, gx0:gx1]))
        erased.append(float(np.mean(obstacles >= FLOOR_FILL) - np.mean(small >= FLOOR_FILL)))

        intensities = processor.process(cropped)
        mapper.shape(intensities, i / REPLAY_FPS)
        for z in ZONE_NAMES:
            perc[z].append(intensities[z])
            drv[z].append(mapper.last_levels[z])

    cap.roi_depth = np.asarray(roi)
    cap.device_depth = np.asarray(dev)
    cap.floor_erased = np.asarray(erased)
    cap.perception = {z: np.asarray(v, dtype=float) for z, v in perc.items()}
    cap.driven = {z: np.asarray(v, dtype=int) for z, v in drv.items()}
    return cap


# --------------------------------------------------------------------------- #
# Scale fit
# --------------------------------------------------------------------------- #

@dataclass
class ScaleFit:
    s: float
    t: float
    n: int
    envs: Tuple[str, ...]
    in_sample: bool     # True when the fit set is also the test set

    def to_metres(self, pred_units: np.ndarray) -> np.ndarray:
        """Model units -> metres under this ruler. Non-physical results -> inf."""
        pred = np.asarray(pred_units, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = self.s / pred + self.t
            metres = np.where(inv > 0, 1.0 / np.where(inv > 0, inv, 1.0), np.inf)
        return metres

    def units_at(self, metres: float) -> float:
        """Metres -> model units: where a threshold in metres lands on the ruler."""
        inv = 1.0 / metres - self.t
        return float(self.s / inv) if inv > 0 else float("inf")


def fit_scale(pred_units: Sequence[float], true_m: Sequence[float],
              scale_only: bool = False) -> Tuple[float, float]:
    """
    Least squares 1/d_true = s * (1/pred) + t. One (pred, true) pair per
    capture — its median — so a 300-frame capture does not outvote a 30-frame
    one. Returns (s, t); t is 0 when scale_only.
    """
    x = 1.0 / np.asarray(pred_units, dtype=float)
    y = 1.0 / np.asarray(true_m, dtype=float)
    if x.size == 0:
        raise ValueError("no calibration captures with a measured distance")
    if scale_only or x.size < 2:
        return float(np.dot(x, y) / np.dot(x, x)), 0.0
    s, t = np.polyfit(x, y, 1)
    return float(s), float(t)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def depth_metrics(pred_m: np.ndarray, true_m: np.ndarray) -> dict:
    """Standard monocular-depth metrics over paired samples (metres)."""
    pred = np.asarray(pred_m, dtype=float)
    true = np.asarray(true_m, dtype=float)
    ok = np.isfinite(pred) & (pred > 0)
    if not np.any(ok):
        return {"n": 0}
    pred, true = pred[ok], true[ok]
    ratio = np.maximum(pred / true, true / pred)
    out = {"n": int(pred.size)}
    for i, thr in enumerate(DELTA_THRESHOLDS, start=1):
        out[f"delta{i}"] = float(np.mean(ratio < thr))
    out["abs_rel"] = float(np.mean(np.abs(pred - true) / true))
    out["rmse"] = float(np.sqrt(np.mean((pred - true) ** 2)))
    out["rmse_log"] = float(np.sqrt(np.mean((np.log(pred) - np.log(true)) ** 2)))
    out["bias"] = float(np.mean(pred - true))
    return out


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Rank correlation, average ranks on ties; nan when undefined."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 3:
        return float("nan")

    def rank(a):
        order = np.argsort(a, kind="mergesort")
        ranks = np.empty(a.size, dtype=float)
        sorted_a = a[order]
        i = 0
        while i < a.size:
            j = i
            while j + 1 < a.size and sorted_a[j + 1] == sorted_a[i]:
                j += 1
            ranks[order[i:j + 1]] = (i + j) / 2.0
            i = j + 1
        return ranks

    rx, ry = rank(x), rank(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def bootstrap_ci(captures: List[Capture], stat, n_boot: int = BOOTSTRAP_N,
                 seed: int = 0) -> Tuple[float, float]:
    """
    95 % percentile CI of `stat(list_of_captures) -> float`, resampling
    CAPTURES with replacement. Frames within a capture are correlated (same
    placement, same lighting, 50 ms apart), so resampling frames would claim
    a precision the data does not have.
    """
    if len(captures) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        sample = [captures[i] for i in rng.integers(0, len(captures), len(captures))]
        v = stat(sample)
        if np.isfinite(v):
            vals.append(v)
    if not vals:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _pooled(captures: Iterable[Capture], level: str) -> Tuple[np.ndarray, np.ndarray]:
    preds, trues = [], []
    for cap in captures:
        p = cap.roi_depth if level == "model" else cap.device_depth
        preds.append(p)
        trues.append(np.full(p.shape, cap.distance_m))
    if not preds:
        return np.array([]), np.array([])
    return np.concatenate(preds), np.concatenate(trues)


def _group(captures: List[Capture], key) -> Dict[str, List[Capture]]:
    groups: Dict[str, List[Capture]] = defaultdict(list)
    for cap in captures:
        groups[str(key(cap))].append(cap)
    return dict(sorted(groups.items(), key=lambda kv: _sort_key(kv[0])))


def _sort_key(s: str):
    try:
        return (0, float(s))
    except ValueError:
        return (1, s)


# --------------------------------------------------------------------------- #
# The evaluation
# --------------------------------------------------------------------------- #

def evaluate(captures: List[Capture], calib_envs: Sequence[str] = (),
             warn_m: float = 1.5, scale_only: bool = False,
             deadband: float = DEADBAND) -> dict:
    """
    Everything the report prints, as one nested dict (also what --json dumps).
    `captures` must already be replayed.
    """
    positives = [c for c in captures if not c.is_open and c.zone is not None]
    opens = [c for c in captures if c.is_open]
    unscorable = [c for c in captures if not c.is_open and c.zone is None]

    calib_envs = tuple(calib_envs)
    if calib_envs:
        calib = [c for c in positives if c.env in calib_envs]
        test = [c for c in positives if c.env not in calib_envs]
        in_sample = False
        if not calib:
            raise ValueError(f"no positive captures in calibration env(s) {calib_envs}")
        if not test:
            test, in_sample = calib, True
    else:
        calib, test, in_sample = positives, positives, True

    report = {
        "counts": {
            "captures": len(captures), "positives": len(positives), "open": len(opens),
            "unscorable": [c.label for c in unscorable],
            "calibration": [c.label for c in calib], "test": [c.label for c in test],
            "frames": int(sum(c.n_frames for c in captures)),
        },
        "warn_m": warn_m,
        "deadband": deadband,
        "in_sample": in_sample,
        "scale": {}, "model": {}, "device": {}, "ordinal": {},
        "detection": {}, "spatial": {}, "temporal": {}, "response": {},
        "thresholds_in_metres": {},
    }
    if not positives:
        return report

    # ---- A. scale + depth metrics, at both levels -------------------------
    for level in ("model", "device"):
        med = lambda c: float(np.median(c.roi_depth if level == "model" else c.device_depth))
        s, t = fit_scale([med(c) for c in calib], [c.distance_m for c in calib], scale_only)
        fit = ScaleFit(s, t, len(calib), calib_envs, in_sample)
        # Scale per environment: the drift finding.
        per_env = {}
        for env, caps in _group(positives, lambda c: c.env).items():
            if len(caps) >= 2:
                es, et = fit_scale([med(c) for c in caps], [c.distance_m for c in caps], scale_only)
                per_env[env] = {"s": es, "t": et, "n": len(caps)}
        report["scale"][level] = {"s": s, "t": t, "n": len(calib), "per_env": per_env}

        pred, true = _pooled(test, level)
        overall = depth_metrics(fit.to_metres(pred), true)

        def d1(caps, fit=fit, level=level):
            p, tr = _pooled(caps, level)
            return depth_metrics(fit.to_metres(p), tr).get("delta1", float("nan"))

        def absrel(caps, fit=fit, level=level):
            p, tr = _pooled(caps, level)
            return depth_metrics(fit.to_metres(p), tr).get("abs_rel", float("nan"))

        overall["delta1_ci"] = bootstrap_ci(test, d1)
        overall["abs_rel_ci"] = bootstrap_ci(test, absrel)
        overall["n_captures"] = len(test)
        by = {}
        for name, key in (("distance", lambda c: c.distance_m), ("target", lambda c: c.target),
                          ("zone", lambda c: c.zone), ("env", lambda c: c.env)):
            by[name] = {}
            for k, caps in _group(test, key).items():
                p, tr = _pooled(caps, level)
                m = depth_metrics(fit.to_metres(p), tr)
                m["n_captures"] = len(caps)
                by[name][k] = m
        report[level] = {"overall": overall, "by": by}

        if level == "device":
            report["thresholds_in_metres"] = {
                "MIN_DEPTH_M": {"units": MIN_DEPTH_M, "metres": float(1.0 / (s / MIN_DEPTH_M + t))
                                if (s / MIN_DEPTH_M + t) > 0 else float("inf")},
                "MAX_DEPTH_M": {"units": MAX_DEPTH_M, "metres": float(1.0 / (s / MAX_DEPTH_M + t))
                                if (s / MAX_DEPTH_M + t) > 0 else float("inf")},
                "warn_m_in_units": fit.units_at(warn_m),
            }

    # ---- D. ordinal — raw output vs distance, no fit ------------------------
    report["ordinal"] = {
        "model_rho": spearman([float(np.median(c.roi_depth)) for c in positives],
                              [c.distance_m for c in positives]),
        "device_rho": spearman([float(np.median(c.device_depth)) for c in positives],
                               [c.distance_m for c in positives]),
        "n": len(positives),
    }

    # ---- B. detection ------------------------------------------------------
    thr = deadband * 255.0
    det = {"perception": _detection(positives, opens, warn_m, thr, "perception"),
           "driven": _detection(positives, opens, warn_m, thr, "driven")}
    report["detection"] = det

    # Response curve: mean target-zone perception per (target, distance).
    resp = {}
    for tgt, caps in _group(positives, lambda c: c.target).items():
        resp[tgt] = {}
        for d, dcaps in _group(caps, lambda c: c.distance_m).items():
            vals = np.concatenate([c.perception[c.zone] for c in dcaps])
            resp[tgt][d] = {"mean": float(vals.mean()), "std": float(vals.std()),
                            "warn_frac": float(np.mean(vals >= thr)), "n_captures": len(dcaps)}
    report["response"] = resp

    # ---- C. spatial --------------------------------------------------------
    near = [c for c in positives if c.distance_m <= warn_m]
    report["spatial"] = {"perception": _confusion(near, thr, "perception"),
                         "driven": _confusion(near, thr, "driven")}

    # ---- temporal stability ------------------------------------------------
    report["temporal"] = _temporal(captures, thr)
    return report


def _detection(positives, opens, warn_m, thr, level) -> dict:
    """
    Per-frame verdicts. A positive capture's truth is "target within warn_m";
    its prediction is whether ITS zone fires. An open capture is a negative for
    every zone; its prediction is whether ANY zone fires (that is what the
    wearer feels). Perception level = processor output >= deadband; driven
    level = the haptics actually driving a non-zero level.
    """
    tp = fp = fn = tn = 0
    for c in positives:
        fired = _fired(c, c.zone, thr, level)
        if c.distance_m <= warn_m:
            tp += int(fired.sum()); fn += int((~fired).sum())
        else:
            fp += int(fired.sum()); tn += int((~fired).sum())
    open_fp = open_n = 0
    for c in opens:
        fired = np.zeros(c.n_frames, dtype=bool)
        for z in ZONE_NAMES:
            fired |= _fired(c, z, thr, level)
        open_fp += int(fired.sum()); open_n += c.n_frames
    fp_all = fp + open_fp
    precision = tp / (tp + fp_all) if tp + fp_all else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if np.isfinite(precision) and np.isfinite(recall) and precision + recall else float("nan"))
    return {"tp": tp, "fp": fp_all, "fn": fn, "tn": tn + (open_n - open_fp),
            "precision": precision, "recall": recall, "f1": f1,
            "open_fpr": open_fp / open_n if open_n else float("nan"),
            "open_frames": open_n,
            "far_positive_fpr": fp / (fp + tn) if fp + tn else float("nan")}


def _fired(c: Capture, zone: str, thr: float, level: str) -> np.ndarray:
    if level == "driven":
        return c.driven[zone] > 0
    return c.perception[zone] >= thr


def _confusion(near: List[Capture], thr: float, level: str) -> dict:
    """
    3x3 true-zone x fired-zone counts over frames, plus a "none" column for
    frames where nothing fired. Argmax over zones; ties go to centre, as the
    haptics' own priority would.
    """
    order = ("center", "left", "right")           # argmax tie-break preference
    matrix = {tz: {z: 0 for z in ZONE_NAMES + ("none",)} for tz in ZONE_NAMES}
    for c in near:
        for i in range(c.n_frames):
            vals = {z: (c.driven[z][i] if level == "driven" else c.perception[z][i]) for z in ZONE_NAMES}
            best = max(order, key=lambda z: vals[z])
            fired = (vals[best] > 0) if level == "driven" else (vals[best] >= thr)
            matrix[c.zone][best if fired else "none"] += 1
    total = sum(sum(r.values()) for r in matrix.values())
    correct = sum(matrix[z][z] for z in ZONE_NAMES)
    return {"matrix": matrix, "accuracy": correct / total if total else float("nan"),
            "n_frames": total, "n_captures": len(near)}


def _temporal(captures: List[Capture], thr: float) -> dict:
    """
    Per capture: std of each zone's perception and how many times the
    warn/no-warn verdict flipped — the 131-flips-in-400-frames measurement,
    generalised. Every capture here is a static placement, so all of this is
    noise the wearer would feel.
    """
    per = {}
    for c in captures:
        entry = {}
        for z in ZONE_NAMES:
            v = c.perception[z]
            fired = v >= thr
            entry[z] = {"std": float(v.std()), "mean": float(v.mean()),
                        "flips": int(np.count_nonzero(np.diff(fired.astype(int)))),
                        "driven_flips": int(np.count_nonzero(np.diff(c.driven[z]))),
                        "floor_erased": float(c.floor_erased.mean())}
        per[c.label] = entry
    worst = max(per.items(), key=lambda kv: max(kv[1][z]["flips"] for z in ZONE_NAMES),
                default=(None, None))
    return {"per_capture": per, "worst_flips": worst[0]}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def _fmt(v, nd=3) -> str:
    if isinstance(v, (tuple, list)):
        return "[" + ", ".join(_fmt(x, nd) for x in v) + "]"
    if v is None:
        return "-"
    if isinstance(v, float):
        return "nan" if not np.isfinite(v) else f"{v:.{nd}f}"
    return str(v)


def print_report(r: dict, out=None) -> None:
    out = out or sys.stdout          # resolved at call time, so capture/redirect works
    p = lambda *a: print(*a, file=out)
    c = r["counts"]
    p("=" * 78)
    p(f"Depth evaluation — {c['captures']} captures, {c['frames']} frames "
      f"({c['positives']} targets, {c['open']} open scenes)")
    p(f"warn distance {r['warn_m']} m | deadband {r['deadband']:.2f} "
      f"({r['deadband'] * 255:.0f}/255) | replay {REPLAY_FPS:.0f} FPS")
    if c["unscorable"]:
        p(f"UNSCORABLE (no zone in label): {', '.join(c['unscorable'])}")
    if r["in_sample"]:
        p("!! IN-SAMPLE: scale fitted on the same captures it is scored on. "
          "Use --calib-env to hold rooms out; these numbers are an upper bound.")
    if not r["scale"]:
        p("No positive captures with a distance and a zone — nothing to score.")
        return

    for level, title in (("model", "A. MODEL depth (target ROI median, native resolution)"),
                         ("device", "A'. DEVICE depth (nearest sub-cell after floor erase — what the motors use)")):
        sc = r["scale"][level]
        ov = r[level]["overall"]
        p("-" * 78)
        p(title)
        p(f"  ruler: 1/d = {sc['s']:.4f} * (1/units) + {sc['t']:+.4f}   "
          f"(fit on n={sc['n']} captures)")
        if sc["per_env"]:
            p("  per-env scale:  " + "  ".join(
                f"{e}: s={v['s']:.3f} t={v['t']:+.3f} (n={v['n']})" for e, v in sc["per_env"].items()))
        if ov.get("n", 0) == 0:
            p("  no scorable frames")
            continue
        p(f"  delta1 {ov['delta1']:.3f}  CI95 {_fmt(ov['delta1_ci'])}   "
          f"delta2 {ov['delta2']:.3f}  delta3 {ov['delta3']:.3f}")
        p(f"  AbsRel {ov['abs_rel']:.3f}  CI95 {_fmt(ov['abs_rel_ci'])}   "
          f"RMSE {ov['rmse']:.3f} m  RMSElog {ov['rmse_log']:.3f}  bias {ov['bias']:+.3f} m   "
          f"n={ov['n']} frames / {ov['n_captures']} captures")
        for name in ("distance", "target", "zone", "env"):
            rows = r[level]["by"][name]
            if len(rows) <= 1 and name != "distance":
                continue
            p(f"  by {name}:")
            p(f"    {'':<12}{'n_cap':>6}{'delta1':>8}{'AbsRel':>8}{'RMSE':>8}{'bias':>8}")
            for k, m in rows.items():
                if m.get("n", 0) == 0:
                    p(f"    {k:<12}{m.get('n_captures', 0):>6}  (no finite predictions)")
                    continue
                p(f"    {k:<12}{m['n_captures']:>6}{m['delta1']:>8.3f}{m['abs_rel']:>8.3f}"
                  f"{m['rmse']:>8.3f}{m['bias']:>+8.3f}")

    t = r["thresholds_in_metres"]
    if t:
        p("-" * 78)
        p("Placeholders on the device ruler:")
        p(f"  MIN_DEPTH_M = {t['MIN_DEPTH_M']['units']:.1f} units  ->  {t['MIN_DEPTH_M']['metres']:.2f} m "
          f"(full warning at/inside)")
        p(f"  MAX_DEPTH_M = {t['MAX_DEPTH_M']['units']:.1f} units  ->  {t['MAX_DEPTH_M']['metres']:.2f} m "
          f"(silent at/beyond)")
        p(f"  --warn-m {r['warn_m']} m  ->  {t['warn_m_in_units']:.1f} units")

    o = r["ordinal"]
    p("-" * 78)
    p(f"D. ORDINAL (no fit): Spearman rho vs true distance  model {o['model_rho']:.3f}  "
      f"device {o['device_rho']:.3f}   (n={o['n']} captures; +1.0 is perfect — nearer = smaller units; "
      f"tied distances cap it below 1)")

    p("-" * 78)
    p(f"B. DETECTION at <= {r['warn_m']} m (per frame)")
    for level, d in r["detection"].items():
        p(f"  {level:<11} P {_fmt(d['precision'])}  R {_fmt(d['recall'])}  F1 {_fmt(d['f1'])}   "
          f"tp {d['tp']} fp {d['fp']} fn {d['fn']} tn {d['tn']}   "
          f"open-scene FPR {_fmt(d['open_fpr'])} ({d['open_frames']} frames)   "
          f"far-target FPR {_fmt(d['far_positive_fpr'])}")
    p("  response curve (target-zone perception, 0-255):")
    for tgt, rows in r["response"].items():
        line = "  ".join(f"{d}m: {v['mean']:.0f}±{v['std']:.0f} ({v['warn_frac']:.0%} warn, n={v['n_captures']})"
                         for d, v in rows.items())
        p(f"    {tgt:<10}{line}")

    p("-" * 78)
    p(f"C. SPATIAL — strongest zone vs true zone, targets <= {r['warn_m']} m")
    for level, s in r["spatial"].items():
        p(f"  {level} (accuracy {_fmt(s['accuracy'])}, {s['n_frames']} frames / {s['n_captures']} captures)")
        p(f"    {'true \\ fired':<14}{'left':>7}{'center':>8}{'right':>7}{'none':>7}")
        for tz in ZONE_NAMES:
            row = s["matrix"][tz]
            p(f"    {tz:<14}{row['left']:>7}{row['center']:>8}{row['right']:>7}{row['none']:>7}")

    p("-" * 78)
    p("TEMPORAL — static placements, per zone: flips of the warn verdict / driven-level changes")
    p(f"    {'capture':<28}{'L std':>7}{'flips':>7}{'C std':>7}{'flips':>7}{'R std':>7}{'flips':>7}{'floor%':>8}")
    for label, e in r["temporal"]["per_capture"].items():
        p(f"    {label[:27]:<28}"
          + "".join(f"{e[z]['std']:>7.1f}{e[z]['flips']:>4}/{e[z]['driven_flips']:<3}" for z in ZONE_NAMES)
          + f"{e['center']['floor_erased']:>8.0%}")
    p("=" * 78)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def run(corpus_dir: str, calib_envs: Sequence[str] = (), warn_m: float = 1.5,
        scale_only: bool = False, deadband: float = DEADBAND,
        progress=None) -> dict:
    captures = load_corpus(corpus_dir)
    for i, cap in enumerate(captures):
        replay(cap)
        if progress:
            progress(f"[{i + 1}/{len(captures)}] {cap.label}: {cap.n_frames} frames")
    return evaluate(captures, calib_envs, warn_m, scale_only, deadband)


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", help="directory with manifest.csv and the .npy frames")
    ap.add_argument("--calib-env", action="append", default=[],
                    help="env (from the label) whose captures fit the scale; repeatable. "
                         "Everything else is the test set. Omit -> in-sample.")
    ap.add_argument("--warn-m", type=float, default=1.5,
                    help="'should warn' distance in metres for the detection metrics")
    ap.add_argument("--scale-only", action="store_true",
                    help="fit s only (t=0) in inverse-depth space")
    ap.add_argument("--deadband", type=float, default=DEADBAND,
                    help="perception fraction (0-1) counted as a warning; default = haptics.DEADBAND")
    ap.add_argument("--json", metavar="FILE", help="also dump the full report as JSON")
    ap.add_argument("--quiet", action="store_true", help="no per-capture progress lines")
    args = ap.parse_args(argv)

    report = run(args.corpus, args.calib_env, args.warn_m, args.scale_only, args.deadband,
                 progress=None if args.quiet else lambda m: print(m, file=sys.stderr))
    print_report(report)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(_json_safe(report), fh, indent=2)
        print(f"report written to {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
