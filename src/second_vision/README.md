
## Setup Instructions

### 1. Install Dependencies
Upon cloning this repo, you need to activate the virtual environment. This project uses Poetry for dependency management. To install all the required packages, run the following in the root directory:
```bash
poetry install
```

### 2. Activate the Environment
You can activate the Poetry virtual environment in a single line depending on your operating system:

**macOS / Linux:**
```bash
eval "$(poetry env activate)"
```

**Windows (PowerShell):**
```powershell
poetry env activate | iex
```

---

## Running the Application

Once your environment is activated, you can run the application directly from the terminal. 

### For Development on Any Machine (No Hardware Needed)
If you don't have the Raspberry Pi, Hailo AI Hat, or camera attached, you can run the system using fake data generators:

```bash
# Run in mock mode
python3 src/second_vision/main.py --mock
```

### For the Raspberry Pi (With Hardware)

*(Note: On the Raspberry Pi, make sure the Hailo environment variables are loaded, e.g., `source /usr/local/hailo/resources/.env`)*

**Development (with display & USB camera):**
```bash
python3 src/second_vision/main.py --input usb
```

**With ESP32 Connected (Haptic Feedback):**
```bash
python3 src/second_vision/main.py --input usb --serial-port /dev/ttyUSB0
```

**Full System (Production, headless with config panel):**
```bash
python3 src/second_vision/main.py --input usb --serial-port /dev/ttyUSB0 --config-port /dev/ttyACM0 --headless
```
