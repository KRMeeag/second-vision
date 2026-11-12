#!/bin/bash
# Wait (up to WAIT_S, default 900 s) for the control panel to report both rockers ON (M:both), then run
# the 20-minute real-boards B3 run. Reads the panel's one-way status lines only; writes nothing to it.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../../.." && pwd)"
echo "# waiting for the panel to report M:both, from $(date -Iseconds)"
"$WT/my_hailo_env/bin/python" -I - "${WAIT_S:-900}" <<'EOF'
import serial, sys, time
end = time.monotonic() + float(sys.argv[1])
last = {}
with serial.Serial("/dev/ttyAMA3", 9600, timeout=0.5) as p:
    while time.monotonic() < end:
        l = p.readline().decode(errors="ignore").strip()
        key = l.split(":")[0] + (":" + l.split(":")[1] if l.startswith("S:") else "")
        if l.startswith(("M:", "S:tts_enabled", "S:vibration_enabled", "S:motor_strength")) and last.get(key) != l:
            print(time.strftime("%T"), l, flush=True)
            last[key] = l
        if l == "M:both":
            print(time.strftime("%T"), "panel reports both rockers ON", flush=True)
            sys.exit(0)
print(time.strftime("%T"), "gave up: no M:both in time", flush=True)
sys.exit(1)
EOF
if [ $? -eq 0 ]; then
  sleep 2
  bash "$HERE/run_b3.sh" realboards
else
  echo "# real-boards re-run NOT started"
fi
