"""
Depth frame capture — build a labelled corpus of REAL model output on the Pi.

Why this exists: every edge-case detector in depth_utils.py was written and
unit-tested against SYNTHETIC arrays. Real SC-DepthV3 output on the OV2640 does
not look like those arrays — the "wall reads as a dome" finding
(depth_utils.detect_blank_wall) proved that the hard way, live, by eye. This
module turns live scenes into .npy files so the detectors can be re-scored
offline, repeatably, as many times as thresholds change.

It is the missing PRODUCER for verify_scene.py, which has always been able to
replay a "captured on the Pi" .npy but had nothing to write one.

    CAPTURE (headless — no video window, works even if the preview is frozen):
        1) SV_CAPTURE=1 ./scripts/sv-main.sh      (or ./scripts/run.sh --input usb)
        2) point the camera at a scene, then in a SECOND ssh terminal:
               echo "wall_far" > calib_label.txt
           re-tag as you move ("open", "thin_pole", "stairs", ...). Frames stream
           to depth_corpus/<label>_NNNN.npy plus a manifest.csv.

        LABEL CONVENTION (what makes a capture SCOREABLE): evaluate.py needs the
        true distance, the zone the target stands in, what it is and where it
        was shot. Encode all of that in the label and it lands in the manifest
        as its own columns:

               <target>_<distance>m_<zone>_<env>       e.g.  board_1.0m_C_room2
               open_<env>                              e.g.  open_hall
               wall_0.5m_C_kitchen

           target   free text (board / person / wall / pole / cable ...; "open"
                    means a negative scene with nothing within range in any zone)
           distance metres, from the LENS PLANE to the target's nearest face,
                    e.g. 0.5m 0.75m 1.0m 1.5m 2.0m 3.0m
           zone     L / C / R (or left / center / right) — where the target is
           env      everything after: the room, used to split calibration from
                    test captures (fit the scale in one room, score in others)

           Anything that does not parse just leaves that column blank; the file
           is still saved. Per-session facts that never change between labels
           go in the environment: SV_CAM_TILT_DEG and SV_CAM_HEIGHT_M.
           A label of "pause" (or "-") saves NOTHING — set it while moving the
           target, so the corpus holds placements only.

        PROTOCOL DRIVER (second ssh terminal, walks the grid and writes the
        label file for you, pausing between placements):
               PYTHONPATH=src python3 -m second_vision.core.capture --drive --env room1
               PYTHONPATH=src python3 -m second_vision.core.capture --plan  --env room1   # progress only

    SCORE (offline, no hardware):
        score_corpus.py / verify_scene.py have NOT been ported to this repo yet —
        they still live in the prototyping repo's custom_depth_detection/. The
        .npy + manifest.csv format written here is the one they read.

Frames are saved BEFORE crop_border/downsample, i.e. exactly what the model
emitted, so the corpus can also be used to validate the border crop itself and
survives any change to the post-processing chain.

Writing happens on a background thread behind a bounded queue that DROPS when
full, so the GStreamer streaming thread is never blocked by disk I/O.
"""

import argparse
import csv
import os
import queue
import re
import sys
import threading
import time
from collections import defaultdict

import numpy as np

DEFAULT_DIR = "depth_corpus"
DEFAULT_EVERY = 3            # save every Nth depth frame (30 FPS -> ~10 saves/s)
DEFAULT_MAX_PER_LABEL = 300  # stop after this many frames for one label
QUEUE_SIZE = 8               # bounded: full means drop, never block the callback

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# The first six describe the file; the rest are ground truth parsed from the
# label (see parse_label) plus per-session rig facts from the environment. A
# manifest written by an older build lacks the GT columns — evaluate.py falls
# back to parsing the label, so old corpora stay scoreable.
MANIFEST_COLUMNS = ("file", "label", "timestamp", "frame", "height", "width",
                    "distance_m", "zone", "target", "env", "tilt_deg", "cam_height_m")

PAUSE_LABELS = ("pause", "-")
_DISTANCE = re.compile(r"^(\d+(?:\.\d+)?)m$", re.IGNORECASE)
_ZONES = {"l": "left", "left": "left", "c": "center", "center": "center",
          "centre": "center", "r": "right", "right": "right"}


def sanitize_label(label: str) -> str:
    """Make a label safe to use as a filename prefix."""
    cleaned = _UNSAFE.sub("_", label.strip()).strip("_")
    return cleaned or "unlabeled"


def parse_label(label: str) -> dict:
    """
    Ground truth encoded in a capture label, as {distance_m, zone, target, env}.

    `board_1.0m_C_room2` -> target "board", distance 1.0, zone "center", env
    "room2". Tokens are matched by SHAPE, not position: the distance is the
    token ending in "m" after a number, the zone is a one-letter or full zone
    name, the target is the first token, and everything left over is the env.
    Missing pieces come back as None / "" rather than raising, so a quick
    ad-hoc label ("wall_far") still captures — it just cannot be scored
    against a distance.
    """
    tokens = [t for t in sanitize_label(label).split("_") if t]
    out = {"distance_m": None, "zone": None, "target": tokens[0] if tokens else "", "env": ""}
    rest = []
    for tok in tokens[1:]:
        m = _DISTANCE.match(tok)
        if m and out["distance_m"] is None:
            out["distance_m"] = float(m.group(1))
        elif tok.lower() in _ZONES and out["zone"] is None:
            out["zone"] = _ZONES[tok.lower()]
        else:
            rest.append(tok)
    out["env"] = "_".join(rest)
    return out


def _env_float(name: str):
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class FrameCapture:
    """
    Streams labelled raw depth frames to disk from inside the depth callback.

    The label is read live from a small text file so it can be changed from
    another ssh session without restarting the pipeline — same mechanism as
    CalibrationLogger, and the two can run at the same time.
    """

    def __init__(
        self,
        out_dir: str = None,
        label_file: str = "calib_label.txt",
        every: int = None,
        max_per_label: int = None,
        print_every: int = 30,
    ):
        self.out_dir = out_dir or os.environ.get("SV_CAPTURE_DIR", DEFAULT_DIR)
        self.label_file = label_file
        self.every = max(int(every if every is not None else _env_int("SV_CAPTURE_EVERY", DEFAULT_EVERY)), 1)
        self.max_per_label = int(
            max_per_label if max_per_label is not None
            else _env_int("SV_CAPTURE_MAX", DEFAULT_MAX_PER_LABEL)
        )
        self.print_every = max(int(print_every), 1)
        # Rig facts are per session, not per label: read once, stamped on every
        # row, so a corpus shot at two tilts can never be pooled by accident.
        self.tilt_deg = _env_float("SV_CAM_TILT_DEG")
        self.cam_height_m = _env_float("SV_CAM_HEIGHT_M")

        os.makedirs(self.out_dir, exist_ok=True)
        self.manifest_path = os.path.join(self.out_dir, "manifest.csv")

        # Counts are owned by the PRODUCER (callback thread) and incremented only
        # on a successful enqueue, so they track files actually written.
        self._counts = defaultdict(int)
        self._dropped = 0

        self._queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()

        print(f"[CAPTURE] writing to {self.out_dir}/ | every {self.every} frames, "
              f"max {self.max_per_label}/label")
        print(f"[CAPTURE] set the scene label with: echo \"wall_far\" > {self.label_file}")

    # ---------------------------------------------------------------- producer

    def _current_label(self) -> str:
        try:
            with open(self.label_file) as f:
                label = f.readline().strip()
            if label:
                return label
        except OSError:
            pass
        return os.environ.get("SV_CALIB_LABEL", "unlabeled")

    def maybe_save(self, depth_map: np.ndarray, frame_count: int) -> None:
        """
        Called every depth frame; saves on the configured interval.

        Cheap and non-blocking: a rate check, a small file read for the label,
        and a put_nowait. The array is copied because the caller keeps mutating
        its buffer (crop/downsample) after we return.
        """
        if frame_count % self.every:
            return

        label = sanitize_label(self._current_label())
        if label.lower() in PAUSE_LABELS:
            return
        if self._counts[label] >= self.max_per_label:
            if self._counts[label] == self.max_per_label:
                self._counts[label] += 1          # print the notice exactly once
                print(f"[CAPTURE] label {label!r} reached {self.max_per_label} frames — "
                      f"re-tag to capture a different scene")
            return

        index = self._counts[label]
        name = f"{label}_{index:04d}.npy"
        h, w = depth_map.shape
        gt = parse_label(label)
        row = {
            "file": name,
            "label": label,
            "timestamp": time.time(),
            "frame": frame_count,
            "height": h,
            "width": w,
            "distance_m": "" if gt["distance_m"] is None else gt["distance_m"],
            "zone": gt["zone"] or "",
            "target": gt["target"],
            "env": gt["env"],
            "tilt_deg": "" if self.tilt_deg is None else self.tilt_deg,
            "cam_height_m": "" if self.cam_height_m is None else self.cam_height_m,
        }

        try:
            self._queue.put_nowait((name, np.asarray(depth_map, dtype=np.float32).copy(), row))
        except queue.Full:
            # Disk can't keep up — drop rather than stall the streaming thread.
            self._dropped += 1
            return

        self._counts[label] = index + 1
        if self._counts[label] % self.print_every == 0:
            print(f"[CAPTURE] label={label!r} saved={self._counts[label]} "
                  f"dropped={self._dropped}")

    # ---------------------------------------------------------------- consumer

    def _writer_loop(self) -> None:
        new_file = (not os.path.exists(self.manifest_path)
                    or os.path.getsize(self.manifest_path) == 0)
        # Appending to a manifest written by an older build: keep ITS columns
        # (extra fields dropped) rather than writing rows that do not match the
        # header — a mixed-schema CSV is unreadable, a narrower one is not, and
        # evaluate.py can re-derive the GT columns from the label anyway.
        fieldnames = list(MANIFEST_COLUMNS)
        if not new_file:
            with open(self.manifest_path, newline="") as fh:
                existing = next(csv.reader(fh), None)
            if existing and existing != fieldnames:
                print(f"[CAPTURE] manifest has an older schema; keeping its {len(existing)} columns")
                fieldnames = existing
        with open(self.manifest_path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            if new_file:
                writer.writeheader()
                fh.flush()
            while not self._stop.is_set():
                try:
                    name, array, row = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    np.save(os.path.join(self.out_dir, name), array)
                    writer.writerow(row)
                    fh.flush()
                except Exception as exc:                     # never kill the thread
                    print(f"[CAPTURE] write failed for {name}: {exc}")

    def close(self) -> None:
        """Flush what is queued, then stop the writer thread."""
        deadline = time.monotonic() + 2.0
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        self._stop.set()
        self._thread.join(timeout=2.0)
        total = sum(v for v in self._counts.values())
        print(f"[CAPTURE] done — {total} frames in {self.out_dir}/ "
              f"({self._dropped} dropped)")

# --------------------------------------------------------------------------- #
# Protocol driver — the capture grid evaluate.py expects, one placement at a time
# --------------------------------------------------------------------------- #

PROTOCOL_DISTANCES = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
PROTOCOL_TARGETS = ("board", "person")
PROTOCOL_WALL_DISTANCES = (0.5, 1.0, 2.0)     # wall fills the zone; fewer levels
PROTOCOL_ZONES = ("L", "C", "R")
PROTOCOL_OPEN_SCENES = 4                       # per env; >= 10 in total across envs


def protocol_labels(env: str, distances=PROTOCOL_DISTANCES, targets=PROTOCOL_TARGETS,
                    zones=PROTOCOL_ZONES, wall_distances=PROTOCOL_WALL_DISTANCES,
                    open_scenes: int = PROTOCOL_OPEN_SCENES) -> list:
    """Every label one environment needs, in a walking-friendly order."""
    labels = []
    for target in targets:
        for zone in zones:
            for d in distances:
                labels.append(f"{target}_{d}m_{zone}_{env}")
    for zone in zones:
        for d in wall_distances:
            labels.append(f"wall_{d}m_{zone}_{env}")
    for i in range(open_scenes):
        labels.append(f"open_{env}_{i + 1}")
    return labels


def manifest_counts(out_dir: str) -> dict:
    """{label: frames saved} from an existing manifest, or {} when none."""
    path = os.path.join(out_dir, "manifest.csv")
    counts = defaultdict(int)
    try:
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                counts[row["label"]] += 1
    except OSError:
        pass
    return dict(counts)


def _instruction(label: str) -> str:
    gt = parse_label(label)
    if gt["target"] == "open":
        return "OPEN scene: nothing within 3 m in ANY zone. Vary the view between open scenes."
    where = {"left": "LEFT", "center": "CENTRE", "right": "RIGHT"}.get(gt["zone"], "?")
    d = gt["distance_m"]
    tgt = gt["target"].upper()
    if gt["target"] == "wall":
        return (f"WALL at {d} m, FILLING the {where} zone (camera square to it). "
                f"Measure lens plane -> wall.")
    return (f"{tgt} at {d} m, centred in the {where} zone. Measure lens plane -> "
            f"nearest face of the {tgt.lower()}, along that zone's central ray.")


def _write_label(label_file: str, label: str) -> None:
    with open(label_file, "w") as fh:
        fh.write(label + "\n")


def drive_protocol(env: str, out_dir: str, label_file: str, plan_only: bool = False,
                   skip_done: int = 30, input_fn=input, print_fn=print) -> None:
    """
    Walk the grid: show the placement, wait for Enter, write the label, wait for
    Enter again, write "pause". Placements that already have >= skip_done frames
    in the manifest are skipped, so an interrupted session resumes where it was.
    """
    counts = manifest_counts(out_dir)
    labels = protocol_labels(env)
    todo = [l for l in labels if counts.get(l, 0) < skip_done]
    print_fn(f"[PROTOCOL] env={env!r}: {len(labels)} placements, "
             f"{len(labels) - len(todo)} already have >= {skip_done} frames, {len(todo)} to go")
    for label in labels:
        mark = "done" if counts.get(label, 0) >= skip_done else "    "
        print_fn(f"  [{mark}] {label:<32} {counts.get(label, 0):>4} frames")
    if plan_only:
        return
    print_fn("\nThe pipeline must be running with SV_CAPTURE=1 in another terminal.")
    print_fn("Rig facts (set in that terminal's env): SV_CAM_HEIGHT_M, SV_CAM_TILT_DEG.\n")
    _write_label(label_file, "pause")
    try:
        for i, label in enumerate(todo, start=1):
            print_fn(f"--- {i}/{len(todo)}  {label}")
            print_fn(f"    {_instruction(label)}")
            if input_fn("    Enter to START capturing (q to quit): ").strip().lower() == "q":
                break
            _write_label(label_file, label)
            input_fn("    capturing ... Enter to STOP: ")
            _write_label(label_file, "pause")
    finally:
        _write_label(label_file, "pause")
        print_fn("[PROTOCOL] label set to 'pause' — nothing is being saved.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Drive the depth-capture protocol grid")
    ap.add_argument("--env", required=True, help="room/environment tag for the labels, e.g. room1")
    ap.add_argument("--drive", action="store_true", help="walk the placements interactively")
    ap.add_argument("--plan", action="store_true", help="print the grid and progress, no prompting")
    ap.add_argument("--dir", default=os.environ.get("SV_CAPTURE_DIR", DEFAULT_DIR))
    ap.add_argument("--label-file", default="calib_label.txt")
    args = ap.parse_args(argv)
    if not (args.drive or args.plan):
        ap.error("choose --drive or --plan")
    drive_protocol(args.env, args.dir, args.label_file, plan_only=not args.drive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
