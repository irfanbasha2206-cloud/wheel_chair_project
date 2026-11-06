/*
 * ==================================================================================
 * SMART WHEELCHAIR PROJECT - ESP8266 AD8232 ECG MONITOR
 * ==================================================================================
 * This sketch reads AD8232 ECG signals and transmits live data to:
 * https://iotcloud22.in/4954/post_value1.php
 *
 * Pin Connections (ESP8266 NodeMCU <-> AD8232 ECG Module):
 * ---------------------------------------------------------
 * AD8232 OUTPUT  --> ESP8266 A0 (Analog Input)
 * AD8232 3.3V    --> ESP8266 3V3
 * AD8232 GND     --> ESP8266 GND
 * AD8232 LO+     --> ESP8266 D5 (GPIO 14) [Leads-Off Detection +]
 * AD8232 LO-     --> ESP8266 D6 (GPIO 12) [Leads-Off Detection -]
 * AD8232 SDN     --> Leave unconnected
 * ==================================================================================
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecure.h>

// Wi-Fi Credentials (same as hotspot)
const char* ssid     = "ras";
const char* password = "12345678";

// IoT Cloud URL for ECG
const char* postUrl = "https://iotcloud22.in/4954/post_value1.php";

// Pin Definitions
const int ECG_PIN  = A0;  // AD8232 Output
const int LO_PLUS  = D5;  // GPIO 14 (LO+)
const int LO_MINUS = D6;  // GPIO 12 (LO-)

// Timer for Cloud Upload
unsigned long lastUploadTime = 0;
const unsigned long uploadInterval = 1500; // Send to cloud every 1.5 seconds

void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println();
  Serial.println("==================================================");
  Serial.println("   AD8232 ECG MONITOR - ESP8266 NODE MCU          ");
  Serial.println("==================================================");

  // Setup Leads-Off Pins
  pinMode(LO_PLUS, INPUT);
  pinMode(LO_MINUS, INPUT);

  // Connect to Wi-Fi
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 30) {
    delay(500);
    Serial.print(".");
    retries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[OK] Wi-Fi Connected Successfully!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WARN] Wi-Fi Connection Failed. Will retry in loop...");
  }
}

void loop() {
  // Ensure Wi-Fi is connected
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(ssid, password);
    delay(1000);
    return;
  }

  // Check Leads-off detection
  bool leadsOff = (digitalRead(LO_PLUS) == HIGH || digitalRead(LO_MINUS) == HIGH);

  float ecg_mv = 0.0;
  if (leadsOff) {
    ecg_mv = 0.0; // Probe disconnected
  } else {
    // Read Analog A0 (0 to 1023)
    // ESP8266 NodeMCU A0 input range is 0 - 3.3V (with internal 220k/100k divider)
    int rawValue = analogRead(ECG_PIN);
    
    // Convert to millivolts (0 - 3300 mV)
    ecg_mv = (rawValue / 1023.0) * 3300.0;
  }

  // Print to Serial Monitor for real-time testing
  Serial.print("ECG_mV: ");
  Serial.println(ecg_mv);

  // Send to Cloud every interval (1.5 seconds)
  if (millis() - lastUploadTime >= uploadInterval) {
    lastUploadTime = millis();
    sendECGToCloud(ecg_mv);
  }

  delay(20); // Fast sampling delay
}

void sendECGToCloud(float ecg_value) {
  WiFiClientSecure client;
  client.setInsecure(); // Skip SSL certificate validation for cloud server

  HTTPClient http;

  if (http.begin(client, postUrl)) {
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");

    // POST data formatted as value1=<ecg_value>
    String httpRequestData = "value1=" + String(ecg_value, 1);

    int httpResponseCode = http.POST(httpRequestData);

    if (httpResponseCode > 0) {
      String response = http.getString();
      Serial.print("☁️ [IoT CLOUD ECG] Sent: ");
      Serial.print(ecg_value);
      Serial.print(" mV | Response: ");
      Serial.println(response);
    } else {
      Serial.print("❌ [IoT CLOUD ERROR] Code: ");
      Serial.println(httpResponseCode);
    }

    http.end();
  } else {
    Serial.println("❌ Unable to connect to ECG post URL");
  }
}
