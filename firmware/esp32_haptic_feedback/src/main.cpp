#include <Arduino.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <DFRobot_BMI160.h>
#include <math.h>

// --- CONFIGURATION & MODES ---
// Set to true for MAC / Standalone testing (cycles incremental test vibrations)
// Set to false for Deployment mode (listens for binary UART packets from the
// Raspberry Pi 5 on the dedicated PiSerial link, see below)
bool MAC_MODE = false;

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
const uint8_t MSG_TURN_EVENT = 0x03;     // ESP32 -> Pi, no ACK — see sendTurnEvent()
const uint8_t MSG_HAZARD_ALERT = 0x04;
const uint8_t MSG_HEARTBEAT = 0xFE;

const uint8_t MOTOR_UPDATE_PAYLOAD_LEN = 3;  // left, center, right
const uint8_t HAZARD_ALERT_PAYLOAD_LEN = 2;  // severity, pattern

// 0x02's `state` byte. Only NORMAL is ever produced now — the BMI160 moved
// from the glasses to the sling bag, so the old head_moving/wrong_pitch
// classification (gyroYaw/accelPitch thresholds tuned for a head-mounted
// sensor) no longer means anything and has been removed. The constant stays
// because 0x02's wire format is frozen as-shipped and still needs a value
// here; the Pi-side IMU_STATE_NAMES mapping is unchanged and still contains
// the now-dead 1/2 entries for the same reason.
const uint8_t IMU_STATE_NORMAL = 0;

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

// --- Turn event: 0x03, ESP32 -> Pi, no ACK ---
// Wire format (deliberately leaner than 0x02 — this is a discrete event, not
// continuous telemetry, so it carries only what a fired event needs):
//
//   byte 0   0xAA start
//   byte 1   0x03 (MSG_TURN_EVENT)
//   byte 2-3 delta_deg, int16, big-endian, signed — accumulated rotation
//            since the last event, in degrees, sign = rotation direction
//   byte 4   checksum = 0x03 ^ delta_hi ^ delta_lo
//
// Signed-degrees was chosen over a separate "direction byte" (left/right)
// because the sign's real-world meaning depends on how the sensor ended up
// mounted — a signed value carries the same information without committing
// to a left/right label. Bench-confirmed across 7+ test rounds: right turns
// produce negative values, left turns positive, for this mounting.
void sendTurnEvent(int16_t deltaDeg) {
  uint8_t hi = (uint8_t)((deltaDeg >> 8) & 0xFF);
  uint8_t lo = (uint8_t)(deltaDeg & 0xFF);
  uint8_t checksum = MSG_TURN_EVENT ^ hi ^ lo;
  uint8_t packet[] = {START_BYTE, MSG_TURN_EVENT, hi, lo, checksum};
  PiSerial.write(packet, sizeof(packet));
}

// --- Turn detection: gyro-X (torso yaw) integrated to a threshold, then
// reported as one event and reset. Same accumulate-until-threshold-then-reset
// shape as HazardDebouncer (src/second_vision/core/depth_utils.py), adapted
// to this file's existing globals-and-functions style rather than a class,
// since nothing else here is object-oriented.
//
// UPDATED after a physical remounting: gyro-Z was the original assumed yaw
// axis, but axis-verification bench testing (all three raw axes logged
// during real body turns) showed gyro-Z capturing only a small fraction of
// real rotation. After remounting, gyro-X shows a large, sustained,
// consistent-sign signal during real turns (gyro-Y is noisy and crosses
// zero) — gyro-X is now the axis that measures body yaw rate for this
// mounting. As with the original Z assumption, this is a mounting-derived
// conclusion, not something verifiable from code, and should be reconfirmed
// if the sensor is remounted again.

// BMI160 default gyro range is +-2000 dps (set in the library's I2cInit,
// unchanged by this file) => 32768 / 2000 = 16.384 LSB per deg/s. Converting
// to real degrees before integrating means the thresholds below are tunable
// in physically meaningful units, not sensor-specific raw counts.
const float GYRO_LSB_PER_DPS = 16.384f;

// BMI160 default accel range is +-2g (also set in I2cInit, also unchanged)
// => 32768 / 2 = 16384 LSB per g. Used by the accel-corroboration check
// below, not by the gyro integration above.
const float ACCEL_LSB_PER_G = 16384.0f;

// Below this rate, treat the sensor as not turning — keeps stationary noise
// from slowly accumulating into a false event over many samples. Same
// "silence is the correct resting state" principle as haptics.py's DEADBAND.
// PLACEHOLDER — reasoned, not measured against the real sensor/mount. Not
// changed for the sample-rate fix below: this is a RATE threshold (deg/s),
// a physical property of the sensor reading itself, not something that
// should shift just because it is now checked more often.
const float TURN_RATE_DEADBAND_DPS = 8.0f;

// Accumulated rotation needed before a turn event fires.
// PLACEHOLDER — "roughly a quarter turn", not tuned against a real wearer.
// Not changed for the sample-rate fix below either: the fix restores
// integration ACCURACY (a real 90 deg turn should now accumulate close to
// 90 deg instead of ~10 deg), so 45 deg remains a sensible "about a quarter
// turn" trigger. Changing this number to compensate for the old undercount
// bug would double-count the fix.
const float TURN_EVENT_THRESHOLD_DEG = 45.0f;

// After firing, further accumulation is ignored for this long, so one
// continuous turn past the threshold doesn't fire repeated events.
// PLACEHOLDER — not tuned. Independent of sample rate (this gates TIME
// between fired events, not integration granularity).
const unsigned long TURN_EVENT_COOLDOWN_MS = 500;

// --- Peak-tracking + settle-detection (replaces the old signed-net-sum,
// fire-on-threshold-crossing design) ---
//
// WHY: extensive bench testing (marker-button-timestamped turns, full
// per-sample raw logging with hand-verified running-total arithmetic, a
// parallel no-deadband accumulator, and the live production accumulator
// exposed in a periodic diagnostic report — all temporary tooling, since
// removed once this investigation concluded) confirmed sampling rate, the
// dt-clamp, the summation arithmetic, the deadband, and the raw gyro sign
// convention were all working correctly — and STILL only ~13-17% of a real
// 90/180 degree body turn was ever reported. Root cause: a real turn shows
// a clean gyro peak, reliably followed by real counter-rotation as the
// body naturally settles right after completing the turn (ordinary human
// movement, not sensor error). Because the OLD total was a plain SIGNED
// running sum, that settle motion subtracted from the peak, cancelling out
// most of the real turn by the time the value was read.
//
// FIX: track the signed PEAK deviation reached (immune to what happens
// after it), and separately detect when the turn has SETTLED — rate has
// stayed within the deadband continuously for TURN_SETTLE_DURATION_MS.
// Report the tracked PEAK (not the running total) once settled, then reset
// both for the next turn. A MAX_TURN_DURATION_MS failsafe forces the same
// finalize even without a clean settle, so a turn that never quiets down
// (continuous slow rotation, spinning in place) cannot hold state open
// indefinitely.

// How long the rate must stay continuously within TURN_RATE_DEADBAND_DPS
// before a turn counts as complete. PLACEHOLDER — unmeasured. Reasoning:
// too short risks calling a brief mid-turn pause (a hitch between strides)
// "done" — but since the report uses the tracked PEAK rather than the
// running total, firing early mostly means reporting a peak that hasn't
// finished growing yet (undercounting), not contaminating it with reverse
// motion, which is a milder failure mode than the old design's. Too long
// delays the haptic feedback the wearer would feel. 300ms is chosen as
// comfortably longer than a single slow/dropped sample (~28ms at the
// ~35Hz actual fast-tick rate) while staying well under a natural pause
// between separate movements.
const unsigned long TURN_SETTLE_DURATION_MS = 300;

// No-settle failsafe: if motion never returns to the deadband continuously
// for TURN_SETTLE_DURATION_MS, force a finalize (report the peak so far, if
// significant) once this much time has elapsed since motion started.
// PLACEHOLDER — unmeasured. 4s is generous versus a typical real turn
// (well under 1.5s even for a slow 180), while still bounding the worst
// case (a wearer spinning continuously) to a few seconds of latency rather
// than holding state open forever.
const unsigned long MAX_TURN_DURATION_MS = 4000;

// --- Fast-rate sampling for the turn detector ---
// Deliberately INDEPENDENT of sensorInterval (200ms) above, which stays
// untouched — 0x02's cadence, wire format and every other behavior around
// it are frozen per this project's scope discipline. This is a second,
// separate millis()-based tick, with its own I2C read, used ONLY by the
// turn detector.
//
// Bench-measured on real hardware: at 200ms/5Hz, a real 90 deg turn only
// accumulated ~10 deg (~11%), and a real 180 deg turn only ~30 deg (~16%) —
// most of a body turn's rotation happens BETWEEN samples at that rate.
// 25ms (40Hz) gives roughly 20 samples across a half-second turn, which
// should capture the great majority of the motion.
//
// Why 25ms is affordable in loop(): this tick does one I2C read (a BMI160
// register burst read over I2C is sub-millisecond at typical bus speeds)
// plus a handful of float multiplies — no Serial printing and no PiSerial
// write unless an event actually fires, which bench data shows is rare.
// The EXISTING 200ms block's Serial.print/serializeJson calls (USB CDC,
// string formatting) are a heavier per-tick cost than this whole function,
// and that block already runs fine inside the same loop(). pollPiSerial()
// runs every loop() iteration regardless and only does work when bytes are
// actually waiting, so a lean 40Hz addition alongside it should not starve
// UART receiving or motor-command responsiveness — flagging this as
// reasoned, not bench-measured against actual loop() timing.
const unsigned long TURN_SAMPLE_INTERVAL_MS = 25;
unsigned long lastTurnSampleTickAt = 0;

// A tick whose dt comes out far larger than TURN_SAMPLE_INTERVAL_MS (e.g. a
// stalled loop() iteration from something else taking too long) would
// integrate an inflated rotation guess at the last-known rate over that
// whole gap. Any tick with dt beyond this is DISCARDED, not clamped: there
// is no way to know what actually happened during a gap this large, and
// integrating a guess is worse than skipping the sample. This is the
// previously-flagged, not-yet-implemented dt-clamp gap, now closed.
const float MAX_TURN_DT_SECONDS = 0.1f;  // 4x the intended tick interval

// --- Accel-based corroboration (false-positive guard) ---
// Bench-confirmed necessary, not just theoretical: a non-turn bounce/jostle
// test produced raw gyro-Z spikes (up to ~62 deg/s) LARGER than the spikes
// during real deliberate turns, and fired a real 48 deg turn event from
// jostling alone. Gyro-Z magnitude cannot by itself distinguish a real body
// turn from the bag swinging or bouncing on the strap.
//
// Heuristic: total accelerometer magnitude stays close to 1g (gravity only)
// during a calm turn, even mid-stride, but a swing or bounce imparts real
// linear acceleration that pushes the magnitude well away from 1g. Using
// magnitude across all three accel axes — rather than a specific
// "horizontal" axis pair — is deliberate: which axis ends up horizontal
// depends on the same unverified mounting orientation flagged above, and
// magnitude is orientation-agnostic, so it does not compound one
// unverified assumption on top of another.
//
// PLACEHOLDERS — reasoned from general wearable-IMU practice (steady state
// gravity-only is ~1g), NOT measured against this project's own bench data
// for what a genuine turn's vs. a jostle's accel signature actually looks
// like. Needs real tuning once more bench data exists.
const float ACCEL_JOSTLE_TOLERANCE_G = 0.3f;    // |magnitude - 1g| beyond this = "not gravity-only"
const float ACCEL_JOSTLE_FRACTION_LIMIT = 0.5f; // reject the event if more than this fraction of its samples looked jostle-like

float turnAccumulatedDeg = 0.0f;     // signed running total since the last reset
float turnPeakDeg = 0.0f;            // signed value of largest |turnAccumulatedDeg| reached since the last reset
unsigned long lastTurnSampleAt = 0;
bool turnSampleValid = false;        // false until the first call seeds the clock
unsigned long turnCooldownUntil = 0;
uint16_t turnSampleCount = 0;        // accumulating samples contributing to the current window
uint16_t turnJostleSampleCount = 0;  // ...of which looked jostle-like (accel corroboration)

bool turnHasMotion = false;          // has this window seen any non-deadband sample yet
unsigned long turnWindowStartAt = 0; // when motion started this window (for the failsafe cap)
bool turnInSettleWindow = false;     // currently inside a continuous within-deadband stretch
unsigned long turnSettleStartAt = 0; // when that stretch began

struct TurnEvent {
  bool fired;
  float deltaDeg;  // signed PEAK value, not the running total — see sign-meaning note on sendTurnEvent() above
};

// Shared by both finalize paths (settle-triggered and the no-settle
// failsafe): decides whether the tracked peak is worth reporting, resets
// all per-window state, and starts the post-fire cooldown either way.
TurnEvent finalizeTurn(unsigned long now) {
  TurnEvent result = {false, 0.0f};

  bool significant = fabsf(turnPeakDeg) >= TURN_EVENT_THRESHOLD_DEG;
  // Corroborated only if MOST of the samples that built this window's peak
  // looked gravity-consistent, not jostle-like — same rule as before, now
  // evaluated at finalize time instead of at the old threshold-crossing
  // moment.
  bool corroborated = turnSampleCount == 0 ||
      ((float)turnJostleSampleCount / (float)turnSampleCount) <= ACCEL_JOSTLE_FRACTION_LIMIT;

  if (significant && corroborated) {
    result.fired = true;
    result.deltaDeg = turnPeakDeg;
  }
  // Reset either way — a sub-threshold or rejected window is not useful
  // information to keep around, same reasoning the old design used.
  turnAccumulatedDeg = 0.0f;
  turnPeakDeg = 0.0f;
  turnSampleCount = 0;
  turnJostleSampleCount = 0;
  turnHasMotion = false;
  turnInSettleWindow = false;
  turnCooldownUntil = now + TURN_EVENT_COOLDOWN_MS;

  return result;
}

// Call once per FAST turn-sample tick (TURN_SAMPLE_INTERVAL_MS — NOT the
// slower 0x02 sensorInterval) with the raw gyro-X (see the axis note above)
// and all three raw accel axes from the SAME sensor read, so the
// corroboration check below is looking at acceleration from the same
// instant as the rotation it is judging. Does not send anything itself —
// the caller sends only when `fired` is true, since this is an event
// channel, not continuous telemetry like 0x02.
TurnEvent updateTurnDetector(int16_t gyroYawRaw, int16_t accelXRaw, int16_t accelYRaw,
                              int16_t accelZRaw, unsigned long now) {
  TurnEvent result = {false, 0.0f};

  if (!turnSampleValid) {
    // First call only seeds the clock — no elapsed time to integrate over
    // yet, so treating it as a real (zero-duration) sample would be wrong,
    // not just harmless.
    lastTurnSampleAt = now;
    turnSampleValid = true;
    return result;
  }

  float dtSeconds = (now - lastTurnSampleAt) / 1000.0f;
  lastTurnSampleAt = now;

  if (dtSeconds > MAX_TURN_DT_SECONDS) {
    // Stalled tick — discard rather than integrate a guess. The clock is
    // already reseeded above, so the NEXT call gets a normal small dt.
    return result;
  }

  if (now < turnCooldownUntil) {
    return result;  // cooling down after a just-finalized window
  }

  float rateDps = gyroYawRaw / GYRO_LSB_PER_DPS;
  bool withinDeadband = (rateDps > -TURN_RATE_DEADBAND_DPS && rateDps < TURN_RATE_DEADBAND_DPS);

  if (withinDeadband) {
    // Below the deadband — not turning, don't accumulate. This is also the
    // settle signal: reuse the same check rather than inventing a second
    // threshold for "settled".
    if (turnHasMotion) {
      if (!turnInSettleWindow) {
        turnInSettleWindow = true;
        turnSettleStartAt = now;
      } else if (now - turnSettleStartAt >= TURN_SETTLE_DURATION_MS) {
        result = finalizeTurn(now);
      }
    }
    // If no motion has happened yet this window, staying within the
    // deadband is just idle — nothing to settle out of.
    return result;
  }

  // Non-deadband sample: motion is happening, so any in-progress settle
  // countdown is cancelled.
  turnInSettleWindow = false;

  if (!turnHasMotion) {
    turnHasMotion = true;
    turnWindowStartAt = now;  // failsafe clock starts at first real motion, not at idle reset
  }

  turnAccumulatedDeg += rateDps * dtSeconds;
  turnSampleCount++;

  if (fabsf(turnAccumulatedDeg) > fabsf(turnPeakDeg)) {
    turnPeakDeg = turnAccumulatedDeg;
  }

  // Accel corroboration: this sample's total accel magnitude, in g. Close
  // to 1g means gravity-only (consistent with a calm turn); far from 1g
  // means real linear acceleration (consistent with jostling/bouncing).
  float accelMagnitudeG = sqrtf((float)accelXRaw * accelXRaw +
                                 (float)accelYRaw * accelYRaw +
                                 (float)accelZRaw * accelZRaw) / ACCEL_LSB_PER_G;
  if (fabsf(accelMagnitudeG - 1.0f) > ACCEL_JOSTLE_TOLERANCE_G) {
    turnJostleSampleCount++;
  }

  if (now - turnWindowStartAt >= MAX_TURN_DURATION_MS) {
    // No-settle failsafe: rate never returned to the deadband long enough
    // to count as settled, but this window has run long enough that it
    // must be finalized anyway rather than holding state open forever.
    result = finalizeTurn(now);
  }

  return result;
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
    // Motor duty is otherwise applied silently (see the file's top-level
    // note on pollPiSerial()/applyMotorDuty()) — this is the only place
    // commanded motor duty is visible on the USB debug console, so it stays
    // as a normal log line rather than being removed with today's bench
    // tooling.
    Serial.printf("[MOTOR] left=%u center=%u right=%u\n",
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
  // FAST TURN-DETECTOR SAMPLING (independent of the 200ms 0x02 tick below)
  // =================================================================
  // See TURN_SAMPLE_INTERVAL_MS above for why this needs to run faster than
  // sensorInterval — at 200ms the detector badly undercounted real turns
  // (bench-confirmed). This tick does its own I2C read and touches nothing
  // 0x02/brownout-related: imuHealthy is only ever SET by the slower block
  // below, this one only reads it, and a failed read here is simply
  // skipped rather than acted on — the slower block's own brownout check
  // remains the sole owner of reboot decisions.
  if (currentMillis - lastTurnSampleTickAt >= TURN_SAMPLE_INTERVAL_MS) {
    lastTurnSampleTickAt = currentMillis;

    if (imuHealthy) {
      int16_t turnSensorData[6] = {0};
      if (bmi160.getAccelGyroData(turnSensorData) == 0) {
        // gx = gyro-X, the yaw axis that now drives production turn
        // detection — see the axis note above updateTurnDetector().
        int16_t gx = turnSensorData[0];
        int16_t ax = turnSensorData[3];
        int16_t ay = turnSensorData[4];
        int16_t az = turnSensorData[5];

        // 0x03 turn event: torso yaw (gyro X — see the axis note above
        // updateTurnDetector()) peak-tracked since the last reset, reported
        // once the rate has settled back within the deadband for
        // TURN_SETTLE_DURATION_MS (or the no-settle failsafe forces it),
        // corroborated by accel magnitude, then reset. Sent ONLY when an
        // event fires — this is a discrete-event channel, not continuous
        // telemetry like 0x02.
        TurnEvent turn = updateTurnDetector(gx, ax, ay, az, currentMillis);
        if (turn.fired) {
          int16_t deltaDeg = (int16_t)lroundf(turn.deltaDeg);
          sendTurnEvent(deltaDeg);
          // Sign convention bench-confirmed across 7+ test rounds: right
          // turns produce negative values, left turns positive.
          Serial.printf("[TURN EVENT] %d deg (peak, settle-triggered)\n", deltaDeg);
        }
      }
    }
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
        // Read only to keep feeding 0x02's unchanged wire format below — the
        // sensor is torso-mounted now, so this is no longer meaningfully
        // "pitch" for anything; nothing in this file classifies it anymore.
        int16_t rawAccelY = sensorData[4];

        // --- SELF-HEALING BROWNOUT RECOVERY ---
        // Unrelated to the retired head-mounted classification — a true
        // sensor/I2C fault plausibly zeroes every axis, so this stays as a
        // generic "did the whole IMU return garbage" check.
        if (gyroYaw == 0 && rawAccelY == 0) {
          Serial.println("[WARNING] SENSOR BROWNOUT DETECTED. REBOOTING IMU...");
          imuHealthy = (bmi160.I2cInit(0x69) == 0);
          return;
        }

        // 0x02 telemetry: wire format, field meanings and cadence (every
        // tick, both modes) all unchanged. `state` is hardcoded to NORMAL —
        // see the IMU_STATE_NORMAL comment above for why.
        sendImuTelemetry(IMU_STATE_NORMAL, rawAccelY, gyroYaw);

        JsonDocument outDoc;
        outDoc["state"] = "normal";
        outDoc["raw_pitch"] = rawAccelY;
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