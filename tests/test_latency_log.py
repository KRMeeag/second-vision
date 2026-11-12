"""
Unit tests for the latency measurement log (core/latency_log.py, SV_LATENCY_LOG=1) and its hooks.

What must hold:
  * Off by default: without SV_LATENCY_LOG=1, latency_log.LOG is None and nothing is recorded.
  * A queued item is linked to its timings by identity, never by a recycled id, and the side table
    is bounded.
  * Haptic rows: capture -> queued -> dequeued -> written; every pass that finds an ACK is its own
    acks.csv row (pairing them with packets is the analysis's job: ACKs carry no sequence number).
  * Audio rows: the track's first qualifying frame and the decision frame travel with the payload to
    the moment the speech engine starts.
  * The hooks in callbacks.py, serial_worker.py and tts_worker.py feed those rows, and the queue items
    keep exactly their contract keys.

Hardware-free: a fake board, a fake hailo ROI and a fake speech process. Run with:

    poetry run pytest tests/test_latency_log.py -v
"""

import csv
import os
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from second_vision.core import latency_log  # noqa: E402
from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.core.priority import PriorityMailbox  # noqa: E402
from second_vision.pipeline import callbacks  # noqa: E402
from second_vision.workers import serial_worker as sw  # noqa: E402
from second_vision.workers import tts_worker  # noqa: E402

DEPTH_KEYS = {"left", "center", "right", "hazard", "hazard_severity"}


class _Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def _rows(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def log(tmp_path):
    return latency_log.LatencyLog(tmp_path, clock=_Clock(), start_writer=False)


@pytest.fixture
def live_log(tmp_path, monkeypatch):
    """A real-clock log installed as latency_log.LOG, as SV_LATENCY_LOG=1 would."""
    lg = latency_log.LatencyLog(tmp_path, start_writer=False)
    monkeypatch.setattr(latency_log, "LOG", lg)
    return lg


# ============================================================
# The module and the side table
# ============================================================
def test_off_unless_the_flag_is_set():
    if os.environ.get("SV_LATENCY_LOG") == "1":
        pytest.skip("SV_LATENCY_LOG=1 is set in this shell")
    assert latency_log.ENABLED is False
    assert latency_log.LOG is None


def test_files_start_with_their_headers(log):
    assert _rows(log.out_dir / "haptic.csv") == []
    assert open(log.out_dir / "haptic.csv").readline().strip().split(",") == latency_log.HAPTIC_FIELDS
    assert open(log.out_dir / "audio.csv").readline().strip().split(",") == latency_log.AUDIO_FIELDS
    assert "monotonic" in (log.out_dir / "clock.txt").read_text()


def test_untag_matches_the_same_object_only(log):
    a, b = {"left": 1}, {"left": 1}
    log.tag(a, "haptic", capture_mono=1.0)
    assert log.untag(b) is None, "an equal but different dict must not pick up the tag"
    assert log.untag(a) == ("haptic", {"capture_mono": 1.0})
    assert log.untag(a) is None, "a tag is used once"


def test_side_table_is_bounded(log, monkeypatch):
    monkeypatch.setattr(latency_log, "MAX_TAGS", 5)
    objs = [{} for _ in range(8)]
    for i, o in enumerate(objs):
        log.tag(o, "haptic", n=i)
    assert log.untag(objs[0]) is None and log.untag(objs[2]) is None
    assert log.untag(objs[3]) == ("haptic", {"n": 3})


# ============================================================
# Record builders
# ============================================================
def test_haptic_row_from_capture_to_write(log):
    clock = log._clock
    pkt = {"left": 10, "center": 20, "right": 30, "hazard": False, "hazard_severity": 0}
    log.tag(pkt, "haptic", capture_mono=99.80, queued_mono=99.90)
    clock.t = 100.00
    rec = log.haptic_dequeued(pkt)
    clock.t = 100.01
    log.haptic_written(rec, True, 10, 20, 30)
    log.flush()
    (row,) = _rows(log.out_dir / "haptic.csv")
    assert float(row["capture_mono"]) == 99.80 and float(row["queued_mono"]) == 99.90
    assert float(row["dequeued_mono"]) == 100.00 and float(row["write_mono"]) == 100.01
    assert (row["left"], row["center"], row["right"], row["write_ok"]) == ("10", "20", "30", "True")
    assert "ack_seen_mono" not in row, "a packet is not paired with an ACK here (see acks.csv)"


def test_ack_observations_are_their_own_rows(log):
    clock = log._clock
    clock.t = 5.0
    log.haptic_ack(False)                      # a pass that found nothing: no row
    log.haptic_ack(True)
    clock.t = 5.033
    log.haptic_ack(True)
    log.flush()
    assert [float(r["ack_seen_mono"]) for r in _rows(log.out_dir / "acks.csv")] == [5.0, 5.033]


def test_an_untagged_motor_dict_makes_no_row(log):
    assert log.haptic_dequeued({"left": 0}) is None


def test_audio_row_from_first_frame_to_speech_start(log):
    log._clock.t = 50.0
    log.track_first_seen(7, 49.0)
    item = {"label": "person", "zone": "center"}
    log.tag(item, "audio", track_id=7, label="person", zone="center", phrase="", tier="normal",
            first_announcement=True, first_capture_mono=log.track_first(7),
            decision_capture_mono=49.3, decision_mono=49.4)
    log.speech_started({"label": "person"}, "espeak-ng")      # a different item: no row
    log.speech_started(item, "espeak-ng")
    log.flush()
    (row,) = _rows(log.out_dir / "audio.csv")
    assert float(row["first_capture_mono"]) == 49.0 and float(row["decision_capture_mono"]) == 49.3
    assert float(row["speech_start_mono"]) == 50.0 and row["engine"] == "espeak-ng"
    assert row["first_announcement"] == "True"


# ============================================================
# The hooks
# ============================================================
def test_publish_motors_tags_without_changing_the_contract(live_log, monkeypatch):
    monkeypatch.setattr(callbacks, "_depth_capture_mono", 12.5)
    ud = SimpleNamespace(serial_queue=queue.Queue(maxsize=3))
    callbacks._publish_motors(ud, {"left": 1, "center": 2, "right": 3})
    pkt = ud.serial_queue.get_nowait()
    assert set(pkt) == DEPTH_KEYS, "the serial_queue contract keys are unchanged"
    kind, fields = live_log.untag(pkt)
    assert kind == "haptic" and fields["capture_mono"] == 12.5


def test_publish_motors_without_the_log_tags_nothing(monkeypatch):
    monkeypatch.setattr(latency_log, "LOG", None)
    ud = SimpleNamespace(serial_queue=queue.Queue(maxsize=3))
    callbacks._publish_motors(ud, {"left": 1, "center": 2, "right": 3})
    assert set(ud.serial_queue.get_nowait()) == DEPTH_KEYS


class _BBox:
    def __init__(self, x, w):
        self._x, self._w = x, w

    def xmin(self):
        return self._x

    def ymin(self):
        return 0.3

    def width(self):
        return self._w

    def height(self):
        return self._w

    def xmax(self):
        return self._x + self._w

    def ymax(self):
        return 0.3 + self._w


class _Det:
    def __init__(self, label, track_id):
        self._label, self._track_id = label, track_id

    def get_label(self):
        return self._label

    def get_confidence(self):
        return 0.9

    def get_bbox(self):
        return _BBox(0.35, 0.3)

    def get_objects_typed(self, kind):
        return [SimpleNamespace(get_id=lambda: self._track_id)]


class _Roi:
    def __init__(self, dets):
        self._dets = dets

    def get_objects_typed(self, kind):
        return list(self._dets)


def test_detection_payload_carries_first_and_decision_frames(live_log, monkeypatch):
    monkeypatch.setattr(callbacks, "hailo", SimpleNamespace(
        HAILO_DETECTION="D", HAILO_UNIQUE_ID="U", get_roi_from_buffer=lambda b: b), raising=False)
    monkeypatch.setattr(callbacks, "MIN_CONFIRMATION_SECONDS", 0.05)
    captures = iter([200.0, 200.4])
    monkeypatch.setattr(callbacks, "_capture_mono", lambda element, buffer: next(captures))
    ud = SimpleNamespace(track_history={}, IDs_changed_zones=set(), head_turn_cooldown_until=0.0,
                         use_frame=False, tts_queue=PriorityMailbox(), get_count=lambda: 1,
                         get_det_fps=lambda: 0.0)
    callbacks._process_real_detections(None, _Roi([_Det("Person", 4)]), ud)
    assert ud.tts_queue.peek() is None, "still inside the confirmation window"
    time.sleep(0.06)
    callbacks._process_real_detections(None, _Roi([_Det("Person", 4)]), ud)
    payload = ud.tts_queue.take()
    assert set(payload) <= {"label", "zone", "confidence", "priority", "tier", "phrase"}
    kind, fields = live_log.untag(payload)
    assert kind == "audio" and fields["track_id"] == 4 and fields["first_announcement"] is True
    assert fields["first_capture_mono"] == 200.0, "the frame that opened the confirmation window"
    assert fields["decision_capture_mono"] == 200.4, "the frame that decided to speak"


def test_speech_worker_records_when_the_engine_started(live_log, monkeypatch):
    class _Proc:
        def poll(self):
            return 0

    monkeypatch.setattr(tts_worker, "_start_espeak", lambda text, amp: _Proc())
    item = {"label": "person", "zone": "left", "confidence": 0.9, "priority": 1.0, "tier": "normal"}
    live_log.tag(item, "audio", track_id=1, label="person", zone="left", phrase="", tier="normal",
                 first_announcement=True, first_capture_mono=1.0, decision_capture_mono=1.3,
                 decision_mono=1.31)
    ud = SimpleNamespace(shutdown_event=threading.Event())
    before = time.monotonic()
    tts_worker._speak_interruptible(item, PriorityMailbox(), ud)
    live_log.flush()
    (row,) = _rows(live_log.out_dir / "audio.csv")
    assert row["engine"] == "espeak-ng" and float(row["speech_start_mono"]) >= before


class _Board:
    """An open port with a board behind it that acknowledges every packet."""

    def __init__(self):
        self.lock = threading.Lock()
        self.inbox = bytearray()

    def write(self, data):
        with self.lock:
            self.inbox += bytes([0xAA, 0xFF, data[1]])
        return len(data)

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.inbox)

    def read(self, n):
        with self.lock:
            out = bytes(self.inbox[:n])
            del self.inbox[:n]
            return out

    def close(self):
        pass


def test_serial_worker_writes_rows_for_tagged_packets(live_log, monkeypatch):
    board = _Board()
    monkeypatch.setattr(sw, "_open_serial_port", lambda cfg: board)
    config = SystemConfig()
    config.update(serial_port="/dev/fake-glasses", pipeline_mode="both")
    ud = SimpleNamespace(serial_queue=queue.Queue(maxsize=10), shutdown_event=threading.Event())
    worker = threading.Thread(target=sw.serial_worker, args=(ud, config), daemon=True)
    worker.start()
    try:
        for i in range(3):
            pkt = {"left": 100, "center": 0, "right": 0, "hazard": False, "hazard_severity": 0}
            live_log.tag(pkt, "haptic", capture_mono=time.monotonic() - 0.1, queued_mono=time.monotonic())
            ud.serial_queue.put(pkt)
            time.sleep(0.1)
    finally:
        ud.shutdown_event.set()
        worker.join(2.0)
    live_log.flush()
    rows = _rows(live_log.out_dir / "haptic.csv")
    assert len(rows) == 3
    for r in rows:
        assert float(r["capture_mono"]) < float(r["dequeued_mono"]) <= float(r["write_mono"])
        assert r["write_ok"] == "True" and r["left"] == "100"
    acks = [float(r["ack_seen_mono"]) for r in _rows(live_log.out_dir / "acks.csv")]
    first_write = min(float(r["write_mono"]) for r in rows)
    assert any(a > first_write for a in acks), "the board's ACKs were noticed after the writes"
