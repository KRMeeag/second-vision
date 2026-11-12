#!/usr/bin/env python3
"""Prepare the Filipino clip set for the device (core/fil_clips.py reads the result).

    # the team's folder, already downloaded (the raw download is never committed):
    my_hailo_env/bin/python scripts/prepare_fil_clips.py --source <download dir> [--out assets/audio/fil]
    # or download it first (gdown must be installed in the venv; at most 50 files per folder):
    my_hailo_env/bin/python scripts/prepare_fil_clips.py --link "<Drive folder link>" --download-to <dir>
    # a STAND-IN set spoken by espeak-ng from a clip list, put through the same preparation
    # (for trying the device side before the real clips exist; never the real set):
    my_hailo_env/bin/python scripts/prepare_fil_clips.py --placeholders tests/data/fil_placeholder/clips.csv --out <dir>

Steps:
  1. Check the set: clips.csv, voice_info.txt, and exactly one <clip_id>.wav per CSV row (16-bit
     PCM, mono). Anything missing or extra: stop and say what, write nothing.
  2. Per clip: trim leading and trailing silence (below TRIM_DBFS, keeping TRIM_PAD_S each side);
     scale to one loudness, the active-speech RMS of `espeak-ng -a 100 --stdout "person left"` (so
     the volume knob sounds the same in both languages); keep peaks at or below PEAK_DBFS (a clip
     that would go over is scaled to the peak limit instead, and the report says so); resample to
     the earphone output's rate (--rate, default 48000; see the report for how it was chosen).
  3. Write to --out: the prepared WAVs, clips.csv and voice_info.txt as delivered, and
     prepare-report.txt / prepare-report.csv (duration, RMS, peak and gain per clip).
Standard library and numpy only.
"""
import argparse
import csv
import io
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "assets" / "audio" / "fil"
REFERENCE_TEXT = "person left"
TRIM_DBFS = -45.0          # a 10 ms window quieter than this, at either end, is silence
TRIM_PAD_S = 0.010         # kept either side of the speech, so no consonant is clipped
ACTIVE_DBFS = -40.0        # 20 ms windows at least this loud count toward the speech RMS
PEAK_DBFS = -1.0
FULL = 32768.0


def dbfs(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9) / FULL)


def read_wav(path_or_bytes) -> tuple[np.ndarray, int]:
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else str(path_or_bytes)
    with wave.open(src, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{w.getnchannels()} channel(s), {8 * w.getsampwidth()}-bit: need 16-bit mono")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
        return data, w.getframerate()


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    out = np.clip(np.rint(samples), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(out.tobytes())


def window_rms(x: np.ndarray, rate: int, seconds: float) -> np.ndarray:
    n = max(1, int(rate * seconds))
    k = len(x) // n
    if k == 0:
        return np.array([np.sqrt(np.mean(x ** 2))]) if len(x) else np.array([0.0])
    return np.sqrt(np.mean(x[: k * n].reshape(k, n) ** 2, axis=1))


def trim(x: np.ndarray, rate: int) -> np.ndarray:
    n = max(1, int(rate * 0.010))
    loud = np.nonzero(window_rms(x, rate, 0.010) >= FULL * 10 ** (TRIM_DBFS / 20))[0]
    if loud.size == 0:
        return x
    pad = int(rate * TRIM_PAD_S)
    return x[max(0, loud[0] * n - pad): min(len(x), (loud[-1] + 1) * n + pad)]


def active_rms(x: np.ndarray, rate: int) -> float:
    w = window_rms(x, rate, 0.020)
    active = w[w >= FULL * 10 ** (ACTIVE_DBFS / 20)]
    return float(np.sqrt(np.mean(active ** 2))) if active.size else float(np.sqrt(np.mean(x ** 2)))


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Band-limited resampling by FFT (zero-padding or truncating the spectrum)."""
    if src == dst or len(x) == 0:
        return x
    n_out = round(len(x) * dst / src)
    spec = np.fft.rfft(x)
    out = np.zeros(n_out // 2 + 1, dtype=complex)
    m = min(len(spec), len(out))
    out[:m] = spec[:m]
    return np.fft.irfft(out, n_out) * (n_out / len(x))


def espeak_wav(text: str, amplitude: int = 100) -> bytes:
    return subprocess.run(["espeak-ng", "-a", str(amplitude), "--stdout", text],
                          check=True, capture_output=True).stdout


def check_set(source: Path) -> tuple[list[dict], list[str]]:
    """The CSV rows, and every problem with the delivered set (empty when it is complete)."""
    problems = []
    for name in ("clips.csv", "voice_info.txt"):
        if not (source / name).is_file():
            problems.append(f"missing {name}")
    if problems and "missing clips.csv" in problems:
        return [], problems
    with open(source / "clips.csv", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    ids = [r["clip_id"].strip() for r in rows]
    if len(set(ids)) != len(ids):
        problems.append("clip_id repeated in clips.csv")
    wavs = {p.stem for p in source.glob("*.wav")}
    problems += [f"missing {i}.wav" for i in ids if i not in wavs]
    problems += [f"extra {w}.wav (not in clips.csv)" for w in sorted(wavs - set(ids))]
    extra = sorted(p.name for p in source.iterdir()
                   if p.is_file() and p.suffix != ".wav" and p.name not in ("clips.csv", "voice_info.txt"))
    problems += [f"extra file {e}" for e in extra]
    for i in ids:
        if i in wavs:
            try:
                read_wav(source / f"{i}.wav")
            except (ValueError, wave.Error, EOFError) as e:
                problems.append(f"{i}.wav: {e}")
    return rows, problems


def make_placeholders(table: Path, dest: Path) -> None:
    """A stand-in delivery: each row spoken by espeak-ng (fil_text when there is one, else
    en_text), resampled to 24 kHz like the real clips. Never the real set."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(table, dest / "clips.csv")
    with open(table, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        text = r["fil_text"] if r.get("fil_text") and not r["fil_text"].startswith("(") else r["en_text"]
        x, rate = read_wav(espeak_wav(text))
        write_wav(dest / f"{r['clip_id']}.wav", resample(x, rate, 24000), 24000)
    (dest / "voice_info.txt").write_text(
        "PLACEHOLDER set: espeak-ng (English voice) reading the clip list; not the team's clips.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--source", type=Path, help="folder holding the delivered set")
    src.add_argument("--link", help="Google Drive folder link (downloaded with gdown into --download-to)")
    src.add_argument("--placeholders", type=Path, metavar="CLIPS_CSV", help="make a stand-in set from this list")
    ap.add_argument("--download-to", type=Path, help="with --link: an empty folder for the raw download")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--rate", type=int, default=48000, help="the earphone output's sample rate")
    args = ap.parse_args()

    if args.link:
        if not args.download_to:
            ap.error("--link needs --download-to")
        if shutil.which("gdown") is None and not (Path(sys.executable).parent / "gdown").exists():
            print("gdown is not installed in this environment; download the folder another way and use --source")
            return 2
        gdown = shutil.which("gdown") or str(Path(sys.executable).parent / "gdown")
        args.download_to.mkdir(parents=True, exist_ok=True)
        subprocess.run([gdown, "--folder", args.link, "-O", str(args.download_to)], check=True)
        found = [p for p in args.download_to.rglob("clips.csv")]
        if len(found) != 1:
            print(f"expected one clips.csv in the download, found {len(found)}")
            return 1
        source = found[0].parent
    elif args.placeholders:
        source = args.out.parent / (args.out.name + "-raw-placeholders")
        make_placeholders(args.placeholders, source)
    else:
        source = args.source

    rows, problems = check_set(source)
    if problems:
        print(f"the set in {source} is not complete; nothing written:")
        print("\n".join(f"  {p}" for p in problems))
        return 1

    ref_x, ref_rate = read_wav(espeak_wav(REFERENCE_TEXT, 100))
    ref = active_rms(trim(ref_x, ref_rate), ref_rate)
    peak_limit = FULL * 10 ** (PEAK_DBFS / 20)

    args.out.mkdir(parents=True, exist_ok=True)
    report = []
    for r in rows:
        cid = r["clip_id"].strip()
        x, rate = read_wav(source / f"{cid}.wav")
        t = trim(x, rate)
        rms = active_rms(t, rate)
        gain = ref / rms if rms > 0 else 1.0
        note = ""
        if np.max(np.abs(t)) * gain > peak_limit:
            gain = peak_limit / np.max(np.abs(t))
            note = "peak-limited: quieter than the reference"
        y = resample(t * gain, rate, args.rate)
        if np.max(np.abs(y)) > peak_limit:                  # resampling can overshoot slightly
            y *= peak_limit / np.max(np.abs(y))
            note = note or "peak-limited after resampling"
        write_wav(args.out / f"{cid}.wav", y, args.rate)
        report.append({"clip_id": cid, "type": r.get("type", ""), "en_text": r["en_text"],
                       "source_rate": rate, "source_s": f"{len(x) / rate:.3f}", "trimmed_s": f"{len(t) / rate:.3f}",
                       "gain_db": f"{20 * np.log10(gain):+.1f}", "rms_dbfs": f"{dbfs(active_rms(y, args.rate)):.1f}",
                       "peak_dbfs": f"{dbfs(float(np.max(np.abs(y)))):.1f}", "note": note})
    shutil.copy(source / "clips.csv", args.out / "clips.csv")
    shutil.copy(source / "voice_info.txt", args.out / "voice_info.txt")

    with open(args.out / "prepare-report.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(report[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(report)
    lines = [f"prepare_fil_clips.py, {time.strftime('%F %T %Z')}",
             f"source: {source}", f"output: {args.out}, {args.rate} Hz, 16-bit mono",
             f"reference loudness: espeak-ng -a 100 \"{REFERENCE_TEXT}\", active-speech RMS {dbfs(ref):.1f} dBFS",
             f"trim below {TRIM_DBFS} dBFS (10 ms windows), {1000 * TRIM_PAD_S:.0f} ms kept each side; "
             f"peaks kept at or below {PEAK_DBFS} dBFS",
             f"clips: {len(report)}; peak-limited: {sum(1 for r in report if r['note'])}", ""]
    lines += [f"{r['clip_id']:<24} {r['trimmed_s']:>6} s  gain {r['gain_db']:>6} dB  RMS {r['rms_dbfs']:>6}  "
              f"peak {r['peak_dbfs']:>5} dBFS  {r['note']}" for r in report]
    (args.out / "prepare-report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
