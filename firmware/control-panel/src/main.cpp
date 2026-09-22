/*
 * Second Vision — MOTOR CONTROLLER link check  (env: motorlink)
 *
 * ***  DIFFERENT BOARD FROM THE REST OF THIS FOLDER.  ***
 *
 * This targets the glasses / vibration-motor ESP32, not the control panel.
 * Everything else in src/ is panel firmware. They share this PlatformIO project
 * only because both are esp32dev with the same toolchain and upload quirks —
 * build_src_filter picks exactly one of them per env.
 *
 *   pio run -e motorlink -t upload -t monitor     THIS board
 *   pio run -e panel     -t upload -t monitor     the control panel
 *
 * NOT motor firmware. There is no PWM, no MOSFET driving, no flyback handling,
 * no protocol beyond the handshake below. It answers one question — is the wire
 * up and is the Pi's framing right — and is to the motor board what
 * polarity_test.cpp is to the panel.
 *
 * Counterpart on the Pi: workers/serial_worker.py (--serial-port), which sends
 * 0xAA and matches ACK_PREFIX against the reply. Binary, 115200, BIDIRECTIONAL.
 * That is a different link from the panel's, which is text, 9600, and one-way;
 * do not point --config-port at this board or --serial-port at the panel.
 *
 * Pin note: GPIO17 is UART2 TX here. On the PANEL firmware GPIO17 is the DETECT
 * rocker, a pulled-up input. Harmless while each firmware stays on its own
 * board, but flashing the wrong one turns that pin from an input into a driven
 * output — worth knowing if a bring-up session starts behaving strangely.
 */
#include <Arduino.h>

// Define UART2 pins for ESP32
#define RXD2 16
#define TXD2 17

void setup() {
  // Serial0 for PC debugging (via USB)
  Serial.begin(115200);
  
  // Serial2 for Raspberry Pi communication (via UART GPIO)
  Serial2.begin(115200, SERIAL_8N1, RXD2, TXD2);
  
  Serial.println("ESP32 Motor Controller Booted.");
  Serial.println("Waiting for UART connection from Raspberry Pi...");
}

void loop() {
  // Check if the Pi sent us anything
  if (Serial2.available() > 0) {
    uint8_t byteIn = Serial2.read();
    
    // Look for the 0xAA start byte from the Python worker
    if (byteIn == 0xAA) {
      Serial.println("Received valid start byte (0xAA) from Pi!");
      
      // Reply with the exact ACK prefix the Python script expects: 0xAA 0xFF <any_byte>
      uint8_t ackPacket[] = {0xAA, 0xFF, 0x01};
      Serial2.write(ackPacket, 3);
      
      Serial.println("ACK sent back to Pi.");
    }
  }
}