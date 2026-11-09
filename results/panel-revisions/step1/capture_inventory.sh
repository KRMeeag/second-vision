#!/bin/bash
# Step 1 inventory for the panel-revision runs: read-only commands, one section each.
# Writes nothing outside this folder. Run from anywhere:
#   bash results/panel-revisions/step1/capture_inventory.sh > results/panel-revisions/step1/inventory.txt 2>&1
# The only elevated commands are the two read-only vcgencmd queries (sudo -n).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../../.." && pwd)"                        # Kenth's worktree
BASE=/home/sv-rpi5/Projects/Second-Vision/second-vision-repo   # what the boot service runs
ZOO="$HERE/../downloads/model-zoo-v2.17.0/hailo8"
export XDG_RUNTIME_DIR=/run/user/1000

s() { echo; echo "### $*"; }
run() { s "$*"; "$@" 2>&1; }

echo "# Step 1 inventory, captured $(date '+%F %T %Z') on $(hostname)"

s "Device, OS, kernel"
tr -d '\000' < /proc/device-tree/model; echo
grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release; echo "debian_version $(cat /etc/debian_version)"
uname -a
run uptime -p
run free -m
run df -h /

s "HailoRT"
hailortcli --version 2>&1
timeout 20 hailortcli fw-control identify 2>&1 | tr -d '\000'
dpkg-query -W -f='${Package} ${Version}\n' hailort hailort-pcie-driver python3-hailort hailo-all hailo-tappas-core 2>&1
run systemctl is-active hailort.service
s "/etc/default/hailort_service (HAILO_MONITOR line)"; grep -E '^HAILO_MONITOR' /etc/default/hailort_service

s "Python and the project venv"
echo "system python3: $(python3 --version 2>&1)"
echo "my_hailo_env in worktree: $(readlink "$WT/my_hailo_env")"
echo "venv python: $("$BASE/my_hailo_env/bin/python" --version 2>&1) -> $(readlink -f "$BASE/my_hailo_env/bin/python")"
grep -E 'include-system-site-packages|version' "$BASE/my_hailo_env/pyvenv.cfg"
"$BASE/my_hailo_env/bin/python" -I -m pip show hailo-apps pytest 2>/dev/null | grep -E '^(Name|Version)'
"$BASE/my_hailo_env/bin/python" -I - <<'EOF' 2>&1
import importlib
for m in ["psutil", "cv2", "numpy", "smbus2", "gpiod", "serial", "pyttsx3", "yaml", "pytest"]:
    try:
        mod = importlib.import_module(m)
        print(f"venv import {m}: {getattr(mod, '__version__', 'ok')}")
    except Exception as e:
        print(f"venv import {m}: MISSING ({type(e).__name__})")
EOF
P2=~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python
echo "poetry env: $P2 -> $($P2 -I -c 'import pytest,cv2,numpy;print("pytest",pytest.__version__,"cv2",cv2.__version__,"numpy",numpy.__version__)' 2>&1)"

s "Boot service"
systemctl is-enabled second-vision.service; systemctl is-active second-vision.service
systemctl show second-vision.service -p FragmentPath -p ExecStart -p WorkingDirectory -p User -p Environment -p MainPID -p ActiveEnterTimestamp 2>&1
s "Checkout the service runs (base repo)"
echo "branch: $(git -C "$BASE" branch --show-current)  HEAD: $(git -C "$BASE" log -1 --format='%h %ad %s' --date=iso)"
echo "uncommitted/untracked in base (git status --short, full):"; git -C "$BASE" status --short; echo "(end)"
s "This worktree"
echo "branch: $(git -C "$WT" branch --show-current)  HEAD: $(git -C "$WT" log -1 --format='%h %ad %s' --date=iso)"
git -C "$WT" status --short; echo "(end)"
grep -n '^DET_MODEL_VERSION' "$BASE/src/second_vision/pipeline/app.py" "$WT/src/second_vision/pipeline/app.py"

s "HEF paths the running service loaded (journal of this boot)"
journalctl -u second-vision -b --no-pager 2>/dev/null | grep -E 'Found HEF in resources|Using HEF from path|Using labels JSON' | tail -3

s "Deployed model files, sha256"
sha256sum "$BASE/models/hef_v3/hef_deploy/best.hef" "$WT/models/hef_v3/hef_deploy/best.hef" \
          "$BASE/models/hef_v3/hef_eval/best.hef" "$BASE/models/hef_v2/hef_deploy/best.hef" \
          /usr/local/hailo/resources/models/hailo8/scdepthv3.hef
ls -l "$BASE/models/hef_v3/hef_deploy/best.hef" /usr/local/hailo/resources/models/hailo8/scdepthv3.hef
s "parse-hef: deployed detection HEF (hef_v3/hef_deploy)"; hailortcli parse-hef "$BASE/models/hef_v3/hef_deploy/best.hef" 2>&1
s "parse-hef: hef_v2/hef_deploy (the 14-class model of the 18 Oct runs), class lines only"
hailortcli parse-hef "$BASE/models/hef_v2/hef_deploy/best.hef" 2>&1 | grep -E 'Classes|number of classes'
s "parse-hef: scdepthv3.hef"; hailortcli parse-hef /usr/local/hailo/resources/models/hailo8/scdepthv3.hef 2>&1
s "labels.json beside the deployed HEF"; cat "$BASE/models/hef_v3/hef_deploy/labels.json"; echo
s "nms_config.json classes"; grep -E '"classes"' "$BASE/models/hef_v3/hef_deploy/nms_config.json"
s "The app's class map (callbacks.DETECTION_CLASS_MAP keys)"
sed -n '/^DETECTION_CLASS_MAP = {/,/^}/p' "$WT/src/second_vision/pipeline/callbacks.py"

s "Model Zoo v2.17.0 HEFs (downloaded), sha256"
( cd "$ZOO" && sha256sum *.hef && ls -l *.hef )
s "Model Zoo HEFs: S3 ETag vs local md5"
for m in yolov8n yolov8s yolov8m yolov8l yolov8x; do
  md5=$(md5sum "$ZOO/$m.hef" | cut -d' ' -f1); et=$(grep -i '^etag' "$ZOO/$m.headers.txt" | tr -d '\r"' | awk '{print $2}')
  echo "$m md5=$md5 etag=$et $([ "$md5" = "$et" ] && echo MATCH || echo DIFF)"
done
s "Model Zoo HEFs: parse-hef summary"
for m in yolov8n yolov8s yolov8m yolov8l yolov8x; do
  echo "$m: $(hailortcli parse-hef "$ZOO/$m.hef" 2>&1 | grep -E 'compiled for|Network group name|number of classes' | sed 's/^ *//' | tr '\n' ' ')"
done
s "Copies already installed by hailo-apps, sha256"
sha256sum /usr/local/hailo/resources/models/hailo8/yolov8{n,s,m}.hef 2>&1
s "hailo-apps' Model Zoo version for hailo8"
grep -n 'H8/H8L: Always' "$BASE/my_hailo_env/lib/python3.13/site-packages/hailo_apps/config/config.yaml"
grep -n '^VALID_H8_MODEL_ZOO_VERSION' "$BASE/my_hailo_env/lib/python3.13/site-packages/hailo_apps/python/core/common/defines.py"

s "Thermals and throttling (read-only)"
echo "thermal_zone0: $(cat /sys/class/thermal/thermal_zone0/temp)"
sudo -n vcgencmd measure_temp 2>&1; sudo -n vcgencmd get_throttled 2>&1
echo "fan rpm: $(cat /sys/class/hwmon/*/fan1_input 2>/dev/null)"
which grim vcgencmd

s "Audio: packages"
dpkg-query -W -f='${Package} ${Version} ${Status}\n' ffmpeg sox libsox3 libsox-fmt-base libsox-fmt-alsa alsa-utils espeak-ng pipewire pipewire-bin pipewire-pulse wireplumber 2>&1
dpkg-query -W -f='${Package} ${Status}\n' pipewire-alsa pulseaudio-utils 2>&1
espeak-ng --version
echo "espeak-ng voices matching fil|tl|tagalog|filipino:"; espeak-ng --voices | awk 'NR>1{print $2}' | grep -iE '^(fil|tl)$|tagalog|filipino' || echo "(none)"
sox --version 2>&1
ffmpeg -version 2>&1 | head -1
ffmpeg -hide_banner -filters 2>/dev/null | grep -E ' (loudnorm|silenceremove|aresample) '
echo "ffmpeg decoders: $(ffmpeg -hide_banner -decoders 2>/dev/null | grep -E ' (aac|mp3|opus|vorbis|amrnb|amrwb|flac|alac) ' | awk '{print $2}' | tr '\n' ' ')"
s "Audio: where sound goes"
cat /proc/asound/cards
echo "ALSA config files: $(ls /etc/asound.conf ~/.asoundrc 2>&1 | tr '\n' ' ')"
echo "ALSA devices (names only):"; aplay -L 2>&1 | grep -E '^(default|sysdefault|null|pipewire|pulse)'
echo "PipeWire sinks (* = default):"; wpctl status 2>&1 | sed -n '/^Audio/,/^Video/p' | sed -n '/Sinks:/,/Sources:/p' | grep -E 'vol:'
pw-metadata -n settings 2>&1 | grep -E "clock.rate|clock.allowed-rates"
s "Earphones (card 2) playback formats"; sed -n '/Playback:/,/Capture:/p' /proc/asound/card2/stream0 2>&1
echo "pw-play options: $(pw-play --help 2>&1 | grep -E -- '--volume|--target' | sed 's/^ *//' | tr '\n' ' ')"

s "Control panel: what the running device receives (journal of this boot, [CONFIG] keys only)"
journalctl -u second-vision -b --no-pager 2>/dev/null | grep -oE '\[CONFIG\] (Updated [a-z_]+|Mode|Button: [a-z]+|Connected to control panel on [^ ]+)' | sort | uniq -c
echo "B:status lines logged this boot: $(journalctl -u second-vision -b --no-pager 2>/dev/null | grep -c 'Button: status')"
