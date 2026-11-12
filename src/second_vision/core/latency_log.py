"""
Latency measurement log: camera capture to motor packet, and to speech start.

OFF unless SV_LATENCY_LOG=1 is set when this module is first imported. Off, LOG is None and every
hook in the app is a single `is None` test, so the per-frame path is unchanged.

On, the app writes two CSV files to SV_LATENCY_DIR (default /tmp/second-vision-latency/<time>-<pid>):

  haptic.csv  one row per motor packet the serial worker wrote:
      capture_mono    camera capture of the depth frame it came from (monotonic clock, s)
      queued_mono     when the depth callback put it on serial_queue
      dequeued_mono   when the serial worker took it
      write_mono      when port.write() returned (write_ok: whether it went out)
      left/center/right   the duties before the strength knob
  acks.csv    one row per serial-worker pass that found an acknowledgement from the board:
      ack_seen_mono   when the worker NOTICED it. The worker checks once per pass, and a pass first
                      checks right after a write (too soon for any reply) and then waits for the next
                      frame, so an ACK is noticed at the first check after the NEXT write, about one
                      frame interval after it arrived. ACKs carry no sequence number, so pairing a
                      packet with the first ACK noticed at least 1 ms after its write (a UART round
                      trip cannot be faster) is left to the analysis.
  audio.csv   one row per announcement the speech worker started for a detection:
      first_capture_mono      camera capture of the first frame on which this track passed the
                              confidence threshold (the start of the 0.3 s confirmation window)
      decision_capture_mono   camera capture of the frame that put it in the speech mailbox
      decision_mono           when that happened
      speech_start_mono       when the speech engine's process had been started (espeak-ng Popen
                              returned). Audio leaves the earphones later; that is not measured.
      first_announcement      True for the first announcement of that track

A queued item and its timings are linked by object identity in a side table (the queue items' keys
are a team contract, AGENTS.md Rule 2, so nothing is added to them). The table holds a reference to
each tagged object, so an id can never be reused while its tag exists, and it is bounded. Rows are
buffered in memory and written by this module's own thread every few seconds and at exit, never
from the GStreamer thread. Panel-revision measurements, 2026-11.
"""

import atexit
import csv
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

ENABLED = os.environ.get("SV_LATENCY_LOG") == "1"
MAX_TAGS = 512            # queued items awaiting the worker; older ones were dropped or replaced
MAX_TRACKS = 512          # tracks whose first qualifying frame is remembered
FLUSH_SECONDS = 5.0

HAPTIC_FIELDS = ["capture_mono", "queued_mono", "dequeued_mono", "write_mono", "write_ok",
                 "left", "center", "right"]
ACK_FIELDS = ["ack_seen_mono"]
AUDIO_FIELDS = ["track_id", "label", "zone", "phrase", "tier", "first_announcement",
                "first_capture_mono", "decision_capture_mono", "decision_mono", "speech_start_mono",
                "engine"]


class LatencyLog:
    """The side table, the record builders and the CSV writer. Thread-safe."""

    def __init__(self, out_dir, clock=time.monotonic, start_writer=True):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._tags = OrderedDict()          # id(obj) -> (obj, kind, fields)
        self._track_first = OrderedDict()   # track_id -> capture_mono of its first qualifying frame
        self._rows = {"haptic": [], "audio": [], "acks": []}
        for name, fields in self._files():
            with open(self.out_dir / f"{name}.csv", "w", newline="") as fh:
                csv.writer(fh, lineterminator="\n").writerow(fields)
        (self.out_dir / "clock.txt").write_text(
            f"monotonic {clock():.6f} = unix {time.time():.6f} (to align with timestamped logs)\n")
        if start_writer:
            threading.Thread(target=self._writer, name="latency-log", daemon=True).start()
            atexit.register(self.flush)

    # ---- the side table -------------------------------------------------------------------------
    def tag(self, obj, kind, **fields):
        with self._lock:
            self._tags[id(obj)] = (obj, kind, fields)
            while len(self._tags) > MAX_TAGS:
                self._tags.popitem(last=False)

    def untag(self, obj):
        """(kind, fields) for a tagged object, removing the tag; None if it was never tagged."""
        with self._lock:
            entry = self._tags.pop(id(obj), None)
        if entry is None or entry[0] is not obj:
            return None
        return entry[1], entry[2]

    # ---- detection side (GStreamer thread) ------------------------------------------------------
    def track_first_seen(self, track_id, capture_mono):
        with self._lock:
            self._track_first[track_id] = capture_mono
            self._track_first.move_to_end(track_id)
            while len(self._track_first) > MAX_TRACKS:
                self._track_first.popitem(last=False)

    def track_first(self, track_id):
        with self._lock:
            return self._track_first.get(track_id)

    # ---- haptic side (serial worker thread) -----------------------------------------------------
    def haptic_dequeued(self, packet):
        """The record for a motor dict the worker just took, or None if it carries no tag."""
        tagged = self.untag(packet)
        if tagged is None or tagged[0] != "haptic":
            return None
        rec = dict(tagged[1])
        rec["dequeued_mono"] = self._clock()
        return rec

    def haptic_written(self, rec, ok, left, center, right):
        rec.update(write_mono=self._clock(), write_ok=ok, left=left, center=center, right=right)
        with self._lock:
            self._rows["haptic"].append(rec)

    def haptic_ack(self, ack_seen):
        """Call once per worker pass with that pass's ACK check; a found ACK becomes an acks.csv row."""
        if not ack_seen:
            return
        now = self._clock()
        with self._lock:
            self._rows["acks"].append({"ack_seen_mono": now})

    # ---- audio side (speech worker thread) ------------------------------------------------------
    def speech_started(self, item, engine):
        tagged = self.untag(item)
        if tagged is None or tagged[0] != "audio":
            return
        rec = dict(tagged[1])
        rec.update(speech_start_mono=self._clock(), engine=engine)
        with self._lock:
            self._rows["audio"].append(rec)

    # ---- output ---------------------------------------------------------------------------------
    def flush(self):
        with self._lock:
            rows, self._rows = self._rows, {"haptic": [], "audio": [], "acks": []}
        for name, fields in self._files():
            if rows[name]:
                with open(self.out_dir / f"{name}.csv", "a", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
                    w.writerows(rows[name])

    @staticmethod
    def _files():
        return (("haptic", HAPTIC_FIELDS), ("audio", AUDIO_FIELDS), ("acks", ACK_FIELDS))

    def _writer(self):
        while True:
            time.sleep(FLUSH_SECONDS)
            try:
                self.flush()
            except Exception as exc:  # a measurement must never take the app down
                print(f"[LATENCY] could not write the log: {exc!r}")


def _make():
    if not ENABLED:
        return None
    out = os.environ.get("SV_LATENCY_DIR") or \
        f"/tmp/second-vision-latency/{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    try:
        log = LatencyLog(out)
    except Exception as exc:
        print(f"[LATENCY] SV_LATENCY_LOG=1 but the log could not be opened ({exc!r}); running without it")
        return None
    print(f"[LATENCY] measurement log on: {log.out_dir}")
    return log


LOG = _make()
