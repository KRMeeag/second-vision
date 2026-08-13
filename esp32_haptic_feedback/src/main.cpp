#include <Arduino.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <DFRobot_BMI160.h>

// --- Pins for the Motors ---
const int leftMotorPin = D0;   
const int centerMotorPin = D1; 
const int rightMotorPin = D2;  

// --- SAFETY FEATURE: Hardware Governor ---
// Limit to 150 (approx. 60% power) so that it wouldn't be a heavy burden on the MOSFET/Motor
const int MAX_PWM = 150; 

DFRobot_BMI160 bmi160;
unsigned long lastSensorTime = 0;
const int sensorInterval = 200; 

void setup() {
  Serial.begin(115200);
  
  pinMode(leftMotorPin, OUTPUT);
  pinMode(centerMotorPin, OUTPUT);
  pinMode(rightMotorPin, OUTPUT);
  analogWrite(leftMotorPin, 0);
  analogWrite(centerMotorPin, 0);
  analogWrite(rightMotorPin, 0);

  Wire.begin(D4, D5);
  bmi160.softReset();
  bmi160.I2cInit(0x69);
}

void loop() {
  // =================================================================
  // TASK 1: RECEIVE COMMAND FROM THE RASPBERRY PI
  // =================================================================
  if (Serial.available() > 0) {
    String incomingData = Serial.readStringUntil('\n');
    incomingData.trim(); 

    if (incomingData.length() > 0) {
      JsonDocument doc; 
      DeserializationError error = deserializeJson(doc, incomingData);

      if (!error && doc.containsKey("left")) {
        // Get the value from the JSON (default is 0 if empty)
        int rawLeft = doc["left"] | 0;
        int rawCenter = doc["center"] | 0;
        int rawRight = doc["right"] | 0;

        // Apply the Safety Limit (Governor)
        // If the raw value exceeds MAX_PWM, it will be capped to MAX_PWM
        int safeLeft = min(rawLeft, MAX_PWM);
        int safeCenter = min(rawCenter, MAX_PWM);
        int safeRight = min(rawRight, MAX_PWM);

        // Update the motor values with the safe values
        analogWrite(leftMotorPin, safeLeft);
        analogWrite(centerMotorPin, safeCenter);
        analogWrite(rightMotorPin, safeRight);
        
        Serial.println("{\"info\": \"MOTORS_UPDATED_WITH_SAFETY_LIMIT\"}");
      }
    }
  }

  // =================================================================
  // TASK 2: SENSOR READING & WALKING TOLERANCE
  // =================================================================
  unsigned long currentMillis = millis();
  
  if (currentMillis - lastSensorTime >= sensorInterval) {
    lastSensorTime = currentMillis; 

    int16_t sensorData[6] = {0}; 
    
    if(bmi160.getAccelGyroData(sensorData) == 0) {
      int16_t gyroYaw = sensorData[2];    
      int16_t accelPitch = sensorData[4]; 

      // --- SELF-HEALING BROWNOUT RECOVERY ---
      if (gyroYaw == 0 && accelPitch == 0) {
        Serial.println("{\"warning\": \"SENSOR BROWNOUT DETECTED. REBOOTING IMU...\"}");
        bmi160.I2cInit(0x69); 
        return; 
      }
      // --------------------------------------

      String currentState = "normal";
      
      // THRESHOLD UPSCALE: So that we can ignore normal walking movements and only detect extreme head movements
      // If the absolute value of gyroYaw exceeds 20000, we consider it as "head_moving"
      if (abs(gyroYaw) > 20000) { 
        currentState = "head_moving";
      } 
      // If the absolute value of accelPitch exceeds 14000, we consider it as "wrong_pitch"
      else if (abs(accelPitch) > 14000) { 
        currentState = "wrong_pitch";
      }

      // Send the sensor data and state back to the Raspberry Pi in JSON format
      JsonDocument outDoc;
      outDoc["state"] = currentState;
      outDoc["raw_pitch"] = accelPitch;
      outDoc["raw_yaw"] = gyroYaw;

      serializeJson(outDoc, Serial);
      Serial.println(); 
    }
  }
}
