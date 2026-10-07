/*
 * Second Vision — physical control panel (ESP32-WROOM-32, 38-pin DevKit)
 *
 * Speaks PROTOCOL v1 (see PROTOCOL.md). One-way, ESP32 -> Pi, 9600 8N1,
 * lines terminated with '\n' only, 64 bytes max:
 *
 *   V:panel:<n>       protocol version, boot only
 *   S:<key>:<value>   setting
 *   M:<mode>          detection | depth | both | none
 *   B:<name>          event
 *
 * TWO LATCHING ROCKERS, one per capability. Each owns a model AND the output
 * channel that model feeds, because a model with no way to reach the user is
 * just heat:
 *
 *   DETECT rocker  ->  YOLOv8 object detection  +  speech   (tts_enabled)
 *   DEPTH  rocker  ->  SC-DepthV3 depth         +  motors   (vibration_enabled)
 *
 * and ONE KNOB per output channel, for how STRONG that channel is — never
 * whether it is on, which stays the rocker's job:
 *
 *   VOLUME   knob  ->  speech volume            (tts_volume,     GPIO33)
 *   STRENGTH knob  ->  motor tap intensity      (motor_strength, GPIO34)
 *
 * There is no STATUS button any more: the volume knob took its place on the
 * panel (2026-10), and GPIO22 is free.
 *
 * Wiring: docs/pot_wiring.svg (controls, as built on the protoboard) and
 * docs/pi_link_wiring.svg (link to the Pi, powered from the X1202 UPS).
 */
#include <Arduino.h>
#include <stdarg.h>

/*
 * NO_BROWNOUT: for running off a supply with no bulk capacitance behind it.
 *
 * The ESP32 pulls a current spike as it boots. On USB that is absorbed by the
 * host's capacitance; on a bare jumper from a header pin the rail dips, the
 * brownout detector fires, and the chip dies partway through setup() — UART
 * already initialised (TX idles high) but nothing ever transmitted.
 *
 * This disables that detector. Running slightly under-volt is survivable;
 * never finishing setup() is not.
 *
 * ONE change only. An earlier attempt also called setCpuFrequencyMhz(80) to
 * halve the current — do NOT do that: the UART divisor comes off the system
 * clock, so changing it around Serial.begin() corrupts every line at both ends.
 */
#ifndef NO_BROWNOUT
#  define NO_BROWNOUT 0
#endif
#if NO_BROWNOUT
#  include "soc/soc.h"
#  include "soc/rtc_cntl_reg.h"
#endif

const uint32_t BAUD = 9600;
const uint8_t  PROTOCOL_VERSION = 1;
const uint16_t DEBOUNCE_MS = 50;     // generous — the rockers' contacts need it
const size_t   MAX_LINE    = 64;     // protocol cap, enforced in emit()

/*
 * The link to the Pi is UART2 with TX remapped to GPIO23 — deliberately NOT
 * UART0. UART0 is the USB serial, and the ROM bootloader dumps its 115200 log
 * there on every reset; on a shared UART that garbage lands in the Pi's parser.
 * On its own UART the Pi never sees it, and USB stays free for flashing and
 * monitoring at the same time.
 *
 * RX is unused. The panel never reads — a reverse channel is a version bump.
 */
const int8_t PIN_LINK_TX = 23;
const int8_t PIN_LINK_RX = -1;
#define LINK Serial2

/*
 * Panel and Pi share the X1202, so they power up together and the Pi is ~30 s
 * from opening the port. A one-shot report at boot would be lost, leaving
 * SystemConfig disagreeing with switches the user can see and feel. So the
 * whole state is re-announced on this timer, forever: it survives the boot
 * race, a dropped byte, and any restart of main.py while the panel stays
 * powered — the common case in development.
 *
 * Safe ONLY because config_reader.py acts on CHANGES. An unconditional reader
 * would rebuild the Hailo pipeline on every one of these.
 */
const uint32_t HEARTBEAT_MS = 10000;

struct Input {
  uint8_t pin;
  bool activeLow;
  int stable;
  int lastRead;
  uint32_t changedAt;
};

/*
 * Pin choice is a safety property, not a preference. GPIO17 and GPIO18 both sit
 * with plain GPIOs either side of them (GPIO5/GPIO16 and GPIO19/GPIO5), so a
 * jumper one hole off reads wrong instead of shorting. GPIO23 is NOT safe for a
 * switch — it is one row from the GND pin, on a row that carries 3V3 on the
 * opposite side of the board — which is why it carries the single soldered link
 * wire and nothing else.
 */
//                  pin  activeLow
Input detect  = { 17,  true,  HIGH, HIGH, 0 };   // rocker — LATCHING — detection + TTS
Input depth   = { 18,  true,  HIGH, HIGH, 0 };   // rocker — LATCHING — depth + motors

/*
 * One flag per potentiometer, because an UNWIRED pot pin must not be read:
 *
 *   HAVE_POT=0         omits the STRENGTH pot — no reads, no S:motor_strength
 *   HAVE_VOLUME_POT=0  omits the VOLUME pot   — no reads, no S:tts_volume
 *
 * This is not a convenience flag. GPIO34 is input-only with NO internal pull-up
 * (correct for a pot, and on ADC1 so it survives WiFi being switched on; ADC2
 * would not). Unconnected, it FLOATS, and the 80-count deadband — sized for a
 * wired pot's dither — is exceeded constantly.
 *
 * Measured on a bare board: ~1,625 spurious S:motor_strength lines in 30 s,
 * about 54/s, at 22 bytes each on both transports. Against a 960 B/s budget at
 * 9600 baud that leaves emit() blocking almost continuously. Heartbeats and
 * rocker events still got through, but on the Pi that noise competes with the
 * pipeline for the link.
 *
 * GPIO33 floats the same way, even though it HAS internal pulls: analogRead()
 * re-runs pinMode(pin, ANALOG) on every call, and that switches both pulls off
 * (cores/esp32/esp32-hal-adc.c, Arduino-ESP32 2.0.17). So no software trick
 * keeps an unwired GPIO33 quiet — hence a second flag, not an auto-detect.
 *
 * A grounding jumper fixes it too, but a jumper is a thing to remember and a
 * build flag is not. Worse here: a jumper left on a pin after its pot is
 * fitted ties that wiper to a rail, and the knob's far end then shorts 3V3 to
 * GND through the pot.
 */
#ifndef HAVE_POT
#  define HAVE_POT 1
#endif
#ifndef HAVE_VOLUME_POT
#  define HAVE_VOLUME_POT 1
#endif

// Both pots are B10K LINEAR, outer legs on 3V3 and GND, wiper on an ADC1 pin.
// ADC1 because it keeps working if WiFi is ever switched on; ADC2 would not.
#if HAVE_POT
// Input-only pin, no internal pull-up — correct for a pot.
const uint8_t PIN_KNOB = 34;                     // STRENGTH wiper
#endif
#if HAVE_VOLUME_POT
/*
 * GPIO33, and the pin choice is a safety property, like the rockers'. It is
 * ADC1; it is not a boot-strapping pin, so the knob's position can never change
 * how the chip boots (on GPIO12, a knob left near full would stop the board
 * booting); and both its neighbours, GPIO32 and GPIO25, are unused. GPIO35 is the
 * nearer ADC1 pin but sits right beside the strength wiper: a solder bridge
 * between two wipers, with the knobs at opposite ends, shorts 3V3 to GND
 * through the pots. GPIO39 has the same problem; GPIO36 sits beside EN.
 *
 * GPIO33 can also be an OUTPUT. Never configure it as one: driven against a
 * wiper resting at an end stop, it would fight a rail through ~0 ohms.
 */
const uint8_t PIN_VOLUME = 33;                   // VOLUME wiper
#endif

#if HAVE_POT || HAVE_VOLUME_POT
// The deadband, in raw ADC counts of 4095 (~2% of travel). Sized for a wired
// pot's dither; see HAVE_POT above for what happens without one.
const int KNOB_DEADBAND = 80;

// True when the knob on `pin` has moved past the deadband since it was last
// reported, and `reported` now holds the new reading. Hysteresis matters: the
// ADC's last bits dither by a few counts even on a motionless knob, and at
// 9600 baud an unfiltered read would fill the link with meaningless updates
// and starve the switch events behind them.
bool knobMoved(uint8_t pin, int &reported) {
  int raw = analogRead(pin);                     // 0..4095
  if (abs(raw - reported) <= KNOB_DEADBAND) return false;
  reported = raw;
  return true;
}
#endif

// Hysteresis-filtered knob readings, shared by the pot handlers and the state
// burst. The burst must resend the SETTLED value, not a fresh analogRead():
// the ADC's last bits dither, and a raw read would flip the %.2f output between
// neighbouring values and look like a change on every heartbeat.
#if HAVE_POT
int lastRaw = -1000;
#endif
#if HAVE_VOLUME_POT
int lastVolumeRaw = -1000;
#endif

bool isOn(const Input &in) {
  return in.activeLow ? in.stable == LOW : in.stable == HIGH;
}

// True when the debounced level CHANGED. Both rockers act on BOTH directions —
// which is the entire reason to use latching switches for the two settings you
// most need to feel the position of without looking.
bool settled(Input &in) {
  int now = digitalRead(in.pin);
  if (now != in.lastRead) { in.lastRead = now; in.changedAt = millis(); }
  if (now != in.stable && millis() - in.changedAt > DEBOUNCE_MS) {
    in.stable = now;
    return true;
  }
  return false;
}

/*
 * Every protocol line goes out BOTH transports, identically and in the same
 * order: LINK for the Pi, USB for the bench and for testing on a laptop with no
 * Raspberry Pi attached. One binary, two listeners, no build flag.
 *
 * On the USB path the ROM bootloader's 115200 log still arrives as garbage at
 * 9600 — receivers must discard unparseable lines (protocol §2.6.1).
 */
void emit(const char *fmt, ...) {
  char buf[MAX_LINE + 1];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  LINK.print(buf);
  Serial.print(buf);
}

/*
 * Pipeline mode is a pure function of the two rockers — there is no independent
 * mode state to drift out of sync with them, which is why the old cycling mode
 * button had to go: two controls writing one setting means the panel can show a
 * position that is not what is running.
 *
 * Both off is "none": run nothing. Not "run both and mute" — a model with no
 * route to the user is wasted compute, and this device runs on a battery.
 */
const char* modeFor(bool det, bool dep) {
  if (det && dep) return "both";
  if (det)        return "detection";
  if (dep)        return "depth";
  return "none";
}

// M: is the COMMIT MARKER. It is always the last line of a state burst, so a
// receiver knows everything before it is current and can ignore partial bursts.
void emitMode() {
  emit("M:%s\n", modeFor(isOn(detect), isOn(depth)));
}

// Either rocker moved. Both flags are sent, not just the one that changed, so
// the burst is self-contained.
void emitRockers() {
  emit("S:tts_enabled:%d\n",       isOn(detect) ? 1 : 0);
  emit("S:vibration_enabled:%d\n", isOn(depth)  ? 1 : 0);
  emitMode();
}

// Everything the Pi needs to reconstruct the panel from scratch.
void emitFullState() {
  emit("S:tts_enabled:%d\n",       isOn(detect) ? 1 : 0);
  emit("S:vibration_enabled:%d\n", isOn(depth)  ? 1 : 0);
#if HAVE_POT
  emit("S:motor_strength:%.2f\n",  lastRaw / 4095.0f);
#endif
#if HAVE_VOLUME_POT
  emit("S:tts_volume:%.2f\n",      lastVolumeRaw / 4095.0f);
#endif
  emitMode();
}

void setup() {
#if NO_BROWNOUT
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
#endif

  Serial.begin(BAUD);                                        // USB
  LINK.begin(BAUD, SERIAL_8N1, PIN_LINK_RX, PIN_LINK_TX);    // the Pi

#if NO_BROWNOUT
  // Diagnostic, this build only: get one line out BEFORE the settle delay and
  // the pin reads. Seeing V:panel:1 and then nothing localises the death to
  // after this point; seeing nothing at all means it dies earlier still.
  // panel/bench keep the original order, where this goes out with the burst.
  emit("V:panel:%d\n", PROTOCOL_VERSION);
#endif

  pinMode(detect.pin,  INPUT_PULLUP);
  pinMode(depth.pin,   INPUT_PULLUP);

  // Just long enough for both UARTs to settle. We do NOT wait for the Pi here:
  // it shares our power supply and is ~30 s behind us, so no delay we could
  // reasonably sit through would help. The heartbeat is what closes that gap.
  delay(200);

  // Latching switches already have a position before we boot. Read both from
  // hardware and report them, never assume — or the physical rockers and
  // SystemConfig disagree until they are next flipped, which is the exact
  // failure a stateful control exists to prevent.
  detect.stable = detect.lastRead = digitalRead(detect.pin);
  depth.stable  = depth.lastRead  = digitalRead(depth.pin);
#if HAVE_POT
  lastRaw = analogRead(PIN_KNOB);
#endif
#if HAVE_VOLUME_POT
  lastVolumeRaw = analogRead(PIN_VOLUME);
#endif

#if !NO_BROWNOUT
  emit("V:panel:%d\n", PROTOCOL_VERSION);
#endif
  emitFullState();
}

void loop() {
  // A rocker moving changes both a capability and its output channel, so the
  // flags go out before the mode. The Pi gates speech and motors on those
  // flags: switching a capability OFF must silence its channel before the
  // pipeline rebuild, never after.
  // Both settled() calls must run every pass — they ARE the debounce state
  // machines, not just queries. `settled(a) || settled(b)` would short-circuit
  // and freeze b's timer on any pass where a changed.
  bool detMoved = settled(detect);
  bool depMoved = settled(depth);
  if (detMoved || depMoved)
    emitRockers();

  // Knobs -> motor_strength / tts_volume, through the deadband in knobMoved().
  // Standalone lines — the mode has not changed, so no M:.
#if HAVE_POT
  if (knobMoved(PIN_KNOB, lastRaw))
    emit("S:motor_strength:%.2f\n", lastRaw / 4095.0f);
#endif
#if HAVE_VOLUME_POT
  if (knobMoved(PIN_VOLUME, lastVolumeRaw))
    emit("S:tts_volume:%.2f\n", lastVolumeRaw / 4095.0f);
#endif

  // Liveness, then state. B:alive is what a receiver's watchdog looks for; the
  // burst after it is what lets a Pi that started late catch up. Cheap: six
  // short lines (93 bytes) per 10 s is ~9 B/s against 960 B/s of 9600-baud
  // budget.
  static uint32_t lastBeat = 0;
  if (millis() - lastBeat >= HEARTBEAT_MS) {
    lastBeat = millis();
    emit("B:alive\n");
    emitFullState();
  }

  delay(5);
}
