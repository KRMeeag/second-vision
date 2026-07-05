# ARCHITECTURE.md — System Architecture

> Consolidated from architecture analysis v1–v8.  
> This is the single source of truth for how the system is built.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     HEAD-MOUNTED UNIT                           │
│  [Camera]  [Motor L]  [Motor C]  [Motor R]  [Earpiece]         │
└──────┬────────┬──────────┬──────────┬───────────┬───────────────┘
       │        │          │          │           │
  USB  │   ┌────┴──────────┴──────────┴────┐     │ audio
       │   │          ESP32                 │     │
       │   │  3× MOSFET PWM + 18650 power  │     │
       │   └──────────────┬────────────────┘     │
       │             USB Serial                   │
       │                  │                       │
┌──────┴──────────────────┴───────────────────────┴───────────────┐
│                    RASPBERRY PI 5                                │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              GStreamer Pipeline (Hailo-8 NPU)             │   │
│  │                                                          │   │
│  │  Camera → tee ──→ SCDepthV3 ──→ depth_callback           │   │
│  │               └──→ YOLOv8n ──→ tracker ──→ det_callback  │   │
│  └──────────────────────────────────────────────────────────┘   │
│           │                              │                      │
│     serial_queue                   tts_queue                    │
│     (queue.Queue)                  (queue.Queue)                │
│           │                              │                      │
│  ┌────────▼────────┐          ┌──────────▼──────────┐          │
│  │  Serial Worker   │          │    TTS Worker        │          │
│  │  (daemon thread) │          │   (daemon thread)    │          │
│  │  binary protocol │          │   espeak-ng + cooldown│          │
│  └────────┬─────────┘          └──────────┬──────────┘          │
│           │                               │                     │
│      USB Serial                      espeak-ng                  │
│      to ESP32                        subprocess                 │
│                                                                 │
│  ┌──────────────────┐     ┌─────────────────┐                  │
│  │  Config Reader    │     │  SystemConfig    │                  │
│  │  (daemon thread)  │────→│  (thread-safe)   │                  │
│  │  Arduino serial   │     │  shared state    │                  │
│  └────────┬──────────┘     └────────┬────────┘                  │
│           │                         │ read by all workers       │
│      USB Serial                     │                           │
└───────────┼─────────────────────────┼───────────────────────────┘
            │                         
┌───────────┴──────────┐              
│   Arduino Control     │              
│   Panel (switches,    │              
│   pots, buttons)      │              
└──────────────────────┘              
```

---

## Threading Model

| # | Thread | Type | Input | Output | Lifetime |
|---|---|---|---|---|---|
| 1 | GLib MainLoop | Main thread | GStreamer bus events | Pipeline state mgmt | App lifetime |
| 2 | Depth streaming | GStreamer-managed | Camera frames | SCDepthV3 inference → depth_callback | While depth pipeline active |
| 3 | Det streaming | GStreamer-managed | Camera frames | YOLOv8 inference → det_callback | While det pipeline active |
| 4 | TTS Worker | `threading.Thread` daemon | `tts_queue` | espeak-ng audio | App lifetime |
| 5 | Serial Writer | `threading.Thread` daemon | `serial_queue` | Binary packets to ESP32 | App lifetime |
| 6 | Config Reader | `threading.Thread` daemon | Arduino serial text | `SystemConfig` updates | App lifetime |
| 7 | *(opt)* Watchdog | `threading.Thread` daemon | Frame count | Restart on stall | If `--enable-watchdog` |
| 8 | *(opt)* Debug Display | `threading.Thread` daemon | All queues | OpenCV composite window | If `--debug-display` |

### Shutdown sequence

1. `SIGINT` (Ctrl+C) or pipeline error
2. `user_data.shutdown_event.set()`
3. All workers check `shutdown_event` in their `queue.get(timeout=1.0)` loop
4. Workers exit within ~1 second
5. `main()` joins threads with `timeout=2.0`
6. Process exits

---

## Data Flow Contracts

### Detection → TTS

```
GStreamer det_callback → user_data.tts_queue.put_nowait({
    "label": "person",          # str: YOLO class label
    "zone": "left",             # str: "left" | "center" | "right"
    "confidence": 0.87,         # float: 0.0-1.0
})

# Mode announcement (special):
user_data.tts_queue.put_nowait({
    "announce": "detection mode"  # str: spoken as-is
})
```

### Depth → Serial

```
GStreamer depth_callback → user_data.serial_queue.put_nowait({
    "left": 200,                # int: 0-255 motor PWM intensity
    "center": 50,               # int: 0-255
    "right": 180,               # int: 0-255
    "hazard": False,            # bool: ground hazard detected?
    "hazard_severity": 0,       # int: 0-255
})
```

### Arduino → Config

```
Text protocol over serial (9600 baud):
  "S:motor_strength:0.75\n"    → config.update(motor_strength=0.75)
  "S:tts_enabled:0\n"          → config.update(tts_enabled=False)
  "M:detection\n"              → config.update(pipeline_mode="detection") + rebuild
  "B:announce_status\n"        → button event handler
```

---

## Binary Serial Protocol (RPi5 → ESP32)

### Packet format

```
| START (0xAA) | MSG_TYPE (1B) | PAYLOAD (N bytes) | CHECKSUM (1B) |
Checksum = XOR of all bytes from MSG_TYPE through end of PAYLOAD
```

### Message types

| Direction | Type | Code | Payload | Total bytes |
|---|---|---|---|---|
| RPi → ESP32 | MOTOR_UPDATE | `0x01` | `left(1B) center(1B) right(1B)` | 6 |
| RPi → ESP32 | HAZARD_ALERT | `0x04` | `severity(1B) pattern(1B)` | 5 |
| RPi → ESP32 | HEARTBEAT | `0xFE` | none | 3 |
| ESP32 → RPi | ACK | `0xFF` | `acked_type(1B)` | 4 |

### Wire config

```
Baud:     115200
Data:     8 bits
Stop:     1 bit
Parity:   None
Flow:     None
```

---

## Pipeline Modes

The system supports three pipeline configurations, switchable at runtime via the Arduino control panel:

| Mode | GStreamer Pipeline | Active Callbacks | Use Case |
|---|---|---|---|
| `"both"` | Source → tee → (SCDepthV3 + YOLOv8) | Both | Default — full system |
| `"detection"` | Source → YOLOv8 → tracker | det_callback only | Familiar routes, TTS only |
| `"depth"` | Source → SCDepthV3 | depth_callback only | Open areas, haptics only |

Switching is done via `GLib.idle_add(app._rebuild_pipeline)`. The ~0.8-1.2s blackout during rebuild is masked by a TTS announcement ("detection mode").

---

## Depth Processing

### Zone splitting (25/50/25)

```
Frame width:
  ├── 0-25% ──┤── 25-75% ──────────────┤── 75-100% ──┤
     LEFT           CENTER                   RIGHT
   (Motor L)       (Motor C)               (Motor R)
```

### Proximity → intensity mapping

```
Depth (meters)     Intensity     User perception
> 3.0              0 (off)       Clear path
2.0 - 3.0          50-100        Gentle hum
1.0 - 2.0          100-180       Moderate
0.5 - 1.0          180-230       Strong
< 0.5              255 (max)     Urgent — stop/turn
```

Uses an exponential/inverse curve — linear feels unnatural to users.

### Ground hazard detection

Analyzes the bottom 25% of the depth map for gradient spikes. A sudden depth increase (> 5x median gradient) indicates a potential drop-off (stairs, ledge).

```python
gradient = np.diff(row_depths)     # row-to-row depth change
ratio = max(gradient) / median(gradient)
hazard = ratio > HAZARD_THRESHOLD  # default: 5.0
```

---

## TTS Cooldown

### Cooldown key: `"{label}-{zone}"`

Each unique label-zone combination has an independent cooldown timer (default 3 seconds):

```
"person-center" → announced at T=0 → blocked until T=3
"person-left"   → announced at T=0 → blocked until T=3 (independent)
"car-center"    → announced at T=1 → blocked until T=4 (independent)
```

### Zone boundary hysteresis

To prevent jitter when objects sit at zone boundaries:

```
Enter left:    center_x < 0.30
Enter center:  center_x > 0.36 AND center_x < 0.64
Enter right:   center_x > 0.70
Hysteresis:    0.30-0.36 and 0.64-0.70 = keep previous zone
```

---

## ESP32 Safety

| Feature | Behavior |
|---|---|
| Watchdog | If no packet received for 3 seconds, set all motors to 0 |
| ACK | Send immediately upon parsing valid packet |
| Hazard pattern | Override normal motor values with pulsing pattern |
| Flyback diodes | 1N4148 across each motor (cathode to positive) |
| Power | 18650 → parallel motor wiring → individual MOSFETs per motor |

---

## File Map

```
src/second_vision/
├── main.py                 ← Orchestrator: creates config, starts workers, runs pipeline
├── pipeline/
│   ├── app.py              ← SecondVisionApp: extends GStreamerParallelApp
│   └── callbacks.py        ← on_det_frame, on_depth_frame: fast, non-blocking
├── workers/
│   ├── tts_worker.py       ← espeak-ng + CooldownManager
│   ├── serial_worker.py    ← binary protocol + ACK handling
│   └── config_reader.py    ← text protocol + pipeline rebuild trigger
├── core/
│   ├── config.py           ← SystemConfig: thread-safe shared state
│   ├── protocol.py         ← struct.pack/unpack for binary packets
│   └── depth_utils.py      ← zone splitting, proximity curves, hazard detection
└── mock/
    └── data_generator.py   ← fake detections + depth for --mock mode
```
