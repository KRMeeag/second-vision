#!/usr/bin/env python3
"""Tester T-17: my own mutants of docs/testing/tools/{demo_run,perf_sample,perf_run}.py, run against the AUTHOR'S tests
(tests/test_demo_run.py, tests/test_perf_tools.py) in a scratch copy. The real tree is never touched.
usage: mutate.py OUTFILE [ID ...]   (no IDs = all)"""
import os, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path("/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth")
PY = str(REPO / "my_hailo_env/bin/python")
WORK = Path(os.environ["MUT_WORK"])
TOOLS = ["demo_run.py", "perf_sample.py", "perf_run.py", "perf_plot.py", "perf_npu.py"]

M = []   # (id, file, old, new, tests, description)
def m(id, file, old, new, tests, desc): M.append((id, file, old, new, tests, desc))
DR, PT = "tests/test_demo_run.py", "tests/test_perf_tools.py"

# ---- demo_run.py
m("D01", "demo_run.py", "if t - start <= e.within and re.search", "if t - start < e.within and re.search", DR, "line exactly at `within` no longer counts")
m("D02", "demo_run.py", "if t - start <= e.within and re.search", "if 0 <= t - start <= e.within and re.search", DR, "negative delays (reaction before the Enter) discarded")
m("D03", "demo_run.py", "else len(self.hits) >= self.expect.count", "else len(self.hits) >= 1", DR, "count=2 is satisfied by one line")
m("D04", "demo_run.py", "(bool(r.hits) or now - start >= r.expect.within))", "True)", DR, "an absent expectation is settled at once (does not wait its window)")
m("D05", "demo_run.py", "deadline = start + max(e.within for e in expects)", "deadline = start + min(e.within for e in expects)", DR, "the watch ends at the SHORTEST window")
m("D06", "demo_run.py", "results = watch(book, time.monotonic(), shown, action.expect)", "results = watch(book, time.monotonic(), begin, action.expect)", DR, "lines from before the prompt (earlier actions of the step) count")
m("D07", "demo_run.py", "os.killpg(app.pid, signal.SIGINT)\n            code", "os.killpg(app.pid, signal.SIGTERM)\n            code", DR, "program stopped with SIGTERM instead of SIGINT")
m("D08", "demo_run.py", "os.killpg(app.pid, signal.SIGKILL)\n            stop_note", "pass\n            stop_note", DR, "no SIGKILL when the program ignores SIGINT")
m("D09", "demo_run.py", "code = app.wait(20)", "code = app.wait(0.0001)", DR, "waits no time for the program to exit")
m("D10", "demo_run.py", "return 0 if rows and all(r[3] != \"MISSED\" for r in rows) else 1", "return 0", DR, "exit code is 0 even when a line was missed")
m("D11", "demo_run.py", "rows.append((n, step.title, \"—\", \"skipped\", missing[step.needs]))", "rows.append((n, step.title, \"—\", \"ok\", missing[step.needs]))", DR, "a step skipped for a missing part is recorded as ok")
m("D12", "demo_run.py", "misses = sum(1 for r in rows if r[3] == \"MISSED\")", "misses = 0", DR, "the summary always says 0 missed")
m("D13", "demo_run.py", "            session.write(", "            if LIVE.search(line): session.write(", DR, "session.log keeps only the 'live' lines")
m("D14", "demo_run.py", "notes.append((n, step.title, note))", "notes.append((1, steps[0].title, note))", DR, "the presenter's words are filed under step 1")
m("D15", "demo_run.py", "except (KeyboardInterrupt, EOFError):", "except KeyboardInterrupt:", DR, "EOF of stdin not handled as a finish")
m("D16", "demo_run.py", 'if r.stdout.strip() == "active":', 'if r.stdout.strip() == "activeX":', DR, "preflight ignores an active boot service")
m("D17", "demo_run.py", 'if not Path(env.get("INPUT", "/dev/video0")).exists():', 'if False:', DR, "preflight does not require a camera")
m("D18", "demo_run.py", "\nclass LogBook:", "\nSTEPS[4], STEPS[5] = STEPS[5], STEPS[4]\n\n\nclass LogBook:", DR, "panel and lens steps swapped (order of the sequence)")
m("D19", "demo_run.py", "Critical|Vibration|Controller|Camera|Everything|Earphones", "Critical|Vibration|Controller|Camera|Earphones", DR, "step 1's 'object announced' also matches 'Everything is connected'")
m("D20", "demo_run.py", 'Expect(r"^\\[CHECK\\] Camera: NOT CONNECTED", 10,', 'Expect(r"^\\[CHECK\\] Camera: NOT CONNECTED", 0.01,', DR, "camera NOT CONNECTED window shrunk to 10 ms")
m("D21", "demo_run.py", "(out / \"steps\").mkdir(parents=True)", "(out / \"steps\").mkdir(parents=True, exist_ok=True)", DR, "two runs in the same second share a folder")
m("D22", "demo_run.py", "os.killpg(app.pid, signal.SIGINT)\n            code", "app.send_signal(signal.SIGINT)\n            code", DR, "SIGINT to the leader only, not the process group")
m("D23", "demo_run.py", "stderr=subprocess.STDOUT", "stderr=subprocess.DEVNULL", DR, "stderr dropped instead of merged")
m("D24", "demo_run.py", "reader.join(5)\n        session.close()", "session.close()", DR, "session.log closed before the reader has drained")
m("D25", "demo_run.py", "if step.needs in missing:", "if False:", DR, "steps whose part is missing are not skipped")
m("D26", "demo_run.py", "answer.strip().lower() in (\"s\", \"q\")", "answer.strip().lower() in (\"s\",)", DR, "'q' no longer finishes")
m("D27", "demo_run.py", "problems.append(\"another copy of the program is running: \" + others[0][:120])", "pass", DR, "preflight ignores another running copy")

# ---- perf_sample.py
m("P01", "perf_sample.py", 'return "1" if req.get_values()[0] == gpiod.line.Value.ACTIVE else "0"', 'return "0" if req.get_values()[0] == gpiod.line.Value.ACTIVE else "1"', PT, "charger line inverted")
m("P02", "perf_sample.py", "config={6: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)}", "config={6: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT, output_value=gpiod.line.Value.INACTIVE)}", PT, "line requested with an output value (would drive it)")
m("P03", "perf_sample.py", "run goes on without it\n        return \"\"", "run goes on without it\n        return \"0\"", PT, "unreadable charger line reported as 'on battery'")
m("P04", "perf_sample.py", 'if row.get("ac_power") == "1":\n        return False', "if False:\n        return False", PT, "charger-in samples can stop the run")
m("P05", "perf_sample.py", 'row["battery_v"] <= volts', 'row["battery_v"] < volts', PT, "voltage boundary: exactly V no longer stops")
m("P06", "perf_sample.py", 'row["battery_pct"] <= threshold', 'row["battery_pct"] < threshold', PT, "percentage boundary: exactly PCT no longer stops")
m("P07", "perf_sample.py", "if low_streak >= 2:", "if low_streak >= 1:", PT, "one low reading ends the run")
m("P08", "perf_sample.py", "if battery_stop(row, args.stop_at_battery, args.stop_at_volts) else 0", "if battery_stop(row, args.stop_at_battery, args.stop_at_volts) else low_streak", PT, "low readings need not be consecutive")
m("P09", "perf_sample.py", "if battery_stop(row, args.stop_at_battery, args.stop_at_volts) else 0", "if battery_stop(row, args.stop_at_battery, args.stop_at_volts) else (low_streak if row['ac_power'] == '1' else 0)", PT, "a charger-in sample no longer resets the streak (design variant)")
m("P10", "perf_sample.py", "* 1.25 / 1000 / 16\n            charge", "* 1.25 / 1000 / 32\n            charge", PT, "wrong voltage scale")
m("P11", "perf_sample.py", "return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)", "return raw", PT, "register bytes not swapped")
m("P12", "perf_sample.py", 'on_battery = source.count("0")', 'on_battery = source.count("1")', PT, "on-battery count is the charger-in count")
m("P13", "perf_sample.py", "if on_battery == 0:", "if False:", PT, "'on the charger throughout' branch removed")
m("P14", "perf_sample.py", "if not source and p1 > p0 + 0.5:", "if p1 > p0 + 0.5:", PT, "ROSE message shown even when ac_power was recorded")
m("P15", "perf_sample.py", "args.stop_at_battery, args.stop_at_volts) else 0", "args.stop_at_battery, None) else 0", PT, "--stop-at-volts ignored by the sampler")
m("P17", "perf_sample.py", "THE CHARGER WAS IN for the rest: this is not a clean battery run", "", PT, "the partial-charger warning removed")
m("P18", "perf_sample.py", "consumer=\"sv-perf-sample\",", "consumer=\"sv-perf-sample\", extra=1,", PT, "(unknown kwarg: request fails -> '' )")

# ---- perf_run.py
m("R01", "perf_run.py", 'and _ac_power() == "1":', 'and _ac_power() == "9":', PT, "rundown not refused with the charger in")
m("R02", "perf_run.py", "if args.wait_for_battery and not rundown:", "if False:", PT, "--wait-for-battery without a rundown flag accepted")
m("R03", "perf_run.py", 'while read() != "0":', 'while read() == "1":', PT, "unreadable line counts as 'charger out'")
m("R04", "perf_run.py", "if clock() > deadline:\n            return False", "if False:\n            return False", PT, "the wait never gives up")
m("R05", "perf_run.py", "limit: float = 2 * 3600", "limit: float = 60", PT, "default wait limit is 60 s, not 2 h")
m("R06", "perf_run.py", '(["--stop-at-volts", str(args.stop_at_volts)] if args.stop_at_volts is not None else [])', "[]", PT, "--stop-at-volts not passed to the sampler")
m("R07", "perf_run.py", "if args.wait_for_battery and _ac_power() != \"0\":", "if args.wait_for_battery and _ac_power() == \"1\":", PT, "unreadable line does not wait (starts at once)")
m("R08", "perf_run.py", 'print("NOT STARTED: the charger was never unplugged within 2 hours")\n            return 1', 'print("NOT STARTED: the charger was never unplugged within 2 hours")\n            return 0', PT, "wait timeout exits 0")
m("R09", "perf_run.py", "sampler.wait(timeout=args.duration / 2)", "sampler.wait(timeout=args.duration * 2)", PT, "app is only stopped after the full duration (rundown ends later)")
m("R10", "perf_run.py", '(["--stop-at-battery", str(args.stop_at_battery)] if args.stop_at_battery is not None else [])', "[]", PT, "--stop-at-battery not passed to the sampler")

def main():
    out = Path(sys.argv[1]); ids = sys.argv[2:]
    todo = [x for x in M if (not ids or x[0] in ids)]
    rows = []
    for id, file, old, new, tests, desc in ([("CTRL", None, None, None, DR + " " + PT, "control: no change")] + todo if not ids or "CTRL" in ids else todo):
        d = WORK / id
        if d.exists(): shutil.rmtree(d)
        (d / "docs/testing/tools").mkdir(parents=True); (d / "tests").mkdir()
        for t in TOOLS: shutil.copy(REPO / "docs/testing/tools" / t, d / "docs/testing/tools" / t)
        for t in ("test_demo_run.py", "test_perf_tools.py", "conftest.py", "gst_helpers.py"): shutil.copy(REPO / "tests" / t, d / "tests" / t)
        os.symlink(REPO / "src", d / "src")
        os.symlink(REPO / "docs/testing/reports", d / "docs/testing/reports") if False else None
        status = "n/a"
        if file:
            p = d / "docs/testing/tools" / file
            text = p.read_text()
            if desc.startswith(("(duplicate", "x")) or text.count(old) < 1:
                rows.append((id, "SKIPPED-NOMATCH" if text.count(old) < 1 else "SKIPPED", desc)); continue
            p.write_text(text.replace(old, new, 1))
        t0 = time.time()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run(["nice", "-n", "19", "timeout", "420", PY, "-m", "pytest", *tests.split(), "-x", "-q", "-p", "no:cacheprovider", "--no-header"],
                           cwd=d, capture_output=True, text=True, env=env)
        tail = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR")) or " passed" in l or " failed" in l][:3]
        status = "SURVIVED" if r.returncode == 0 else ("TIMEOUT(caught)" if r.returncode == 124 else "CAUGHT")
        rows.append((id, status, desc, f"{time.time() - t0:.0f}s", " | ".join(tail)))
        print(rows[-1], flush=True)
        with open(out, "a") as fh:
            fh.write("\t".join(str(x) for x in rows[-1]) + "\n")
if __name__ == "__main__":
    main()
