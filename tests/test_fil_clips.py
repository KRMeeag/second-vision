"""
Filipino speech from pre-made clips (core/fil_clips.py, and the speech worker's use of it).

Hardware-free. The clip list is the team's DRAFT (tests/data/fil_placeholder/clips.csv, a copy of
results/panel-revisions/session-c/evidence/second_vision_tts_clips.csv, 2026-11-12); the final list
comes with the clips. The clips themselves are placeholders: a synthetic WAV per row, written into
a temporary folder with known samples, so render() can be checked sample by sample.

  1. inventory guard: every sentence the code speaks, and every detection label of the model in
     use, resolves to clips (a spoken string changed in the code fails here, instead of quietly
     going back to English on the device). Run on the draft list, and on the shipped set in
     assets/audio/fil when it exists.
  2. all 208 detection phrases resolve to the right clip sequence.
  3. text the clips cannot say completely resolves to None.
  4. render(): length, gain, no int16 wrap-around.
  5-7. the speech worker: see the second half of this file.
"""

import ast
import csv
import io
import itertools
import json
import re
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from second_vision.core import fil_clips  # noqa: E402
from second_vision.core.fil_clips import CLIP_GAP_SECONDS, ClipBank  # noqa: E402

PLACEHOLDER_DIR = REPO_ROOT / "tests" / "data" / "fil_placeholder"
SHIPPED_DIR = fil_clips.DEFAULT_CLIP_DIR
RATE = 24000                       # the delivered clips' rate (handoff 2026-11-12)
SRC = REPO_ROOT / "src" / "second_vision"

ZONES = ("left", "center", "right")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav(path: Path, samples, rate: int = RATE, channels: int = 1, width: int = 2) -> None:
    data = np.asarray(samples, dtype="<i2")
    if channels == 2:
        data = np.repeat(data, 2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data.tobytes() if width == 2 else bytes(len(samples)))


def _synthetic(i: int) -> np.ndarray:
    """Clip number i: 200 + 37*i samples, a ramp no other clip shares."""
    n = 200 + 37 * i
    return ((np.arange(n) * (i + 3)) % 2000 - 1000 + 7 * i).astype(np.int16)


def _rows(folder: Path = PLACEHOLDER_DIR) -> list[dict]:
    with open(folder / "clips.csv", newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def clip_dir(tmp_path):
    """The placeholder clip list with a synthetic WAV for every row. Returns (folder, samples)."""
    shutil.copy(PLACEHOLDER_DIR / "clips.csv", tmp_path / "clips.csv")
    samples = {}
    for i, row in enumerate(_rows()):
        samples[row["clip_id"]] = _synthetic(i)
        _write_wav(tmp_path / f"{row['clip_id']}.wav", samples[row["clip_id"]])
    return tmp_path, samples


@pytest.fixture
def bank(clip_dir):
    return ClipBank.load(clip_dir[0])


def _word_id(word: str) -> str:
    return "w_" + word.replace(" ", "_")


def _detection_phrases(labels):
    """Every phrase pipeline/callbacks.py can build, with the placeholder ids it should give."""
    for label, multiple in itertools.product(labels, (False, True)):
        forms = [(f"{label} {z}", [label, z]) for z in ZONES]
        forms += [(f"{label} still {z}", [label, "still", z]) for z in ZONES]
        forms += [(f"{label} leaving to {z}", [label, "leaving to", z]) for z in ("left", "right")]
        for text, words in forms:
            if multiple:
                text, words = f"multiple {text}", ["multiple"] + words
            yield text, [_word_id(w) for w in words]


def _string_constants(path: Path, pattern: str) -> dict[str, str]:
    """Module-level NAME = "literal" assignments whose name matches `pattern`, read from the
    source (pipeline/app.py cannot be imported without GStreamer). A matching name assigned
    anything but a string literal fails, so this test is updated with the code."""
    found = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if re.fullmatch(pattern, name):
                assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str), \
                    f"{path.name}: {name} is no longer a plain string; update this test"
                found[name] = node.value.value
    return found


def _spoken_sentences() -> dict[str, str]:
    """Every whole sentence the device speaks: the alert texts and the mode announcements."""
    texts = {}
    for rel in ("pipeline/app.py", "core/camera_view.py", "core/connections.py"):
        texts.update(_string_constants(SRC / rel, r"[A-Z_]+_TEXT"))
    # Raised as a silent alert, never spoken (core/connections.py says so), so it has no clip.
    texts.pop("EARPHONES_MISSING_TEXT")
    reader = ast.parse((SRC / "workers" / "config_reader.py").read_text())
    [modes] = [ast.literal_eval(n.value) for n in reader.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "VALID_MODES" for t in n.targets)]
    for mode in modes:
        texts[f"VALID_MODES {mode}"] = f"{mode} mode"    # config_reader: {"announce": f"{new_mode} mode"}
    return texts


def _spoken_labels() -> list[str]:
    """The words the model in use can be announced as: DETECTION_CLASS_MAP over its labels.json."""
    from second_vision.pipeline import callbacks
    app = (SRC / "pipeline" / "app.py").read_text()
    version = re.search(r'^DET_MODEL_VERSION = "([^"]+)"$', app, re.M).group(1)
    names = json.loads((REPO_ROOT / "models" / version / "hef_deploy" / "labels.json").read_text())["labels"]
    spoken = {callbacks.DETECTION_CLASS_MAP[n] for n in names if n in callbacks.DETECTION_CLASS_MAP}
    return sorted(spoken - {"pole"})       # hef_v2 only; no clip by design (handoff section 10)


def _clip_sets():
    sets = [pytest.param("placeholder", id="draft-list")]
    if (SHIPPED_DIR / "clips.csv").is_file():
        sets.append(pytest.param("shipped", id="shipped-set"))
    return sets


@pytest.fixture
def any_bank(request, clip_dir):
    if request.param == "shipped":
        b = ClipBank.load(SHIPPED_DIR)
        assert not b.problems, f"shipped clip set has problems: {b.problems}"
        return b
    return ClipBank.load(clip_dir[0])


# ---------------------------------------------------------------------------
# 1. Inventory guard
# ---------------------------------------------------------------------------

def test_inventory_sources_are_what_the_handoff_lists():
    """The guard below is only as good as its inventory: 11 alert sentences and 4 modes."""
    sentences = _spoken_sentences()
    assert len(sentences) == 15, sorted(sentences)
    assert set(_spoken_labels()) >= {"person", "vehicle", "motorcycle", "animal", "door", "chair",
                                     "table", "pothole", "trash bin", "stairs", "bench"}


@pytest.mark.parametrize("any_bank", _clip_sets(), indirect=True)
def test_every_spoken_sentence_has_a_clip(any_bank):
    missing = {name: text for name, text in _spoken_sentences().items() if any_bank.resolve(text) is None}
    assert not missing, f"spoken in English, no clip: {missing}"
    for text in _spoken_sentences().values():
        assert len(any_bank.resolve(text)) == 1, text          # a sentence is one clip, not spliced


@pytest.mark.parametrize("any_bank", _clip_sets(), indirect=True)
def test_every_label_of_the_model_in_use_has_a_clip(any_bank):
    labels = _spoken_labels()
    missing = [t for t, _ in _detection_phrases(labels) if any_bank.resolve(t) is None]
    assert not missing, f"{len(missing)} detection phrases go back to English, e.g. {missing[:5]}"


# ---------------------------------------------------------------------------
# 2. All 208 detection phrases
# ---------------------------------------------------------------------------

def test_all_208_detection_phrases_resolve_to_the_right_clips(bank):
    labels = sorted(bank.labels)
    assert len(labels) == 13
    phrases = list(_detection_phrases(labels))
    assert len(phrases) == 208                     # 13 x (3 + 3 + 2) x (with, without "multiple")
    for text, expected in phrases:
        assert bank.resolve(text) == expected, text


def test_labels_come_from_the_clip_list_not_the_code(clip_dir):
    """A new "word: object" row is a new label, with no change to fil_clips.py."""
    folder, _ = clip_dir
    with open(folder / "clips.csv", "a", newline="") as fh:
        csv.writer(fh).writerow(["w_kalabaw", "word: object", "water buffalo", "kalabaw", "", "test"])
    _write_wav(folder / "w_kalabaw.wav", _synthetic(99))
    b = ClipBank.load(folder)
    assert b.resolve("multiple water buffalo leaving to right") == [
        "w_multiple", "w_kalabaw", "w_leaving_to", "w_right"]


def test_case_spacing_and_final_full_stop_do_not_matter(bank):
    assert bank.resolve("Camera view is back") == ["s_view_back"]
    assert bank.resolve("  CAMERA   view is back. ") == ["s_view_back"]
    assert bank.resolve("Person  Left") == ["w_person", "w_left"]


# ---------------------------------------------------------------------------
# 3. What the clips cannot say
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "person leaving to center",          # callbacks never builds it; there is no such sentence
    "multiple person leaving to center",
    "giraffe left",                      # unknown label
    "pole left",                         # hef_v2's label: no clip by design
    "person left now",                   # extra words
    "the person left",
    "person still leaving to left",
    "multiple multiple person left",
    "person",                            # no zone
    "left",
    "person up",
    "",
    "Camera has been reconnected again",
])
def test_unresolvable_text_is_none(bank, text):
    assert bank.resolve(text) is None


# ---------------------------------------------------------------------------
# 4. render()
# ---------------------------------------------------------------------------

def _decode(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth()) == (1, 2)
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2"), w.getframerate()


def test_render_is_the_clips_with_gaps_between(bank, clip_dir):
    _, samples = clip_dir
    ids = ["w_multiple", "w_trash_bin", "w_still", "w_left"]
    out, rate = _decode(bank.render(ids, 100))
    gap = round(CLIP_GAP_SECONDS * RATE)
    assert rate == RATE
    assert len(out) == sum(len(samples[i]) for i in ids) + gap * (len(ids) - 1)
    expected = np.concatenate([np.concatenate([samples[i], np.zeros(gap, np.int16)]) for i in ids])[:len(out)]
    assert np.array_equal(out, expected)           # amplitude 100: the samples unchanged


def test_render_of_one_clip_has_no_gap(bank, clip_dir):
    out, _ = _decode(bank.render(["s_camera_back"], 100))
    assert np.array_equal(out, clip_dir[1]["s_camera_back"])


def test_render_gain_is_amplitude_over_100(bank, clip_dir):
    src = clip_dir[1]["w_person"]
    out, _ = _decode(bank.render(["w_person"], 10))
    assert np.array_equal(out, np.rint(src.astype(np.float64) * 0.1).astype(np.int16))
    out50, _ = _decode(bank.render(["w_person"], 50))
    assert np.array_equal(out50, np.rint(src.astype(np.float64) * 0.5).astype(np.int16))


def test_render_never_wraps_around(clip_dir):
    folder, _ = clip_dir
    _write_wav(folder / "w_left.wav", [32767, -32768, 32767, -32768, 0])
    b = ClipBank.load(folder)
    for amplitude, top, bottom in ((100, 32767, -32768), (10, 3277, -3277), (140, 32767, -32768)):
        out, _ = _decode(b.render(["w_left"], amplitude))
        assert out.max() == top and out.min() == bottom, amplitude


# ---------------------------------------------------------------------------
# 7. Missing and malformed clips
# ---------------------------------------------------------------------------

def test_missing_clip_only_affects_the_utterances_that_need_it(clip_dir):
    folder, _ = clip_dir
    (folder / "w_left.wav").unlink()
    b = ClipBank.load(folder)
    assert any("w_left" in p for p in b.problems)
    assert b.resolve("person left") is None                     # never half an utterance
    assert b.resolve("multiple person still left") is None
    assert b.resolve("person right") == ["w_person", "w_right"]
    assert b.resolve("both mode") == ["s_mode_both"]


@pytest.mark.parametrize("bad", ["stereo", "8-bit", "other rate", "empty", "not a wav"])
def test_malformed_clip_is_recorded_not_fatal(clip_dir, bad):
    folder, _ = clip_dir
    path = folder / "s_panel_back.wav"
    if bad == "stereo":
        _write_wav(path, _synthetic(1), channels=2)
    elif bad == "8-bit":
        _write_wav(path, _synthetic(1), width=1)
    elif bad == "other rate":
        _write_wav(path, _synthetic(1), rate=48000)
    elif bad == "empty":
        _write_wav(path, [])
    else:
        path.write_bytes(b"this is not a wav file")
    b = ClipBank.load(folder)
    assert any(p.startswith("s_panel_back") for p in b.problems), b.problems
    assert b.resolve("Controller board connected") is None
    assert b.resolve("Camera has been reconnected") == ["s_camera_back"]
    assert len(b) == 33


def test_missing_folder_is_an_empty_bank(tmp_path):
    b = ClipBank.load(tmp_path / "nowhere")
    assert len(b) == 0 and b.problems
    assert b.resolve("person left") is None and b.resolve("both mode") is None


def test_the_worker_bank_warns_once_when_there_are_no_clips(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fil_clips, "DEFAULT_CLIP_DIR", tmp_path / "nowhere")
    monkeypatch.setattr(fil_clips, "_bank", None)
    first = fil_clips.bank()
    assert fil_clips.bank() is first                            # loaded once
    out = capsys.readouterr().out
    assert out.count("WARNING: no Filipino clips") == 1 and "speaking English" in out


def test_the_worker_bank_reports_what_it_loaded(clip_dir, monkeypatch, capsys):
    monkeypatch.setattr(fil_clips, "DEFAULT_CLIP_DIR", clip_dir[0])
    monkeypatch.setattr(fil_clips, "_bank", None)
    assert len(fil_clips.bank()) == 34
    assert f"34 loaded from {clip_dir[0]} at {RATE} Hz" in capsys.readouterr().out


def test_tests_never_get_the_real_bank():
    """tests/conftest.py gives every test an empty bank, so no test plays the shipped clips."""
    assert len(fil_clips.bank()) == 0


# ===========================================================================
# The speech worker (workers/tts_worker.py)
#   5. _start_speech(): fil + resolvable -> the clips through pw-play; fil + unresolvable -> espeak
#      with the same argv as before; en -> espeak always.
#   6. a cut (terminate) or a natural end removes the temporary WAV.
#   7. a missing clip -> English, logged once per text.
# The player is a stand-in Python process (tw._player_argv patched), so nothing is played.
# ===========================================================================

import glob  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.core.priority import TIER_NORMAL, TIER_URGENT, PriorityMailbox  # noqa: E402
from second_vision.workers import tts_worker as tw  # noqa: E402

needs_shm = pytest.mark.skipif(not os.path.isdir("/dev/shm"), reason="no /dev/shm on this machine")


def _own_clip_files():
    return glob.glob(os.path.join(tw._CLIP_TMP_DIR, f"{tw._CLIP_TMP_PREFIX}{os.getpid()}-*.wav"))


@pytest.fixture
def fil_bank(clip_dir, monkeypatch):
    """The synthetic clip bank installed as the worker's bank; fallback log cleared."""
    b = ClipBank.load(clip_dir[0])
    monkeypatch.setattr(fil_clips, "_bank", b)
    monkeypatch.setattr(tw, "_fallback_noted", set())
    return b


@pytest.fixture
def player(monkeypatch):
    """A stand-in for pw-play: a Python process that sleeps `seconds` (per call, in order)."""
    durations = []

    def argv(path):
        seconds = durations.pop(0) if durations else 0.0
        return [sys.executable, "-c", f"import time; time.sleep({seconds})", path]

    monkeypatch.setattr(tw, "_player_argv", argv)
    return durations


@pytest.fixture
def espeak(monkeypatch):
    """Record what espeak-ng would have been started with, without starting it."""
    calls = []

    class _Rec:
        def __init__(self, argv, **kwargs):
            calls.append(list(argv))
            self.args = argv

        def poll(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr(tw.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tw.subprocess, "Popen", _Rec)
    return calls


def _espeak_argv(text, amplitude):
    """espeak-ng's command line exactly as _start_espeak built it before the clips existed."""
    return ["espeak-ng", "-s", "160", "-a", str(amplitude), text]


def _finish(proc, timeout=5.0):
    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        assert time.monotonic() < deadline, "the stand-in player did not end"
        time.sleep(0.01)


def test_the_player_is_pw_play():
    assert tw._PLAYER == "pw-play" and tw._CLIP_TMP_DIR == "/dev/shm"
    assert tw._player_argv("/dev/shm/x.wav") == ["pw-play", "/dev/shm/x.wav"]


def test_language_defaults_to_filipino():
    assert SystemConfig().language == "fil"


@needs_shm
def test_fil_and_resolvable_plays_the_rendered_clips_from_dev_shm(fil_bank, player):
    player.append(5)
    proc = tw._start_speech("multiple person still left", 37, "fil")
    try:
        assert isinstance(proc, tw._ClipPlayback)
        path = proc.args[-1]
        assert path.startswith(f"/dev/shm/{tw._CLIP_TMP_PREFIX}") and path.endswith(".wav")
        assert open(path, "rb").read() == fil_bank.render(
            ["w_multiple", "w_person", "w_still", "w_left"], 37)
    finally:
        proc.terminate()
        proc.wait()


@pytest.mark.parametrize("text", ["pole left", "person leaving to center", "something new"])
def test_fil_and_unresolvable_is_espeak_with_the_same_argv(fil_bank, espeak, text):
    proc = tw._start_speech(text, 37, "fil")
    assert not isinstance(proc, tw._ClipPlayback)
    assert espeak == [_espeak_argv(text, 37)]


@pytest.mark.parametrize("language", ["en", None])
def test_english_is_always_espeak(fil_bank, espeak, language):
    for text in ("person left", "both mode", "Camera view is back."):
        assert not isinstance(tw._start_speech(text, 100, language), tw._ClipPlayback)
    assert espeak == [_espeak_argv(t, 100) for t in ("person left", "both mode", "Camera view is back.")]


@needs_shm
def test_a_cut_removes_the_temporary_wav(fil_bank, player):
    player.append(30)
    proc = tw._start_speech("person center", 100, "fil")
    path = proc.args[-1]
    assert os.path.exists(path)
    proc.terminate()
    assert not os.path.exists(path)
    proc.wait(5)
    assert proc.returncode is not None


@needs_shm
def test_a_natural_end_removes_the_temporary_wav(fil_bank, player):
    player.append(0)
    proc = tw._start_speech("both mode", 100, "fil")
    path = proc.args[-1]
    _finish(proc)
    assert not os.path.exists(path)


@needs_shm
def test_an_urgent_item_cuts_a_playing_clip_and_nothing_is_left(fil_bank, player, capsys):
    """The real interrupt loop, with real processes: the long first utterance is cut by the
    urgent item already waiting, which then plays to the end."""
    player.extend([30, 0])
    mb = PriorityMailbox()
    ud = type("UD", (), {"tts_queue": mb, "shutdown_event": threading.Event()})()
    mb.offer({"label": "vehicle", "zone": "center", "confidence": 0.9, "priority": 5.0, "tier": TIER_URGENT})
    t0 = time.monotonic()
    tw._speak_interruptible({"label": "person", "zone": "left", "confidence": 0.9, "priority": 2.0,
                             "tier": TIER_NORMAL}, mb, ud, 100, language="fil")
    assert time.monotonic() - t0 < 5, "the first clip was not cut"
    out = capsys.readouterr().out
    assert "[TTS] 'person left' (fil)" in out and "[TTS] 'vehicle center' (fil)" in out
    assert _own_clip_files() == []


@needs_shm
def test_shutdown_while_playing_removes_the_temporary_wav(fil_bank, player):
    player.append(30)
    mb = PriorityMailbox()
    ud = type("UD", (), {"tts_queue": mb, "shutdown_event": threading.Event()})()
    threading.Timer(0.3, ud.shutdown_event.set).start()
    tw._speak_interruptible({"announce": "both mode"}, mb, ud, 100, language="fil")
    assert _own_clip_files() == []


def test_a_missing_clip_goes_to_english_logged_once(clip_dir, monkeypatch, espeak, capsys):
    (clip_dir[0] / "w_left.wav").unlink()
    monkeypatch.setattr(fil_clips, "_bank", ClipBank.load(clip_dir[0]))
    monkeypatch.setattr(tw, "_fallback_noted", set())
    for _ in range(3):
        tw._start_speech("person left", 56, "fil")
    assert espeak == [_espeak_argv("person left", 56)] * 3
    assert capsys.readouterr().out.count("speaking English: 'person left'") == 1


def test_no_clips_at_all_logs_nothing_per_text(monkeypatch, espeak, capsys):
    monkeypatch.setattr(tw, "_fallback_noted", set())          # conftest gives an empty bank
    tw._start_speech("person left", 100, "fil")
    assert espeak == [_espeak_argv("person left", 100)]
    assert "speaking English" not in capsys.readouterr().out


@needs_shm
def test_no_player_goes_to_english_and_leaves_no_file(fil_bank, monkeypatch, espeak, capsys):
    monkeypatch.setattr(tw, "_player_argv", lambda path: ["/nonexistent/pw-play", path])
    tw._start_speech("person right", 100, "fil")
    assert espeak == [_espeak_argv("person right", 100)]
    assert "clip playback failed" in capsys.readouterr().out
    assert _own_clip_files() == []


@needs_shm
def test_the_latency_log_names_the_clip_engine(fil_bank, player, monkeypatch):
    player.append(0)
    engines = []
    monkeypatch.setattr(tw.latency_log, "LOG", type("L", (), {
        "speech_started": staticmethod(lambda item, engine: engines.append(engine))})())
    mb = PriorityMailbox()
    ud = type("UD", (), {"tts_queue": mb, "shutdown_event": threading.Event()})()
    tw._speak_interruptible({"announce": "depth mode"}, mb, ud, 100, language="fil")
    tw._speak_interruptible({"announce": "something new"}, mb, ud, 100, language="en")
    assert engines[0] == "fil-clips" and engines[1] in ("espeak-ng", "pyttsx3-fallback")
