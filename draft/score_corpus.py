"""
Batch-score a captured depth corpus — did the edge-case detectors do the right
thing on REAL camera frames?

verify_scene.py answers that for ONE frame, by eye. This answers it for a whole
labelled capture, as numbers: per-label pass rates, which detector actually drove
each scene, and a confusion matrix. That is the difference between "the overlay
looked right when I stood there" and evidence you can put in a report.

Needs only numpy + opencv — no Hailo, no GStreamer, no Pi. Runs the exact
production chain (crop_border -> downsample -> zone_warning_breakdown +
detect_ground_hazard), so re-scoring after a threshold change costs a second.

Usage
-----
    # capture first on the Pi:  SV_CAPTURE=1 python -m ...sv_dual_callback_withdepth
    python -m ...score_corpus --dir depth_corpus

    # also write one annotated PNG per label (representative frame)
    python -m ...score_corpus --dir depth_corpus --annotate corpus_report/

    # judge a fixed zone instead of whichever zone is loudest
    python -m ...score_corpus --dir depth_corpus --zone center

    # show which label keywords map to which expectation
    python -m ...score_corpus --rules

Expectations come from KEYWORDS in the label you typed during capture, so
`echo "wall_far" > calib_label.txt` is scored against the floor-to-wall
detector automatically. Labels that match nothing are still reported — just
without a pass/fail verdict.
"""

import argparse
import os
import re
from collections import Counter, defaultdict

import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_utils import (
    ZONE_NAMES,
    DepthPostProcessor,
    crop_border,
    detect_ground_hazard,
    downsample_depth,
    zone_warning_breakdown,
)

# A zone this quiet counts as "no warning" (0-255 motor units, ~5%).
QUIET_MAX = 12
# Per-detector firing threshold in 0-1 breakdown units (same 5%).
FIRE_MIN = QUIET_MAX / 255.0

# Label keyword -> expected outcome. Ordered: the FIRST match wins, so the
# specific rules ("wall_far" -> floor-to-wall) must precede the general ones
# ("wall" -> blank-wall).
#
# NOTE on "fires" rather than "wins": zone_warning is max(sub, wall, f2w), and
# sub-grid is by construction never LESS sensitive than the others (it takes the
# nearest cell's near-cluster, which is <= any whole-zone aggregate). So on a
# close surface sub-grid almost always holds the max, and grading on "which
# detector won" would fail every wall scene while the device behaved perfectly.
# The question that actually matters for a fail-safe max() ensemble is
#   (a) did the specialist detector fire at all, and
#   (b) was it NECESSARY — would the scene go silent without it?
# (b) is the ablation column in the report, and it is the only real evidence
# that a detector earns its place.
RULES = [
    (r"open|empty|clear|corridor|baseline|none", {"quiet": True}),
    (r"far.?wall|wall.?far|f2w|washed", {"fires": "f2w"}),
    (r"thin|pole|leg|cable|broom|stick|rail", {"fires": "sub", "shape": "thin"}),
    (r"wall|door|board|panel", {"fires": "wall", "shape": "broad"}),
    (r"drop.?off|stair|descend", {"hazard": True, "direction": "down"}),
    (r"curb|step.?up|riser", {"hazard": True, "direction": "up"}),
]

_PART_TO_NAME = {"sub": "sub-grid (near/thin)", "wall": "blank-wall", "f2w": "floor-to-wall"}


def expectation_for(label: str):
    """First matching rule for a label, or None if we have no expectation."""
    low = label.lower()
    for pattern, expected in RULES:
        if re.search(pattern, low):
            return expected
    return None


def analyse_frame(depth: np.ndarray, zone_choice: str = "auto") -> dict:
    """
    Run the production pipeline on one raw depth frame.

    Mirrors verify_scene.analyse() exactly — crop, downsample, fresh EMA so the
    result is a single-frame verdict rather than a smoothed one.
    """
    small = downsample_depth(crop_border(depth))
    intensities = DepthPostProcessor().process(small)
    hazard, severity, direction = detect_ground_hazard(small, small.shape[0])

    w = small.shape[1]
    slices = {
        "left": small[:, :w // 4],
        "center": small[:, w // 4: 3 * w // 4],
        "right": small[:, 3 * w // 4:],
    }
    breakdowns = {name: zone_warning_breakdown(sl) for name, sl in slices.items()}

    if zone_choice == "auto":
        # The zone actually driving the device — what the user would feel.
        zone = max(ZONE_NAMES, key=lambda z: intensities[z])
    else:
        zone = zone_choice

    quiet = all(intensities[z] <= QUIET_MAX for z in ZONE_NAMES)
    return {
        "small": small,
        "intensities": intensities,
        "hazard": hazard,
        "severity": severity,
        "direction": direction,
        "zone": zone,
        "breakdown": breakdowns[zone],
        "quiet": quiet,
    }


def necessity(parts: dict, target: str) -> bool:
    """
    Would removing `target` from the max() lose the warning for this frame?

    True means the detector is doing work no other detector is doing here — the
    only evidence that distinguishes "my wall detector works" from "sub-grid was
    covering for it and I'd never know".
    """
    others = [v for k, v in parts.items() if k != target]
    return parts.get(target, 0.0) > FIRE_MIN and max(others, default=0.0) <= FIRE_MIN


def check(result: dict, expected: dict) -> list:
    """Return a list of failure reasons for one frame ([] means it passed)."""
    if expected is None:
        return []
    fails = []
    bd = result["breakdown"]

    if expected.get("quiet"):
        if not result["quiet"]:
            i = result["intensities"]
            fails.append(f"expected silence, got L/C/R {i['left']}/{i['center']}/{i['right']}")
        if result["hazard"]:
            fails.append(f"expected no hazard, got {result['direction']}")
        return fails

    if "fires" in expected:
        target = expected["fires"]
        if result["quiet"]:
            fails.append("no warning raised at all")
        elif bd["parts"].get(target, 0.0) <= FIRE_MIN:
            others = " ".join(f"{k}={v:.2f}" for k, v in bd["parts"].items())
            fails.append(f"{_PART_TO_NAME[target]} did not fire ({others})")
    if "shape" in expected and not result["quiet"]:
        if bd["shape"] != expected["shape"]:
            fails.append(f"shape {bd['shape']!r}, expected {expected['shape']!r}")
    if expected.get("hazard"):
        if not result["hazard"]:
            fails.append("no ground hazard detected")
        elif "direction" in expected and result["direction"] != expected["direction"]:
            fails.append(f"direction {result['direction']!r}, expected {expected['direction']!r}")
    return fails


def load_corpus(directory: str) -> dict:
    """{label: [(path, array), ...]} — label parsed from the filename prefix."""
    if not os.path.isdir(directory):
        raise SystemExit(f"no such directory: {directory}")
    groups = defaultdict(list)
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".npy"):
            continue
        label = re.sub(r"_\d+$", "", os.path.splitext(name)[0])
        path = os.path.join(directory, name)
        arr = np.load(path).astype(np.float32)
        if arr.ndim != 2:
            print(f"  skipping {name}: expected 2-D, got shape {arr.shape}")
            continue
        groups[label].append((path, arr))
    if not groups:
        raise SystemExit(f"no .npy frames found in {directory}/ — "
                         f"run the pipeline with SV_CAPTURE=1 first")
    return groups


def _fmt_dist(counter: Counter, n: int, top: int = 2) -> str:
    if not counter:
        return "-"
    return " ".join(f"{k} {v * 100 // n}%" for k, v in counter.most_common(top))


def score(directory: str, zone_choice: str, annotate_dir: str = None, verbose: bool = False) -> None:
    groups = load_corpus(directory)
    total_frames = sum(len(v) for v in groups.values())
    print(f"\n=== {directory}/  —  {total_frames} frames, {len(groups)} labels ===\n")

    rows, confusion, all_fail_reasons = [], {}, {}

    for label in sorted(groups):
        frames = groups[label]
        expected = expectation_for(label)
        winners, shapes, directions = Counter(), Counter(), Counter()
        passes, hazards, reasons = 0, 0, Counter()
        fired, needed = 0, 0
        sums = {z: 0 for z in ZONE_NAMES}
        driving_values = []
        target = (expected or {}).get("fires")

        for path, arr in frames:
            r = analyse_frame(arr, zone_choice)
            driving_values.append((r["breakdown"]["value"], path, r))
            for z in ZONE_NAMES:
                sums[z] += r["intensities"][z]
            if r["quiet"]:
                winners["quiet"] += 1
            else:
                winners[r["breakdown"]["winner"]] += 1
                shapes[r["breakdown"]["shape"]] += 1
            if target:
                if r["breakdown"]["parts"].get(target, 0.0) > FIRE_MIN:
                    fired += 1
                if necessity(r["breakdown"]["parts"], target):
                    needed += 1
            if r["hazard"]:
                hazards += 1
                directions[r["direction"]] += 1
            fails = check(r, expected)
            if not fails:
                passes += 1
            for f in fails:
                reasons[f] += 1

        n = len(frames)
        rows.append({
            "label": label, "n": n, "target": target,
            "pass": None if expected is None else passes / n,
            "fired": fired / n if target else None,
            "needed": needed / n if target else None,
            "means": {z: sums[z] / n for z in ZONE_NAMES},
            "winners": winners, "shapes": shapes,
            "hazard_rate": hazards / n, "directions": directions,
        })
        confusion[label] = winners
        if reasons:
            all_fail_reasons[label] = reasons

        if annotate_dir:
            driving_values.sort(key=lambda t: t[0])
            _write_annotated(annotate_dir, label, driving_values[len(driving_values) // 2])

    # ------------------------------------------------------------- summary
    print(f"{'label':<16}{'n':>5}{'pass':>7}{'fires':>7}{'only':>7}   {'mean L/C/R':>14}   "
          f"{'driving':<18}{'shape':<13}{'hazard':>7}")
    print("-" * 104)
    for r in rows:
        m = r["means"]
        lcr = f"{m['left']:.0f}/{m['center']:.0f}/{m['right']:.0f}"
        pct = "  n/a" if r["pass"] is None else f"{r['pass'] * 100:4.0f}%"
        fires = "    -" if r["fired"] is None else f"{r['fired'] * 100:4.0f}%"
        only = "    -" if r["needed"] is None else f"{r['needed'] * 100:4.0f}%"
        haz = f"{r['hazard_rate'] * 100:.0f}%"
        if r["directions"]:
            haz += f" {_fmt_dist(r['directions'], r['n'], 1)}"
        print(f"{r['label']:<16}{r['n']:>5}{pct:>7}{fires:>7}{only:>7}   {lcr:>14}   "
              f"{_fmt_dist(r['winners'], r['n']):<18}{_fmt_dist(r['shapes'], r['n']):<13}{haz:>7}")
    print("\n  fires = the scene's own detector produced a warning")
    print("  only  = it was the ONLY one to — remove it and the scene goes silent (ablation)")
    print("          a detector at only=0% is contributing nothing this corpus can see")

    # ------------------------------------------------------------- confusion
    keys = ["quiet", "sub", "wall", "f2w"]
    print(f"\n--- Confusion: scene label -> detector that drove the warning ---")
    print(f"{'':<16}" + "".join(f"{k:>8}" for k in keys))
    for label in sorted(confusion):
        n = sum(confusion[label].values()) or 1
        cells = "".join(f"{confusion[label].get(k, 0) * 100 // n:>7}%" for k in keys)
        print(f"{label:<16}{cells}")

    # ------------------------------------------------------------- failures
    if all_fail_reasons:
        print("\n--- Why frames failed ---")
        for label, reasons in all_fail_reasons.items():
            print(f"  {label}:")
            for reason, count in reasons.most_common(4 if not verbose else None):
                print(f"    {count:>5}x  {reason}")

    unscored = [r["label"] for r in rows if r["pass"] is None]
    if unscored:
        print(f"\nNo expectation matched (reported but not graded): {', '.join(unscored)}")
        print("Rename the capture label to include a keyword — see --rules.")
    print()


def _write_annotated(out_dir: str, label: str, entry) -> None:
    """Write the representative frame for a label as an annotated PNG."""
    import cv2
    from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_view import cv2_draw_depth

    _, _, r = entry
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{label}.png")
    cv2.imwrite(path, cv2_draw_depth(r["small"], r["intensities"], r["hazard"],
                                     r["severity"], r["direction"]))
    print(f"  annotated {label} -> {path}")


def print_rules() -> None:
    print("\nLabel keyword -> expectation (first match wins)\n")
    for pattern, expected in RULES:
        parts = []
        if expected.get("quiet"):
            parts.append(f"all zones <= {QUIET_MAX}/255, no hazard")
        if "fires" in expected:
            parts.append(f"{_PART_TO_NAME[expected['fires']]} fires")
        if "shape" in expected:
            parts.append(f"shape={expected['shape']}")
        if expected.get("hazard"):
            parts.append(f"ground hazard, direction={expected.get('direction', 'any')}")
        print(f"  {pattern}\n      -> {'; '.join(parts)}")
    print("\nSo `echo \"wall_far\" > calib_label.txt` during capture is graded "
          "against floor-to-wall.\n")


def main():
    ap = argparse.ArgumentParser(description="Score a captured depth corpus against the edge cases")
    ap.add_argument("--dir", default="depth_corpus", help="corpus directory (default: depth_corpus)")
    ap.add_argument("--zone", default="auto", choices=["auto", *ZONE_NAMES],
                    help="which zone to grade; 'auto' uses the loudest (default)")
    ap.add_argument("--annotate", metavar="DIR", help="write one annotated PNG per label")
    ap.add_argument("--verbose", action="store_true", help="list every distinct failure reason")
    ap.add_argument("--rules", action="store_true", help="print the label->expectation table and exit")
    args = ap.parse_args()

    if args.rules:
        print_rules()
        return
    score(args.dir, args.zone, args.annotate, args.verbose)


if __name__ == "__main__":
    main()
