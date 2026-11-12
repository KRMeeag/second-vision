#!/usr/bin/env python3
"""B1: score the 12 object-detection tracker cases from the deployed model's detections, through the REAL callback.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python \
        results/panel-revisions/session-b/b1/evaluate_b1.py [--test ~/v3p_eval/test]

Inputs (this folder): selection.json (select_b1.py), predictions.json (hef_predict.py: the deployed HEF on the
Hailo-8, letterboxed to 640, HailoRT NMS at the app's score 0.3 / IoU 0.45; boxes normalised to the image).

Each image's detections go through pipeline/callbacks.py's _process_real_detections, the function the app runs
on every detection frame, with duck-typed Hailo objects (the repo's test harness pattern): the model's own label
names, its confidences and boxes, one track id per box. The same frame is given twice, 0.35 s apart, so the
callback's 0.3 s confirmation window passes as it would on a still scene. What it puts in the speech mailbox,
and its track table, are the system's answer. Two confidence levels matter and both are reported:
  model    a box HailoRT's NMS kept (score >= 0.3, the app's nms-score-threshold)
  system   a box the callback acts on (score >= callbacks.CONFIDENCE_THRESHOLD, 0.70): tracked, zoned, spoken
A box "finds" a labelled object when it has the same class and IoU >= 0.5 with it.

Writes b1-results.csv (one row per case, the tracker's columns), b1-details.csv (one row per image) and prints a
summary. Nothing in the app is changed: thresholds, weights and zones are the code's own.
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "src"))

from second_vision.core import priority  # noqa: E402
from second_vision.core.priority import PriorityMailbox  # noqa: E402
from second_vision.pipeline import callbacks  # noqa: E402

NAMES = ["Person", "Vehicle", "Motorcycle", "Animals", "Doors", "Chairs", "Tables", "Tricycle", "Potholes",
         "Trash Bins", "Bicycle", "Stairs", "Bench"]
MODEL_CONF = 0.3
SYSTEM_CONF = callbacks.CONFIDENCE_THRESHOLD
IOU = 0.5
CONFIRM_WAIT = callbacks.MIN_CONFIRMATION_SECONDS + 0.05


# ---- duck-typed Hailo objects (tests/test_detection_classes.py's harness) ----
class _BBox:
    def __init__(self, x1, y1, x2, y2):
        self._v = (x1, y1, x2 - x1, y2 - y1)

    def xmin(self): return self._v[0]
    def ymin(self): return self._v[1]
    def width(self): return self._v[2]
    def height(self): return self._v[3]
    def xmax(self): return self._v[0] + self._v[2]
    def ymax(self): return self._v[1] + self._v[3]


class _TrackId:
    def __init__(self, i): self._i = i
    def get_id(self): return self._i


class _Det:
    def __init__(self, label, track_id, conf, box):
        self._label, self._tid, self._conf, self._box = label, track_id, conf, _BBox(*box)

    def get_label(self): return self._label
    def get_confidence(self): return self._conf
    def get_bbox(self): return self._box

    def get_objects_typed(self, kind):
        return [_TrackId(self._tid)]


class _Roi:
    def __init__(self, dets): self._dets = dets
    def get_objects_typed(self, kind): return list(self._dets)


callbacks.hailo = SimpleNamespace(HAILO_DETECTION="HAILO_DETECTION", HAILO_UNIQUE_ID="HAILO_UNIQUE_ID",
                                  get_roi_from_buffer=lambda b: b)


def new_user_data():
    return SimpleNamespace(track_history={}, IDs_changed_zones=set(), head_turn_cooldown_until=0.0, use_frame=False,
                           tts_queue=PriorityMailbox(), get_count=lambda: 1, get_det_fps=lambda: 0.0)


def run_callback(preds, frames=None):
    """preds: [[x1,y1,x2,y2,conf,cls], ...]. frames: optional list of earlier frames (lists of preds, same track
    ids by position) given first. Returns (payload or None, track_history, per-track priority at the last frame)."""
    ud = new_user_data()
    sequence = (frames or []) + [preds]
    for frame in sequence:
        dets = [_Det(NAMES[int(p[5])], i + 1, float(p[4]), p[:4]) for i, p in enumerate(frame)]
        callbacks._process_real_detections(None, _Roi(dets), ud)
        time.sleep(CONFIRM_WAIT)
        callbacks._process_real_detections(None, _Roi(dets), ud)
    payload = ud.tts_queue.peek()
    prios = {}
    for i, p in enumerate(preds):
        h = ud.track_history.get(i + 1)
        if h is not None:
            area = (p[2] - p[0]) * (p[3] - p[1])
            prios[i + 1] = priority.compute_priority(float(p[4]), area, h["zone"], h["label"], None, 0.0)
    return payload, ud.track_history, prios


def gt_boxes(test: Path, stem: str):
    rows = [r.split() for r in (test / "labels" / f"{stem}.txt").read_text().splitlines() if r.strip()]
    out = []
    for r in rows:
        c, cx, cy, w, h = int(r[0]), *map(float, r[1:])
        out.append((c, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    return out


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def finds(preds, gt, conf):
    """Best-confidence box of gt's class with IoU >= 0.5 at or above conf, or None."""
    hits = [p for p in preds if int(p[5]) == gt[0] and p[4] >= conf and iou(p[:4], gt[1:]) >= IOU]
    return max(hits, key=lambda p: p[4]) if hits else None


def top_on(preds, gt, conf):
    """The most confident box of ANY class overlapping gt (IoU >= 0.5) at or above conf."""
    hits = [p for p in preds if p[4] >= conf and iou(p[:4], gt[1:]) >= IOU]
    return max(hits, key=lambda p: p[4]) if hits else None


def recall(preds, gts, conf):
    """Greedy one-to-one matching, most confident first (the usual detection-recall rule)."""
    free = list(range(len(gts)))
    for p in sorted((p for p in preds if p[4] >= conf), key=lambda p: -p[4]):
        best, bi = 0.0, None
        for k in free:
            if int(p[5]) == gts[k][0]:
                v = iou(p[:4], gts[k][1:])
                if v >= IOU and v > best:
                    best, bi = v, k
        if bi is not None:
            free.remove(bi)
    return len(gts) - len(free)


def fmt(p):
    return f"{NAMES[int(p[5])]} {p[4]:.2f}" if p is not None else "none"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=Path, default=Path.home() / "v3p_eval" / "test")
    a = ap.parse_args()
    sel = json.loads((HERE / "selection.json").read_text())
    preds_all = json.loads((HERE / "predictions.json").read_text())
    snap = {}
    try:
        import yaml
        snap = yaml.safe_load((REPO / "docs" / "testing" / "uat-snapshot.yaml").read_text())["cases"]
    except Exception:
        pass
    details, results = [], []
    today = dt.date.today().isoformat()

    def P(stem):
        return preds_all.get(stem, [])

    def detail(case, stem, **kw):
        details.append({"case": case, "image": stem, **kw})

    def result(case, ok, actual, comments, images):
        results.append({"test_case_id": case, "scenario_title": snap.get(case, {}).get("scenario_title", ""),
                        "input": f"{images} image(s) from ~/v3p_eval/test, chosen by select_b1.py (selection.json)",
                        "expected": snap.get(case, {}).get("expected", ""), "actual": actual,
                        "status": "Passed" if ok else "Failed", "comments": comments, "date_tested": today})

    # ---- TC-001-001 / TC-001-003: correct label at confidence > 0.5 ----
    def per_class_case(case, groups):
        oks, lines = [], []
        for cls_name, stems in groups.items():
            for s in stems:
                gt = gt_boxes(a.test, s)[0]
                top = top_on(P(s), gt, 0.0)
                ok = top is not None and int(top[5]) == gt[0] and top[4] > 0.5
                payload, _, _ = run_callback(P(s))
                spoken = payload["label"] if payload else "nothing"
                oks.append(ok)
                lines.append(f"{cls_name}: {fmt(top)}{'' if ok else ' FAIL'}")
                detail(case, s, expected=cls_name, model_top_on_object=fmt(top), passed=ok, announced=spoken)
        return all(oks), sum(oks), len(oks), "; ".join(lines)

    ok, n, tot, txt = per_class_case("TC-001-001", sel["TC-001-001"]["images"])
    result("TC-001-001", ok, f"{n} of {tot} images: the most confident box on the object has the right class at > 0.5. {txt}",
           "Pole dropped (13-class model). Score = the most confident box overlapping the labelled object (IoU >= 0.5).",
           tot)

    # ---- TC-001-002: confusion pairs ----
    pairs = sel["TC-001-002"]["pairs"]
    oks, lines, cross_model = [], [], 0
    for cls_name, stems in sel["TC-001-002"]["images"].items():
        for s in stems:
            gt = gt_boxes(a.test, s)[0]
            own = finds(P(s), gt, 0.5)
            others = [p for p in P(s) if NAMES[int(p[5])] in pairs[cls_name] and iou(p[:4], gt[1:]) >= IOU]
            cross = [p for p in others if p[4] > 0.5]
            cross_model += bool(others)
            ok = own is not None and not cross
            oks.append(ok)
            payload, _, _ = run_callback(P(s))
            lines.append(f"{cls_name}: own {fmt(own)}, paired class {', '.join(fmt(p) for p in others) or 'none'}"
                         f"{'' if ok else ' FAIL'}")
            detail("TC-001-002", s, expected=cls_name, own_label=fmt(own), paired_class_boxes=[fmt(p) for p in others],
                   passed=ok, announced=payload["label"] if payload else "nothing")
    result("TC-001-002", all(oks), f"{sum(oks)} of {len(oks)} images pass. " + "; ".join(lines),
           "Pairs Motorcycle/Bicycle (the tracker's example), Bicycle/Tricycle and Chairs/Bench (the brief). Pass: "
           "own class at > 0.5 and no box of the paired class above 0.5 on the object. Paired-class boxes at any "
           f"score >= 0.3 on the object: {cross_model} image(s).", len(oks))

    # ---- TC-001-003 ----
    ok, n, tot, txt = per_class_case("TC-001-003", sel["TC-001-003"]["images"])
    result("TC-001-003", ok, f"{n} of {tot} images correct at > 0.5. {txt}",
           "The tracker's expected result is leftover template text (a password message), so TC-001-001's criterion "
           "is applied. Thinnest classes taken from the test split's instance counts (the training set is not on the "
           "Pi): " + sel["TC-001-003"]["rule"].split("(")[1].split(")")[0] + ".", tot)

    # ---- TC-002-001: person over a lower-priority class ----
    oks, lines = [], []
    for s in sel["TC-002-001"]["images"]:
        payload, hist, prios = run_callback(P(s))
        gts = gt_boxes(a.test, s)
        person_sys = finds(P(s), next(g for g in gts if g[0] == 0), SYSTEM_CONF)
        other = next(g for g in gts if g[0] != 0)
        other_sys = finds(P(s), other, SYSTEM_CONF)
        ok = payload is not None and payload["label"] == "person"
        both = person_sys is not None and other_sys is not None
        oks.append(ok)
        lines.append(f"{NAMES[other[0]]} image: announced {payload['label'] + ' ' + payload['zone'] if payload else 'nothing'}"
                     f" (person {fmt(person_sys)}, {NAMES[other[0]]} {fmt(other_sys)} at the system level)")
        detail("TC-002-001", s, other_class=NAMES[other[0]], announced=payload["label"] if payload else "nothing",
               person=fmt(person_sys), other=fmt(other_sys), both_reach_callback=both,
               priorities={hist[k]["label"]: round(v, 3) for k, v in prios.items()}, passed=ok)
    result("TC-002-001", all(oks), f"{sum(oks)} of {len(oks)} images announce the person. " + "; ".join(lines),
           "Lower priority = a class at the default weight 0.5 (person 0.7); vehicles, bicycles, tricycles and "
           "motorcycles outrank a person by design and were not used. Only 1 image in the split has a person and a "
           "trash bin alone at comparable sizes.", len(oks))

    # ---- TC-002-002: the nearer of two people ----
    oks, lines = [], []
    for s in sel["TC-002-002"]["images"]:
        gts = sorted(gt_boxes(a.test, s), key=lambda g: -(g[3] - g[1]) * (g[4] - g[2]))
        near, far = finds(P(s), gts[0], SYSTEM_CONF), finds(P(s), gts[1], SYSTEM_CONF)
        payload, hist, prios = run_callback(P(s))
        winner = None
        if payload is not None:
            winner = next((k for k, v in prios.items() if abs(v - payload["priority"]) < 1e-9), None)
        near_tid = next((i + 1 for i, p in enumerate(P(s)) if p is near), None)
        far_tid = next((i + 1 for i, p in enumerate(P(s)) if p is far), None)
        ok = payload is not None and winner is not None and winner == near_tid
        oks.append(ok)
        if payload is None:
            said = "nothing announced"
        else:
            wp = P(s)[winner - 1] if winner else None
            which = ("the nearer person" if winner == near_tid else "the farther person" if winner == far_tid else
                     f"a box on neither labelled person ({fmt(wp)}, IoU with the nearer "
                     f"{iou(wp[:4], gts[0][1:]):.2f})" if wp is not None else "an unidentified box")
            said = f"announced '{payload['label']} {payload['zone']}' = {which}"
        lines.append(f"nearer {fmt(near)}, farther {fmt(far)} at the system level; {said}")
        detail("TC-002-002", s, nearer=fmt(near), farther=fmt(far), announced_track=winner, nearer_track=near_tid,
               passed=ok, both_reach_callback=near is not None and far is not None)
    result("TC-002-002", all(oks), f"{sum(oks)} of {len(oks)} images announce the nearer person. " + "; ".join(lines),
           "Nearer = the larger labelled box (at least 3x the other's area); the code's proximity proxy is box area. "
           "The announced track is found by matching the mailbox item's priority to each track's.", len(oks))

    # ---- TC-002-003: zones ----
    oks, lines = [], []
    for zone, stems in sel["TC-002-003"]["images"].items():
        s = stems[0]
        payload, _, _ = run_callback(P(s))
        gt = gt_boxes(a.test, s)[0]
        ok = payload is not None and payload["zone"] == zone and payload["label"] == callbacks.DETECTION_CLASS_MAP[NAMES[gt[0]]]
        oks.append(ok)
        best = top_on(P(s), gt, 0.0)
        why = ""
        if payload is None:
            why = (f" (the object's best box: {fmt(best)}, below the callback's 0.70; that box would be zoned "
                   f"'{callbacks._get_detection_zone(_BBox(*best[:4]))}')" if best is not None else
                   " (no box on the object at all)")
        lines.append(f"{zone}: {NAMES[gt[0]]} at x={(gt[1] + gt[3]) / 2:.2f} -> "
                     f"{payload['label'] + ' ' + payload['zone'] if payload else 'nothing announced'}{why}")
        detail("TC-002-003", s, expected_zone=zone, announced=f"{payload['label']} {payload['zone']}" if payload else "nothing",
               passed=ok)
    result("TC-002-003", all(oks), f"{sum(oks)} of 3 zone images announced in the right zone. " + "; ".join(lines),
           "Zones from callbacks.py: left < 0.22, centre 0.25-0.75, right > 0.78 (box centre across).", 3)

    # ---- TC-002-004: the hysteresis bands ----
    # The zone comes from the MODEL's box, not the label, so an image chosen by its label can miss the band. What
    # the selected band images gave is reported; the zone logic itself is tested with a real detection the callback
    # acts on (the centre image of TC-002-003, score >= 0.70) placed with its centre in the middle of each band.
    observed = []
    for band, stems in sel["TC-002-004"]["images"].items():
        s = stems[0]
        gt = gt_boxes(a.test, s)[0]
        best = top_on(P(s), gt, 0.0)
        observed.append(f"{band} image ({NAMES[gt[0]]}, labelled centre x={(gt[1] + gt[3]) / 2:.3f}): model box "
                        + (f"{fmt(best)} centred at x={(best[0] + best[2]) / 2:.3f}" if best is not None else "none"))
        detail("TC-002-004", s, band=band, model_box=fmt(best),
               model_center_x=round((best[0] + best[2]) / 2, 3) if best is not None else "")
    cs = sel["TC-002-003"]["images"]["center"][0]
    src = finds(P(cs), gt_boxes(a.test, cs)[0], SYSTEM_CONF)
    lines, flagged, kept, n = [], 0, 0, 0
    if src is not None:
        w = src[2] - src[0]
        at = lambda x: [x - w / 2, src[1], x + w / 2, src[3], src[4], src[5]]
        for band, (x, inner) in {"left band 0.22-0.25": (0.235, "center"), "right band 0.75-0.78": (0.765, "center")}.items():
            n += 1
            payload, hist, _ = run_callback([at(x)])
            flagged += payload is not None
            payload2, hist2, _ = run_callback([at(x)], frames=[[at(0.5)]])
            kept += hist2[1]["zone"] == inner
            lines.append(f"{band}, centre x={x}: as a new object -> "
                         f"{payload['label'] + ' ' + payload['zone'] if payload else 'not announced'}; after being "
                         f"in the centre zone -> zone '{hist2[1]['zone']}'")
    result("TC-002-004", n > 0 and flagged == 0,
           f"As written (\"must not flag any classes within this zone\"): an object in the band WAS flagged in "
           f"{flagged} of {n} band positions. " + "; ".join(lines) + ". Selected images: " + "; ".join(observed),
           f"The tracker and the code disagree. The code (callbacks._get_detection_zone) gives a NEW object in a band "
           f"the outer zone and lets a TRACKED object keep its previous zone (the brief's wording, \"keeps its "
           f"previous zone\", holds in {kept} of {n}). Marked against the tracker's text. The band positions are a "
           f"real detection ({fmt(src)}, from the TC-002-003 centre image) moved across: the zone depends on the "
           f"model's box, and the images chosen by their labels did not put a box the callback acts on in a band.", n)

    # ---- TC-002-005: empty scenes ----
    empties = sel["TC-002-005"]["images"]
    at = {c: sum(1 for s in empties if any(p[4] >= c for p in P(s))) for c in (MODEL_CONF, 0.5, SYSTEM_CONF)}
    announced, fp_classes = [], {}
    for s in empties:
        if any(p[4] >= SYSTEM_CONF for p in P(s)):
            payload, _, _ = run_callback(P(s))
            if payload is not None:
                announced.append(s)
                fp_classes[payload["label"]] = fp_classes.get(payload["label"], 0) + 1
        detail("TC-002-005", s, boxes_model=sum(p[4] >= MODEL_CONF for p in P(s)),
               boxes_system=sum(p[4] >= SYSTEM_CONF for p in P(s)), announced=s in announced)
    # Each announced image was looked at (b1-empty-scene-review.csv): an "empty" label file can hide a real
    # object of a trained class that the dataset never labelled.
    review = {}
    rf = HERE / "b1-empty-scene-review.csv"
    if rf.exists():
        review = {r["image"]: r["verdict"] for r in csv.DictReader(open(rf))}
    false_alarms = [s for s in announced if review.get(s) == "false alarm"]
    unlabelled = [s for s in announced if review.get(s) == "real object not labelled"]
    unreviewed = [s for s in announced if s not in review]
    truly_empty = len(empties) - len(unlabelled)
    result("TC-002-005", not false_alarms and not unreviewed,
           f"{len(empties)} images with an empty label file: a box at >= 0.3 in {at[MODEL_CONF]}, >= 0.5 in {at[0.5]}, "
           f">= 0.70 in {at[SYSTEM_CONF]}; something announced for {len(announced)} "
           f"({', '.join(f'{k} {v}' for k, v in sorted(fp_classes.items())) or 'none'}). Looked at by eye: "
           f"{len(unlabelled)} show a real person, vehicle, table, chair or door the dataset did not label; "
           f"{len(false_alarms)} are false alarms ({', '.join(s.split('__')[-1] for s in false_alarms)}: a pantry "
           f"item and a figurine, both announced as 'person'); {len(unreviewed)} not reviewed. False announcements: "
           f"{len(false_alarms)} of the {truly_empty} images that are really empty.",
           "Every image in the split whose label file is empty. Pass only if no really empty image produces an "
           "announcement. The review is by eye, from the images with the system's boxes drawn "
           "(b1-empty-scene-review.csv).", len(empties))

    # ---- TC-003-001: low light ----
    tier_lines, tier_ok = [], {}
    for tier, stems in sel["TC-003-001"]["images"].items():
        oks = []
        for s in stems:
            gts = [g for g in gt_boxes(a.test, s) if (g[3] - g[1]) * (g[4] - g[2]) >= 0.01]
            classes = sorted({g[0] for g in gts})
            got_sys = {c for c in classes if any(finds(P(s), g, SYSTEM_CONF) for g in gts if g[0] == c)}
            got_model = {c for c in classes if any(finds(P(s), g, MODEL_CONF) for g in gts if g[0] == c)}
            ok = got_sys == set(classes)
            oks.append(ok)
            tier_lines.append(f"{tier} {s.split('__')[-1]}: expected {', '.join(NAMES[c] for c in classes)}; system "
                              f"found {', '.join(NAMES[c] for c in sorted(got_sys)) or 'none'}; model (>= 0.3) found "
                              f"{', '.join(NAMES[c] for c in sorted(got_model)) or 'none'}")
            detail("TC-003-001", s, tier=tier, expected=[NAMES[c] for c in classes],
                   system=[NAMES[c] for c in sorted(got_sys)], model=[NAMES[c] for c in sorted(got_model)], passed=ok)
        tier_ok[tier] = (sum(oks), len(oks))
    ok_all = all(v[0] == v[1] for v in tier_ok.values())
    result("TC-003-001", ok_all, "; ".join(tier_lines),
           "Two tiers: the 3 darkest images in the split (mean grey 1-3 of 255) and 3 moderate (mean grey 20-40). "
           f"Darkest {tier_ok['darkest'][0]}/{tier_ok['darkest'][1]}, moderate {tier_ok['moderate'][0]}/"
           f"{tier_ok['moderate'][1]} images with every expected class found at the system level (>= 0.70). "
           "Expected = classes with a labelled box of at least 1 % of the image.", sum(v[1] for v in tier_ok.values()))

    # ---- TC-003-002: motion blur ----
    oks, lines = [], []
    for s in sel["TC-003-002"]["images"]:
        gt = max(gt_boxes(a.test, s), key=lambda g: (g[3] - g[1]) * (g[4] - g[2]))
        sysb, modb = finds(P(s), gt, SYSTEM_CONF), top_on(P(s), gt, MODEL_CONF)
        ok = sysb is not None
        oks.append(ok)
        lines.append(f"{NAMES[gt[0]]}: system {fmt(sysb)}; most confident box on it {fmt(modb)}")
        detail("TC-003-002", s, expected=NAMES[gt[0]], system=fmt(sysb), model_top=fmt(modb), passed=ok)
    result("TC-003-002", all(oks), f"{sum(oks)} of {len(oks)} motion-blurred images: the object's class found at "
           f"the system level. " + "; ".join(lines),
           "Chosen by blur statistics, then by eye (selection.json lists the shortlist).", len(oks))

    # ---- TC-003-003: crowded ----
    oks, lines = [], []
    for s, info in sel["TC-003-003"]["images"].items():
        c = NAMES.index(info["class"])
        gts = [g for g in gt_boxes(a.test, s) if g[0] == c]
        rs, rm = recall(P(s), gts, SYSTEM_CONF), recall(P(s), gts, MODEL_CONF)
        ok = rs >= 0.7 * len(gts)
        oks.append(ok)
        areas = sorted((g[3] - g[1]) * (g[4] - g[2]) for g in gts)
        med = areas[len(areas) // 2]
        lines.append(f"{info['class']} x{len(gts)} (median box {100 * med:.2f} % of the image): system {rs} "
                     f"({100 * rs / len(gts):.0f} %), model {rm} ({100 * rm / len(gts):.0f} %)")
        detail("TC-003-003", s, cls=info["class"], instances=len(gts), found_system=rs, found_model=rm, passed=ok)
    result("TC-003-003", all(oks), f"{sum(oks)} of {len(oks)} crowded images reach 70 % at the system level. " +
           "; ".join(lines), "Instances found = one-to-one matches at IoU >= 0.5, most confident first.", len(oks))

    # ---- TC-003-004: partial occlusion ----
    oks, lines = [], []
    for s in sel["TC-003-004"]["images"]:
        gts = gt_boxes(a.test, s)

        def ov(p, q):
            ix = max(0.0, min(p[3], q[3]) - max(p[1], q[1]))
            iy = max(0.0, min(p[4], q[4]) - max(p[2], q[2]))
            return ix * iy / max(min((p[3] - p[1]) * (p[4] - p[2]), (q[3] - q[1]) * (q[4] - q[2])), 1e-9)
        pairs_ = [(ov(p, q), p, q) for i, p in enumerate(gts) for q in gts[i + 1:]
                  if 0.3 <= ov(p, q) <= 0.8 and min((p[3] - p[1]) * (p[4] - p[2]), (q[3] - q[1]) * (q[4] - q[2])) >= 0.02]
        _, g1, g2 = max(pairs_, key=lambda t: t[0])
        f1, f2 = finds(P(s), g1, SYSTEM_CONF), finds(P(s), g2, SYSTEM_CONF)
        ok = f1 is not None and f2 is not None
        oks.append(ok)
        lines.append(f"{NAMES[g1[0]]} / {NAMES[g2[0]]} overlapping {100 * ov(g1, g2):.0f} %: {fmt(f1)} / {fmt(f2)}")
        detail("TC-003-004", s, pair=f"{NAMES[g1[0]]}/{NAMES[g2[0]]}", first=fmt(f1), second=fmt(f2), passed=ok)
    result("TC-003-004", all(oks), f"{sum(oks)} of {len(oks)} images: both overlapping objects found at the system "
           "level. " + "; ".join(lines), "Overlap = intersection over the smaller box, 30-80 %.", len(oks))

    with open(HERE / "b1-results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(results)
    with open(HERE / "b1-details.csv", "w", newline="") as fh:
        keys = sorted({k for d in details for k in d}, key=lambda k: (k not in ("case", "image"), k))
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        w.writerows(details)
    for r in results:
        print(f"{r['test_case_id']}  {r['status']:<7} {r['actual']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
