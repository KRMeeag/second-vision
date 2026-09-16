"""
Tests for the accuracy evaluator (core/evaluate.py) and the manifest ground
truth it reads (core/capture.parse_label).

The evaluator is what will produce the thesis numbers, so it is tested against
a SYNTHETIC corpus whose scale is known by construction: model units are
exactly UNITS_PER_M * metres plus a little noise. Then the fitted ruler must
come back as s = UNITS_PER_M, t = 0, delta1 must be 1.0, and detection /
spatial results must match what the device chain is documented to do at the
current placeholders. Any of these failing means the SCORER is wrong, which
would poison every number reported from real captures.

Runs anywhere: no Hailo, no camera; a handful of 256x320 arrays in tmp_path.
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.core.depth_utils as du
from second_vision.core.capture import MANIFEST_COLUMNS, parse_label
from second_vision.core.evaluate import (
    Capture,
    ScaleFit,
    depth_metrics,
    evaluate,
    fit_scale,
    load_corpus,
    main,
    replay,
    spearman,
    zone_nearest_depth,
)

H, W = 256, 320
UNITS_PER_M = 20.0          # synthetic "model": 20 units per metre, t = 0
BACKGROUND_M = 4.0          # everything else sits well past MAX_DEPTH_M
NOISE = 0.3
FRAMES = 8


# --------------------------------------------------------------------------- #
# Synthetic corpus
# --------------------------------------------------------------------------- #

def _scene(rng, distance_m=None, zone=None):
    """Background at BACKGROUND_M; optionally a board filling most of `zone`."""
    frame = np.full((H, W), UNITS_PER_M * BACKGROUND_M, dtype=np.float32)
    if distance_m is not None:
        x0, x1 = du._zone_bounds(W)[zone]
        zw = x1 - x0
        c0, c1 = x0 + int(zw * 0.2), x1 - int(zw * 0.2)
        r0, r1 = int(H * 0.2), int(H * 0.8)
        frame[r0:r1, c0:c1] = UNITS_PER_M * distance_m
    frame += rng.normal(0.0, NOISE, frame.shape).astype(np.float32)
    return frame


def make_corpus(root: Path, with_gt_columns: bool = True, envs=("room1", "room2"),
                distances=(0.5, 1.0, 2.0), zones=("left", "center", "right"), seed=0):
    """Write a manifest + frames like capture.FrameCapture would."""
    rng = np.random.default_rng(seed)
    root.mkdir(parents=True, exist_ok=True)
    cols = list(MANIFEST_COLUMNS) if with_gt_columns else \
        ["file", "label", "timestamp", "frame", "height", "width"]
    labels = []
    for env in envs:
        for d in distances:
            for z in zones:
                labels.append((f"board_{d}m_{z[0].upper()}_{env}", d, z, "board", env))
        labels.append((f"open_{env}", None, None, "open", env))

    with open(root / "manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for label, d, z, target, env in labels:
            for i in range(FRAMES):
                name = f"{label}_{i:04d}.npy"
                np.save(root / name, _scene(rng, d, z))
                row = {"file": name, "label": label, "timestamp": float(i), "frame": i * 3,
                       "height": H, "width": W}
                if with_gt_columns:
                    row.update({"distance_m": "" if d is None else d, "zone": z or "",
                                "target": target, "env": env, "tilt_deg": 0.0,
                                "cam_height_m": 1.6})
                w.writerow(row)
    return labels


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    make_corpus(root)
    return root


@pytest.fixture(scope="module")
def replayed(corpus):
    caps = load_corpus(str(corpus))
    for c in caps:
        replay(c)
    return caps


# --------------------------------------------------------------------------- #
# Label parsing (the ground-truth contract)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("label,expect", [
    ("board_1.0m_C_room2", ("board", 1.0, "center", "room2")),
    ("person_0.75m_left_corridor_b", ("person", 0.75, "left", "corridor_b")),
    ("wall_2m_R_hall", ("wall", 2.0, "right", "hall")),
    ("open_hall", ("open", None, None, "hall")),
    ("wall_far", ("wall", None, None, "far")),
    ("", ("unlabeled", None, None, "")),
])
def test_parse_label(label, expect):
    got = parse_label(label)
    assert (got["target"], got["distance_m"], got["zone"], got["env"]) == expect


def test_parse_label_tokens_matched_by_shape_not_position():
    # Zone before distance, and a zone-looking word that is really the env.
    got = parse_label("pole_L_1.5m_lab")
    assert (got["distance_m"], got["zone"], got["env"]) == (1.5, "left", "lab")


# --------------------------------------------------------------------------- #
# Corpus loading
# --------------------------------------------------------------------------- #

def test_load_corpus_groups_frames_per_label_with_gt(corpus):
    caps = {c.label: c for c in load_corpus(str(corpus))}
    assert len(caps) == 2 * (3 * 3 + 1)
    c = caps["board_1.0m_C_room1"]
    assert (c.target, c.distance_m, c.zone, c.env) == ("board", 1.0, "center", "room1")
    assert c.n_frames == FRAMES and not c.is_open
    o = caps["open_room2"]
    assert o.is_open and o.zone is None and o.env == "room2"


def test_load_corpus_falls_back_to_label_when_gt_columns_missing(tmp_path):
    make_corpus(tmp_path, with_gt_columns=False, envs=("r",), distances=(1.0,), zones=("left",))
    caps = {c.label: c for c in load_corpus(str(tmp_path))}
    c = caps["board_1.0m_L_r"]
    assert (c.distance_m, c.zone, c.env) == (1.0, "left", "r")


def test_load_corpus_requires_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_corpus(str(tmp_path))


# --------------------------------------------------------------------------- #
# Replay measures what the device chain measures
# --------------------------------------------------------------------------- #

def test_replay_reads_the_board_at_both_levels(replayed):
    c = next(x for x in replayed if x.label == "board_1.0m_C_room1")
    assert c.roi_depth.shape == (FRAMES,)
    assert np.allclose(c.roi_depth, UNITS_PER_M * 1.0, atol=1.0)
    assert np.allclose(c.device_depth, UNITS_PER_M * 1.0, atol=1.5)
    # The board is the nearest thing in its zone, so the zone must warn ...
    assert c.perception["center"].min() >= 0.18 * 255
    # ... and the empty zones must not.
    assert c.perception["left"].max() == 0 and c.perception["right"].max() == 0


def test_replay_open_scene_is_silent_everywhere(replayed):
    c = next(x for x in replayed if x.label == "open_room1")
    for z in du.ZONE_NAMES:
        assert c.perception[z].max() == 0
        assert c.driven[z].max() == 0


def test_zone_nearest_depth_is_the_nearest_cell_p10():
    slice_ = np.full((48, 32), 50.0)
    slice_[0:12, 0:8] = 12.0                     # one sub-cell of near object
    assert zone_nearest_depth(slice_) == pytest.approx(12.0)
    # A single pixel is rejected by the percentile, as the device rejects it.
    slice_ = np.full((48, 32), 50.0)
    slice_[3, 3] = 5.0
    assert zone_nearest_depth(slice_) > 40.0


# --------------------------------------------------------------------------- #
# Scale fit + metrics
# --------------------------------------------------------------------------- #

def test_fit_scale_recovers_known_ruler():
    d = np.array([0.5, 1.0, 2.0, 3.0])
    s, t = fit_scale(UNITS_PER_M * d, d)
    assert s == pytest.approx(UNITS_PER_M, rel=1e-6)
    assert t == pytest.approx(0.0, abs=1e-9)
    s0, t0 = fit_scale(UNITS_PER_M * d, d, scale_only=True)
    assert s0 == pytest.approx(UNITS_PER_M, rel=1e-6) and t0 == 0.0


def test_fit_scale_with_shift():
    d = np.array([0.5, 1.0, 2.0, 3.0])
    pred = 1.0 / (0.05 / d + 0.1)                # 1/d = 0.05*(1/pred)... inverted below
    # Construct pred so that 1/d = s*(1/pred) + t exactly with s=2, t=0.25.
    inv_pred = (1.0 / d - 0.25) / 2.0
    pred = 1.0 / inv_pred
    s, t = fit_scale(pred, d)
    assert s == pytest.approx(2.0, rel=1e-6) and t == pytest.approx(0.25, abs=1e-9)


def test_scalefit_round_trips_metres():
    fit = ScaleFit(s=UNITS_PER_M, t=0.0, n=1, envs=(), in_sample=True)
    assert np.allclose(fit.to_metres(np.array([10.0, 20.0, 60.0])), [0.5, 1.0, 3.0])
    assert fit.units_at(1.5) == pytest.approx(30.0)
    assert fit.to_metres(np.array([1e9]))[0] == pytest.approx(1e9 / UNITS_PER_M, rel=1e-3)


def test_depth_metrics_perfect_and_off_by_30_percent():
    m = depth_metrics(np.array([1.0, 2.0]), np.array([1.0, 2.0]))
    assert m["delta1"] == 1.0 and m["abs_rel"] == 0.0 and m["rmse"] == 0.0
    m = depth_metrics(np.array([1.3, 2.6]), np.array([1.0, 2.0]))
    assert m["delta1"] == 0.0 and m["delta2"] == 1.0
    assert m["abs_rel"] == pytest.approx(0.3)
    assert depth_metrics(np.array([np.inf]), np.array([1.0]))["n"] == 0


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert np.isnan(spearman([1, 1, 1], [1, 2, 3]))
    assert np.isnan(spearman([1, 2], [1, 2]))


# --------------------------------------------------------------------------- #
# The whole evaluation on the synthetic corpus
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def report(replayed):
    return evaluate(replayed, calib_envs=("room1",), warn_m=1.5)


def test_report_recovers_the_synthetic_scale_out_of_sample(report):
    assert not report["in_sample"]
    assert set(report["counts"]["calibration"]) == {l for l in report["counts"]["calibration"] if "room1" in l}
    assert all("room2" in l for l in report["counts"]["test"])
    sc = report["scale"]["model"]
    assert sc["s"] == pytest.approx(UNITS_PER_M, rel=0.02)
    assert abs(sc["t"]) < 0.01
    assert set(sc["per_env"]) == {"room1", "room2"}
    # The device grades a cell's p10, not its median, so it reads every target
    # a noise-quantile NEARER than the ROI does; the fit absorbs that as a
    # slightly different ruler. Real, by design, and why both levels are scored.
    sc = report["scale"]["device"]
    assert sc["s"] == pytest.approx(UNITS_PER_M, rel=0.10)
    assert abs(sc["t"]) < 0.05


def test_report_depth_metrics_are_near_perfect_on_a_perfect_model(report):
    for level in ("model", "device"):
        ov = report[level]["overall"]
        assert ov["delta1"] == 1.0
        assert ov["abs_rel"] < 0.05
        lo, hi = ov["delta1_ci"]
        assert lo == 1.0 and hi == 1.0
        by_d = report[level]["by"]["distance"]
        assert set(by_d) == {"0.5", "1.0", "2.0"}
        assert all(m["n_captures"] == 3 for m in by_d.values())


def test_report_translates_placeholders_into_metres(report):
    t = report["thresholds_in_metres"]
    assert t["MIN_DEPTH_M"]["metres"] == pytest.approx(du.MIN_DEPTH_M / UNITS_PER_M, rel=0.05)
    assert t["MAX_DEPTH_M"]["metres"] == pytest.approx(du.MAX_DEPTH_M / UNITS_PER_M, rel=0.05)
    assert t["warn_m_in_units"] == pytest.approx(1.5 * UNITS_PER_M, rel=0.05)


def test_report_ordinal_is_monotonic(report):
    # Six captures share each true distance (3 zones x 2 rooms), so rank ties
    # cap rho below 1 even for a perfect model; the sign and size are what matter.
    assert report["ordinal"]["model_rho"] > 0.9
    assert report["ordinal"]["device_rho"] > 0.9


def test_report_detection_at_current_placeholders(report):
    # At the current ruler 0.5 m = 10 units (full warning), 1.0 m = 20 units
    # (curve 0.44 -> well above the deadband), 2.0 m = 40 units (beyond
    # MAX_DEPTH_M -> silent), open = silent. So a 1.5 m warn line is separable
    # perfectly at perception level.
    d = report["detection"]["perception"]
    assert d["recall"] == 1.0 and d["precision"] == 1.0 and d["f1"] == 1.0
    assert d["open_fpr"] == 0.0 and d["far_positive_fpr"] == 0.0
    # The haptics need PULSE_RISE_FRAMES consecutive frames to leave silence,
    # so the driven level lags by a couple of frames but never false-fires.
    dd = report["detection"]["driven"]
    assert dd["precision"] == 1.0 and dd["open_fpr"] == 0.0
    assert 0.5 < dd["recall"] < 1.0


def test_report_response_curve_is_monotonic(report):
    rows = report["response"]["board"]
    assert rows["0.5"]["mean"] > rows["1.0"]["mean"] > rows["2.0"]["mean"] == 0.0
    assert rows["0.5"]["warn_frac"] == 1.0 and rows["2.0"]["warn_frac"] == 0.0


def test_report_spatial_confusion_is_diagonal(report):
    s = report["spatial"]["perception"]
    assert s["accuracy"] == 1.0
    for tz in du.ZONE_NAMES:
        assert s["matrix"][tz][tz] > 0
        assert sum(v for z, v in s["matrix"][tz].items() if z != tz) == 0
    # With centre priority, side targets still land in their own zone.
    sd = report["spatial"]["driven"]
    for tz in du.ZONE_NAMES:
        others = [sd["matrix"][tz][z] for z in du.ZONE_NAMES if z != tz]
        assert sum(others) == 0


def test_report_temporal_static_scene_does_not_flip(report):
    for label, entry in report["temporal"]["per_capture"].items():
        for z in du.ZONE_NAMES:
            assert entry[z]["flips"] == 0, (label, z)
            assert entry[z]["floor_erased"] == 0.0     # flat background: no floor to erase


def test_in_sample_when_no_calib_env(replayed):
    r = evaluate(replayed, calib_envs=(), warn_m=1.5)
    assert r["in_sample"] is True
    assert len(r["counts"]["test"]) == len(r["counts"]["calibration"])


def test_unknown_calib_env_is_an_error(replayed):
    with pytest.raises(ValueError):
        evaluate(replayed, calib_envs=("nowhere",))


def test_open_only_corpus_reports_nothing_to_score(tmp_path):
    make_corpus(tmp_path, envs=("r",), distances=(), zones=())
    caps = load_corpus(str(tmp_path))
    for c in caps:
        replay(c)
    r = evaluate(caps)
    assert r["counts"]["positives"] == 0 and r["scale"] == {}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_prints_and_writes_json(corpus, tmp_path, capsys):
    out = tmp_path / "report.json"
    rc = main([str(corpus), "--calib-env", "room1", "--warn-m", "1.5", "--json", str(out), "--quiet"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "A. MODEL depth" in text and "B. DETECTION" in text and "C. SPATIAL" in text
    assert "IN-SAMPLE" not in text
    data = json.loads(out.read_text())
    assert data["scale"]["model"]["s"] == pytest.approx(UNITS_PER_M, rel=0.05)
    assert data["model"]["overall"]["delta1"] == 1.0


# --------------------------------------------------------------------------- #
# Capture-side protocol driver
# --------------------------------------------------------------------------- #

from second_vision.core.capture import (  # noqa: E402
    FrameCapture, PAUSE_LABELS, drive_protocol, manifest_counts, protocol_labels,
)


def test_protocol_labels_all_parse_and_cover_the_grid():
    labels = protocol_labels("room1")
    assert len(labels) == 2 * 3 * 6 + 3 * 3 + 4
    for l in labels:
        gt = parse_label(l)
        assert gt["env"].startswith("room1")
        if gt["target"] != "open":
            assert gt["distance_m"] is not None and gt["zone"] in du.ZONE_NAMES


def test_drive_protocol_writes_labels_then_pauses(tmp_path):
    label_file = tmp_path / "calib_label.txt"
    seen, prompts = [], iter([""] * 4 + ["q"])
    def fake_input(prompt):
        seen.append(label_file.read_text().strip())
        return next(prompts)
    drive_protocol("r", str(tmp_path), str(label_file), input_fn=fake_input, print_fn=lambda *a: None)
    labels = protocol_labels("r")
    # Before START the file says pause; while "capturing" it holds the label.
    assert seen[0] == "pause" and seen[1] == labels[0]
    assert seen[2] == "pause" and seen[3] == labels[1]
    assert label_file.read_text().strip() == "pause"


def test_drive_protocol_skips_placements_already_captured(tmp_path):
    make_corpus(tmp_path, envs=("r",), distances=(0.5,), zones=("left",))
    # board_0.5m_L_r has FRAMES (8) frames: skipped at skip_done=5, not at 30.
    assert manifest_counts(str(tmp_path))["board_0.5m_L_r"] == FRAMES
    started = []
    def fake_input(prompt):
        started.append(tmp_path.joinpath("l.txt").read_text().strip())
        return "" if len(started) < 2 else "q"
    drive_protocol("r", str(tmp_path), str(tmp_path / "l.txt"), skip_done=5,
                   input_fn=fake_input, print_fn=lambda *a: None)
    assert started[1] != "board_0.5m_L_r"


def test_pause_label_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "calib_label.txt").write_text("pause\n")
    cap = FrameCapture(out_dir=str(tmp_path / "c"), every=1)
    try:
        for i in range(1, 6):
            cap.maybe_save(np.zeros((4, 4), np.float32), i)
    finally:
        cap.close()
    assert manifest_counts(str(tmp_path / "c")) == {}
    (tmp_path / "calib_label.txt").write_text("board_1.0m_C_r\n")
    cap = FrameCapture(out_dir=str(tmp_path / "c"), every=1)
    try:
        cap.maybe_save(np.zeros((4, 4), np.float32), 1)
    finally:
        cap.close()
    rows = list(csv.DictReader(open(tmp_path / "c" / "manifest.csv")))
    assert rows[0]["distance_m"] == "1.0" and rows[0]["zone"] == "center" and rows[0]["env"] == "r"
