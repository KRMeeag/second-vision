#include <Arduino.h>

// Define UART pins for ESP32-C3 connecting to Raspberry Pi
#define RXD2 16
#define TXD2 17

// Create a HardwareSerial instance for UART 1 on the ESP32-C3
HardwareSerial PiSerial(1);

void setup() {
  // Serial0 for PC debugging (via USB)
  Serial.begin(115200);
  
  // Initialize UART1 for Raspberry Pi communication using custom pins 16 and 17
  PiSerial.begin(115200, SERIAL_8N1, RXD2, TXD2);
  
  Serial.println("ESP32-C3 Motor Controller Booted.");
  Serial.println("Waiting for UART packets from Raspberry Pi...");
}

void loop() {
  // Wait until we have at least 2 bytes available on PiSerial
  if (PiSerial.available() > 1) {
    uint8_t byteIn = PiSerial.read();
    
    // Look for the 0xAA start byte from the Python worker
    if (byteIn == 0xAA) {
      uint8_t msgType = PiSerial.read(); // Read the next byte
      
      if (msgType == 0xFE) {
        Serial.println("✅ Heartbeat received from Pi!");
      } else if (msgType == 0x01) {
        Serial.println("🚀 Motor command received from Pi!");
      } else {
        Serial.printf("📦 Other packet received: 0x%02X\n", msgType);
      }
      
      // Reply with the ACK prefix using PiSerial
      uint8_t ackPacket[] = {0xAA, 0xFF, msgType};
      PiSerial.write(ackPacket, 3);
      
      Serial.println("ACK sent back to Pi.");
    }
  }
}