// ==============================================================================
// Walvoil Viscosity Flow Measurement System — Production Firmware (Static IP)
// Single LED Indicator, Static IP Config, UDP Telemetry & Heartbeat, Modbus Jumbo Display
// ==============================================================================

#include <ModbusRTU.h>
#include <WiFi.h>
#include <WiFiUdp.h>

// === Pin Definitions ===
const int LED_PIN = 18;  // Single Status LED (Solid on boot, 200ms blink on UDP send)

// === WiFi Credentials ===
const char* WIFI_SSID     = "Alfdvisco@12345";  
const char* WIFI_PASSWORD = "Alfdvisco_123";

// === Static IP Configuration ===
IPAddress local_IP(192, 168, 0, 204);   // Static IP of this ESP32 station
IPAddress gateway(192, 168, 0, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress primaryDNS(8, 8, 8, 8);
IPAddress secondaryDNS(8, 8, 4, 4);

// === Server & UDP Configuration ===
const char* SERVER_IP = "192.168.0.152";  // Flask / CentOS server IP
const int   UDP_PORT  = 63363;            // Server UDP port
const int   DEVICE_ID = 120;              // Station device identifier
WiFiUDP udp;
bool udpStarted       = false;

// === MAX485 Pins ===
#define MAX485_DE 4
#define MAX485_RE 4

// === Modbus Configuration ===
HardwareSerial modbusSerial(2); 
ModbusRTU modbus;

const uint8_t  SLAVE_ID    = 1;  // Jumbo Display Modbus Slave ID
const uint16_t REG_ADDRESS = 1;  // Jumbo Display Register Address (2 registers for float)

// === IR Sensor (Digital) ===
const int irSensorDigitalPin = 27;

// === Water Flow Timing Variables ===
unsigned long lastSteadyTime = 0;
unsigned long debounceDelay  = 500;
bool isSteady        = false;
bool waterFlowing    = false;
unsigned long startTime   = 0;
unsigned long elapsedTime = 0;
bool readyToSend     = false;
bool sent           = false;
float elapsedSeconds = 0.0;

// === Heartbeat Variables ===
unsigned long lastHeartbeat = 0;
const unsigned long heartbeatInterval = 5000; // 5-second heartbeat keeps dashboard ONLINE

// Convert WiFi status enum to readable string
const char* wifiStatusToStr(wl_status_t st) {
  switch (st) {
    case WL_IDLE_STATUS:     return "IDLE";
    case WL_NO_SSID_AVAIL:   return "NO_SSID_AVAIL";
    case WL_CONNECTED:       return "CONNECTED";
    case WL_CONNECT_FAILED:  return "CONNECT_FAILED";
    case WL_CONNECTION_LOST: return "CONNECTION_LOST";
    case WL_DISCONNECTED:    return "DISCONNECTED";
    default:                 return "UNKNOWN";
  }
}

// Convert float to 2 Modbus registers (IEEE-754 32-bit format)
void floatToModbusRegisters(float value, uint16_t *registers) {
  uint8_t bytes[4];
  memcpy(bytes, &value, sizeof(float));

  registers[0] = (bytes[1] << 8) | bytes[0];
  registers[1] = (bytes[3] << 8) | bytes[2];
}

// Write viscosity elapsed seconds (float across 2 registers) to RS485 Modbus Jumbo Display
bool writeDisplayValue(float value) {
  uint16_t registers[2];
  floatToModbusRegisters(value, registers);

  digitalWrite(MAX485_RE, HIGH);
  digitalWrite(MAX485_DE, HIGH);
  bool result = modbus.writeHreg(SLAVE_ID, REG_ADDRESS, registers, 2);
  digitalWrite(MAX485_RE, LOW);
  digitalWrite(MAX485_DE, LOW);

  return result;
}

// Check WiFi connection and start UDP socket once connected
void checkWiFiAndUDP() {
  if (WiFi.status() == WL_CONNECTED && !udpStarted) {
    Serial.println("\n✅ WiFi Connected!");
    Serial.print("Station IP: "); Serial.println(WiFi.localIP());
    udpStarted = udp.begin(UDP_PORT);
  } else if (WiFi.status() != WL_CONNECTED && udpStarted) {
    udpStarted = false; // Reset if connection dropped
  }
}

// Transmit periodic UDP heartbeat ping so app.py marks station ONLINE
void sendHeartbeatUDP() {
  if (WiFi.status() != WL_CONNECTED || !udpStarted) return;
  
  char message[16];
  snprintf(message, sizeof(message), "%d,PING", DEVICE_ID);

  udp.beginPacket(SERVER_IP, UDP_PORT);
  udp.write((uint8_t*)message, strlen(message));
  udp.endPacket();
}

// Transmit completed viscosity test result to Flask server
void sendUDP(float value) {
  if (WiFi.status() != WL_CONNECTED) return;
  
  char message[32];
  snprintf(message, sizeof(message), "%d,%.2f", DEVICE_ID, value);

  udp.beginPacket(SERVER_IP, UDP_PORT);
  udp.write((uint8_t*)message, strlen(message));
  if (udp.endPacket()) {
    Serial.print("📤 UDP Packet Sent: ");
    Serial.println(message);
    
    // Single LED transmission success blink
    digitalWrite(LED_PIN, HIGH);
    delay(200);
    digitalWrite(LED_PIN, LOW);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  // Power-on test: single LED solid for 1 second
  digitalWrite(LED_PIN, HIGH);
  delay(1000);
  digitalWrite(LED_PIN, LOW);

  // MAX485 Control Pins
  pinMode(MAX485_DE, OUTPUT);
  pinMode(MAX485_RE, OUTPUT);
  digitalWrite(MAX485_RE, LOW);
  digitalWrite(MAX485_DE, LOW);

  // Modbus Serial Initialization (Port 2, Pins 32 RX / 33 TX at 9600 baud)
  modbusSerial.begin(9600, SERIAL_8N1, 32, 33);
  modbus.begin(&modbusSerial, MAX485_RE);
  modbus.master();
  modbus.task();

  // IR Flow Sensor Pin
  pinMode(irSensorDigitalPin, INPUT);
  
  // Static IP Configuration & WiFi Connection
  if (!WiFi.config(local_IP, gateway, subnet, primaryDNS, secondaryDNS)) {
    Serial.println("⚠️ Static IP configuration failed!");
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setAutoReconnect(true); 

  // Reset Jumbo Display to 0.0 at boot
  writeDisplayValue(0.0);
  
  Serial.println("🚀 System Initialized - Connecting to WiFi with Static IP 192.168.0.204...");
}

void loop() {
  modbus.task();
  unsigned long now = millis();

  // Check WiFi connection and start UDP socket when connected
  checkWiFiAndUDP();

  // Heartbeat - Periodically transmits UDP ping to keep station ONLINE on web dashboard
  if (now - lastHeartbeat >= heartbeatInterval) {
    lastHeartbeat = now;
    sendHeartbeatUDP();
    Serial.println("\n--- HEARTBEAT (UDP PING SENT) ---");
    Serial.print("WiFi: "); Serial.println(wifiStatusToStr(WiFi.status()));
    Serial.print("Server: "); Serial.print(SERVER_IP); Serial.print(":"); Serial.println(UDP_PORT);
    Serial.print("Flowing: "); Serial.println(waterFlowing ? "YES" : "NO");
    Serial.print("Sent: "); Serial.println(sent ? "YES" : "NO");
    Serial.println("--------------------------------\n");
  }

  // If test reading was already transmitted, idle until device reset (heartbeat still continues above)
  if (sent) {
    delay(10);
    return;
  }

  int irValue = digitalRead(irSensorDigitalPin);

  // IR Flow Timing Logic
  if (irValue == 0) {
    // Flow detected (liquid broken the IR beam)
    if (!isSteady && (now - lastSteadyTime > debounceDelay)) {
      isSteady = true;
      waterFlowing = true;
      startTime = now;
      Serial.println("💧 Flow detected.");
    }
    lastSteadyTime = now;
  } else {
    // Flow stopped (cup emptied / beam restored)
    if (isSteady && (now - lastSteadyTime > debounceDelay)) {
      isSteady = false;
      if (waterFlowing) {
        waterFlowing = false;
        elapsedSeconds = (now - startTime) / 1000.0;
        Serial.print("🚫 Flow stopped: "); Serial.println(elapsedSeconds);
        
        // 1. Immediately write elapsed time to Modbus Jumbo Display (float across 2 registers)
        bool modbusSuccess = writeDisplayValue(elapsedSeconds);
        if (modbusSuccess) {
          Serial.println("✅ Modbus Write OK.");
        } else {
          Serial.println("❌ Modbus Write Failed.");
        }

        readyToSend = true;
      }
    }
  }

  // SENDING SEQUENCE (Transmits reading to Flask backend over UDP)
  if (readyToSend && !sent) {
    if (WiFi.status() == WL_CONNECTED) {
      sendUDP(elapsedSeconds);
      sent = true; 
      readyToSend = false; 
      Serial.println("🏁 Sequence Complete.");
    } else {
      static unsigned long lastWaitMsg = 0;
      if (now - lastWaitMsg > 3000) {
        Serial.println("⏳ Waiting for WiFi connection to send data...");
        lastWaitMsg = now;
      }
    }
  }

  delay(10); 
}
