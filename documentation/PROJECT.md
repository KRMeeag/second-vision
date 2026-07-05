# PROJECT.md — Second Vision Overview

## Project Title

**Second Vision: IoT-Based Smart Glass for the Visually Impaired**

## Researchers

- Kenzhu Aguilera
- Waldric Jude S. Garcia
- Kenth Razen M. Magbanua
- Reiven O. Jasa

---

## What It Does

Second Vision is a head-mounted assistive device that gives visually impaired users real-time spatial awareness through two complementary feedback channels:

1. **Audio (TTS)**: Announces *known* objects detected by AI — "person left", "car center", "bicycle right"
2. **Haptic (vibration motors)**: Provides proportional vibration for *all* obstacles (including those AI can't classify) — closer obstacle = stronger vibration

Users continue using their white cane for ground-level hazards (curbs, stairs). Second Vision covers what the cane can't — obstacles at body/head height and approaching objects.

---

## Hardware

### Compute Unit
| Component | Spec |
|---|---|
| SBC | Raspberry Pi 5 (8GB RAM) |
| AI Accelerator | Hailo AI Hat+ (Hailo-8, 26 TOPS) |
| Camera | OV2640 USB Camera Module |
| OS | Raspberry Pi OS Trixie (64-bit) |

### Head Unit
| Component | Spec |
|---|---|
| Camera mount | Forehead (center, angled ~10-15° down) |
| Motor L | Vibration ERM — left temple |
| Motor C | Vibration ERM — forehead |
| Motor R | Vibration ERM — right temple |
| Audio | Bone-conduction earphones |

### Motor Controller
| Component | Spec |
|---|---|
| MCU | ESP32 (wired USB serial to RPi5) |
| Motor drivers | 3× N-channel MOSFETs (one per motor) |
| Flyback protection | 1N4148 diode across each motor |
| Power | 18650 Li-ion cell (3.7V, parallel motor wiring) |

### Control Panel
| Component | Spec |
|---|---|
| MCU | Arduino (wired USB serial to RPi5) |
| Inputs | Toggle switches (mode, TTS on/off), potentiometers (motor strength, cooldown), momentary button (status) |

---

## Software Stack

| Layer | Technology |
|---|---|
| Inference Runtime | HailoRT + GStreamer pipeline |
| Detection Model | YOLOv8n (compiled to HEF for Hailo-8) |
| Depth Model | SC-DepthV3 (compiled to HEF for Hailo-8) |
| Framework | `hailo_apps` (pip-installed library) |
| Application | `second_vision` Python package |
| TTS Engine | pyttsx3 / espeak-ng |
| Serial Protocol | Binary (RPi5 → ESP32), Text (Arduino → RPi5) |
| Language | Python 3.13 |

---

## System Data Flow

```
Camera → GStreamer Pipeline → Hailo-8 NPU
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
              YOLOv8n                   SC-DepthV3
           (detection)               (depth estimation)
                    │                         │
                    ▼                         ▼
            Detection Callback         Depth Callback
            (label + zone)          (zone proximity 0-255)
                    │                         │
              ┌─────▼─────┐            ┌──────▼──────┐
              │ TTS Worker │            │Serial Worker│
              │ (espeak-ng)│            │(binary pkts)│
              └─────┬──────┘            └──────┬──────┘
                    │                          │
                    ▼                     USB Serial
                Speaker                       │
               (audio)                   ┌────▼────┐
                                         │  ESP32   │
                                         │ 3× PWM  │
                                         └──┬─┬─┬──┘
                                            │ │ │
                                         L  C  R  Motors
```

---

## Key Design Principles

1. **Callbacks are fast** — GStreamer callbacks extract data and `put_nowait()` into queues. No blocking.
2. **Workers are independent** — Each daemon thread consumes from one queue, processes at its own pace.
3. **Config is shared** — Thread-safe `SystemConfig` object, updated by Arduino control panel, read by all workers.
4. **Stubs first** — Every component starts as a working stub. Replace internals incrementally.
5. **Two feedback modalities** — Detection → Audio (TTS), Depth → Haptic (motors). No cross-referencing.

---

## Development Modes

| Mode | Command | What it does |
|---|---|---|
| Mock | `--mock` | No hardware needed. Fake detections + depth data. For development on any machine. |
| Display | `--input usb` | Real pipeline with display windows. For visual verification. |
| Headless | `--input usb --headless` | Real pipeline, no display. For production / battery testing. |
| Full | `--input usb --serial-port /dev/ttyUSB0 --config-port /dev/ttyACM0 --headless` | Complete system. |
