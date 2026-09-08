#include <Arduino.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <DFRobot_BMI160.h>

// --- CONFIGURATION & MODES ---
// Set to true for MAC / Standalone testing (cycles incremental test vibrations)
// Set to false for Deployment mode (listens to UART commands from Raspberry Pi 5)
bool MAC_MODE = true; 

// --- Pins for the Motors (Using XIAO ESP32-C3 mappings) ---
const int leftMotorPin = D0;   
const int centerMotorPin = D1; 
const int rightMotorPin = D2;  

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

void setup() {
  Serial.begin(115200);
  
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
  // MODE 2: DEPLOYMENT MODE (Connected to RPi5 via UART)
  // =================================================================
  else {
    if (Serial.available() > 0) {
      String incomingData = Serial.readStringUntil('\n');
      incomingData.trim(); 

      if (incomingData.length() > 0) {
        JsonDocument doc; 
        DeserializationError error = deserializeJson(doc, incomingData);

        if (!error && doc.containsKey("left")) {
          int rawLeft = doc["left"] | 0;
          int rawCenter = doc["center"] | 0;
          int rawRight = doc["right"] | 0;

          // Apply maximum Safety Governor Limit (255)
          int safeLeft = min(rawLeft, MAX_PWM);
          int safeCenter = min(rawCenter, MAX_PWM);
          int safeRight = min(rawRight, MAX_PWM);

          analogWrite(leftMotorPin, safeLeft);
          analogWrite(centerMotorPin, safeCenter);
          analogWrite(rightMotorPin, safeRight);
          
          Serial.printf("[UART CMD] Applied -> L: %d | C: %d | R: %d\n", safeLeft, safeCenter, safeRight);
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
        int16_t accelPitch = sensorData[4]; 

        // --- SELF-HEALING BROWNOUT RECOVERY ---
        if (gyroYaw == 0 && accelPitch == 0) {
          Serial.println("[WARNING] SENSOR BROWNOUT DETECTED. REBOOTING IMU...");
          imuHealthy = (bmi160.I2cInit(0x69) == 0); 
          return; 
        }

        String currentState = "normal";
        if (abs(gyroYaw) > 20000) { 
          currentState = "head_moving";
        } else if (abs(accelPitch) > 14000) { 
          currentState = "wrong_pitch";
        }

        // Stream telemetry back to the host/RPi
        JsonDocument outDoc;
        outDoc["state"] = currentState;
        outDoc["raw_pitch"] = accelPitch;
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