#include <Arduino.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <DFRobot_BMI160.h>

// --- CONFIGURATION & MODES ---
// Set to true for MAC / Standalone testing (cycles incremental test vibrations)
// Set to false for Deployment mode (listens for binary UART packets from the
// Raspberry Pi 5 on the dedicated PiSerial link, see below)
bool MAC_MODE = true;

// --- Pins for the Motors (Using XIAO ESP32-C3 mappings) ---
const int leftMotorPin = D0;
const int centerMotorPin = D1;
const int rightMotorPin = D2;

// --- UART link to the Raspberry Pi (XIAO ESP32-C3) ---
// D6 (TX) / D7 (RX) map to GPIO21 / GPIO20 on this board — confirmed wiring,
// not the default USB debug port. The ESP32-C3's UART peripherals are
// GPIO-matrix-routable, so binding UART1 to these two pins is a config
// choice, not a fixed-function requirement; it keeps the Pi link off `Serial`
// (native USB CDC) entirely, so debug prints never collide with command bytes
// the way they would if both shared one port.
#define PI_TX_PIN D6
#define PI_RX_PIN D7
HardwareSerial PiSerial(1);

// --- Wire protocol (matches src/second_vision/workers/serial_worker.py) ---
// Every packet starts with 0xAA. The byte after that selects the type, which
// fixes how many bytes follow it:
//
//   0x01  motor update   left, center, right         + 1 checksum byte
//   0x04  hazard alert   severity, pattern            + 1 checksum byte
//   0xFE  heartbeat      (no payload)                 + 1 checksum byte
//
// checksum = msgType XOR every payload byte (0 payload bytes for heartbeat,
// so its checksum is just 0xFE). The board answers every *valid* packet with
// 0xAA 0xFF <msgType> — that 2-byte prefix is what serial_worker.py scans for.
const uint8_t START_BYTE = 0xAA;
const uint8_t ACK_TYPE = 0xFF;
const uint8_t MSG_MOTOR_UPDATE = 0x01;
const uint8_t MSG_IMU_TELEMETRY = 0x02;  // ESP32 -> Pi, no ACK expected back
const uint8_t MSG_HAZARD_ALERT = 0x04;
const uint8_t MSG_HEARTBEAT = 0xFE;

const uint8_t MOTOR_UPDATE_PAYLOAD_LEN = 3;  // left, center, right
const uint8_t HAZARD_ALERT_PAYLOAD_LEN = 2;  // severity, pattern

// IMU telemetry states, sent as a single byte instead of the JSON string used
// on the USB debug path — cheap to add a fourth value later, but the Pi side
// must be updated in lockstep since these are positional, not named.
const uint8_t IMU_STATE_NORMAL = 0;
const uint8_t IMU_STATE_HEAD_MOVING = 1;
const uint8_t IMU_STATE_WRONG_PITCH = 2;

// A packet that stops arriving mid-way (cable pulled, Pi crashed while
// writing) must not wedge the reader forever waiting for bytes that are never
// coming. Anything slower than this between bytes of the SAME packet is
// treated as a dead packet, and the reader resyncs on the next 0xAA.
const unsigned long BYTE_TIMEOUT_MS = 50;

// The Pi treats >2.5 s of no ACK as the board being gone (ACK_TIMEOUT_SECONDS
// in serial_worker.py) and stops trusting the link. Symmetrically, the board
// must not keep the motors running on a command the Pi sent a long time ago —
// mirrors the "ESP32 zeroes the motors after 3 s without traffic" contract
// serial_worker.py's heartbeat comment documents.
const unsigned long WATCHDOG_TIMEOUT_MS = 3000;

// How long a hazard alert overrides the per-zone motor duty before the last
// known-good motor update is restored. Short and blunt on purpose — a ground
// hazard is meant to read as distinct from the continuous per-zone channel,
// not as a fourth intensity level on it.
const unsigned long HAZARD_PULSE_MS = 150;

// --- Reader state machine ---
enum ReadState {
  WAIT_START,
  WAIT_TYPE,
  READ_PAYLOAD,
  READ_CHECKSUM,
};

ReadState readState = WAIT_START;
uint8_t currentType = 0;
uint8_t packetPayload[MOTOR_UPDATE_PAYLOAD_LEN];
uint8_t payloadLen = 0;
uint8_t payloadIndex = 0;
unsigned long lastByteAt = 0;

// --- Motor state (shared with DEPLOYMENT mode's binary receiver) ---
uint8_t motorDuty[3] = {0, 0, 0};  // left, center, right — last valid update
unsigned long lastPacketAt = 0;
bool motorsZeroed = true;  // starts true: nothing commanded yet

bool hazardActive = false;
unsigned long hazardEndsAt = 0;

// --- SAFETY FEATURE: Hardware Governor ---
// Raised to 255 (100% full power) to maximize vibration intensity
const int MAX_PWM = 255;

DFRobot_BMI160 bmi160;
unsigned long lastSensorTime = 0;
const int sensorInterval = 200;

// --- INCREMENTAL PULSE VARIABLES (MAC MODE) ---
unsigned long lastFadeTime = 0;
const int fadeInterval = 50; // Increases PWM every 50ms
int currentPWM = 0;
const int fadeStep = 15;     // How much the power increases per step

bool imuHealthy = false;

void applyMotorDuty(uint8_t left, uint8_t center, uint8_t right) {
  analogWrite(leftMotorPin, left);
  analogWrite(centerMotorPin, center);
  analogWrite(rightMotorPin, right);
}

void sendAck(uint8_t msgType) {
  uint8_t ackPacket[] = {START_BYTE, ACK_TYPE, msgType};
  PiSerial.write(ackPacket, sizeof(ackPacket));
}

// Outbound ESP32 -> Pi. Big-endian (high byte first) for the two int16
// fields, matching typical wire-protocol convention; the Pi-side parser must
// agree on this byte order. No ACK is expected back for this message type —
// unlike the inbound motor/hazard/heartbeat packets, nothing on the ESP32
// currently needs to know the Pi received it.
void sendImuTelemetry(uint8_t state, int16_t rawPitch, int16_t rawYaw) {
  uint8_t pitchHi = (uint8_t)((rawPitch >> 8) & 0xFF);
  uint8_t pitchLo = (uint8_t)(rawPitch & 0xFF);
  uint8_t yawHi = (uint8_t)((rawYaw >> 8) & 0xFF);
  uint8_t yawLo = (uint8_t)(rawYaw & 0xFF);
  uint8_t checksum = MSG_IMU_TELEMETRY ^ state ^ pitchHi ^ pitchLo ^ yawHi ^ yawLo;

  uint8_t packet[] = {START_BYTE, MSG_IMU_TELEMETRY, state,
                       pitchHi, pitchLo, yawHi, yawLo, checksum};
  PiSerial.write(packet, sizeof(packet));
}

void resetReader() {
  readState = WAIT_START;
  payloadIndex = 0;
}

// Applies the effect of one fully-received, checksum-valid packet. Any valid
// packet — including a heartbeat — counts as link traffic and feeds the
// watchdog; only a motor update changes what the motors are doing.
void handlePacket(uint8_t msgType, const uint8_t *body, uint8_t bodyLen) {
  lastPacketAt = millis();

  if (msgType == MSG_MOTOR_UPDATE) {
    motorDuty[0] = body[0];
    motorDuty[1] = body[1];
    motorDuty[2] = body[2];
    motorsZeroed = false;
    if (!hazardActive) {
      applyMotorDuty(motorDuty[0], motorDuty[1], motorDuty[2]);
    }
    // BENCH-TEST DEBUG — REMOVE AFTER RPi->MOTOR PATH IS VERIFIED. This
    // packet is normally applied silently (see the file's top-level note on
    // pollPiSerial()/applyMotorDuty()); this print exists only so the
    // depth-estimation-to-motor-duty values arriving from the Pi are visible
    // on the USB debug console during bench testing, without needing to
    // trust the Pi-side log alone.
    Serial.printf("[BENCH MOTOR] left=%u center=%u right=%u\n",
                   motorDuty[0], motorDuty[1], motorDuty[2]);
  } else if (msgType == MSG_HAZARD_ALERT) {
    uint8_t severity = body[0];
    // `pattern` (body[1]) is reserved on the Pi side for future variants and
    // is always 1 today — every pattern value drives the same pulse for now,
    // per the "ignore unknown/future values, don't crash" rule the rest of
    // this project's protocols already follow.
    hazardActive = true;
    hazardEndsAt = millis() + HAZARD_PULSE_MS;
    applyMotorDuty(severity, severity, severity);
  }
  // MSG_HEARTBEAT: nothing to apply beyond the watchdog feed above.

  sendAck(msgType);
}

void pollPiSerial() {
  while (PiSerial.available() > 0) {
    uint8_t byteIn = PiSerial.read();
    unsigned long now = millis();

    // A byte that shows up after the in-progress packet has gone stale is
    // treated as the start of a fresh attempt rather than being stitched onto
    // a dead one.
    if (readState != WAIT_START && (now - lastByteAt) > BYTE_TIMEOUT_MS) {
      resetReader();
    }
    lastByteAt = now;

    switch (readState) {
      case WAIT_START:
        if (byteIn == START_BYTE) {
          readState = WAIT_TYPE;
        }
        // Anything else is noise (boot chatter, line noise) — discard and
        // keep scanning.
        break;

      case WAIT_TYPE:
        currentType = byteIn;
        if (currentType == MSG_MOTOR_UPDATE) {
          payloadLen = MOTOR_UPDATE_PAYLOAD_LEN;
        } else if (currentType == MSG_HAZARD_ALERT) {
          payloadLen = HAZARD_ALERT_PAYLOAD_LEN;
        } else if (currentType == MSG_HEARTBEAT) {
          payloadLen = 0;
        } else {
          // Unknown type: its length isn't known, so the packet can't be
          // framed at all. Drop just this byte and resync on the next 0xAA
          // rather than guessing a length and misreading everything after.
          resetReader();
          break;
        }
        payloadIndex = 0;
        readState = (payloadLen == 0) ? READ_CHECKSUM : READ_PAYLOAD;
        break;

      case READ_PAYLOAD:
        packetPayload[payloadIndex++] = byteIn;
        if (payloadIndex >= payloadLen) {
          readState = READ_CHECKSUM;
        }
        break;

      case READ_CHECKSUM: {
        uint8_t checksum = currentType;
        for (uint8_t i = 0; i < payloadLen; i++) {
          checksum ^= packetPayload[i];
        }
        if (byteIn == checksum) {
          handlePacket(currentType, packetPayload, payloadLen);
        }
        // A bad checksum is dropped silently: acting on a corrupted motor
        // command is worse than a skipped frame, and the next frame is only
        // ~33 ms behind it.
        resetReader();
        break;
      }
    }
  }
}

void applyWatchdog() {
  if (motorsZeroed) {
    return;
  }
  if (millis() - lastPacketAt > WATCHDOG_TIMEOUT_MS) {
    motorDuty[0] = motorDuty[1] = motorDuty[2] = 0;
    hazardActive = false;
    applyMotorDuty(0, 0, 0);
    motorsZeroed = true;
    Serial.println("[WATCHDOG] No traffic from Pi for 3s — motors zeroed.");
  }
}

void applyHazardExpiry() {
  if (hazardActive && millis() >= hazardEndsAt) {
    hazardActive = false;
    applyMotorDuty(motorDuty[0], motorDuty[1], motorDuty[2]);
  }
}

void setup() {
  Serial.begin(115200);

  // Dedicated UART to the Pi — separate from the USB `Serial` used for debug
  // prints and IMU telemetry above, so command bytes never share a port with
  // log output.
  PiSerial.begin(115200, SERIAL_8N1, PI_RX_PIN, PI_TX_PIN);

  pinMode(leftMotorPin, OUTPUT);
  pinMode(centerMotorPin, OUTPUT);
  pinMode(rightMotorPin, OUTPUT);
  analogWrite(leftMotorPin, 0);
  analogWrite(centerMotorPin, 0);
  analogWrite(rightMotorPin, 0);

  // Initialize I2C for IMU sensor (BMI160)
  Wire.begin(D4, D5);
  bmi160.softReset();
  
  if (bmi160.I2cInit(0x69) == 0) {
    imuHealthy = true;
    Serial.println("[SYSTEM] BMI160 IMU Initialized Successfully.");
  } else {
    imuHealthy = false;
    Serial.println("[ERROR] BMI160 IMU Initialization Failed! Check wiring.");
  }

  Serial.println(MAC_MODE ? "\n[SYSTEM] --- BOOTED IN MAC (INCREMENTAL FADE) MODE ---" : "\n[SYSTEM] --- BOOTED IN DEPLOYMENT (UART) MODE ---");
}

void loop() {
  unsigned long currentMillis = millis();

  // =================================================================
  // MODE 1: MAC MODE (Incremental Power: Low to High, then Loop)
  // =================================================================
  if (MAC_MODE) {
    if (currentMillis - lastFadeTime >= fadeInterval) {
      lastFadeTime = currentMillis;
      
      // Apply current power to all motors
      analogWrite(leftMotorPin, currentPWM);
      analogWrite(centerMotorPin, currentPWM);
      analogWrite(rightMotorPin, currentPWM);
      
      Serial.printf("[MAC MODE] Motors vibrating at PWM: %d\n", currentPWM);
      
      // Increment power
      currentPWM += fadeStep;
      
      // Loop back to low once it exceeds maximum power
      if (currentPWM > MAX_PWM) {
        currentPWM = 0; 
        Serial.println("[MAC MODE] --- Max power reached. Looping back to 0 ---");
      }
    }
  } 
  // =================================================================
  // MODE 2: DEPLOYMENT MODE (Connected to RPi5 via PiSerial, D6/D7)
  // =================================================================
  // Binary protocol matching src/second_vision/workers/serial_worker.py —
  // see the packet definitions and pollPiSerial() above. Motor duty arrives
  // pre-shaped (0-255) from the Pi's HapticMapper; this stage applies it
  // as-is and does not add its own curve or floor.
  else {
    pollPiSerial();
    applyHazardExpiry();
    applyWatchdog();
  }

  // =================================================================
  // SENSOR READING & WALKING TOLERANCE (IMU Telemetry)
  // =================================================================
  if (currentMillis - lastSensorTime >= sensorInterval) {
    lastSensorTime = currentMillis; 

    if (imuHealthy) {
      int16_t sensorData[6] = {0}; 
      if(bmi160.getAccelGyroData(sensorData) == 0) {
        int16_t gyroYaw = sensorData[2];    
        int16_t accelPitch = sensorData[4]; 

        // --- SELF-HEALING BROWNOUT RECOVERY ---
        if (gyroYaw == 0 && accelPitch == 0) {
          Serial.println("[WARNING] SENSOR BROWNOUT DETECTED. REBOOTING IMU...");
          imuHealthy = (bmi160.I2cInit(0x69) == 0); 
          return; 
        }

        String currentState = "normal";
        uint8_t stateByte = IMU_STATE_NORMAL;
        if (abs(gyroYaw) > 20000) {
          currentState = "head_moving";
          stateByte = IMU_STATE_HEAD_MOVING;
        } else if (abs(accelPitch) > 14000) {
          currentState = "wrong_pitch";
          stateByte = IMU_STATE_WRONG_PITCH;
        }

        // Binary packet to the Pi, over the real PiSerial link (D6/D7) — see
        // MSG_IMU_TELEMETRY above. Sent every tick regardless of MAC_MODE;
        // harmless if nothing is listening on the other end.
        sendImuTelemetry(stateByte, accelPitch, gyroYaw);

        // USB debug mirror — unchanged, still JSON, still on Serial (not the
        // Pi link).
        JsonDocument outDoc;
        outDoc["state"] = currentState;
        outDoc["raw_pitch"] = accelPitch;
        outDoc["raw_yaw"] = gyroYaw;

        Serial.print("[IMU TELEMETRY] ");
        serializeJson(outDoc, Serial);
        Serial.println();
      } else {
        imuHealthy = false;
        Serial.println("[ERROR] Lost connection to BMI160 IMU! Telemetry suspended.");
      }
    }
  }
}