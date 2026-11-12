"""Filipino speech from pre-made WAV clips: a bank of clips, spliced into whole utterances.

eSpeak NG has no Filipino voice, so the device speaks Filipino by playing recorded clips. The speech
worker (workers/tts_worker.py) is still handed the English text it always was; resolve() turns that
text into a list of clips, and render() joins them into one WAV:

  * sentence clips: a mode announcement or a system alert, matched on its whole English text;
  * word clips: a detection phrase, parsed into its words and spliced, for example
    "multiple person still left" -> [multiple] [person] [still] [left].

Anything the bank cannot build completely is None, and the worker speaks the whole utterance in
English with espeak-ng as before. Never half an utterance in Filipino.

The clip folder holds clips.csv (columns clip_id, type, en_text, fil_text, use, notes) and one
<clip_id>.wav per row: 16-bit PCM, mono, every clip at the same sample rate. It is read once, at
the speech worker's start. A missing folder or CSV gives an empty bank (one warning, everything in
English); a missing or malformed WAV is recorded in `problems` and only the utterances that need it
fall back to English.

The CSV's `type` column decides how a row is used: "word: object" rows are the object names a
detection phrase may contain (the parser takes its labels from them, not from a list in the code);
other "word" rows are the phrase's fixed words, found by their English text (multiple, still,
leaving to, left, center, right); every other row is a whole-sentence clip.
"""

import csv
import io
import re
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLIP_DIR = REPO_ROOT / "assets" / "audio" / "fil"
CSV_NAME = "clips.csv"

# Silence between spliced word clips. PLACEHOLDER: 0.06 s is the handoff's starting point, to be
# tuned by ear on the device with the real clips.
CLIP_GAP_SECONDS = 0.06

# The fixed words of a detection phrase (pipeline/callbacks.py builds "{label} {zone}",
# "{label} still {zone}", "{label} leaving to {zone}", each optionally prefixed "multiple ").
WORD_MULTIPLE = "multiple"
WORD_STILL = "still"
WORD_LEAVING_TO = "leaving to"
ZONES = ("left", "center", "right")
# "leaving to" fires only when an object leaves the centre, so it is followed by left or right.
LEAVING_ZONES = ("left", "right")


def _norm(text: str) -> str:
    """Text compared case-insensitively, with runs of white space as one space."""
    return " ".join(str(text).split()).casefold()


def _sentence_key(text: str) -> str:
    """A sentence clip's lookup key: _norm(), without a final full stop, ! or ?."""
    return _norm(text).rstrip(".!?").rstrip()


def _row_kind(type_text: str) -> tuple[str, str]:
    """("word", "object") from "word: object"; ("sentence", "alert") from "sentence: alert"."""
    kind, _, sub = (type_text or "").partition(":")
    return kind.strip().casefold(), sub.strip().casefold()


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Samples (int16) and sample rate of a 16-bit mono PCM WAV. Raises ValueError otherwise."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1:
            raise ValueError(f"{w.getnchannels()} channels, need mono")
        if w.getsampwidth() != 2:
            raise ValueError(f"{8 * w.getsampwidth()}-bit, need 16-bit")
        if w.getcomptype() != "NONE":
            raise ValueError(f"compressed ({w.getcomptype()}), need PCM")
        frames = w.readframes(w.getnframes())
        rate = w.getframerate()
    samples = np.frombuffer(frames, dtype="<i2").astype(np.int16)
    if samples.size == 0:
        raise ValueError("no samples")
    return samples, rate


class ClipBank:
    """The loaded clips and the English text each one stands for. See the module docstring."""

    def __init__(self, folder: Path | None = None):
        self.folder = folder
        self.rate: int | None = None
        self.clips: dict[str, np.ndarray] = {}     # clip_id -> samples, only clips that loaded
        self.problems: list[str] = []              # what did not load, and why
        self._sentences: dict[str, str] = {}       # _sentence_key(en_text) -> clip_id
        self._words: dict[str, str] = {}           # _norm(en_text) -> clip_id, fixed words and labels
        self.labels: tuple[str, ...] = ()          # _norm(en_text) of the "word: object" rows
        self._detection_re: re.Pattern | None = None

    @classmethod
    def empty(cls) -> "ClipBank":
        """A bank with no clips: resolve() is always None, so everything is spoken in English."""
        return cls()

    @classmethod
    def load(cls, folder: Path | str = DEFAULT_CLIP_DIR) -> "ClipBank":
        """Read clips.csv and its WAVs from `folder`. Never raises: what fails is in `problems`."""
        bank = cls(Path(folder))
        table = bank.folder / CSV_NAME
        if not table.is_file():
            bank.problems.append(f"no {CSV_NAME} in {bank.folder}")
            return bank
        try:
            with open(table, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            bank.problems.append(f"{table}: {e}")
            return bank

        labels = []
        for n, row in enumerate(rows, start=2):        # line 1 is the header
            clip_id = (row.get("clip_id") or "").strip()
            en_text = (row.get("en_text") or "").strip()
            if not clip_id or not en_text:
                bank.problems.append(f"{CSV_NAME} line {n}: no clip_id or en_text")
                continue
            kind, sub = _row_kind(row.get("type", ""))
            if kind == "word":
                bank._words[_norm(en_text)] = clip_id
                if sub == "object":
                    labels.append(_norm(en_text))
            else:
                bank._sentences[_sentence_key(en_text)] = clip_id
            bank._load_clip(clip_id)
        bank.labels = tuple(labels)
        bank._detection_re = _detection_pattern(bank.labels)
        return bank

    def _load_clip(self, clip_id: str) -> None:
        path = self.folder / f"{clip_id}.wav"
        if not path.is_file():
            self.problems.append(f"{clip_id}: no {path.name}")
            return
        try:
            samples, rate = _read_wav(path)
        except (OSError, EOFError, ValueError, wave.Error) as e:
            self.problems.append(f"{clip_id}: {path.name}: {e}")
            return
        if self.rate is None:
            self.rate = rate
        elif rate != self.rate:
            self.problems.append(f"{clip_id}: {rate} Hz, the bank is {self.rate} Hz")
            return
        self.clips[clip_id] = samples

    def __len__(self) -> int:
        return len(self.clips)

    def resolve(self, text: str) -> list[str] | None:
        """
        The clip_ids that say `text`, in order, or None when the bank cannot say all of it.

        A sentence clip matches the whole text (case, spacing and a final full stop ignored). A
        detection phrase "[multiple ]<label>[ still| leaving to] <zone>" is spliced from word clips;
        "leaving to" only goes with left or right, as pipeline/callbacks.py only builds it that way.
        """
        if not self.clips:
            return None
        clip_id = self._sentences.get(_sentence_key(text))
        if clip_id is not None:
            return [clip_id] if clip_id in self.clips else None
        if self._detection_re is None:
            return None
        m = self._detection_re.match(_norm(text))
        if m is None:
            return None
        multiple, label, middle, zone = m.groups()
        if middle == WORD_LEAVING_TO and zone not in LEAVING_ZONES:
            return None
        words = ([WORD_MULTIPLE] if multiple else []) + [label] + ([middle] if middle else []) + [zone]
        ids = [self._words.get(w) for w in words]
        if any(i is None or i not in self.clips for i in ids):
            return None
        return ids

    def render(self, clip_ids: list[str], amplitude: int = 100) -> bytes:
        """
        One WAV (bytes) of `clip_ids` in order, CLIP_GAP_SECONDS of silence between them.

        `amplitude` is the speech worker's espeak-ng amplitude (10-100, from the volume knob): the
        samples are scaled by amplitude / 100, the same level change espeak-ng's -a gives
        (20*log10(a/100) dB, as measured in tts_worker.py). 100 leaves the samples unchanged.
        """
        gap = np.zeros(round(CLIP_GAP_SECONDS * self.rate), dtype=np.int16)
        parts = []
        for i, clip_id in enumerate(clip_ids):
            if i:
                parts.append(gap)
            parts.append(self.clips[clip_id])
        samples = np.concatenate(parts)
        if amplitude != 100:
            scaled = np.rint(samples.astype(np.float64) * (amplitude / 100))
            samples = np.clip(scaled, -32768, 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(samples.astype("<i2").tobytes())
        return buf.getvalue()


def _detection_pattern(labels: tuple[str, ...]) -> re.Pattern | None:
    """The detection-phrase parser for these labels (longest first, so no label hides another)."""
    if not labels:
        return None
    names = "|".join(re.escape(l) for l in sorted(set(labels), key=len, reverse=True))
    middles = "|".join(re.escape(w) for w in (WORD_LEAVING_TO, WORD_STILL))
    zones = "|".join(ZONES)
    return re.compile(rf"^(?:({re.escape(WORD_MULTIPLE)}) )?({names})(?: ({middles}))? ({zones})$")


# The speech worker's bank, read on first use (the worker asks at its start, so that is when).
_bank: ClipBank | None = None


def bank() -> ClipBank:
    """The clip bank from DEFAULT_CLIP_DIR, loaded once. Logs one line about what it found."""
    global _bank
    if _bank is None:
        _bank = ClipBank.load(DEFAULT_CLIP_DIR)
        if not _bank.clips:
            print(f"[TTS] WARNING: no Filipino clips loaded from {DEFAULT_CLIP_DIR} "
                  f"({'; '.join(_bank.problems[:3]) or 'empty'}): speaking English", flush=True)
        else:
            print(f"[TTS] Filipino clips: {len(_bank.clips)} loaded from {DEFAULT_CLIP_DIR} at "
                  f"{_bank.rate} Hz" + (f"; {len(_bank.problems)} problem(s): {'; '.join(_bank.problems)}"
                                        if _bank.problems else ""), flush=True)
    return _bank
