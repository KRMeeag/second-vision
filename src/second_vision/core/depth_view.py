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

# HUD: a dedicated text panel BELOW the depth image, not painted on top of it.
# Everything textual lives there on a fixed row grid, one column per zone. See
# draw_hud for why the numbers were moved off the colormap.
FONT = cv2.FONT_HERSHEY_SIMPLEX
HUD_BG = (30, 30, 30)
HUD_PAD = 8
HUD_ROWS = 4            # zone readout, parts (2 lines), motor
HUD_ROW_H = 19
HUD_LEGEND_H = 15
HUD_H = HUD_PAD + HUD_ROWS * HUD_ROW_H + 2 * HUD_LEGEND_H + 14


def fit_text(text, max_w, scale, thickness=1, min_scale=0.30):
    """
    Return (text, scale) guaranteed to render within `max_w` pixels.

    Shrinks the font first, and only truncates once it has hit the smallest size
    still readable on the Pi's display. Every string on this view is variable
    width — intensities are 1-3 digits, the tag list is 0-4 letters, "(silenced)"
    appears and disappears — so a fixed scale that fits the widest case would be
    unreadable in the common one, and one that fits the common case spills in the
    widest. Measuring per string is the only thing that holds for both.
    """
    scale = max(scale, min_scale)
    while True:
        (w, _), _ = cv2.getTextSize(text, FONT, scale, thickness)
        if w <= max_w:
            return text, scale
        if scale <= min_scale:
            break
        scale = max(min_scale, round(scale - 0.02, 2))
    while text and cv2.getTextSize(text + "..", FONT, scale, thickness)[0][0] > max_w:
        text = text[:-1]
    return (text + ".." if text else ""), scale


def put_fitted(img, text, org, max_w, scale, color, thickness=1):
    """putText that cannot overflow `max_w` — clipped text beats invisible text."""
    text, scale = fit_text(text, max_w, scale, thickness)
    cv2.putText(img, text, org, FONT, scale, color, thickness, cv2.LINE_AA)


def put_plated(frame, text, org, scale, color, thickness=2, pad=4):
    """
    Draw text on a darkened plate.

    The colormap runs the full JET range, so a single ink color is illegible
    somewhere in every frame — white vanishes on yellow, dark vanishes on blue.
    Dimming the pixels behind the glyphs gives the text a consistent background
    to sit on regardless of what the depth map is doing underneath it.
    """
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thickness)
    x, y = org
    x0, y0 = max(0, x - pad), max(0, y - th - pad)
    x1, y1 = min(frame.shape[1], x + tw + pad), min(frame.shape[0], y + base + pad)
    roi = frame[y0:y1, x0:x1]
    if roi.size:
        frame[y0:y1, x0:x1] = (roi * 0.35).astype(roi.dtype)
    cv2.putText(frame, text, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


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

    # The legend for these marks lives in the HUD panel (see draw_hud), not here:
    # this one used to print at view_h - 40, one baseline away from the zone
    # readouts, and the two collided on every frame.
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
    (text_w, _), _ = cv2.getTextSize(text, FONT, 0.6, 2)
    put_plated(frame, text, (view_w - text_w - 12, 26), 0.6, (0, 255, 255))
    return frame


def draw_hud(zone_blocks, legend_lines, view_w=VIEW_W, hud_h=HUD_H):
    """
    Render the text panel that sits BELOW the depth image.

    `zone_blocks` is [(x0, x1, [(text, scale, color), ...]), ...] — one column per
    zone, aligned with that zone's span in the image above it, so a column is read
    under the zone it describes. `legend_lines` spans the full width underneath.

    The readouts used to be painted onto the colormap at hand-picked offsets, and
    three things went wrong there that no choice of offset can fix. The tag legend
    and the left zone's readout were both written at view_h - 48, so they printed
    through each other. The parts line is wider than the 160px left and right
    zones, so it ran across its neighbours. And the right zone's MOTOR line starts
    at x=488 and is longer than the 152px remaining, so it left the frame entirely
    and could not be read at all. Giving the text its own real estate, on a fixed
    row grid, with every string measured against its column before it is drawn,
    makes all three impossible by construction instead of by tuning constants.
    """
    hud = np.full((hud_h, view_w, 3), HUD_BG, dtype=np.uint8)
    grid_bottom = HUD_PAD + HUD_ROWS * HUD_ROW_H

    for x0, x1, lines in zone_blocks:
        col_w = max(1, (x1 - x0) - 2 * HUD_PAD)
        for i, (text, scale, color) in enumerate(lines[:HUD_ROWS]):
            baseline = HUD_PAD + HUD_ROW_H * (i + 1) - 5
            put_fitted(hud, text, (x0 + HUD_PAD, baseline), col_w, scale, color)

    # Column rules on the zone boundaries, so a reading is unambiguously tied to
    # its zone even when a shrunken string ends well short of the next column.
    for x0, _, _ in zone_blocks[1:]:
        cv2.line(hud, (x0, 2), (x0, grid_bottom), (75, 75, 75), 1)
    cv2.line(hud, (0, grid_bottom + 2), (view_w, grid_bottom + 2), (75, 75, 75), 1)

    for i, text in enumerate(legend_lines):
        baseline = grid_bottom + 2 + HUD_LEGEND_H * (i + 1)
        put_fitted(hud, text, (HUD_PAD, baseline), view_w - 2 * HUD_PAD, 0.42,
                   (185, 185, 185))
    return hud


def cv2_draw_depth(small, intensities, hazard_detected, severity, direction="none",
                   thin=None, fps=None, motors=None, levels=None):
    """
    Render the post-processing state as a BGR image: colorized depth map
    (hot = close), zone dividers, optional sub-grid overlay, per-zone intensity
    bars tagged with the active detectors, ground-hazard strip (with step
    direction: DOWN = fall hazard, UP = trip hazard), raw depth stats for
    calibration, and the depth branch's own FPS (top right).

    Returns a VIEW_W x (VIEW_H + HUD_H) image: the depth map on top, and the
    per-zone readouts in a text panel below it (draw_hud). Only the three labels
    that must be read against the picture — depth stats, FPS, hazard banner —
    stay on the image itself, each on a darkened plate so they survive whatever
    color the map puts behind them.

    `fps` is optional so off-device replay tools, which have no frame rate to
    report, can call this unchanged — the counter is simply omitted then.

    `motors` / `levels` are the post-haptics duty and level per zone (see
    core/haptics.py). They are drawn as a second line under each zone because
    they are the only numbers on this display the WEARER actually experiences:
    `intensities` is what the detectors perceived, and after the deadband,
    quantizer and PWM floor the two routinely disagree — a zone can read a
    healthy 60 and correctly drive nothing at all. Debugging the device off the
    perception numbers alone means debugging something nobody feels. Both are
    optional so replay tools that only have a depth frame still work.

    NOTE: this used to also draw a cyan contour around every pixel the
    thin-structure detector fired on. It was removed on field evidence: the
    top-hat is a SHAPE test with no notion of what a shape means, so table
    edges, chair backs, door frames and depth-map noise outlined as readily as
    cables did, and testers could not tell a real wire from an artifact. An
    overlay that is wrong as often as it is right is worse than none — it lends
    the detector a credibility the model cannot support. The `thin` numbers and
    the T tag remain, so the reading is still auditable; it is only the
    pixel-level claim that is gone.
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
    zone_blocks = []
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
        # W means the blank-wall CORRECTION fired, not merely "this looks flat".
        # detect_blank_wall now returns 0.0 unless it actually pushed the reading
        # up, so this tag no longer lights on every smooth surface in the room.
        if p["wall"] > 0.01:
            active += "W"
        if p["f2w"] > 0.01:
            active += "F"
        # The parts breakdown is split over two rows rather than one long line:
        # the left and right zones are only a quarter of the width each, and one
        # line of it can only be made to fit them by shrinking it past legibility.
        lines = [
            (f"{zone[0].upper()}={val} [{active or '-'}]", 0.55,
             (255, 255, 255) if val else (150, 150, 150)),
            (f"t{p['thin']:.2f} s{p['sub']:.2f} w{p['wall']:.2f}", 0.42, (215, 215, 215)),
            (f"f{p['f2w']:.2f} cov{bd['coverage']:.0%}", 0.42, (215, 215, 215)),
        ]

        # What the motor is actually doing. Green when driving, grey when the
        # haptic stage deliberately silenced a non-zero perception — the second
        # case is the one worth being able to see at a glance, because from the
        # outside it is indistinguishable from a detector that failed.
        if motors is not None:
            duty = motors.get(zone, 0)
            lvl = None if levels is None else levels.get(zone)
            tag = f"MOTOR {duty}" + ("" if lvl is None else f" L{lvl}")
            if duty == 0 and val > 0:
                tag += " (silenced)"
            # Grey still reads "not driving", but it is now grey-on-dark-panel
            # rather than grey-on-colormap, so it can be lifted for legibility
            # without losing the contrast against green.
            lines.append((tag, 0.48, (0, 255, 0) if duty > 0 else (190, 190, 190)))
        zone_blocks.append((x0, x1, lines))

    # Raw depth stats — the numbers needed to calibrate MIN/MAX_DEPTH_M
    put_plated(frame, f"depth p1/p50/p99: {d_lo:.2f} / {d_med:.2f} / {d_hi:.2f}",
               (8, 26), 0.6, (255, 255, 255))

    # Depth-branch frame rate, top right — separate from the detection overlay's
    # top-left FPS, which counts the user/detection frames in the other window.
    if fps is not None:
        frame = draw_depth_fps(frame, fps, view_w)
    if hazard_detected:
        # DOWN = drop-off / descending stairs (fall) — red.
        # UP   = curb / step-up (trip) — orange.
        label = {"down": "HAZARD: DROP-OFF", "up": "HAZARD: STEP-UP"}.get(direction, "HAZARD")
        color = (0, 140, 255) if direction == "up" else (0, 0, 255)
        put_plated(frame, f"{label} sev={severity}", (8, 60), 0.8, color)

    legend = [
        "active: T=thin obj  C=concentrated near  N=near surface  "
        "W=blank-wall  F=floor-to-wall",
        "subgrid 4x4: red cell=danger  white ring=worst cell  "
        "red bar=zone intensity  t/s/w/f=detector parts",
    ]
    return np.vstack([frame, draw_hud(zone_blocks, legend, view_w)])