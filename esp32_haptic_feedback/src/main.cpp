#include <Arduino.h>
#include <ArduinoJson.h> 

const int leftMotorPin = D0;   
const int centerMotorPin = D1; 
const int rightMotorPin = D2;  

void setup() {
  Serial.begin(115200);
  
  pinMode(leftMotorPin, OUTPUT);
  pinMode(centerMotorPin, OUTPUT);
  pinMode(rightMotorPin, OUTPUT);

  analogWrite(leftMotorPin, 0);
  analogWrite(centerMotorPin, 0);
  analogWrite(rightMotorPin, 0);
}

void loop() {
  if (Serial.available() > 0) {
    
    String incomingData = Serial.readStringUntil('\n');
    incomingData.trim(); 

    JsonDocument doc; 
    DeserializationError error = deserializeJson(doc, incomingData);

    if (!error) {
      int leftPower = doc["left"] | 0;
      int centerPower = doc["center"] | 0;
      int rightPower = doc["right"] | 0;

      // Send the physical electrical pulses
      analogWrite(leftMotorPin, leftPower);
      analogWrite(centerMotorPin, centerPower);
      analogWrite(rightMotorPin, rightPower);
      
      // --- NEW: Leader's Suggested Serial Print Verification ---
      Serial.print("VERIFIED PWM OUTPUT -> Left: ");
      Serial.print(leftPower);
      Serial.print(" | Center: ");
      Serial.print(centerPower);
      Serial.print(" | Right: ");
      Serial.println(rightPower);
      // ---------------------------------------------------------
      
      Serial.println("Success: Motors updated!\n");
    } else {
      Serial.print("Failed to parse JSON. Reason: ");
      Serial.println(error.c_str());
    }
  }
}