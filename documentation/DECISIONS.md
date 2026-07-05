# DECISIONS.md — Architectural Decision Log

> All decisions made during the architecture planning phase (v1–v8).  
> Each decision includes the rationale and alternatives considered.

---

## Decision Index

| # | Decision | Choice | Rationale Summary |
|---|---|---|---|
| 1 | Consumer threading model | `threading.Thread` + `queue.Queue` | I/O-bound consumers; GIL irrelevant for serial/TTS |
| 2 | TTS engine | pyttsx3 / espeak-ng | < 1% CPU, < 50ms latency; utterances too short for neural TTS quality to matter |
| 3 | Serial protocol (ESP32) | Binary: `0xAA` + type + payload + XOR | Deterministic parsing on MCU, no string allocation |
| 4 | Baud rate | 115,200 | < 1% wire capacity used; universally reliable |
| 5 | ACK strategy | Non-blocking, 10ms timeout | Stale data worse than missing data in obstacle avoidance |
| 6 | Display mode | `--headless` flag → `fakesink` | Callbacks still fire regardless of sink; saves CPU/power |
| 7 | Depth zone split | 25% / 50% / 25% (L/C/R) | Center zone wider — forward obstacles matter most |
| 8 | Motor controller | ESP32 via wired USB serial | Reliable, no BLE latency, supports high baud rates |
| 9 | Motor wiring | Parallel + individual MOSFETs | Series wiring prevents independent motor control |
| 10 | Cooldown strategy | Per `"{label}-{zone}"` key, 3s default | Prevents redundant spam; per-label-zone, not per-instance |
| 11 | TTS scope | Detection only — no depth in speech | Prevents info overload; motors handle depth |
| 12 | Motor mode | Proportional (all motors active) | Single-motor mode has dangerous corridor failure mode |
| 13 | TTS phrasing | Short: `"person left"` | 0.6s per utterance; leaves silence between cooldowns |
| 14 | ESP32 firmware | Teammate-owned; protocol spec provided | Independent development against the binary protocol spec |
| 15 | Form factor | Head-mounted | Camera, motors, earpiece all on headband |
| 16 | Target user | Visually impaired | White cane as fallback for ground-level hazards |
| 17 | Downward hazards | Software ground-plane departure detection | Depth gradient spike analysis; catches stairs/ledges |
| 18 | Control panel protocol | Text: `"S:key:value\n"` | Human-speed input; debuggable with serial monitor |
| 19 | Config sharing | `SystemConfig` with `threading.Lock` | Workers poll on each loop iteration (< 1s latency) |
| 20 | Pipeline switching | Dynamic rebuild via `_rebuild_pipeline()` | Saves thermal/power on battery-powered head-mounted device |
| 21 | Rebuild blackout | ~0.8-1.2s, masked by TTS announcement | ESP32 watchdog holds last motor values during gap |
| 22 | Thread count | 6 core + 2 optional, all daemon | Single `shutdown_event` for clean exit |
| 23 | Project structure | `src/second_vision/` imports `hailo_apps` as library | Never modify library; extend by subclassing |
| 24 | Entry point | `main.py` starts workers, then `app.run()` (blocking) | Workers must start BEFORE the blocking GLib loop |
| 25 | Dev approach | Walking skeleton with stubs | Full system runs end-to-end from day 1 |
| 26 | Mock mode | `--mock` flag with fake data generators | Enables development without RPi5/Hailo/camera |
| 27 | Stub pattern | Private `_functions` under `# STUBS BELOW` | Replace internals, never change the interface |
| 28 | Interface contract | Queue dict keys are the API | Team-agreed, change requires coordination |
| 29 | Directly implemented | `SystemConfig`, `protocol.py`, mock generators | Simple enough to implement fully from start |

---

## Detailed Rationale

### D1: Threads over Processes

**Alternatives considered**: `multiprocessing.Process`, `concurrent.futures.ThreadPoolExecutor`, `asyncio`

**Why threads win**:
- Consumers (TTS, serial) are **I/O-bound** — the GIL is released during espeak-ng subprocess calls and `serial.write()` system calls
- GStreamer callbacks already run in GLib-managed threads — adding worker threads fits naturally
- `multiprocessing` adds serialization overhead, sentinel patterns, potential deadlocks on `multiprocessing.Queue`
- `ThreadPoolExecutor` is designed for discrete tasks with `Future` results, not long-running consumer loops
- `asyncio` conflicts with GLib's own main loop

### D2: pyttsx3/espeak-ng over Piper TTS

**Alternatives considered**: Piper TTS (neural, ONNX-based), Google TTS (cloud)

**Why espeak wins**:
- Utterances are 3-5 words ("person left") — neural quality is imperceptible at this length
- espeak-ng: < 1% CPU spike, < 50ms latency
- Piper: 15-30% CPU spike, 200-500ms latency, 50-150MB model in RAM
- The device is already running two CV models on the Hailo NPU — CPU budget is tight
- Piper's `TextToSpeechProcessor` in the hailo_apps repo is designed for GenAI voice assistants (Hailo-10H), not pipeline detection announcements

### D9: Parallel Motor Wiring (Critical)

**The series mistake**: If motors are wired in series, opening any transistor breaks the entire circuit — all motors stop. You physically cannot control them independently. PWM on one motor affects current through all three.

**The fix**: Each motor has its own N-channel MOSFET. All motors share the 18650 power rail in parallel. Each MOSFET gate is driven by a separate ESP32 PWM pin. Flyback diodes (1N4148) across each motor protect against back-EMF spikes.

### D12: Proportional Motor Mode over Single-Motor

**The corridor problem**: If only the highest-proximity motor vibrates, the user walking between two walls feels vibration on one side only. They drift toward the "silent" side and hit the other wall.

**Proportional mode**: All three motors vibrate based on their zone's proximity. The user's brain naturally performs relative comparison — "both sides are buzzing, center is quiet" means "stay centered."

### D17: Software Downward Hazard Detection

**The limitation**: Monocular depth estimation (SC-DepthV3) reads stairs going down as "far" — the floor drops away, and depth increases. This looks like open space, not danger.

**The workaround**: Ground plane departure detection. The bottom 20-30% of the depth map shows ground 1-3m ahead. On flat ground, the depth gradient is smooth. A staircase creates a sudden gradient spike (depth jumps from "close ground" to "far nothing"). Detecting this spike ratio (> 5x median gradient) flags potential drop-offs.

**Limitations**: Works for staircases (3+ steps) and ledges. Small curbs and potholes may be below the noise floor. Not a replacement for the white cane.

### D20: Dynamic Pipeline Rebuild over "Ignore Output"

**Alternative**: Keep both pipelines always running, just `if not enabled: return` in callbacks.

**Why rebuild wins for this device**:
- Head-mounted = user wears the heat source — thermal matters
- Battery-powered = power draw matters
- Mode switches are infrequent (physical toggle, not per-frame)
- ~1s blackout during rebuild is acceptable and masked by TTS announcement
- The framework's `_rebuild_pipeline()` already handles the full teardown/rebuild cycle
