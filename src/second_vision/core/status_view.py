"""The failsafe status drawn on the camera picture (and on a dark screen when there is no picture).

What is drawn (a status from core/status_board.py):
  * along the bottom, every check in a strip: CAMERA OK  VIEW OK  GLASSES OK  EARPHONES OK  PANEL OK, each
    word coloured: green working, red failing, yellow being confirmed, grey not in use;
  * at the top, one red banner per failing check ("CAMERA VIEW: BLOCKED OR TOO DARK"), with its measured
    values under it, and a yellow line for a change being confirmed ("Camera view: looks dark (2 of 3)");
  * just above the strip, for SAID_SECONDS, the last failsafe sentence the device said, since only the
    wearer hears the earphones.
Text goes through core/depth_view.py's put_plated (a darkened plate, so it reads on any picture) and
fit_text (it shrinks, then clips, rather than run off the frame). The top-left corner is left to the
detection overlay's FPS counter.

numpy and cv2 only (no gi, no hailo), so it renders off the device too. status_layout() is the pure part
(what goes where); draw_status() puts it on a BGR frame in place.
"""

import cv2
import numpy as np

from second_vision.core.depth_view import FONT, fit_text, put_plated
from second_vision.core.status_board import OFF, OK, PENDING, PROBLEM, SAID_SECONDS

COLORS = {OK: (80, 220, 80), PROBLEM: (60, 60, 255), PENDING: (0, 215, 255), OFF: (160, 160, 160)}  # BGR
LABEL_COLOR = (235, 235, 235)
DETAIL_COLOR = (235, 235, 235)
SAID_COLOR = (255, 255, 0)          # cyan
CANVAS_BG = (30, 30, 30)

MARGIN = 8
STRIP_H = 30
STRIP_SCALE = 0.45
BANNER_TOP = 64                     # first banner baseline: under the overlay's "FPS:" at y=30
BANNER_SCALE = 0.7
DETAIL_SCALE = 0.5
PENDING_SCALE = 0.6
SAID_SCALE = 0.55
GAP = 14                            # between strip items


def status_layout(status, width: int, height: int, now: float) -> dict:
    """
    Where every piece of text goes, without drawing it.

    Returns {"strip_top": y, "strip": [(text, (x, y), scale, color, thickness), ...],
    "lines": [(text, (x, y), scale, color, thickness), ...]}. Every piece fits inside `width`.
    """
    max_w = width - 2 * MARGIN
    strip_top = height - STRIP_H
    strip = _strip(status, max_w, strip_top + STRIP_H - 10)

    said_lines = []
    if status.said and status.said_at is not None and 0 <= now - status.said_at <= SAID_SECONDS:
        said = _wrap(f'Said: "{status.said}"', max_w, SAID_SCALE, 2, lines=2)
        y = strip_top - 10 - 26 * (len(said) - 1)
        for text, scale in said:
            said_lines.append((text, (MARGIN, y), scale, SAID_COLOR, 2))
            y += 26
    # The banners stop above the "Said" lines and the strip: on a small frame the last ones are left
    # out rather than drawn over them or past the bottom.
    limit = (said_lines[0][1][1] - 26) if said_lines else strip_top - 8

    lines = []
    y = BANNER_TOP
    for c in status.problems() + status.pending():
        problem = c.state == PROBLEM
        heading = f"{c.name.upper()}: {c.text}" if problem else f"{c.name}: {c.text}"
        text, scale = fit_text(heading, max_w, BANNER_SCALE if problem else PENDING_SCALE, 2)
        if y > limit:
            break
        lines.append((text, (MARGIN, y), scale, COLORS[c.state], 2))
        y += 26
        if c.detail and y <= limit:
            text, scale = fit_text(c.detail, max_w, DETAIL_SCALE, 1)
            lines.append((text, (MARGIN + 2, y), scale, DETAIL_COLOR, 1))
            y += 24
        y += 6 if problem else 0
    return {"strip_top": strip_top, "strip": strip, "lines": lines + said_lines}


def draw_status(frame, status, now: float):
    """Draw `status` on a BGR `frame` (H, W, 3), in place. Returns the frame."""
    height, width = frame.shape[:2]
    layout = status_layout(status, width, height, now)
    top = layout["strip_top"]
    frame[top:height] = (frame[top:height] * 0.25).astype(frame.dtype)   # the strip's plate
    for text, org, scale, color, thickness in layout["strip"]:
        cv2.putText(frame, text, org, FONT, scale, color, thickness, cv2.LINE_AA)
    for text, org, scale, color, thickness in layout["lines"]:
        put_plated(frame, text, org, scale, color, thickness)
    return frame


def status_canvas(width: int, height: int, status, now: float, note: str):
    """
    A dark screen with the status, for when there is no live camera picture to draw it on (no camera,
    depth-only, both rockers off). `note` says why there is no picture, in the middle of the screen.
    """
    canvas = np.full((height, width, 3), CANVAS_BG, dtype=np.uint8)
    text, scale = fit_text(note, width - 2 * MARGIN, 0.7, 2)
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, 2)
    cv2.putText(canvas, text, ((width - tw) // 2, height // 2), FONT, scale, (200, 200, 200), 2, cv2.LINE_AA)
    return draw_status(canvas, status, now)


SHORT_LABELS = {"camera": "CAM", "view": "VIEW", "glasses": "GLS", "earphones": "EAR", "panel": "PNL"}
MIN_STRIP_SCALE = 0.3


def _strip(status, max_w, baseline):
    """
    The bottom strip: label and short state per check, on one line. Shrinks the font first; on a frame
    too narrow even then, uses three-letter labels; and as a last resort leaves out what still does not
    fit, so nothing is ever drawn past the edge.
    """
    right = MARGIN + max_w
    for short_labels in (False, True):
        scale = STRIP_SCALE
        while scale >= MIN_STRIP_SCALE:
            pieces, end = _strip_pieces(status, baseline, scale, short_labels)
            if end <= right:
                return pieces
            scale = round(scale - 0.02, 2)
    pieces, _ = _strip_pieces(status, baseline, MIN_STRIP_SCALE, True)
    return [p for p in pieces if p[1][0] + cv2.getTextSize(p[0], FONT, p[2], p[4])[0][0] <= right]


def _strip_pieces(status, baseline, scale, short_labels):
    """The strip's pieces at one size, and where the last one ends."""
    pieces, x = [], MARGIN
    for c in status.checks:
        label = SHORT_LABELS.get(c.key, c.label) if short_labels else c.label
        label_w = cv2.getTextSize(label, FONT, scale, 1)[0][0]
        value_w = cv2.getTextSize(c.short, FONT, scale, 2)[0][0]
        pieces.append((label, (x, baseline), scale, LABEL_COLOR, 1))
        pieces.append((c.short, (x + label_w + 6, baseline), scale, COLORS[c.state], 2))
        x += label_w + 6 + value_w + GAP
    return pieces, x - GAP


def _wrap(text, max_w, scale, thickness, lines):
    """Split `text` at spaces into at most `lines` lines that fit `max_w`; the last one is fitted (clipped)."""
    words, out, current = text.split(), [], ""
    for i, word in enumerate(words):
        trial = f"{current} {word}".strip()
        if cv2.getTextSize(trial, FONT, scale, thickness)[0][0] <= max_w or not current:
            current = trial
            continue
        out.append(current)
        current = word
        if len(out) == lines - 1:
            current = " ".join([current] + words[i + 1:])
            break
    if current:
        out.append(current)
    return [fit_text(line, max_w, scale, thickness) for line in out[:lines]]
