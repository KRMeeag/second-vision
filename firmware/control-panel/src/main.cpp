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