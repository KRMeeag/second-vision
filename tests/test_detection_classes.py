"""
Unit tests for the temporary detection class set (callbacks.DETECTION_CLASS_MAP).

Two guarantees:
  * Off-list COCO classes never reach tracking state, the overlay or TTS, and
    on-list ones arrive under their mapped name ("car" -> "vehicle").
  * core/priority.py's class tables are keyed on the mapped names. A raw COCO
    key there never matches and silently drops a class to the default weight
    or out of the urgent tier — the regression this file exists to catch.

Hardware-independent: `hailo` is replaced by a duck-typed stand-in (the
mocked-hailo harness this project uses for callback logic). Run with:

    poetry run pytest tests/test_detection_classes.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from second_vision.core import priority
from second_vision.core.priority import TIER_URGENT, PriorityMailbox
from second_vision.pipeline import callbacks


# ============================================================
# Mocked-hailo harness
# ============================================================
_DETECTION = "HAILO_DETECTION"
_UNIQUE_ID = "HAILO_UNIQUE_ID"


class _BBox:
    def __init__(self, xmin, ymin, width, height):
        self._v = (xmin, ymin, width, height)

    def xmin(self):
        return self._v[0]

    def ymin(self):
        return self._v[1]

    def width(self):
        return self._v[2]

    def height(self):
        return self._v[3]

    def xmax(self):
        return self._v[0] + self._v[2]

    def ymax(self):
        return self._v[1] + self._v[3]


class _TrackId:
    def __init__(self, track_id):
        self._id = track_id

    def get_id(self):
        return self._id


class _Det:
    def __init__(self, label, track_id, confidence=0.9, center_x=0.5, size=0.3):
        self._label = label
        self._track_id = track_id
        self._confidence = confidence
        self._bbox = _BBox(center_x - size / 2, 0.3, size, size)

    def get_label(self):
        return self._label

    def get_confidence(self):
        return self._confidence

    def get_bbox(self):
        return self._bbox

    def get_objects_typed(self, kind):
        assert kind == _UNIQUE_ID
        return [_TrackId(self._track_id)]


class _Roi:
    def __init__(self, dets):
        self._dets = dets

    def get_objects_typed(self, kind):
        assert kind == _DETECTION
        return list(self._dets)


@pytest.fixture
def run_frame(monkeypatch):
    """Feed one frame of fake detections through _process_real_detections."""
    fake_hailo = SimpleNamespace(
        HAILO_DETECTION=_DETECTION,
        HAILO_UNIQUE_ID=_UNIQUE_ID,
        get_roi_from_buffer=lambda buffer: buffer,
    )
    monkeypatch.setattr(callbacks, "hailo", fake_hailo, raising=False)
    # Announce on first sight so one frame is enough to see the payload.
    monkeypatch.setattr(callbacks, "MIN_CONFIRMATION_SECONDS", 0.0)

    user_data = SimpleNamespace(
        track_history={},
        IDs_changed_zones=set(),
        head_turn_cooldown_until=0.0,
        use_frame=False,
        tts_queue=PriorityMailbox(),
        get_count=lambda: 1,
        get_det_fps=lambda: 0.0,
    )

    def _run(dets):
        callbacks._process_real_detections(None, _Roi(dets), user_data)
        return user_data

    return _run


# ============================================================
# Filtering + mapping through the real callback
# ============================================================
def test_off_list_classes_are_dropped_entirely(run_frame):
    ud = run_frame([_Det("couch", 1), _Det("bottle", 2), _Det("traffic light", 3)])
    assert ud.track_history == {}
    assert ud.tts_queue.peek() is None


def test_off_list_class_cannot_outrank_an_on_list_one(run_frame):
    # The couch is bigger, more confident and centred — it would win on priority
    # if it were scored at all.
    ud = run_frame([
        _Det("couch", 1, confidence=0.99, size=0.8),
        _Det("chair", 2, confidence=0.75, center_x=0.1, size=0.1),
    ])
    assert set(ud.track_history) == {2}
    payload = ud.tts_queue.peek()
    assert payload["label"] == "chair"


@pytest.mark.parametrize("coco,mapped", [
    ("person", "person"),
    ("dining table", "table"),
    ("chair", "chair"),
    ("bench", "bench"),
    ("motorcycle", "motorcycle"),
    ("car", "vehicle"),
    ("truck", "vehicle"),
    ("bicycle", "vehicle"),
    ("dog", "animal"),
    ("horse", "animal"),
])
def test_on_list_classes_arrive_under_mapped_name(run_frame, coco, mapped):
    ud = run_frame([_Det(coco, 7)])
    assert ud.track_history[7]["label"] == mapped
    assert ud.tts_queue.peek()["label"] == mapped


def test_centred_vehicle_is_still_urgent_after_mapping(run_frame):
    # Before the tables were re-keyed, "vehicle" matched nothing in
    # URGENT_CLASSES and a centred car fell to the normal tier.
    ud = run_frame([_Det("car", 1, confidence=0.75, size=0.05)])
    payload = ud.tts_queue.peek()
    assert payload["label"] == "vehicle"
    assert payload["tier"] == TIER_URGENT


# ============================================================
# Vocabulary consistency between callbacks.py and priority.py
# ============================================================
def test_map_emits_exactly_the_agreed_classes():
    assert set(callbacks.DETECTION_CLASS_MAP.values()) == {
        "person", "table", "vehicle", "chair", "animal", "motorcycle", "bench",
    }


def test_priority_tables_use_mapped_names_only():
    emitted = set(callbacks.DETECTION_CLASS_MAP.values())
    assert set(priority.CLASS_WEIGHTS) <= emitted
    assert priority.URGENT_CLASSES <= emitted
