/*
 * STEP 3 — polarity check. Upload this BEFORE the real firmware.
 * Serial Monitor @ 9600. Actuate each control and watch which number flips.
 *
 * Expected at rest (both rockers OFF):  17=1  18=1  str=0..4095  vol=0..4095
 * Flipping a rocker ON must take its number to 0 and LEAVE it there — these
 * are LATCHING switches, so a number that springs back means you have wired a
 * momentary part by mistake.
 * Turning each pot end to end must sweep ITS number smoothly, clockwise UP,
 * and leave the other pot's number where it was. A number that never settles
 * with the knob still means that wiper is not connected; one stuck at 4095 or
 * 0 means its GND or 3V3 leg is not.
 *
 *   17  = DETECT rocker    -> object detection + TTS
 *   18  = DEPTH  rocker    -> depth estimation + vibration motors
 *   str = STRENGTH pot     (GPIO34) -> motor tap intensity
 *   vol = VOLUME pot       (GPIO33) -> speech volume
 *
 * There is no STATUS button any more (GPIO22): the volume pot replaced it.
 */
#include <Arduino.h>

void setup() {
  Serial.begin(9600);
  pinMode(17, INPUT_PULLUP);   // detect rocker  — LATCHING
  pinMode(18, INPUT_PULLUP);   // depth rocker   — LATCHING
  // GPIO34 is input-only with no internal pull-up — correct for a pot, and it
  // needs no pinMode() at all. GPIO33 does have pulls, but analogRead() turns
  // them off on every read, so it needs none either.
}

void loop() {
  int det = digitalRead(17);
  int dep = digitalRead(18);

  // Spell out the mode the pair implies, so you can confirm the two rockers
  // combine correctly BEFORE trusting the real firmware to send it. Active LOW:
  // 0 means the switch is ON.
  const char *mode = (!det && !dep) ? "both"
                   : (!det)         ? "detection"
                   : (!dep)         ? "depth"
                                    : "none";

  Serial.printf("17=%d  18=%d   str=%4d  vol=%4d   mode=%s\n",
    det, dep, analogRead(34), analogRead(33), mode);
  delay(200);
}
