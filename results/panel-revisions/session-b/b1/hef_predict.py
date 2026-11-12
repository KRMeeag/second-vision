#!/usr/bin/env python3
"""Run a detection HEF over a folder of images and write predictions for a backend-agnostic scorer.

Output, one detection per line, in the ORIGINAL image frame:
    <image_id> <class_idx> <conf> <x1> <y1> <x2> <y2>
image_id is the file stem. class_idx is the HEF's own index, in metadata.yaml order (0-based).
A <out>.meta.json beside the output records the settings, HEF hash, counts and wall-clock.
--json also writes the format eval_map.py actually reads: {"<stem>": [[x1, y1, x2, y2, conf, class_id], ...]},
normalized to the original image, with an entry for every image including those with no boxes.

Preprocessing copies Ultralytics 8.4.118 validation with rect=False (data/base.py load_image, then
LetterBox(new_shape=640, scaleup=False), then BGR to RGB), and predictions are mapped back the way
ops.scale_boxes does. So the FP32 side must be run with rect=False for the two to share input geometry.

HailoRT usage follows hailo_apps' HailoInfer and the docstrings in hailo_platform/pyhailort/pyhailort.py:
the NMS output is a list with one (n, 5) array per class, holding y_min, x_min, y_max, x_max, score,
normalized to the 640x640 input.

    --selftest           checks the geometry on synthetic images. Needs no device.
    --conf/--iou/--max-per-class   override HailoRT's post-processing at run time. Omitted: as compiled.
    --per-image-cap N    keeps the top N boxes per image after NMS, the rule Ultralytics applies as max_det.

The boot service need not stop: the chip is shared through hailort_service and output is deterministic.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

IMGSZ = 640
EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def preprocess(bgr: np.ndarray, imgsz: int = IMGSZ) -> tuple[np.ndarray, float, int, int]:
    """Return (RGB uint8 imgsz x imgsz image, gain, pad_x, pad_y), as Ultralytics validation builds them."""
    h0, w0 = bgr.shape[:2]
    r = imgsz / max(h0, w0)
    im = bgr
    if r != 1:
        w, h = min(math.ceil(w0 * r), imgsz), min(math.ceil(h0 * r), imgsz)
        im = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_LINEAR)
    h, w = im.shape[:2]
    dw, dh = (imgsz - w) / 2, (imgsz - h) / 2
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    assert im.shape[:2] == (imgsz, imgsz), f"letterbox produced {im.shape}"
    # Ultralytics takes gain from the height ratio for both axes (ratio_pad[0][0] in scale_boxes).
    return im[..., ::-1].copy(), h / h0, left, top


def to_original(box_norm: np.ndarray, gain: float, pad_x: int, pad_y: int, w0: int, h0: int,
                imgsz: int = IMGSZ) -> tuple[float, float, float, float]:
    """Map y_min, x_min, y_max, x_max (normalized to the input) to clipped x1, y1, x2, y2 in the original image."""
    y1, x1, y2, x2 = (float(v) * imgsz for v in box_norm[:4])
    return (min(max((x1 - pad_x) / gain, 0.0), w0), min(max((y1 - pad_y) / gain, 0.0), h0),
            min(max((x2 - pad_x) / gain, 0.0), w0), min(max((y2 - pad_y) / gain, 0.0), h0))


def selftest() -> int:
    """Draw a known rectangle, push it through preprocess, find it again, map it back and compare."""
    worst = 0.0
    sizes = [(480, 640), (640, 480), (1080, 1920), (1920, 1080), (481, 641), (200, 300), (300, 200),
             (640, 640), (1000, 1000), (333, 777), (777, 333), (37, 900)]
    for h0, w0 in sizes:
        img = np.zeros((h0, w0, 3), np.uint8)
        x1, y1, x2, y2 = int(w0 * .2), int(h0 * .3), int(w0 * .7), int(h0 * .8)
        img[y1:y2, x1:x2] = 255
        rgb, gain, px, py = preprocess(img)
        mask = rgb[..., 0] > 200
        ys, xs = np.where(mask)
        norm = np.array([ys.min() / IMGSZ, xs.min() / IMGSZ, (ys.max() + 1) / IMGSZ, (xs.max() + 1) / IMGSZ])
        got = to_original(norm, gain, px, py, w0, h0)
        # One pixel of error at 640 is 1/gain pixels in the original image.
        err = max(abs(a - b) for a, b in zip(got, (x1, y1, x2, y2))) * gain
        # Extreme strips: Ultralytics rounds the short side up, then uses the height ratio for both axes, so its
        # own mapping drifts. Mirrored on purpose so both backends share one geometry. Reported, not failed.
        extreme = max(w0, h0) / min(w0, h0) > 8
        status = "known Ultralytics rounding" if extreme and err > 1.5 else "ok" if err <= 1.5 else "FAIL"
        print(f"  {w0:>5}x{h0:<5} gain={gain:.4f} pad=({px},{py}) worst error at 640 scale={err:.2f}px {status}")
        if status == "FAIL":
            return 1
        if not extreme:
            worst = max(worst, err)
    # A wide probe gets padded top and bottom. Red must stay red, which confirms the channel flip, and the
    # padding must be 114, the value Ultralytics' LetterBox uses.
    probe = np.zeros((5, 10, 3), np.uint8)
    probe[..., 2] = 200
    rgb, *_ = preprocess(probe)
    if rgb[320, 320].tolist() != [200, 0, 0] or rgb[0, 0].tolist() != [114, 114, 114]:
        print(f"  probe FAIL: centre {rgb[320, 320].tolist()}, corner {rgb[0, 0].tolist()}")
        return 1
    print(f"selftest PASS, worst {worst:.2f}px at 640 scale on normal shapes; channels RGB; padding 114")
    return 0


def class_names(hef: Path) -> list[str]:
    """Class names in index order, from the metadata.yaml delivered beside the HEF."""
    import yaml
    meta = yaml.safe_load((hef.parent / "metadata.yaml").read_text())
    return [meta["names"][i] for i in sorted(meta["names"])]


def hailort_service_active() -> bool:
    """Same check the app makes: is the HailoRT daemon there to own the device?"""
    return subprocess.run(["systemctl", "is-active", "--quiet", "hailort.service"], timeout=5).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hef", type=Path)
    ap.add_argument("--images", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--conf", type=float)
    ap.add_argument("--iou", type=float)
    ap.add_argument("--max-per-class", type=int)
    ap.add_argument("--per-image-cap", type=int)
    ap.add_argument("--json", type=Path, help="also write eval_map.py's predictions.json format here")
    ap.add_argument("--limit", type=int, help="first N images only; for smoke tests")
    ap.add_argument("--draw", type=Path, help="save annotated copies of the first --draw-count images here")
    ap.add_argument("--draw-count", type=int, default=6)
    ap.add_argument("--draw-conf", type=float, default=0.3)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.hef and a.images and a.out):
        ap.error("--hef, --images and --out are required")

    from hailo_platform import FormatType, HailoSchedulingAlgorithm, VDevice

    names = class_names(a.hef)
    files = sorted(p for p in a.images.rglob("*") if p.suffix.lower() in EXTENSIONS and not p.name.startswith("._"))
    stems = [p.stem for p in files]
    if len(set(stems)) != len(stems):
        raise SystemExit("Two images share a file stem, so image_id would be ambiguous.")
    if a.limit:
        files = files[:a.limit]
    if not files:
        raise SystemExit(f"No images under {a.images}")

    params = VDevice.create_params()
    params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
    params.group_id = "SHARED"
    params.multi_process_service = hailort_service_active()

    a.out.parent.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc)
    t0 = time.monotonic()
    boxes_total = boxes_dropped_by_image_cap = unreadable = images_at_class_cap = 0
    class_counts = [0] * len(names)
    json_out: dict[str, list[list[float]]] = {}
    min_conf, max_in_class = math.inf, 0

    with VDevice(params) as vdevice:
        model = vdevice.create_infer_model(str(a.hef))
        model.set_batch_size(1)
        model.input().set_format_type(FormatType.UINT8)
        out = model.output()
        out.set_format_type(FormatType.FLOAT32)
        if a.conf is not None:
            out.set_nms_score_threshold(a.conf)
        if a.iou is not None:
            out.set_nms_iou_threshold(a.iou)
        if a.max_per_class is not None:
            out.set_nms_max_proposals_per_class(a.max_per_class)
        out_buffer = np.empty(model.output().shape, dtype=np.float32)

        with model.configure() as configured, a.out.open("w") as f:
            for n, path in enumerate(files):
                bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if bgr is None:
                    unreadable += 1
                    continue
                h0, w0 = bgr.shape[:2]
                rgb, gain, px, py = preprocess(bgr)
                bindings = configured.create_bindings()
                bindings.input().set_buffer(rgb)
                bindings.output().set_buffer(out_buffer)
                configured.run([bindings], 30000)
                per_class = bindings.output().get_buffer()
                if len(per_class) != len(names):
                    raise SystemExit(f"HEF returned {len(per_class)} classes, metadata.yaml has {len(names)}")

                dets = []
                at_cap = False
                for cls, arr in enumerate(per_class):
                    max_in_class = max(max_in_class, len(arr))
                    cap = a.max_per_class if a.max_per_class is not None else 100
                    at_cap = at_cap or len(arr) >= cap
                    dets.extend((float(d[4]), cls, d) for d in arr)
                images_at_class_cap += at_cap
                dets.sort(key=lambda t: -t[0])
                if a.per_image_cap is not None and len(dets) > a.per_image_cap:
                    boxes_dropped_by_image_cap += len(dets) - a.per_image_cap
                    dets = dets[:a.per_image_cap]

                canvas = bgr.copy() if a.draw and n < a.draw_count else None
                rows = json_out.setdefault(path.stem, [])
                for score, cls, d in dets:
                    x1, y1, x2, y2 = to_original(d, gain, px, py, w0, h0)
                    f.write(f"{path.stem} {cls} {score:.6f} {x1:.3f} {y1:.3f} {x2:.3f} {y2:.3f}\n")
                    rows.append([x1 / w0, y1 / h0, x2 / w0, y2 / h0, score, cls])
                    boxes_total += 1
                    class_counts[cls] += 1
                    min_conf = min(min_conf, score)
                    if canvas is not None and score >= a.draw_conf:
                        p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
                        cv2.rectangle(canvas, p1, p2, (0, 255, 0), 2)
                        cv2.putText(canvas, f"{names[cls]} {score:.2f}", (p1[0] + 2, max(p1[1] - 4, 12)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
                if canvas is not None:
                    a.draw.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(a.draw / f"{path.stem}.jpg"), canvas)

    seconds = time.monotonic() - t0
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(json_out))
    done = len(files) - unreadable
    meta = {
        "hef": str(a.hef), "hef_sha256": hashlib.sha256(a.hef.read_bytes()).hexdigest(),
        "images_dir": str(a.images), "images_seen": len(files), "images_unreadable": unreadable,
        "overrides": {"conf": a.conf, "iou": a.iou, "max_per_class": a.max_per_class},
        "per_image_cap": a.per_image_cap,
        "multi_process_service": params.multi_process_service,
        "boxes_written": boxes_total, "boxes_dropped_by_image_cap": boxes_dropped_by_image_cap,
        "lowest_conf_written": None if min_conf is math.inf else round(min_conf, 6),
        "largest_class_count_in_one_image": max_in_class, "images_with_a_class_at_its_cap": images_at_class_cap,
        "boxes_per_class": dict(zip(names, class_counts)),
        "started_utc": started.isoformat(timespec="seconds"), "wall_clock_s": round(seconds, 1),
        "images_per_s": round(done / seconds, 2) if seconds else None,
    }
    Path(f"{a.out}.meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps({k: meta[k] for k in ("overrides", "per_image_cap", "boxes_written", "lowest_conf_written",
                                           "largest_class_count_in_one_image", "images_with_a_class_at_its_cap",
                                           "boxes_dropped_by_image_cap", "wall_clock_s", "images_per_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
