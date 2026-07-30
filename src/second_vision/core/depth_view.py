"""
Depth post-processing visualization — pure rendering, no hardware dependencies.

Deliberately imports only numpy/cv2/depth_utils (NO gi, hailo, GStreamer), so the
exact same view can be produced:
  * on the Pi, inside the display process (see sv_dual_callback_withdepth), and
  * on any laptop, from a saved depth frame or a hand-drawn scene
    (see verify_scene.py) — no Hailo device required.

Keeping one implementation means what you verify off-device is what actually runs.
"""
import os

import cv2
import numpy as np

from second_vision.core.depth_utils import (
    DANGER_CELL,
    MIN_DEPTH_M,
    SUBGRID_SHAPE,
    subgrid_cell_edges,
    subgrid_cell_proximities,
    zone_warning_breakdown,
)

# Sub-grid overlay: 4x4 grid per zone, shaded danger cells, white ring on the
# worst (nearest) cell. Rendering happens off the inference thread, so it is ON by
# default to keep the edge cases verifiable. Disable with SV_SUBGRID_OVERLAY=0.
SHOW_SUBGRID_OVERLAY = os.environ.get("SV_SUBGRID_OVERLAY", "1") != "0"
# (DANGER_CELL is imported from depth_utils so overlay and breakdown agree)

# Color scale: FIXED by default (red = at/below MIN_DEPTH_M, blue = at/beyond
# COLOR_FAR), the same ruler the demos use, so colors mean the same thing in
# every frame and every room. The old per-frame grading painted the farthest
# part of ANY scene blue — even the middle of a close wall — which read as
# "safe" when it wasn't. SV_COLOR_RELATIVE=1 restores per-frame contrast.
COLOR_FAR = 50.0    # model units; tighten after calibration measures open scenes
USE_RELATIVE_COLOR = os.environ.get("SV_COLOR_RELATIVE", "0") == "1"

VIEW_W, VIEW_H = 640, 480


def zone_grid_cells(small, x0, x1, grid=SUBGRID_SHAPE):
    """
    Yield (gx0, gy0, gx1, gy1, proximity) in grid coordinates for each sub-cell
    of a zone.

    Reuses depth_utils.subgrid_cell_proximities (one vectorized percentile per
    zone) and its matching cell edges, so the overlay is an exact reflection of
    the real computation rather than a recomputed approximation.
    """
    zone = small[:, x0:x1]
    prox = subgrid_cell_proximities(zone, grid)
    row_edges, col_edges = subgrid_cell_edges(zone.shape[0], zone.shape[1], grid)
    if prox.shape != (len(row_edges) - 1, len(col_edges) - 1):
        return  # tiny-zone fallback shape; nothing meaningful to outline
    for r in range(prox.shape[0]):
        for c in range(prox.shape[1]):
            yield (x0 + int(col_edges[c]), int(row_edges[r]),
                   x0 + int(col_edges[c + 1]), int(row_edges[r + 1]), float(prox[r, c]))


def draw_subgrid_overlay(frame, small, view_w=VIEW_W, view_h=VIEW_H):
    """
    Draw the 4x4 sub-grid on each zone: faint grid lines, red-shaded danger cells,
    and a white ring on the worst (nearest) cell per zone — the cell that actually
    drives that zone's intensity. One overlay copy + one blend keeps it cheap.
    """
    h, w = small.shape
    sx, sy = view_w / w, view_h / h
    zone_bounds = {"left": (0, w // 4), "center": (w // 4, 3 * w // 4), "right": (3 * w // 4, w)}

    danger_rects, rings = [], []
    for x0, x1 in zone_bounds.values():
        best_box, best_val = None, 0.0
        for gx0, gy0, gx1, gy1, val in zone_grid_cells(small, x0, x1):
            p0 = (int(gx0 * sx), int(gy0 * sy))
            p1 = (int(gx1 * sx), int(gy1 * sy))
            cv2.rectangle(frame, p0, p1, (70, 70, 70), 1)   # faint grid (opaque, cheap)
            if val >= DANGER_CELL:
                danger_rects.append((p0, p1))
            if val > best_val:
                best_val, best_box = val, (p0, p1)
        if best_box and best_val >= DANGER_CELL:
            rings.append(best_box)

    if danger_rects:
        overlay = frame.copy()
        for p0, p1 in danger_rects:
            cv2.rectangle(overlay, p0, p1, (0, 0, 255), -1)
        frame = cv2.addWeighted(overlay, 0.30, frame, 0.70, 0)
    for p0, p1 in rings:
        cv2.rectangle(frame, p0, p1, (255, 255, 255), 2)

    cv2.putText(frame, "subgrid 4x4: red cell=danger  ring=worst", (8, view_h - 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def draw_thin_mask(frame, thin_mask, view_w=VIEW_W, view_h=VIEW_H):
    """
    Outline the pixels the thin-structure detector fired on, in cyan.

    Without this the detector is unfalsifiable by eye: a cable is invisible in
    the colourized depth map (that is exactly why the magnitude detectors miss
    it), so "did it see the wire?" could only be answered from numbers. Drawn as
    contours rather than a fill so a 1-2 px structure stays visible.
    """
    mask = cv2.resize(thin_mask.astype(np.uint8) * 255, (view_w, view_h),
                      interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(frame, contours, -1, (255, 255, 0), 2)
    return frame


def draw_depth_fps(frame, fps, view_w=VIEW_W):
    """
    Draw the DEPTH branch's frame rate in the top-right corner.

    Deliberately its own number, in its own window, in the opposite corner from
    the detection overlay's top-left "FPS:". The depth and detection branches run
    independently off the tee and do NOT keep step — depth inference is the
    heavier of the two — so reading one rate as if it were the other misreports
    exactly the thing this is here to show. Right-aligned via getTextSize so the
    label stays pinned to the corner as the digits change width.
    """
    text = f"DEPTH FPS: {fps:.1f}"
    (text_w, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.putText(frame, text, (view_w - text_w - 8, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    return frame


def cv2_draw_depth(small, intensities, hazard_detected, severity, direction="none",
                   thin=None, thin_mask=None, fps=None):
    """
    Render the post-processing state as a BGR image: colorized depth map
    (hot = close), zone dividers, optional sub-grid overlay, per-zone intensity
    bars tagged with the active detectors, ground-hazard strip (with step
    direction: DOWN = fall hazard, UP = trip hazard), raw depth stats for
    calibration, and the depth branch's own FPS (top right).

    `fps` is optional so off-device replay tools, which have no frame rate to
    report, can call this unchanged — the counter is simply omitted then.
    """
    view_w, view_h = VIEW_W, VIEW_H

    d_lo, d_med, d_hi = np.percentile(small, [1, 50, 99])
    # Invert so near = hot (red), far = cold (blue)
    if USE_RELATIVE_COLOR:
        span = max(d_hi - d_lo, 1e-6)
        norm = np.clip((d_hi - small) / span, 0.0, 1.0)
    else:
        norm = np.clip((COLOR_FAR - small) / (COLOR_FAR - MIN_DEPTH_M), 0.0, 1.0)
    colored = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    frame = cv2.resize(colored, (view_w, view_h), interpolation=cv2.INTER_NEAREST)

    if SHOW_SUBGRID_OVERLAY:
        frame = draw_subgrid_overlay(frame, small, view_w, view_h)

    if thin_mask is not None and np.any(thin_mask):
        frame = draw_thin_mask(frame, thin_mask, view_w, view_h)

    # Zone dividers (25/50/25 split used by compute_zone_intensities)
    q1, q3 = view_w // 4, 3 * view_w // 4
    cv2.line(frame, (q1, 0), (q1, view_h), (255, 255, 255), 2)
    cv2.line(frame, (q3, 0), (q3, view_h), (255, 255, 255), 2)

    # Ground-hazard strip boundary (bottom 25% of the frame).
    # Red = drop-off (fall), orange = step-up (trip), yellow = clear.
    strip_y = int(view_h * 0.75)
    if hazard_detected:
        strip_color = (0, 140, 255) if direction == "up" else (0, 0, 255)
    else:
        strip_color = (0, 255, 255)
    cv2.line(frame, (0, strip_y), (view_w, strip_y), strip_color, 2)

    # Per-zone intensity bars, tagged with WHICH edge-case detector is active, so
    # blank-wall / floor-to-wall are verifiable too, not just thin objects.
    gw = small.shape[1]
    grid_zones = {
        "left": small[:, :gw // 4],
        "center": small[:, gw // 4: 3 * gw // 4],
        "right": small[:, 3 * gw // 4:],
    }
    bar_max_h = view_h // 3
    zone_spans = {"left": (0, q1), "center": (q1, q3), "right": (q3, view_w)}
    for zone, (x0, x1) in zone_spans.items():
        val = intensities[zone]
        bar_h = int(bar_max_h * val / 255)
        if bar_h > 0:
            overlay = frame.copy()
            cv2.rectangle(overlay, (x0 + 4, view_h - bar_h), (x1 - 4, view_h), (0, 0, 255), -1)
            frame = cv2.addWeighted(overlay, 0.5, frame, 0.5, 0)

        # Show every ACTIVE detector, not just the winner. T now comes from the
        # real thin-structure detector (cable/pole/branch/railing), which is the
        # only one that can tell a narrow object from a surface. The near-cluster
        # detector keeps its own shape label — C (concentrated) vs N (broad near
        # surface) — because it fires on any close thing, wall included; it used
        # to claim "thin object" on a wall, which was misleading (live Pi finding).
        bd = zone_warning_breakdown(grid_zones[zone],
                                    None if thin is None else thin.get(zone))
        p = bd["parts"]
        active = ""
        if p["thin"] > 0.01:
            active += "T"
        if p["sub"] > 0.01:
            active += "N" if bd["shape"] == "broad" else "C"
        if p["wall"] > 0.01:
            active += "W"
        if p["f2w"] > 0.01:
            active += "F"
        cv2.putText(frame, f"{zone[0].upper()}={val} [{active or '-'}]", (x0 + 8, view_h - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(frame,
                    f"t{p['thin']:.2f} s{p['sub']:.2f} w{p['wall']:.2f} f{p['f2w']:.2f} "
                    f"cov{bd['coverage']:.0%}",
                    (x0 + 8, view_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1, cv2.LINE_AA)

    # Raw depth stats — the numbers needed to calibrate MIN/MAX_DEPTH_M
    cv2.putText(frame, f"depth p1/p50/p99: {d_lo:.2f} / {d_med:.2f} / {d_hi:.2f}",
                (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Depth-branch frame rate, top right — separate from the detection overlay's
    # top-left FPS, which counts the user/detection frames in the other window.
    if fps is not None:
        frame = draw_depth_fps(frame, fps, view_w)
    if hazard_detected:
        # DOWN = drop-off / descending stairs (fall) — red.
        # UP   = curb / step-up (trip) — orange.
        label = {"down": "HAZARD: DROP-OFF", "up": "HAZARD: STEP-UP"}.get(direction, "HAZARD")
        color = (0, 140, 255) if direction == "up" else (0, 0, 255)
        cv2.putText(frame, f"{label} sev={severity}", (8, 52),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    cv2.putText(frame,
                "active: T=thin obj (cyan outline)  C=concentrated near  N=near surface  "
                "W=blank-wall  F=floor-to-wall",
                (8, view_h - 48), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

    return frame