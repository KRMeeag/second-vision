# Second Vision: IoT-Based Smart Glass for the Visually Impaired

Real-time object detection and depth estimation pipeline deployed on edge hardware. This system provides spatial awareness to visually impaired users through synthesized audio queues and haptic feedback.

**Researchers:** Kenzhu Aguilera, Waldric Jude S. Garcia, Kenth Razen M. Magbanua, Reiven O. Jasa

## System Architecture

### Hardware Components
* **Compute:** Raspberry Pi 5 (8GB RAM recommended)
* **AI Acceleration:** Hailo AI Hat+ (26 TOPS)
* **Vision:** OV3660 USB Camera Module
* **Haptics Controller:** ESP32 (communicating via UART)
* **Audio Output:** Bone-conduction earphones (Bluetooth)

### Software Stack
* **OS:** Raspberry Pi OS (Bookworm, 64-bit)
* **Environment:** Docker & Docker Compose
* **Models:** YOLOv8n (Object Detection), SC-DepthV3 (Depth Estimation)
* **Inference Runtime:** HailoRT
* **Audio Synthesis:** `pyttsx3`

## Repository Structure (Proposed)

```text
second-vision/
├── docker-compose.yml        # Container orchestration and hardware passthrough
├── Dockerfile                # Core dependency stack definition
├── requirements.txt          # Python packages
├── config/
│   └── settings.yaml         # Centralized parameters (UART, thresholds, tuning)
├── models/                   # Compiled Hailo Executable Format (.hef) binaries
├── scripts/                  # Utilities for compilation and diagnostics
├── src/                      
│   ├── main.py               # Asynchronous execution entry point
│   ├── core/                 # Pipeline and Hailo API abstraction
│   └── hardware/             # Isolated interfaces (Camera, UART, Bluetooth TTS)
└── tests/                    # Validation scripts for hardware mocks


