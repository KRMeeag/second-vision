#!/usr/bin/env python3
"""B1: choose the test images for the 12 object-detection tracker cases from the frozen test split.

    my_hailo_env/bin/python results/panel-revisions/session-b/b1/select_b1.py [--test ~/v3p_eval/test] [--out <b1 dir>]

Kenth approved picking the images from ~/v3p_eval/test (2026-11-12): 6,403 images with YOLO labels, the split the
model was scored on. Every choice below is made from the LABELS and simple PIXEL STATISTICS only, never from the
model's output, and random choices use a fixed seed, so the picks cannot favour the model. Writes:
  selection.json        case -> images, with the rule that chose them and each image's statistics
  image-stats.csv       brightness, sharpness and blur direction of every image
  images/<stem>.<ext>   symbolic links to the chosen images (the prediction step reads this folder)

Pixel statistics, on the grey image as stored (the split is capped at 640 px on the long side):
  brightness   mean grey level, 0-255
  sharpness    variance of the Laplacian (low = blurred)
  anisotropy   ratio of the larger to the smaller of the horizontal and vertical gradient energies
               (motion blur smears one direction, so it raises this ratio; defocus does not)
"""
import argparse
import csv
import json
import random
from pathlib import Path

import cv2
import numpy as np

NAMES = ["Person", "Vehicle", "Motorcycle", "Animals", "Doors", "Chairs", "Tables", "Tricycle", "Potholes",
         "Trash Bins", "Bicycle", "Stairs", "Bench"]          # metadata.yaml order = label class ids
SEED = 20261112
EXT = {".jpg", ".jpeg", ".png"}


def boxes(label_file: Path) -> list[tuple[int, float, float, float, float]]:
    rows = [r.split() for r in label_file.read_text().splitlines() if r.strip()]
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def stats(path: Path) -> dict:
    g = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if g is None:
        return {}
    gx = cv2.Sobel(g, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_64F, 0, 1, ksize=3)
    ex, ey = float(np.mean(gx ** 2)), float(np.mean(gy ** 2))
    return {"w": g.shape[1], "h": g.shape[0], "brightness": round(float(g.mean()), 1),
            "sharpness": round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1),
            "anisotropy": round(max(ex, ey) / max(min(ex, ey), 1e-9), 2)}


def area(b) -> float:
    return b[3] * b[4]


def xyxy(b):
    return b[1] - b[3] / 2, b[2] - b[4] / 2, b[1] + b[3] / 2, b[2] + b[4] / 2


def overlap_of_smaller(a, b) -> float:
    ax1, ay1, ax2, ay2 = xyxy(a)
    bx1, by1, bx2, by2 = xyxy(b)
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    return inter / max(min(area(a), area(b)), 1e-9)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=Path, default=Path.home() / "v3p_eval" / "test")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    a = ap.parse_args()
    rng = random.Random(SEED)

    images = {p.stem: p for p in (a.test / "images").iterdir() if p.suffix.lower() in EXT}
    data = {}
    for stem, p in sorted(images.items()):
        lf = a.test / "labels" / f"{stem}.txt"
        if lf.exists():
            data[stem] = {"path": p, "boxes": boxes(lf), **stats(p)}
    with open(a.out / "image-stats.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["image", "width", "height", "boxes", "brightness", "sharpness", "anisotropy"])
        for stem, d in data.items():
            w.writerow([stem, d["w"], d["h"], len(d["boxes"]), d["brightness"], d["sharpness"], d["anisotropy"]])

    sharp_median = float(np.median([d["sharpness"] for d in data.values()]))
    clear = lambda d: 70 <= d["brightness"] <= 190 and d["sharpness"] >= sharp_median
    used: set[str] = set()
    sel: dict[str, dict] = {}

    def pick(cands: list[str], n: int) -> list[str]:
        cands = sorted(c for c in cands if c not in used)
        got = rng.sample(cands, min(n, len(cands)))
        used.update(got)
        return sorted(got)

    def single(cls: int, centred: bool, relax: bool = False) -> list[str]:
        dx, amin, amax = (0.2, 0.03, 0.8) if relax else (0.15, 0.05, 0.6)
        out = []
        for s, d in data.items():
            if len(d["boxes"]) != 1 or d["boxes"][0][0] != cls or not clear(d):
                continue
            b = d["boxes"][0]
            if not amin <= area(b) <= amax:
                continue
            if centred and not (abs(b[1] - 0.5) <= dx and abs(b[2] - 0.5) <= 0.2):
                continue
            out.append(s)
        return out

    # TC-001-001: one image per class, a single centred object in clear conditions.
    rule1 = ("exactly one labelled box; box centre within 0.15 of the image centre across and 0.2 down; box area "
             "5-60 % of the image; brightness 70-190; sharpness at or above the split's median "
             f"({sharp_median:.0f}); one image per class drawn at random (seed {SEED})")
    per_class, relaxed = {}, []
    for c, name in enumerate(NAMES):
        cands = single(c, True)
        if not cands:
            cands = single(c, True, relax=True)
            relaxed.append(name)
        per_class[name] = pick(cands, 1)
    sel["TC-001-001"] = {"rule": rule1 + (f"; relaxed to centre within 0.2 and area 3-80 % for: {', '.join(relaxed)}"
                                          if relaxed else ""), "images": per_class}

    # TC-001-002: the confusion pairs. The tracker names Motorcycle/Bicycle "like" an example; the brief names
    # Motorcycle/Bicycle, Bicycle/Tricycle and Chairs/Bench. All three pairs: 3 images of each class involved.
    pairs = {"Motorcycle": ["Bicycle"], "Bicycle": ["Motorcycle", "Tricycle"], "Tricycle": ["Bicycle"],
             "Chairs": ["Bench"], "Bench": ["Chairs"]}
    sel["TC-001-002"] = {"rule": "3 images per class of the pairs Motorcycle/Bicycle, Bicycle/Tricycle, Chairs/Bench; "
                                 "same filter as TC-001-001 without the centring condition; not reused",
                         "pairs": pairs,
                         "images": {n: pick(single(NAMES.index(n), False), 3) for n in pairs}}

    # TC-001-003: the thinnest-sourced classes. The training set is not on the Pi, so "thinnest" is taken from the
    # test split: the 3 classes with the fewest labelled instances.
    counts = {n: 0 for n in NAMES}
    for d in data.values():
        for b in d["boxes"]:
            counts[NAMES[b[0]]] += 1
    thin = sorted(NAMES, key=lambda n: counts[n])[:3]
    sel["TC-001-003"] = {"rule": f"the 3 classes with the fewest instances in the test split ({', '.join(f'{n} {counts[n]}' for n in thin)}); "
                                 "3 single-object clear images each, as TC-001-002",
                         "instance_counts": counts,
                         "images": {n: pick(single(NAMES.index(n), False), 3) for n in thin}}

    # TC-002-001: a person and a lower-priority class (the tracker's example: a trash bin). Comparable sizes, so the
    # class is what decides: the larger box at most 2x the smaller.
    P, TB = NAMES.index("Person"), NAMES.index("Trash Bins")
    cands = []
    for s, d in data.items():
        ps = [b for b in d["boxes"] if b[0] == P]
        ts = [b for b in d["boxes"] if b[0] == TB]
        if len(ps) == 1 and len(ts) == 1 and len(d["boxes"]) == 2 and 70 <= d["brightness"] <= 190:
            r = area(ps[0]) / max(area(ts[0]), 1e-9)
            if 0.5 <= r <= 2 and min(area(ps[0]), area(ts[0])) >= 0.01:
                cands.append(s)
    sel["TC-002-001"] = {"rule": "exactly one Person and one Trash Bins box and nothing else; areas within 2x of each "
                                 "other, each at least 1 % of the image; brightness 70-190; up to 3 at random",
                         "candidates": len(cands), "images": pick(cands, 3)}

    # TC-002-002: two people, one clearly nearer (box area at least 3x the other), both recognisable (>= 1 %).
    cands = []
    for s, d in data.items():
        if len(d["boxes"]) == 2 and all(b[0] == P for b in d["boxes"]) and 70 <= d["brightness"] <= 190:
            a1, a2 = sorted(area(b) for b in d["boxes"])
            if a1 >= 0.01 and a2 >= 3 * a1:
                cands.append(s)
    sel["TC-002-002"] = {"rule": "exactly two Person boxes and nothing else; the larger at least 3x the smaller's area; "
                                 "the smaller at least 1 % of the image; brightness 70-190; up to 3 at random",
                         "candidates": len(cands), "images": pick(cands, 3)}

    # TC-002-003: one image per zone, the object's centre well inside it (callbacks.py: left < 0.22, centre
    # 0.25-0.75, right > 0.78).
    zone_rule = {"left": (0.0, 0.15), "center": (0.45, 0.55), "right": (0.85, 1.0)}
    z = {}
    for zone, (lo, hi) in zone_rule.items():
        cands = [s for s, d in data.items() if len(d["boxes"]) == 1 and clear(d) and lo <= d["boxes"][0][1] <= hi
                 and 0.02 <= area(d["boxes"][0]) <= 0.3]
        z[zone] = pick(cands, 1)
    sel["TC-002-003"] = {"rule": "one single-object clear image per zone, box centre across at 0-0.15 (left), 0.45-0.55 "
                                 "(center), 0.85-1 (right); box area 2-30 %", "images": z}

    # TC-002-004: an object whose centre lies in a hysteresis band (0.22-0.25 or 0.75-0.78), one per band.
    bands = {"left band 0.22-0.25": (0.225, 0.245), "right band 0.75-0.78": (0.755, 0.775)}
    sel["TC-002-004"] = {"rule": "one single-object clear image per band, box centre across inside the band with a "
                                 "0.005 margin; box area 2-30 %",
                         "images": {k: pick([s for s, d in data.items() if len(d["boxes"]) == 1 and clear(d)
                                             and lo <= d["boxes"][0][1] <= hi and 0.02 <= area(d["boxes"][0]) <= 0.3], 1)
                                    for k, (lo, hi) in bands.items()}}

    # TC-002-005: empty scenes: every image whose label file is empty (no trained-class object).
    empty = sorted(s for s, d in data.items() if not d["boxes"])
    sel["TC-002-005"] = {"rule": "every image in the split with an empty label file", "images": empty}

    # TC-003-001: low light: the 3 darkest images (mean grey) with at least one object of at least 2 % area.
    cands = sorted((d["brightness"], s) for s, d in data.items()
                   if d["boxes"] and max(area(b) for b in d["boxes"]) >= 0.02 and d["brightness"] < 50)
    sel["TC-003-001"] = {"rule": "the 3 darkest images (mean grey < 50) with at least one labelled object of at "
                                 "least 2 % area", "candidates": len(cands), "images": [s for _, s in cands[:3]]}
    used.update(sel["TC-003-001"]["images"])

    # TC-003-002: motion blur: lowest sharpness among strongly directional images, at least one object >= 2 %.
    cands = sorted((d["sharpness"], s) for s, d in data.items()
                   if d["boxes"] and max(area(b) for b in d["boxes"]) >= 0.02 and d["anisotropy"] >= 3
                   and 50 <= d["brightness"] <= 200 and s not in used)
    sel["TC-003-002"] = {"rule": "anisotropy >= 3 (one gradient direction at least 3x the other), brightness 50-200, "
                                 "at least one object >= 2 %; the 6 least sharp, of which the first 3 that look "
                                 "motion-blurred on inspection are used (recorded in RESULTS.md)",
                         "candidates": len(cands), "images": [s for _, s in cands[:6]]}

    # TC-003-003: crowded: the 3 images with the most instances of one class.
    crowd = []
    for s, d in data.items():
        if d["boxes"]:
            cc = np.bincount([b[0] for b in d["boxes"]], minlength=13)
            crowd.append((int(cc.max()), NAMES[int(cc.argmax())], s))
    crowd.sort(key=lambda t: (-t[0], t[2]))
    sel["TC-003-003"] = {"rule": "the 3 images with the most labelled instances of a single class",
                         "images": {s: {"class": n, "instances": k} for k, n, s in crowd[:3]}}

    # TC-003-004: partial occlusion: two labelled objects overlapping by 30-80 % of the smaller box, both >= 2 %.
    cands = []
    for s, d in data.items():
        bs = d["boxes"]
        if 2 <= len(bs) <= 6 and 70 <= d["brightness"] <= 190 and s not in used:
            if any(0.3 <= overlap_of_smaller(p, q) <= 0.8 and min(area(p), area(q)) >= 0.02
                   for i, p in enumerate(bs) for q in bs[i + 1:]):
                cands.append(s)
    sel["TC-003-004"] = {"rule": "2-6 labelled objects, at least one pair overlapping by 30-80 % of the smaller box, "
                                 "both at least 2 % of the image; brightness 70-190; 3 at random",
                         "candidates": len(cands), "images": pick(cands, 3)}

    # The chosen files, linked into one folder for the prediction step.
    chosen = set()
    for case in sel.values():
        imgs = case["images"]
        vals = imgs.values() if isinstance(imgs, dict) else [imgs]
        for v in vals:
            chosen.update(v if isinstance(v, list) else [v] if isinstance(v, str) else [])
        if isinstance(imgs, dict) and all(isinstance(v, dict) for v in imgs.values()):
            chosen.update(imgs)
    link_dir = a.out / "images"
    link_dir.mkdir(exist_ok=True)
    for s in sorted(chosen):
        target = data[s]["path"]
        link = link_dir / target.name
        if not link.exists():
            link.symlink_to(target)
    for case in sel.values():
        case["image_stats"] = {}
    for name, case in sel.items():
        imgs = case["images"]
        stems = (imgs if isinstance(imgs, list) else
                 [x for v in imgs.values() for x in (v if isinstance(v, list) else [])] or list(imgs))
        if name != "TC-002-005":
            case["image_stats"] = {s: {k: data[s][k] for k in ("w", "h", "brightness", "sharpness", "anisotropy")}
                                   | {"boxes": [[NAMES[b[0]], *map(lambda v: round(v, 4), b[1:])] for b in data[s]["boxes"]]}
                                   for s in stems}
    (a.out / "selection.json").write_text(json.dumps(sel, indent=1))
    print(f"images with labels: {len(data)}; sharpness median {sharp_median:.0f}; chosen: {len(chosen)} "
          f"(of which empty scenes {len(empty)})")
    for name, case in sel.items():
        imgs = case["images"]
        n = len(imgs) if isinstance(imgs, list) else sum(len(v) if isinstance(v, list) else 1 for v in imgs.values())
        print(f"  {name}: {n} image(s); candidates {case.get('candidates', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
