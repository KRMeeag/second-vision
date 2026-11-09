#!/bin/bash
# Step 1 code inventory: read-only greps and git reads, one section each.
#   bash results/panel-revisions/step1/capture_code_inventory.sh > results/panel-revisions/step1/code-inventory.txt 2>&1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../../.." && pwd)"
cd "$WT" || exit 1
S=src/second_vision
s() { echo; echo "### $*"; }

echo "# Step 1 code inventory, captured $(date '+%F %T %Z'); worktree $(git branch --show-current) @ $(git log -1 --format=%h)"

s "Speech worker: engine (workers/tts_worker.py)"
grep -n -E '_ESPEAK_SPEED =|_POLL_SECONDS =|_AMPLITUDE_(MAX|FLOOR) =|TURN_MUTE_SECONDS =|def (tts_worker|_speak_interruptible|_cuts|_pace|_text_for|_start_espeak|_speak_blocking)|"espeak-ng", "-s"|proc.terminate\(\)|import pyttsx3' $S/workers/tts_worker.py
s "Speech pacing and priority constants (core/priority.py)"
grep -n -E '^[A-Z_]+ *= ' $S/core/priority.py

s "Every place that queues speech"
echo "-- direct offers to the mailbox:"
grep -rn -E 'tts_queue\.offer\(' $S --include=*.py | grep -v -E 'TBR-|main2\.py'
echo "-- alerts raised through the AlertScheduler (spoken unless speak=False):"
grep -rn -E 'raise_alert\(' $S --include=*.py | grep -v -E 'TBR-|core/alerts\.py|connections\.py:2[0-9]:|main\.py:99:'
echo "-- detection phrases built in pipeline/callbacks.py:"
grep -n -E 'phrase = f"|payload\["phrase"\] = f"|f"\{label\} \{zone\}"|return f"\{item' $S/pipeline/callbacks.py $S/workers/tts_worker.py
echo "-- fixed sentences (*_TEXT constants):"
grep -rn -E '^[A-Z_]*_TEXT *= ' $S --include=*.py | grep -v TBR-
echo "-- reconnect sentence builder:"
grep -n -E 'def reconnect_text|EVERYTHING_CONNECTED_TEXT' $S/core/connections.py
echo "-- labels the speech uses for each model class (callbacks.DETECTION_CLASS_MAP):"
sed -n '/^DETECTION_CLASS_MAP = {/,/^}/p' $S/pipeline/callbacks.py
echo "-- sentences the brief lists that are NOT in the code (battery, startup, shutdown), all branches:"
for b in $(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes); do
  git grep -n -iE '"Battery low"|battery_low|Device shutting down|shutting down\.|Second Vision (is )?(ready|starting)' "$b" -- src 2>/dev/null
done | sort -u
echo "(end)"

s "Control panel: Pi side (workers/config_reader.py)"
grep -n -E 'prefix == "(S|M|B|V)"|parts\[1\] == "alive"|Button: |bool_keys|float_keys' $S/workers/config_reader.py
s "Control panel firmware in THIS checkout (firmware/control-panel/src/control_panel.cpp)"
grep -n -E 'statBtn|B:status|PIN_KNOB|PIN_VOLUME|HAVE_VOLUME_POT|tts_volume' firmware/control-panel/src/control_panel.cpp
s "Control panel firmware with the volume knob (commit 73eab9b, only on feat/depth-callback)"
git log -1 --format='%h %ad %an %s' --date=short 73eab9b
echo "branches containing 73eab9b: $(git branch -a --contains 73eab9b | tr -d ' *' | tr '\n' ' ')"
git show 73eab9b:firmware/control-panel/src/control_panel.cpp | grep -n -E 'STATUS button|GPIO22 is free|statBtn|B:status|PIN_VOLUME|S:tts_volume'
git show 73eab9b:firmware/control-panel/PROTOCOL.md | grep -n -E 'B:status|STATUS button was removed|never acted on'

s "Glasses link: ACK format (serial_worker.py, firmware)"
grep -n -E '^ACK_PREFIX|^ACK_TIMEOUT_SECONDS|0xAA 0xFF' $S/workers/serial_worker.py
grep -n -E 'ACK_TYPE =|sendAck\(uint8_t|answers every' firmware/esp32_haptic_feedback/src/main.cpp

s "18 Oct real-time run and battery run: tools (docs/testing/tools/)"
ls -l docs/testing/tools/perf_run.py docs/testing/tools/perf_sample.py docs/testing/tools/perf_plot.py docs/testing/tools/perf_npu.py
git log -1 --format='last commit touching perf tools: %h %ad %s' --date=short -- docs/testing/tools/perf_run.py docs/testing/tools/perf_sample.py docs/testing/tools/perf_plot.py
s "18 Oct real-time run and battery run: evidence (docs/testing/reports/T-15/)"
ls -l docs/testing/reports/T-15/ docs/testing/reports/T-15/evidence/
echo "-- tracked by git:"; git ls-files docs/testing/reports/T-15 | sed 's/^/  /'
echo "-- present but NOT tracked (raw app logs etc.):"; git ls-files --others --ignored --exclude-standard docs/testing/reports/T-15 | sed 's/^/  /'
s "18 Oct run: system file and summary"
cat docs/testing/reports/T-15/evidence/perf-both-20min-system.txt
cat docs/testing/reports/T-15/evidence/perf-both-20min.txt
s "Battery run: system file"
cat docs/testing/reports/T-15/evidence/perf-rundown-system.txt
s "Commit named in the brief (cb0b20e) and the commits around 18 Oct"
git log -1 --format='%h %ad %s' --date=iso cb0b20e
git log --format='%h %ad %s' --date=iso --since=2026-10-15 --until=2026-10-19 | cat

s "Object-detection unit cases in the tracker (uat-snapshot.yaml, generated from the workbook)"
sha256sum tests/Group2-SV_Test-Tracker.xlsx
grep -E '^  (file|sha256|extracted_at|case_count):' docs/testing/uat-snapshot.yaml
"$WT/my_hailo_env/bin/python" -I - <<'EOF'
import yaml
d = yaml.safe_load(open("docs/testing/uat-snapshot.yaml"))
print("columns of a case:", list(next(iter(d["cases"].values())).keys()))
for cid, c in d["cases"].items():
    if c["module"] != "M01":
        continue
    print(f"\n{cid} [{c['scenario']} {c['scenario_title']}] status={c['status']} date={c['date_tested']} cell={c['cell']}")
    print("  steps:", " / ".join(s.strip() for s in (c["steps"] or "").splitlines() if s.strip()))
    print("  input:", c["input"])
    print("  expected:", c["expected"])
EOF
s "Scripts the tracker's steps name, searched on every branch and on disk"
for b in $(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes); do
  git ls-tree -r --name-only "$b" | grep -E 'obj_det_unit|class_coverage|det_behavior|physical_env_robustness|setup_env\.sh' | sed "s|^|$b: |"
done
find /home/sv-rpi5 -xdev \( -name 'class_coverage*' -o -name 'det_behavior*' -o -name 'physical_env_robustness*' -o -name obj_det_unit \) 2>/dev/null
echo "(end of search)"
s "Detection-related unit tests in tests/"
grep -c -E '^def test_' tests/test_detection_classes.py tests/test_priority.py
s "Labelled test split on the Pi (candidate source of B1 images; not the tracker's images)"
ls -d ~/v3p_eval/test 2>&1; ls ~/v3p_eval/test 2>/dev/null | head; find ~/v3p_eval/test -type f \( -iname '*.jpg' -o -iname '*.png' \) 2>/dev/null | wc -l
