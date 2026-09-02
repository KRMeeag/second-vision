#include <Arduino.h>

// Define UART2 pins for ESP32 connecting to Raspberry Pi
#define RXD2 16
#define TXD2 17

void setup() {
  // Serial0 for PC debugging (via USB)
  Serial.begin(115200);
  
  // Serial2 for Raspberry Pi communication (via UART GPIO)
  Serial2.begin(115200, SERIAL_8N1, RXD2, TXD2);
  
  Serial.println("ESP32 Motor Controller Booted.");
  Serial.println("Waiting for UART packets from Raspberry Pi...");
}

void loop() {
  // Wait until we have at least 2 bytes (Start Byte + Message Type)
  if (Serial2.available() > 1) {
    uint8_t byteIn = Serial2.read();
    
    // Look for the 0xAA start byte from the Python worker
    if (byteIn == 0xAA) {
      uint8_t msgType = Serial2.read(); // Read the next byte
      
      // Print exactly what kind of packet the Pi sent us
      if (msgType == 0xFE) {
        Serial.println("✅ Heartbeat received from Pi!");
      } else if (msgType == 0x01) {
        Serial.println("🚀 Motor command received from Pi!");
      } else {
        Serial.printf("📦 Other packet received: 0x%02X\n", msgType);
      }
      
      // Reply with the ACK prefix the Python script expects: 0xAA 0xFF <msg_type>
      uint8_t ackPacket[] = {0xAA, 0xFF, msgType};
      Serial2.write(ackPacket, 3);
      
      Serial.println("ACK sent back to Pi.");
    }
  }
}