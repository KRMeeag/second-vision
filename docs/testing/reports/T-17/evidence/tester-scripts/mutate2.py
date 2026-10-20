#!/usr/bin/env python3
"""Tester T-17, pass 2: the mutants that SURVIVED the author's tests, run against MY tests (so the report can say which of my
checks would have caught each one). Scratch copy of the tools; the real tree is never touched."""
import os, shutil, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutate as mu

HERE = Path(__file__).resolve().parent
PY = mu.PY
W = lambda f: f"{HERE / f}"
MAP = {
 "D01": [W("test_t17_watch.py")], "D04": [W("test_t17_watch.py")], "D05": [W("test_t17_watch.py")],
 "D18": [W("test_t17_watch.py")], "D19": [W("test_t17_watch.py")],
 "D06": [W("test_t17_real_emitters.py") + "::test_lens_run_where_the_flash_is_never_taken_away_misses_exactly_the_last_line"],
 "D08": [W("test_t17_routes.py") + "::test_ignoring_program_is_killed_after_20_s_not_before_and_nothing_is_left[q]"],
 "D14": [W("test_t17_routes2.py") + "::test_the_presenters_words_are_filed_under_the_right_step"],
 "D15": [W("test_t17_routes.py") + "::test_route_eof_on_stdin"],
 "D21": [W("test_t17_routes2.py") + "::test_two_runs_in_the_same_second_do_not_overwrite"],
 "D22": [W("test_t17_routes2.py") + "::test_a_normal_child_in_the_programs_group_is_stopped_too"],
 "D23": [W("test_t17_routes.py") + "::test_stderr_is_merged_and_kept"],
 "D24": [W("test_t17_routes2.py") + "::test_lines_the_program_prints_while_it_is_stopping_are_all_kept"],
 "P02": [W("test_t17_perf.py")], "P09": [W("test_t17_perf.py")], "P14": [W("test_t17_perf.py")], "P18": [W("test_t17_perf.py")],
 "R05": [W("test_t17_perf.py")], "R07": [W("test_t17_perf.py")], "R08": [W("test_t17_perf.py")],
 "R06": [W("test_t17_perf_e2e.py") + "::test_rundown_by_voltage_stops_the_sampler_then_the_app_and_draws_the_chart"],
 "R10": [W("test_t17_perf_e2e.py") + "::test_rundown_by_percentage_also_stops"],
 "R09": [W("test_t17_perf_e2e.py") + "::test_charger_in_during_the_run_never_stops_it_and_the_summary_says_so"],
}
out = Path(sys.argv[1]); ids = sys.argv[2:] or list(MAP)
for id in ids:
    row = next(x for x in mu.M if x[0] == id)
    _, file, old, new, _, desc = row
    d = mu.WORK / ("my-" + id)
    if d.exists(): shutil.rmtree(d)
    (d / "docs/testing/tools").mkdir(parents=True)
    for t in mu.TOOLS: shutil.copy(mu.REPO / "docs/testing/tools" / t, d / "docs/testing/tools" / t)
    os.symlink(mu.REPO / "src", d / "src"); os.symlink(mu.REPO / "scripts", d / "scripts")
    (d / "docs/testing/reports").mkdir(parents=True)
    for sub in ("T-15",):
        os.symlink(mu.REPO / "docs/testing/reports" / sub, d / "docs/testing/reports" / sub)
    p = d / "docs/testing/tools" / file
    text = p.read_text(); assert old in text, id
    p.write_text(text.replace(old, new, 1))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", T17_REPO=str(d))
    t0 = time.time()
    r = subprocess.run(["nice", "-n", "19", "timeout", "400", PY, "-m", "pytest", *MAP[id], "-x", "-q", "-p", "no:cacheprovider", "--no-header"],
                       cwd=HERE, capture_output=True, text=True, env=env)
    tail = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR")) or " passed" in l or " failed" in l][:3]
    status = "CAUGHT-BY-MY-TESTS" if r.returncode != 0 else "STILL-SURVIVES"
    line = "\t".join([id, status, desc, f"{time.time() - t0:.0f}s", " | ".join(tail)])
    print(line, flush=True)
    with open(out, "a") as fh: fh.write(line + "\n")
