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

import csv
import os
import queue
import re
import threading
import time
from collections import defaultdict

import numpy as np

DEFAULT_DIR = "depth_corpus"
DEFAULT_EVERY = 3            # save every Nth depth frame (30 FPS -> ~10 saves/s)
DEFAULT_MAX_PER_LABEL = 300  # stop after this many frames for one label
QUEUE_SIZE = 8               # bounded: full means drop, never block the callback

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

MANIFEST_COLUMNS = ("file", "label", "timestamp", "frame", "height", "width")


def sanitize_label(label: str) -> str:
    """Make a label safe to use as a filename prefix."""
    cleaned = _UNSAFE.sub("_", label.strip()).strip("_")
    return cleaned or "unlabeled"


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
        if self._counts[label] >= self.max_per_label:
            if self._counts[label] == self.max_per_label:
                self._counts[label] += 1          # print the notice exactly once
                print(f"[CAPTURE] label {label!r} reached {self.max_per_label} frames — "
                      f"re-tag to capture a different scene")
            return

        index = self._counts[label]
        name = f"{label}_{index:04d}.npy"
        h, w = depth_map.shape
        row = {
            "file": name,
            "label": label,
            "timestamp": time.time(),
            "frame": frame_count,
            "height": h,
            "width": w,
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
        with open(self.manifest_path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(MANIFEST_COLUMNS))
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