/*
 * STEP 3 — polarity check. Upload this BEFORE the real firmware.
 * Serial Monitor @ 9600. Actuate each control and watch which number flips.
 *
 * Expected at rest (both rockers OFF):  17=1  18=1  22=1  pot=0..4095
 * Flipping a rocker ON must take its number to 0 and LEAVE it there — these
 * are LATCHING switches, so a number that springs back means you have wired a
 * momentary part by mistake.
 * Turning the pot end to end must sweep the last number smoothly.
 *
 *   17 = DETECT rocker  -> object detection + TTS
 *   18 = DEPTH  rocker  -> depth estimation + vibration motors
 *   22 = status button  (momentary — 1 at rest, 0 only while held)
 */
#include <Arduino.h>

void setup() {
  Serial.begin(9600);
  pinMode(17, INPUT_PULLUP);   // detect rocker  — LATCHING
  pinMode(18, INPUT_PULLUP);   // depth rocker   — LATCHING
  pinMode(22, INPUT_PULLUP);   // status button  — momentary
  // GPIO34 is input-only with no internal pull-up — correct for a pot,
  // and it needs no pinMode() call at all.
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
                                    : "(none - both off, nothing announced)";

  Serial.printf("17=%d  18=%d  22=%d   pot=%4d   mode=%s\n",
    det, dep, digitalRead(22), analogRead(34), mode);
  delay(200);
}
