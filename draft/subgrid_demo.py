"""
Sub-grid pooling demo — Small / thin object edge case (Second Vision, depth).

Visualizes, side by side on the SAME synthetic depth scene, why sub-grid
pooling matters:

    LEFT  panel  = OLD behaviour: one near-cluster percentile over the whole zone
    RIGHT panel  = NEW behaviour: each zone split into a 4x4 grid, worst cell wins

A thin "pole" sweeps across the view. The old method averages it away and the
Center bar stays low (MISSED); the new method lights up the single 4x4 cell the
pole falls in, so the Center bar spikes (DETECTED). Uses the real depth_utils
functions so the demo matches production behaviour exactly.

Run:
    python -m hailo_apps.python.pipeline_apps.custom_depth_detection.subgrid_demo
    python -m ...subgrid_demo --live      # interactive window (q to quit)
    python -m ...subgrid_demo --save out  # write out.mp4 + out.png, no display

Nothing here touches the running pipeline or the detection side — it is a
standalone teaching/demo tool.
"""
import argparse
import os

import cv2
import numpy as np

from hailo_apps.python.pipeline_apps.custom_depth_detection.depth_utils import (
    SUBGRID_SHAPE,
    MIN_DEPTH_M,
    MAX_DEPTH_M,
    compute_proximity,
    subgrid_proximity,
    to_motor_intensity,
)

GRID_W, GRID_H = 64, 48          # the real working resolution
SCALE = 9                        # upscale factor for display
VIEW_W, VIEW_H = GRID_W * SCALE, GRID_H * SCALE
HEADER_H, FOOTER_H, GAP, CAPTION_H = 46, 96, 28, 30
BAR_MAX = 48                     # fixed bar height so labels never collide
DANGER = 0.5                     # cell proximity at/above this is "danger" (for highlight)

FAR = 46.0                       # open background, well beyond the safe-zone cutoff
ZONE_BOUNDS = (GRID_W // 4, 3 * GRID_W // 4)   # 25 / 50 / 25 split


def make_scene(pole_x: int) -> np.ndarray:
    """Far-field background with a 1-column near 'pole' at column pole_x."""
    depth = np.full((GRID_H, GRID_W), FAR, dtype=np.float32)
    depth[:, pole_x] = MIN_DEPTH_M              # the thin obstacle
    # a little sensor speckle so the demo isn't unrealistically clean
    ys = np.random.randint(0, GRID_H, 12)
    xs = np.random.randint(0, GRID_W, 12)
    depth[ys, xs] = np.random.uniform(MIN_DEPTH_M, FAR, 12)
    return depth


def colorize(depth: np.ndarray) -> np.ndarray:
    """JET colormap, inverted so near = red (matches the app's preview)."""
    lo, hi = np.percentile(depth, [2, 98])
    norm = np.clip((hi - depth) / max(hi - lo, 1e-6), 0.0, 1.0)
    small = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    return cv2.resize(small, (VIEW_W, VIEW_H), interpolation=cv2.INTER_NEAREST)


def zone_slices(depth: np.ndarray):
    q1, q3 = ZONE_BOUNDS
    return {"left": depth[:, :q1], "center": depth[:, q1:q3], "right": depth[:, q3:]}


def zone_x_ranges():
    q1, q3 = ZONE_BOUNDS
    return {"left": (0, q1), "center": (q1, q3), "right": (q3, GRID_W)}


def cell_boxes(x0: int, x1: int, grid=SUBGRID_SHAPE):
    """Yield (gx0, gy0, gx1, gy1, cell_cols, cell_rows) grid-space boxes for a zone,
    matching np.array_split's boundaries exactly."""
    rows, cols = grid
    row_edges = np.linspace(0, GRID_H, rows + 1).astype(int)
    col_edges = np.linspace(x0, x1, cols + 1).astype(int)
    for r in range(rows):
        for c in range(cols):
            yield col_edges[c], row_edges[r], col_edges[c + 1], row_edges[r + 1]


def draw_zone_dividers(canvas):
    for gx in ZONE_BOUNDS:
        x = gx * SCALE
        cv2.line(canvas, (x, 0), (x, VIEW_H), (255, 255, 255), 2)


def draw_subgrid(canvas, depth):
    """Outline every 4x4 cell; fill the danger cells red-ish, ring the worst one."""
    for zone, (x0, x1) in zone_x_ranges().items():
        best_box, best_val = None, 0.0
        for gx0, gy0, gx1, gy1 in cell_boxes(x0, x1):
            cell = depth[gy0:gy1, gx0:gx1]
            if cell.size == 0:
                continue
            val = compute_proximity(cell)
            p0 = (gx0 * SCALE, gy0 * SCALE)
            p1 = (gx1 * SCALE, gy1 * SCALE)
            cv2.rectangle(canvas, p0, p1, (60, 60, 60), 1)      # faint grid
            if val >= DANGER:
                overlay = canvas.copy()
                cv2.rectangle(overlay, p0, p1, (0, 0, 255), -1)
                cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0, canvas)
            if val > best_val:
                best_val, best_box = val, (p0, p1)
        if best_box and best_val >= DANGER:
            cv2.rectangle(canvas, best_box[0], best_box[1], (255, 255, 255), 2)


def draw_footer(panel, intensities, tag):
    """Three labelled L/C/R bars under the view."""
    bar_top = HEADER_H + VIEW_H + 12
    slot = VIEW_W // 3
    for i, zone in enumerate(("left", "center", "right")):
        val = intensities[zone]                          # 0..255
        h = int(BAR_MAX * val / 255)
        cx0 = i * slot + 14
        cx1 = (i + 1) * slot - 14
        base = bar_top + BAR_MAX
        color = (0, 0, 255) if val >= 128 else (0, 200, 255) if val > 10 else (90, 90, 90)
        cv2.rectangle(panel, (cx0, base - h), (cx1, base), color, -1)
        cv2.rectangle(panel, (cx0, bar_top), (cx1, base), (120, 120, 120), 1)
        cv2.putText(panel, f"{zone[0].upper()}={val}", (cx0, base + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(panel, tag, (14, HEADER_H + VIEW_H + 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)


def build_panel(depth, title, subtitle, use_subgrid):
    panel = np.full((HEADER_H + VIEW_H + FOOTER_H, VIEW_W, 3), 24, np.uint8)
    view = colorize(depth)
    draw_zone_dividers(view)
    if use_subgrid:
        draw_subgrid(view, depth)
    panel[HEADER_H:HEADER_H + VIEW_H, :] = view

    agg = subgrid_proximity if use_subgrid else compute_proximity
    intensities = {z: to_motor_intensity(agg(s)) for z, s in zone_slices(depth).items()}

    cv2.putText(panel, title, (14, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    detected = intensities["center"] >= 128
    verdict = "CENTER: DETECTED" if detected else "CENTER: MISSED"
    vcolor = (80, 255, 80) if detected else (80, 80, 255)
    cv2.putText(panel, verdict, (VIEW_W - 250, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, vcolor, 2, cv2.LINE_AA)
    draw_footer(panel, intensities, subtitle)
    return panel


def render_frame(pole_x, depth=None):
    if depth is None:
        depth = make_scene(pole_x)
    old = build_panel(depth, "OLD  whole-zone", "one percentile over the whole zone", False)
    new = build_panel(depth, f"NEW  sub-grid {SUBGRID_SHAPE[0]}x{SUBGRID_SHAPE[1]}",
                      "worst 4x4 cell wins -> thin objects survive", True)
    ph, pw = old.shape[:2]
    frame = np.full((ph + CAPTION_H, pw * 2 + GAP, 3), 18, np.uint8)
    frame[:ph, :pw] = old
    frame[:ph, pw + GAP:] = new
    cv2.putText(frame, "Second Vision  |  thin-object edge case  |  a thin pole sweeps L->R",
                (14, ph + CAPTION_H - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (170, 170, 170), 1, cv2.LINE_AA)
    return frame


def sweep_columns(n_frames):
    """Pole x-positions sweeping across, dwelling in the center zone."""
    xs = np.linspace(4, GRID_W - 5, n_frames).astype(int)
    return xs


def main():
    ap = argparse.ArgumentParser(description="Sub-grid pooling thin-object demo")
    ap.add_argument("--live", action="store_true", help="show an interactive window (q quits)")
    ap.add_argument("--save", metavar="PREFIX", default=None,
                    help="write PREFIX.mp4 + PREFIX.png instead of / in addition to display")
    ap.add_argument("--frames", type=int, default=90, help="animation length")
    ap.add_argument("--fps", type=int, default=12)
    args = ap.parse_args()

    xs = sweep_columns(args.frames)
    can_display = args.live or (not args.save and os.environ.get("DISPLAY"))

    writer, still_saved = None, False
    if args.save:
        sample = render_frame(GRID_W // 2)
        h, w = sample.shape[:2]
        writer = cv2.VideoWriter(f"{args.save}.mp4",
                                 cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (w, h))

    for i, x in enumerate(xs):
        frame = render_frame(int(x))
        if writer is not None:
            writer.write(frame)
            # save the still at the moment the pole is dead-center (best contrast)
            if not still_saved and abs(int(x) - GRID_W // 2) <= 1:
                cv2.imwrite(f"{args.save}.png", frame)
                still_saved = True
        if can_display:
            cv2.imshow("Second Vision - sub-grid pooling demo", frame)
            if cv2.waitKey(int(1000 / args.fps)) & 0xFF == ord("q"):
                break

    if writer is not None:
        if not still_saved:
            cv2.imwrite(f"{args.save}.png", render_frame(GRID_W // 2))
        writer.release()
        print(f"wrote {args.save}.mp4 and {args.save}.png")
    if can_display:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
