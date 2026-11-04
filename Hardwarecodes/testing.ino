#include <ModbusRTU.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WiFiManager.h>
#include <Preferences.h>

// === Pin Definitions ===
#define BOOT_BTN_PIN 0

// === UDP Configuration ===
char SERVER_IP[40] = "192.168.0.159";  // CentOS / server IP address
char UDP_PORT_STR[6] = "63363";
int UDP_PORT  = 63363;
const int DEVICE_ID = 120;                  // This device's identifier
const int LED_PIN   = 18; // set based on the gateway
WiFiUDP udp;

// === MAX485 Pins ===
#define MAX485_DE 4
#define MAX485_RE 4

// === Modbus Configuration ===
HardwareSerial modbusSerial(2); 
ModbusRTU modbus;

const uint8_t  SLAVE_ID    = 1; //set  slave id as per your jumbo display
const uint16_t REG_ADDRESS = 1; //set  register address as per your jumbo display

// === IR Sensor (Digital) ===
const int irSensorDigitalPin = 27;

// === Water Flow Timing Variables ===
unsigned long lastSteadyTime = 0;
unsigned long debounceDelay  = 500;
bool isSteady      = false;
bool waterFlowing  = false;
unsigned long startTime   = 0;
unsigned long elapsedTime = 0;
bool readyToSend = false;
bool sent         = false;
float elapsedSeconds = 0.0;

// === Heartbeat Variables ===
unsigned long lastHeartbeat = 0;
const unsigned long heartbeatInterval = 5000; 
bool udpStarted = false; 

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

// === Convert float to 2 Modbus registers ===
void floatToModbusRegisters(float value, uint16_t *registers) {
  uint8_t bytes[4];
  memcpy(bytes, &value, sizeof(float));

  registers[0] = (bytes[1] << 8) | bytes[0];
  registers[1] = (bytes[3] << 8) | bytes[2];
}

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

// We no longer need the blocking connectWiFi function because WiFi connects in the background.
// Instead, we will handle UDP initialization in the loop when WiFi connects.
void checkWiFiAndUDP() {
  if (WiFi.status() == WL_CONNECTED && !udpStarted) {
    Serial.println("\n✅ WiFi Connected in background.");
    udpStarted = udp.begin(UDP_PORT);
  } else if (WiFi.status() != WL_CONNECTED && udpStarted) {
    udpStarted = false; // Reset if we lose connection
  }
}

void sendHeartbeatUDP() {
  if (WiFi.status() != WL_CONNECTED || !udpStarted) return;
  
  char message[16];
  snprintf(message, sizeof(message), "%d,PING", DEVICE_ID);

  udp.beginPacket(SERVER_IP, UDP_PORT);
  udp.write((uint8_t*)message, strlen(message));
  udp.endPacket();
}

void sendUDP(float value) {
  if (WiFi.status() != WL_CONNECTED) return;
  
  char message[32];
  snprintf(message, sizeof(message), "%d,%.2f", DEVICE_ID, value);

  udp.beginPacket(SERVER_IP, UDP_PORT);
  udp.write((uint8_t*)message, strlen(message));
  if (udp.endPacket()) {
    Serial.print("📤 UDP Packet Sent: ");
    Serial.println(message);
    
    // Success blink
    digitalWrite(LED_PIN, HIGH);
    delay(200);
    digitalWrite(LED_PIN, LOW);
  }
}

// === Configuration Portal ===
void startConfigPortal() {
  WiFiManager wm;
  
  // Custom parameters
  WiFiManagerParameter custom_server_ip("server", "Server IP", SERVER_IP, 40);
  WiFiManagerParameter custom_udp_port("port", "UDP Port", UDP_PORT_STR, 6);
  
  wm.addParameter(&custom_server_ip);
  wm.addParameter(&custom_udp_port);
  
  Serial.println("🌐 Starting WiFi Config Portal...");
  Serial.println("Connect to Access Point: ESP32_Config");
  
  // Start captive portal (Blocks here until configured or timeout)
  if (!wm.startConfigPortal("ESP32_Config")) {
    Serial.println("❌ Failed to connect and hit timeout. Restarting...");
    delay(3000);
    ESP.restart();
  }
  
  // If we get here, settings were saved!
  Serial.println("✅ Config Saved!");
  
  // Copy new parameters
  strcpy(SERVER_IP, custom_server_ip.getValue());
  strcpy(UDP_PORT_STR, custom_udp_port.getValue());
  UDP_PORT = atoi(UDP_PORT_STR);
  
  // Save custom parameters to non-volatile memory
  Preferences preferences;
  preferences.begin("config", false);
  preferences.putString("server_ip", String(SERVER_IP));
  preferences.putString("udp_port", String(UDP_PORT_STR));
  preferences.end();
  
  Serial.println("🔄 Restarting in 1 second to apply new settings...");
  delay(1000);
  ESP.restart();
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BOOT_BTN_PIN, INPUT_PULLUP);

  // Load saved custom parameters
  Preferences preferences;
  preferences.begin("config", true); // true = Read Only
  String savedIP = preferences.getString("server_ip", "192.168.0.159");
  String savedPort = preferences.getString("udp_port", "63363");
  savedIP.toCharArray(SERVER_IP, 40);
  savedPort.toCharArray(UDP_PORT_STR, 6);
  UDP_PORT = savedPort.toInt();
  preferences.end();

  // Power-on test
  digitalWrite(LED_PIN, HIGH);
  delay(1000);
  digitalWrite(LED_PIN, LOW);

  pinMode(MAX485_DE, OUTPUT);
  pinMode(MAX485_RE, OUTPUT);
  digitalWrite(MAX485_RE, LOW);
  digitalWrite(MAX485_DE, LOW);

  modbusSerial.begin(9600, SERIAL_8N1, 32, 33); // Change this Baud rate to match your Modbus device
  modbus.begin(&modbusSerial, MAX485_RE);
  modbus.master();
  modbus.task();

  pinMode(irSensorDigitalPin, INPUT);
  
  // Background Auto-Connect (Using credentials saved by WiFiManager)
  WiFi.mode(WIFI_STA);
  WiFi.begin(); // No SSID/PW arguments needed, it uses the saved ones
  // Auto-reconnect if connection drops
  WiFi.setAutoReconnect(true); 

  writeDisplayValue(0.0);
  
  Serial.println("🚀 System Initialized - WiFi connecting in background...");
}

void loop() {
  modbus.task();
  unsigned long now = millis();

  // Check BOOT Button for 3 seconds
  static unsigned long btnPressTime = 0;
  static bool btnPressed = false;
  
  if (digitalRead(BOOT_BTN_PIN) == LOW) {
    if (!btnPressed) {
      btnPressed = true;
      btnPressTime = millis();
    } else if (millis() - btnPressTime > 3000) {
      startConfigPortal(); // Blocks here
      btnPressed = false; 
    }
  } else {
    btnPressed = false;
  }

  // Check background WiFi connection and start UDP if ready
  checkWiFiAndUDP();

  // Heartbeat - Only shows WiFi status if we have tried to connect
  if (now - lastHeartbeat >= heartbeatInterval) {
    lastHeartbeat = now;
    sendHeartbeatUDP();
    Serial.println("\n--- HEARTBEAT (UDP PING SENT) ---");
    Serial.print("WiFi: "); Serial.println(wifiStatusToStr(WiFi.status()));
    Serial.print("Flowing: "); Serial.println(waterFlowing ? "YES" : "NO");
    Serial.print("Sent: "); Serial.println(sent ? "YES" : "NO");
    Serial.println("-----------------\n");
  }

  if (sent) {
    delay(10);
    return;
  }

  int irValue = digitalRead(irSensorDigitalPin);

  // IR Logic
  if (irValue == 0) {
    if (!isSteady && (now - lastSteadyTime > debounceDelay)) {
      isSteady = true;
      waterFlowing = true;
      startTime = now;
      Serial.println("💧 Flow detected.");
    }
    lastSteadyTime = now;
  } else {
    if (isSteady && (now - lastSteadyTime > debounceDelay)) {
      isSteady = false;
      if (waterFlowing) {
        waterFlowing = false;
        elapsedSeconds = (now - startTime) / 1000.0;
        Serial.print("🚫 Flow stopped: "); Serial.println(elapsedSeconds);
        
        // 1. MODBUS WRITE FIRST (Happens immediately when flow stops)
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

  // SENDING SEQUENCE (Only handles Network Transmission)
  if (readyToSend && !sent) {
    // Check if WiFi is connected
    if (WiFi.status() == WL_CONNECTED) {
      // 2. SEND UDP
      sendUDP(elapsedSeconds);
      sent = true; 
      readyToSend = false; // Successfully sent, we can stop trying
      Serial.println("🏁 Sequence Complete.");
    } else {
      // Wait for background WiFi to connect without spamming the serial monitor
      static unsigned long lastWaitMsg = 0;
      if (now - lastWaitMsg > 3000) {
        Serial.println("⏳ Waiting for WiFi connection to send data...");
        lastWaitMsg = now;
      }
    }
  }

  delay(10); 
}