#!/usr/bin/env python3
"""Tester (T-17) harness: the program's REAL emitters on fake hardware, driven by trigger files.

Nothing here prints a [TTS]/[CHECK]/[ALERT]/[PATTERN]/[SERIAL]/[CONFIG]/[EARPHONES] line itself: they come from
the real tts_worker, alert_worker, status_worker (StatusReporter), AlertScheduler, PatternPlayer, serial_worker,
config_reader_worker, EarphonesWatcher, CameraViewWatch and SecondVisionApp's camera handlers.
Fake: the glasses board and the panel (pseudo-terminals), /proc/asound (a temp tree), the camera frames (numpy),
espeak-ng (a stub script that sleeps).   usage: real_app.py TRIGGERDIR [no-earphones]
"""
import os, sys, time, threading, signal, tty, select, tempfile
from pathlib import Path

REPO = Path("/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth")
sys.path.insert(0, str(REPO / "src"))
TRIG = Path(sys.argv[1])
OPTS = set(sys.argv[2:])

import numpy as np
from gi.repository import GLib
import second_vision.core.connections as conn
from second_vision.core.config import SystemConfig
from second_vision.main import SecondVisionUserData
from second_vision.workers.tts_worker import tts_worker
from second_vision.workers.alert_worker import alert_worker
from second_vision.workers.serial_worker import serial_worker
from second_vision.workers.earphones_worker import earphones_worker
from second_vision.workers.status_worker import status_worker
from second_vision.workers.config_reader import config_reader_worker
from second_vision.pipeline.app import SecondVisionApp, CAMERA_OK, CAMERA_LOST

# ---- fake /proc/asound: HDMI card (no usbid), camera mic (usbid, capture only), earphones (usbid + playback)
ASOUND = Path(tempfile.mkdtemp(prefix="asound-"))
for name in ("card0",):
    (ASOUND / name).mkdir()
    (ASOUND / name / "pcm0p").mkdir()
(ASOUND / "card3").mkdir(); (ASOUND / "card3" / "usbid").write_text("0c45:0261"); (ASOUND / "card3" / "pcm0c").mkdir()
conn.ASOUND_ROOT = ASOUND

def earphones(plugged: bool):
    card = ASOUND / "card2"
    if plugged and not card.exists():
        card.mkdir(); (card / "usbid").write_text("1234:5678"); (card / "pcm0p").mkdir()
    elif not plugged and card.exists():
        for p in sorted(card.rglob("*"), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        card.rmdir()

earphones("no-earphones" not in OPTS)

# ---- fake glasses board and panel on pseudo-terminals
ACK = bytes([0xAA, 0xFF, 0x01])
LEN = {0x01: 6, 0xFE: 3, 0x04: 5}
board_answers = threading.Event(); board_answers.set()
def open_pty():
    master, slave = os.openpty(); tty.setraw(slave)
    return master, slave, os.ttyname(slave)
b_master, b_slave, board_path = open_pty()
def board():
    buf = b""
    while True:
        if not select.select([b_master], [], [], 0.1)[0]:
            continue
        try:
            buf += os.read(b_master, 4096)
        except OSError:
            time.sleep(0.05); continue
        while buf:
            i = buf.find(b"\xAA")
            if i < 0 or len(buf[i:]) < 2:
                buf = buf[i:] if i >= 0 else b""; break
            buf = buf[i:]
            n = LEN.get(buf[1])
            if n is None:
                buf = buf[1:]; continue
            if len(buf) < n:
                break
            buf = buf[n:]
            if board_answers.is_set():
                try: os.write(b_master, ACK)
                except OSError: pass
threading.Thread(target=board, daemon=True).start()

p_master, p_slave, panel_path = open_pty()
panel_beats = threading.Event(); panel_beats.set()
def panel():
    while True:
        if panel_beats.is_set():
            try: os.write(p_master, b"B:alive\n")
            except OSError: pass
        for _ in range(100):                 # 10 s between heartbeats, like the real panel (HEARTBEAT_MS)
            time.sleep(0.1)
threading.Thread(target=panel, daemon=True).start()

# ---- the program's own shared state and workers
config = SystemConfig()
config.update(serial_port=board_path, config_port=panel_path)
ud = SecondVisionUserData(config)
threads = []
def start(fn, name, *args):
    t = threading.Thread(target=fn, args=args, daemon=True, name=name); t.start(); threads.append(t)
start(tts_worker, "tts", ud, config)
start(alert_worker, "alerts", ud, config)
start(serial_worker, "serial", ud, config)
start(earphones_worker, "earphones", ud, config)
start(status_worker, "status", ud, config)
start(config_reader_worker, "panel", ud, config, panel_path, None)
print("[MAIN] Status worker started", flush=True)

# ---- the camera: real SecondVisionApp handlers on a __new__ instance, a GLib loop for its timer
loop = GLib.MainLoop(); threading.Thread(target=loop.run, daemon=True).start()
app = object.__new__(SecondVisionApp)
app.user_data = ud; app._camera_view = ud.camera_view; app._camera_state = CAMERA_OK
app._shutting_down = False; app._camera_back_token = 0
camera_ok = threading.Event(); camera_ok.set()
view = {"kind": "ok"}
rng = np.random.RandomState(1)
def frame():
    k = view["kind"]
    if k == "dark":
        return np.full((480, 640, 3), 2, np.uint8)
    if k == "bright":
        return np.full((480, 640, 3), 250, np.uint8)
    return (rng.randint(70, 180, (480, 640, 1)) + np.zeros((480, 640, 3), int)).astype(np.uint8)
def frames():
    while True:
        if camera_ok.is_set():
            try: ud.camera_view.observe_frame(frame())
            except Exception as e: print(f"[HARNESS] frame error {e!r}", flush=True)
        time.sleep(1.0)
threading.Thread(target=frames, daemon=True).start()

stop = threading.Event()
signal.signal(signal.SIGINT, lambda *_: stop.set())
done = set()
def act(name):
    if name == "camera_off":
        camera_ok.clear(); app._camera_state = CAMERA_LOST; app._raise_camera_missing()
    elif name == "camera_on":
        camera_ok.set(); app._camera_state = CAMERA_OK; app._announce_camera_back()
    elif name == "glasses_off": board_answers.clear()
    elif name == "glasses_on": board_answers.set()
    elif name == "earphones_off": earphones(False)
    elif name == "earphones_on": earphones(True)
    elif name == "panel_off": panel_beats.clear()
    elif name == "panel_on": panel_beats.set()
    elif name.startswith("view_"): view["kind"] = name.split("_")[1]      # view_dark / view_bright / view_ok / view_ok2
    elif name == "detect":
        ud.tts_queue.offer({"label": "person", "zone": "left", "confidence": 0.9, "priority": 8.0, "tier": "normal"})
    elif name == "mode":
        ud.tts_queue.offer({"announce": "detection mode"})
while not stop.is_set():
    for f in sorted(TRIG.iterdir(), key=lambda p: p.stat().st_mtime_ns) if TRIG.exists() else []:
        if f.name not in done:
            done.add(f.name); act(f.name.split("-", 1)[1])
    time.sleep(0.05)
print("[MAIN] Shutting down...", flush=True)
ud.shutdown_event.set()
for t in threads: t.join(timeout=2.0)
print("[MAIN] Done.", flush=True)
