#!/usr/bin/env python3
"""Build Session A's matrix from the runner's raw outputs. Standard library only.

    python3 -I results/panel-revisions/session-a/parse_session_a.py [runs.csv]

Reads runs.csv (one row per attempt, from run_session_a.sh), the raw benchmark outputs (raw/*.txt) and
the run2 JSON files (raw/*.json). Uses only attempts with exit code 0 and throttle verdict "ok".
Joins the Model Zoo columns from step1/model-zoo-v2.17-yolov8.csv, copied as written; the only
computed Zoo figure is "COCO mAP hardware (computed)" = float − Hardware Degradation (Kenth, 2026-11-09).
Writes matrix.csv and prints the Markdown tables and the threshold statements for RESULTS.md.
"""
import csv
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZOO_CSV = HERE.parent / "step1" / "model-zoo-v2.17-yolov8.csv"
MODELS = ["yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x", "deployed-13class"]
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse_benchmark(path: Path) -> dict:
    """FPS hw_only, FPS streaming and HW latency from a `hailortcli benchmark` summary."""
    text = ANSI.sub("", path.read_text(errors="replace").replace("\r", "\n"))
    out = {}
    m = re.search(r"FPS\s+\(hw_only\)\s+=\s+([\d.]+)", text)
    if m:
        out["fps_hw_only"] = float(m.group(1))
    m = re.search(r"\(streaming\)\s+=\s+([\d.]+)", text)
    if m:
        out["fps_streaming"] = float(m.group(1))
    m = re.search(r"Latency \(hw\)\s+=\s+([\d.]+) ms", text)
    if m:
        out["hw_latency_ms"] = float(m.group(1))
    return out


def parse_run2(path: Path) -> dict:
    """Per-network FPS from run2's JSON: the first network group is the detector, the second depth."""
    groups = json.loads(path.read_text())["network_groups"]
    return {"det_fps_with_depth": float(groups[0]["FPS"]), "depth_fps_with_det": float(groups[1]["FPS"]),
            "det_net_name": groups[0]["name"], "depth_net_name": groups[1]["name"]}


def stats(values):
    if not values:
        return None
    return {"mean": statistics.fmean(values), "min": min(values), "max": max(values), "n": len(values)}


def fmt(s, digits=1):
    if s is None:
        return "not measured"
    if s["n"] == 1:
        return f"{s['mean']:.{digits}f} (n=1)"
    return f"{s['mean']:.{digits}f} ({s['min']:.{digits}f}–{s['max']:.{digits}f})"


def main() -> int:
    runs_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "runs.csv"
    raw = runs_csv.parent / ("raw-quick" if "quick" in runs_csv.name else "raw")
    rows = list(csv.DictReader(open(runs_csv)))
    good = [r for r in rows if r["exit_code"] == "0" and r["throttle_verdict"] == "ok"]
    bad = [r for r in rows if r not in good]

    per = {}  # (model, metric) -> [values]
    for r in good:
        f = raw / r["raw_file"]
        vals = parse_benchmark(f) if r["kind"] == "benchmark" else parse_run2(f.with_suffix(".json"))
        for k, v in vals.items():
            if isinstance(v, float):
                per.setdefault((r["model"], k), []).append(v)

    zoo = {z["Network Name"]: z for z in csv.DictReader(open(ZOO_CSV))}
    metrics = ["fps_hw_only", "fps_streaming", "hw_latency_ms", "det_fps_with_depth", "depth_fps_with_det"]
    out_rows = []
    for m in MODELS + ["scdepthv3"]:
        z = zoo.get(m, {})
        row = {"model": m,
               "params_m": z.get("Params (M)", ""), "ops_g": z.get("OPS (G)", ""),
               "coco_map_float": z.get("float", ""), "hardware_degradation": z.get("Hardware Degradation", ""),
               "coco_map_hardware_computed": (f"{float(z['float']) - float(z['Hardware Degradation']):.1f}"
                                              if z else ""),
               "zoo_fps_batch1": z.get("FPS (Batch Size=1)", "")}
        for k in metrics:
            s = stats(per.get((m, k), []))
            for part in ("mean", "min", "max", "n"):
                row[f"{k}_{part}"] = "" if s is None else (s[part] if part == "n" else round(s[part], 3))
        out_rows.append(row)
    with open(runs_csv.parent / ("matrix-quick.csv" if "quick" in runs_csv.name else "matrix.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)

    print("| Model | Params (M) | OPS (G) | COCO mAP float | COCO mAP hardware (computed) | Zoo FPS (batch 1) "
          "| FPS alone, HW-only | FPS alone, streaming | HW latency (ms) | Det FPS with depth | Depth FPS with det |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for m in MODELS:
        z = zoo.get(m, {})
        hw = f"{float(z['float']) - float(z['Hardware Degradation']):.1f}" if z else "—"
        g = lambda k, d=1: fmt(stats(per.get((m, k), [])), d)
        print(f"| {m} | {z.get('Params (M)', '—')} | {z.get('OPS (G)', '—')} | {z.get('float', '—')} | {hw} "
              f"| {z.get('FPS (Batch Size=1)', '—')} | {g('fps_hw_only')} | {g('fps_streaming')} "
              f"| {g('hw_latency_ms', 2)} | {g('det_fps_with_depth')} | {g('depth_fps_with_det')} |")
    d = lambda k, dd=1: fmt(stats(per.get(("scdepthv3", k), [])), dd)
    print(f"\nDepth alone (scdepthv3, reference): FPS HW-only {d('fps_hw_only')}, streaming {d('fps_streaming')}, "
          f"HW latency {d('hw_latency_ms', 2)} ms")

    print("\nThresholds, with depth (both networks; judged on the LOWEST of the runs, and on the mean):")
    for limit in (30, 25):
        for label, pick in (("every run", "min"), ("mean", "mean")):
            ok = [m for m in MODELS
                  if (sd := stats(per.get((m, "det_fps_with_depth"), []))) and (sp := stats(per.get((m, "depth_fps_with_det"), [])))
                  and sd[pick] >= limit and sp[pick] >= limit]
            print(f"  >= {limit} FPS ({label}): {', '.join(ok) if ok else 'none'}")

    print(f"\nAttempts: {len(rows)} total, {len(good)} used, {len(bad)} not used")
    for r in bad:
        print(f"  not used: {r['run_id']} exit={r['exit_code']} verdict={r['throttle_verdict']} "
              f"throttled {r['throttled_before']}->{r['throttled_after']}")
    temps = [float(r[k]) for r in rows for k in ("temp_before_c", "temp_after_c") if r[k]]
    flags = sorted({r[k] for r in rows for k in ("throttled_before", "throttled_after")})
    print(f"SoC temperature around the runs (thermal_zone0): {min(temps):.1f} to {max(temps):.1f} C; "
          f"throttle flags seen: {', '.join(flags)}")
    print(f"First start {rows[0]['start_iso']}, last end {rows[-1]['end_iso']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
