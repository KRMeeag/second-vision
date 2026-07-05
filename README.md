# Second Vision: IoT-Based Smart Glass for the Visually Impaired

Real-time object detection and depth estimation pipeline deployed on edge hardware. This system provides spatial awareness to visually impaired users through synthesized audio queues and haptic feedback.

**Researchers:** Kenzhu Aguilera, Waldric Jude S. Garcia, Kenth Razen M. Magbanua, Reiven O. Jasa

## System Architecture

### Hardware Components
* **Compute:** Raspberry Pi 5 (8GB RAM recommended)
* **AI Acceleration:** Hailo AI Hat+ (26 TOPS)
* **Vision:** OV2640 USB Camera Module
* **Haptics Controller:** ESP32 (communicating via UART)
* **Audio Output:** Bone-conduction earphones (Bluetooth)

### Software Stack
* **OS:** Raspberry Pi OS (Trixie, 64-bit)
* **Models:** YOLOv8n (Object Detection), SC-DepthV3 (Depth Estimation)
* **Inference Runtime:** HailoRT
* **Audio Synthesis:** `pyttsx3`

## Repository Structure 

```
second-vision-repo/
├── my_hailo_env/                  ← Python venv (hailo_apps installed here)
├── src/
│   └── second_vision/
│       ├── __init__.py
│       ├── main.py                ← Entry point: python3 src/second_vision/main.py
│       ├── pipeline/
│       │   ├── app.py             ← SecondVisionApp (extends GStreamerParallelApp)
│       │   └── callbacks.py       ← on_det_frame, on_depth_frame
│       ├── workers/
│       │   ├── tts_worker.py      ← TTS consumer (espeak-ng + cooldown)
│       │   ├── serial_worker.py   ← ESP32 motor commands (binary protocol)
│       │   └── config_reader.py   ← Arduino settings reader
│       ├── core/
│       │   ├── config.py          ← SystemConfig (thread-safe shared state)
│       │   ├── protocol.py        ← Binary packet encode/decode
│       │   └── depth_utils.py     ← Zone splitting, proximity, hazard detection
│       └── mock/
│           └── data_generator.py  ← Fake data for --mock mode
├── scripts/
│   └── run.sh                     ← Activates venv + runs main.py
├── config/
│   └── defaults.yaml              ← Default thresholds, zones, baud rate
├── tests/
├── documentation/                 ← Project documentation 
└── pyproject.toml
```


