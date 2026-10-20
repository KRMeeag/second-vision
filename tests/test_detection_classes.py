"""
Unit tests for the detection class set (callbacks.DETECTION_CLASS_MAP).

Three guarantees:
  * Off-list labels never reach tracking state, the overlay or TTS, and
    on-list ones arrive under their mapped name ("Tricycle" -> "vehicle").
  * core/priority.py's class tables are keyed on the mapped names. A raw model
    label there never matches and silently drops a class to the default weight
    or out of the urgent tier — the regression this file exists to catch.
  * The labels.json beside each custom HEF lists the model's classes in the
    model's own index order, and every one of them is mapped. Get the order
    wrong and the postprocess names each box after a different class.

Hardware-independent: `hailo` is replaced by a duck-typed stand-in (the
mocked-hailo harness this project uses for callback logic). Run with:

    poetry run pytest tests/test_detection_classes.py -v
"""

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# The model folder the pipeline uses is app.py's DET_MODEL_VERSION. Read from the
# source text, not imported: importing app needs GStreamer, which the Poetry
# environment lacks.
_APP_SOURCE = (REPO_ROOT / "src" / "second_vision" / "pipeline" / "app.py").read_text()
DET_MODEL_VERSION = re.search(r'^DET_MODEL_VERSION = "([^"]+)"$', _APP_SOURCE, re.M).group(1)
MODEL_DIRS = [REPO_ROOT / "models" / DET_MODEL_VERSION / "hef_deploy",
              REPO_ROOT / "models" / DET_MODEL_VERSION / "hef_eval"]

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(REPO_ROOT / "src"))

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


def test_coco_fallback_names_are_dropped(run_frame):
    # Without labels.json the postprocess names the custom model's classes 0-2
    # from the COCO table: "person", "bicycle", "car". None may get through —
    # "bicycle" would be announced for what the model saw as a Vehicle.
    ud = run_frame([_Det("person", 1), _Det("bicycle", 2), _Det("car", 3)])
    assert ud.track_history == {}
    assert ud.tts_queue.peek() is None


def test_off_list_class_cannot_outrank_an_on_list_one(run_frame):
    # The couch is bigger, more confident and centred — it would win on priority
    # if it were scored at all.
    ud = run_frame([
        _Det("couch", 1, confidence=0.99, size=0.8),
        _Det("Chairs", 2, confidence=0.75, center_x=0.1, size=0.1),
    ])
    assert set(ud.track_history) == {2}
    payload = ud.tts_queue.peek()
    assert payload["label"] == "chair"


@pytest.mark.parametrize("model_label,mapped", [
    ("Person", "person"),
    ("Vehicle", "vehicle"),
    ("Bicycle", "vehicle"),
    ("Tricycle", "vehicle"),
    ("Motorcycle", "motorcycle"),
    ("Animals", "animal"),
    ("Chairs", "chair"),
    ("Tables", "table"),
    ("Bench", "bench"),
    ("Pole", "pole"),
    ("Doors", "door"),
    ("Potholes", "pothole"),
    ("Stairs", "stairs"),
    ("Trash Bins", "trash bin"),
])
def test_on_list_classes_arrive_under_mapped_name(run_frame, model_label, mapped):
    ud = run_frame([_Det(model_label, 7)])
    assert ud.track_history[7]["label"] == mapped
    assert ud.tts_queue.peek()["label"] == mapped


def test_centred_vehicle_is_still_urgent_after_mapping(run_frame):
    # Before the tables were re-keyed, "vehicle" matched nothing in
    # URGENT_CLASSES and a centred car fell to the normal tier.
    ud = run_frame([_Det("Vehicle", 1, confidence=0.75, size=0.05)])
    payload = ud.tts_queue.peek()
    assert payload["label"] == "vehicle"
    assert payload["tier"] == TIER_URGENT


# ============================================================
# Vocabulary consistency between callbacks.py and priority.py
# ============================================================
def test_map_emits_exactly_the_agreed_classes():
    assert set(callbacks.DETECTION_CLASS_MAP.values()) == {
        "person", "table", "vehicle", "chair", "animal", "motorcycle", "bench",
        "pole", "door", "pothole", "stairs", "trash bin",
    }


def test_priority_tables_use_mapped_names_only():
    emitted = set(callbacks.DETECTION_CLASS_MAP.values())
    assert set(priority.CLASS_WEIGHTS) <= emitted
    assert priority.URGENT_CLASSES <= emitted


# ============================================================
# labels.json <-> model metadata <-> class map
# ============================================================
def _labels_json(model_dir):
    return json.loads((model_dir / "labels.json").read_text())["labels"]


@pytest.mark.parametrize("model_dir", MODEL_DIRS, ids=lambda d: d.name)
def test_labels_json_follows_model_class_order(model_dir):
    # HailoNMSDecode names class_id N with labels[N + 1], so index 0 is the
    # "unlabeled" placeholder and the rest must be metadata.yaml's names in
    # class-index order.
    yaml = pytest.importorskip("yaml")
    names = yaml.safe_load((model_dir / "metadata.yaml").read_text())["names"]
    labels = _labels_json(model_dir)
    assert labels[0] == "unlabeled"
    assert labels[1:] == [names[i] for i in sorted(names)]


@pytest.mark.parametrize("model_dir", MODEL_DIRS, ids=lambda d: d.name)
def test_every_model_label_is_mapped(model_dir):
    # A subset, not equality: the map may keep a class the deployed model no longer
    # has (hef_v3 dropped "Pole"), so a model that brings it back still works. What
    # must never happen is a model label with no entry: it would be dropped silently.
    unmapped = set(_labels_json(model_dir)[1:]) - set(callbacks.DETECTION_CLASS_MAP)
    assert not unmapped, f"model labels with no entry in DETECTION_CLASS_MAP: {sorted(unmapped)}"


# ============================================================
# The deployed model: the pipeline's default HEF, its labels, its class count
# ============================================================
def test_the_pipeline_default_hef_is_the_deployed_model_and_exists():
    m = re.search(r'^DEFAULT_DET_HEF_PATH = REPO_ROOT / "models" / DET_MODEL_VERSION / "hef_deploy" / "best.hef"$',
                  _APP_SOURCE, re.M)
    assert m, "app.py's DEFAULT_DET_HEF_PATH is no longer built from DET_MODEL_VERSION"
    hef = REPO_ROOT / "models" / DET_MODEL_VERSION / "hef_deploy" / "best.hef"
    assert hef.is_file() and hef.stat().st_size > 1_000_000, f"the default detection HEF is missing: {hef}"


@pytest.mark.parametrize("model_dir", MODEL_DIRS, ids=lambda d: d.name)
def test_the_deployed_model_ships_a_labels_json_beside_its_hef(model_dir):
    """Without labels.json the postprocess emits COCO names and detection goes silent (app.DET_LABELS_SIDECAR)."""
    assert (model_dir / "labels.json").is_file(), \
        f"{model_dir} has no labels.json: write it from metadata.yaml ('unlabeled', then the names in index order)"


@pytest.mark.parametrize("model_dir", MODEL_DIRS, ids=lambda d: d.name)
def test_the_hefs_class_count_matches_its_labels(model_dir):
    """nms_config.json is the config the HEF was compiled with; its class count is the HEF's (hailortcli parse-hef
    agrees, checked by hand for hef_v3: 13). The labels must have exactly that many names after 'unlabeled'."""
    classes = json.loads((model_dir / "nms_config.json").read_text())["classes"]
    assert len(_labels_json(model_dir)) - 1 == classes, (len(_labels_json(model_dir)) - 1, classes)


def test_pole_is_not_a_class_of_the_deployed_model_but_its_map_entry_is_harmless():
    """hef_v3 dropped "Pole": detection cannot announce a pole, and the stale map key changes nothing."""
    labels = _labels_json(MODEL_DIRS[0])
    if DET_MODEL_VERSION == "hef_v2":
        pytest.skip("hef_v2 still has Pole")
    assert "Pole" not in labels
    assert "Pole" in callbacks.DETECTION_CLASS_MAP
