# **Second Vision: IoT-Based Smart Glasses for the Visually Impaired**

Real-time object detection and depth estimation on edge hardware, delivering spatial awareness through audio and haptic feedback.

---

## What It Does

Second Vision is a head-mounted assistive device that helps visually impaired users navigate their surroundings:

- **🔊 Audio Feedback** — Announces detected objects with spatial position: *"person left"*, *"car center"*, *"bicycle right"*
- **📳 Haptic Feedback** — Three vibration motors (left, center, right) vibrate proportionally to obstacle proximity
- **⚠️ Hazard Detection** — Software-based detection of downward hazards (stairs, ledges) via depth map analysis

Object detection identifies *known* objects (people, cars, obstacles). Depth estimation detects *all* obstacles, including those the AI can't classify. 

---

## Hardware

| Component | Spec |
|---|---|
| Compute | Raspberry Pi 5 (8GB RAM) |
| AI Accelerator | Hailo AI Hat+ (Hailo-8, 26 TOPS) |
| Camera | OV2640 USB Camera Module |
| Motor Controller | ESP32 (wired USB serial) |
| Vibration Motors | 3× ERM (left temple, forehead, right temple) |
| Audio | Bone-conduction earphones |
| Control Panel | Arduino (switches, potentiometers, buttons) |
| Power (motors) | 18650 Li-ion cell via MOSFETs |
| OS | Raspberry Pi OS Trixie (64-bit) |

## Software Stack

| Layer | Technology |
|---|---|
| Detection Model | YOLOv8n (Hailo HEF) |
| Depth Model | SC-DepthV3 (Hailo HEF) |
| Inference Runtime | HailoRT + GStreamer |
| Framework | [`hailo_apps`](https://github.com/hailo-ai/hailo-apps) (pip-installed library) |
| TTS | pyttsx3 / espeak-ng |
| Serial Protocol | Binary (RPi5 ↔ ESP32), Text (Arduino → RPi5) |
| Language | Python 3.13 |

---

## Quick Start

### Prerequisites

- Raspberry Pi 5 with Hailo AI Hat+ installed
- `hailo_apps` package installed in virtual environment
- OV2640 camera connected via USB

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd second-vision-repo

# (FOR RPI ONLY) Make sure to have the necessary PyGOBject and GStreamer bindings (required)
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0

# Install necessary project dependencies
poetry install 

# Create and activate virtual environment
python3 -m venv --system-site-packages my_hailo_env
source my_hailo_env/bin/activate
```

### Run

```bash
# Mock mode (no hardware needed — for development)
./scripts/run.sh --mock

# With camera (display mode for testing)
./scripts/run.sh --input rawusb:///dev/video0 --width 640 --height 360 --frame-rate 25

# Full system (headless, with ESP32 and Arduino)
./scripts/run.sh --input rawusb:///dev/video0 --width 640 --height 360 --frame-rate 25 \
    --serial-port /dev/ttyUSB0 \
    --config-port /dev/ttyACM0 \
    --headless
```

---

## Architecture

```
Camera → Hailo-8 NPU ─┬─→ YOLOv8n (detection) → TTS → Speaker
                       └─→ SC-DepthV3 (depth)  → ESP32 → 3× Motors

Arduino Control Panel → RPi5 → Runtime configuration
```

The system runs a GStreamer pipeline with two parallel inference branches. Each branch has a callback that pushes results into a thread-safe queue. Daemon worker threads consume from the queues and produce outputs (audio, serial commands).

See [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md) for the full architecture document.

---

## Project Structure

```
second-vision-repo/
├── src/
│   └── second_vision/
│       ├── main.py                ← Entry point
│       ├── pipeline/              ← GStreamer pipeline + callbacks
│       ├── workers/               ← TTS, serial, config reader threads
│       ├── core/                  ← Config, protocol, depth utilities
│       └── mock/                  ← Fake data generators for --mock mode
├── scripts/
│   └── run.sh                     ← Launch script
├── config/                        ← Default settings
├── tests/                         ← Unit tests
├── documentation/                       ← Documentation
│   ├── PROJECT.md                 ← Project overview
│   ├── ARCHITECTURE.md            ← System architecture
│   ├── DECISIONS.md               ← Architectural decisions log
│   ├── PLAN.md                    ← Implementation phases
│   └── TASKS.md                   ← Task breakdown
├── CLAUDE.md                      ← AI agent instructions
└── README.md                      ← You are here

```

---

## Development

### Mock Mode

Run the full system without any hardware. Generates fake detections and depth data for testing workers:

```bash
./scripts/run.sh --mock
```

### Stub Pattern

Every component starts as a working stub. Replace the private functions marked `# STUBS BELOW` with real implementations. The worker loop, queue interface, and config integration never change.

See [`documentation/PLAN.md`](documentation/PLAN.md) for the phased implementation plan.

### Running Tests

```bash
source my_hailo_env/bin/activate
python3 -m pytest tests/
```

---

## Documentation

| Document | Description |
|---|---|
| [`AGENT.md`](AGENT.md) | Instructions for AI coding agents — read this first |
| [`PROJECT.md`](documentation/PROJECT.md) | Project overview, team, hardware specs |
| [`ARCHITECTURE.md`](documentation/ARCHITECTURE.md) | System architecture, data flow, protocols |
| [`DECISIONS.md`](documentation/DECISIONS.md) | All architectural decisions with rationale |
| [`PLAN.md`](documentation/PLAN.md) | 10-phase implementation plan |
| [`TASKS.md`](documentation/TASKS.md) | Granular task checklist |

---

## Researchers

- **Kenzhu Aguilera**
- **Waldric Jude S. Garcia**
- **Kenth Razen M. Magbanua**
- **Reiven O. Jasa**
