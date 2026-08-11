"""
Off-device edge-case verifier — run the REAL depth post-processing on a picture.

Needs only numpy + opencv (no Hailo, no GStreamer, no Pi), so you can check the
detectors on your own laptop instead of waiting on whoever has the hardware.

Accepts either:
  * a .npy depth array captured on the Pi (exactly what the model produced), or
  * a grayscale PICTURE used as a depth map — bright = near by default, so you can
    literally draw a test scene in any image editor and see how the detectors react.

Usage
-----
    # generate a set of ready-made test scenes (one per edge case)
    python -m ...verify_scene --make-samples samples/

    # run the pipeline on one scene -> annotated PNG + printed report
    python -m ...verify_scene --input samples/thin_pole.png --out result.png

    # a real capture from the Pi
    python -m ...verify_scene --input depth_frame.npy

    # dark = near instead
    python -m ...verify_scene --input scene.png --invert

The annotated output is produced by the same depth_view.cv2_draw_depth the live
app uses, so what you see here is what the device does.
"""
import argparse
import os

import cv2
import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_utils import (
    MIN_DEPTH_M,
    DepthPostProcessor,
    crop_border,
    detect_ground_hazard,
    downsample_depth,
    zone_warning_breakdown,
)
from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_view import cv2_draw_depth

FAR_VALUE = 50.0          # what pure black (or white with --invert) maps to


def depth_from_image(path: str, invert: bool = False) -> np.ndarray:
    """Read a picture as a depth map: brightness -> distance in model units."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise SystemExit(f"could not read image: {path}")
    v = img.astype(np.float32) / 255.0          # 0 (black) .. 1 (white)
    if invert:
        v = 1.0 - v
    # bright (1.0) -> nearest, dark (0.0) -> far
    return (FAR_VALUE - v * (FAR_VALUE - MIN_DEPTH_M)).astype(np.float32)


def load_scene(path: str, invert: bool = False) -> np.ndarray:
    if path.lower().endswith(".npy"):
        arr = np.load(path).astype(np.float32)
        if arr.ndim != 2:
            raise SystemExit(f"expected a 2-D depth array, got shape {arr.shape}")
        return arr
    return depth_from_image(path, invert)


def analyse(depth: np.ndarray):
    """Run the exact production pipeline; returns (small, intensities, hazard, sev, direction)."""
    depth = crop_border(depth)
    small = downsample_depth(depth)
    intensities = DepthPostProcessor().process(small)   # fresh EMA: single-frame result
    hazard, severity, direction = detect_ground_hazard(small, small.shape[0])
    return small, intensities, hazard, severity, direction


def report(small, intensities, hazard, severity, direction) -> None:
    w = small.shape[1]
    zones = {
        "left": small[:, :w // 4],
        "center": small[:, w // 4: 3 * w // 4],
        "right": small[:, 3 * w // 4:],
    }
    lo, med, hi = np.percentile(small, [1, 50, 99])
    print(f"\n  raw depth p1/p50/p99 : {lo:.2f} / {med:.2f} / {hi:.2f}   (relative model units)")
    dir_note = {"down": "DOWN — drop-off, fall hazard", "up": "UP — curb/step, trip hazard"}.get(direction, "-")
    print(f"  ground hazard        : {hazard}  (severity {severity}, direction {dir_note})")
    print(f"\n  {'zone':<8}{'out':>5}   {'near':>6}{'wall':>7}{'floor2wall':>12}{'cov':>7}   active")
    for name, sl in zones.items():
        bd = zone_warning_breakdown(sl)
        p = bd["parts"]
        active = ""
        if p["sub"] > 0.01:
            active += "N" if bd["shape"] == "broad" else "T"
        if p["wall"] > 0.01:
            active += "W"
        if p["f2w"] > 0.01:
            active += "F"
        print(f"  {name:<8}{intensities[name]:>5}   {p['sub']:>6.2f}{p['wall']:>7.2f}"
              f"{p['f2w']:>12.2f}{bd['coverage']:>7.0%}   [{active or '-'}]")
    print("\n  active: T=thin object  N=near surface (broad)  W=blank-wall  F=floor-to-wall\n")


# --------------------------------------------------------------------------- #
# Ready-made sample scenes (written as PNGs you can open, edit and re-run)
# --------------------------------------------------------------------------- #

def _to_png(depth: np.ndarray) -> np.ndarray:
    """Inverse of depth_from_image, so samples round-trip."""
    v = (FAR_VALUE - depth) / (FAR_VALUE - MIN_DEPTH_M)
    return np.clip(v * 255.0, 0, 255).astype(np.uint8)


def make_samples(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    H, W = 192, 256
    rng = np.random.default_rng(0)
    scenes = {}

    # open room: far, textured -> everything should stay quiet
    scenes["open_room"] = np.clip(rng.normal(42, 7, (H, W)), MIN_DEPTH_M, FAR_VALUE)

    # thin pole in the centre -> sub-grid only
    s = np.full((H, W), 44.0); s[:, W // 2 - 2: W // 2 + 2] = MIN_DEPTH_M
    scenes["thin_pole"] = s

    # blank wall filling the view, close and flat -> variance detector
    scenes["blank_wall"] = np.full((H, W), 23.0) + rng.normal(0, 0.6, (H, W))

    # near floor at the bottom, wall above that the model reads as FAR
    prof = np.concatenate([np.linspace(19, 20, H // 4), np.full(H - H // 4, 46.0)])[::-1]
    scenes["wall_read_as_far"] = np.repeat(prof[:, None], W, axis=1) + rng.normal(0, 0.12, (H, W))

    # floor receding then dropping away -> ground hazard.
    # NOTE: detect_ground_hazard only inspects the bottom GROUND_STRIP_FRACTION
    # (25%) of the frame, so the break must sit inside that strip to be seen — a
    # drop-off further ahead appears higher up and is out of its view by design.
    floor_rows = H // 8
    prof = np.concatenate([np.linspace(19, 24, floor_rows), np.full(H - floor_rows, 52.0)])[::-1]
    scenes["dropoff_stairs"] = np.repeat(prof[:, None], W, axis=1) + rng.normal(0, 0.3, (H, W))

    # receding floor that breaks NEARER: a curb / step-up (trip hazard).
    # The riser face sits closer than the floor's extrapolation, so walking
    # forward the depth jumps DOWNWARD in value -> direction "up".
    prof = np.concatenate([np.linspace(19, 24, floor_rows), np.full(H - floor_rows, 19.0)])[::-1]
    scenes["step_up_curb"] = np.repeat(prof[:, None], W, axis=1) + rng.normal(0, 0.3, (H, W))

    for name, depth in scenes.items():
        path = os.path.join(out_dir, f"{name}.png")
        cv2.imwrite(path, _to_png(np.asarray(depth, dtype=np.float32)))
        print(f"  wrote {path}")
    print(f"\n{len(scenes)} sample scenes in {out_dir}/  (bright = near)")
    print("Run one with:  --input " + os.path.join(out_dir, "thin_pole.png"))


def main():
    ap = argparse.ArgumentParser(description="Verify depth edge cases on a picture, off-device")
    ap.add_argument("--input", help=".npy depth array, or an image used as a depth map")
    ap.add_argument("--out", help="annotated PNG to write (default <input>_result.png)")
    ap.add_argument("--invert", action="store_true", help="treat DARK as near instead of bright")
    ap.add_argument("--make-samples", metavar="DIR", help="write ready-made test scenes and exit")
    args = ap.parse_args()

    if args.make_samples:
        make_samples(args.make_samples)
        return
    if not args.input:
        ap.error("--input is required (or use --make-samples)")

    depth = load_scene(args.input, args.invert)
    small, intensities, hazard, severity, direction = analyse(depth)

    print(f"\n=== {args.input} ===  (input {depth.shape[1]}x{depth.shape[0]})")
    report(small, intensities, hazard, severity, direction)

    out = args.out or os.path.splitext(args.input)[0] + "_result.png"
    cv2.imwrite(out, cv2_draw_depth(small, intensities, hazard, severity, direction))
    print(f"  annotated view -> {out}\n")


if __name__ == "__main__":
    main()
