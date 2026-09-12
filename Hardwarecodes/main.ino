// This is code for Vicosity Flow Measurement System.
// Assign Static IP to this device, connect to WiFi, and send flow time via UDP to a server.

#include <ModbusRTU.h>
#include <WiFi.h>
#include <WiFiUdp.h>

// === WiFi Configuration ===
const char* WIFI_SSID     = "Alfdvisco@12345";  
const char* WIFI_PASSWORD = "Alfdvisco_123";

// === STATIC IP CONFIG ===
IPAddress local_IP(192, 168, 0, 204); //Static ip address 
IPAddress gateway(192, 168, 0, 1);
IPAddress subnet(255, 255, 255, 0);

// === UDP Configuration ===
const char* UDP_IP   = "192.168.0.150"; // Server ip address
const int   UDP_PORT = 63363; //UDP port
WiFiUDP udp;
bool udpReady = false;

// === MAX485 Pins ===
#define MAX485_DE 4
#define MAX485_RE 4

// === LED PINS ===
#define YELLOW_LED 13
#define GREEN_LED  15
#define RED_LED    22

// === Modbus ===
HardwareSerial modbusSerial(2);
ModbusRTU modbus;
const uint8_t  SLAVE_ID    = 1;
const uint16_t REG_ADDRESS = 0;
bool modbusWriteDone = false;

// === IR Sensor ===
const int irSensorDigitalPin = 27;

// === Flow Variables ===
unsigned long lastSteadyTime = 0;
unsigned long debounceDelay  = 500;
bool isSteady     = false;
bool waterFlowing = false;
unsigned long startTime   = 0;
unsigned long elapsedTime = 0;
float elapsedSeconds = 0.0;
bool readyToSend = false;
bool sent        = false;

// === Non-blocking LED timer ===
bool ledActive          = false;
unsigned long ledOnTime = 0;
const unsigned long LED_DURATION = 3000;

// === Non-blocking WiFi state machine ===
enum WiFiState {
  WIFI_IDLE,
  WIFI_CONNECTING,
  WIFI_CONNECTED_OK,
  WIFI_FAILED
};
WiFiState wifiState       = WIFI_IDLE;
unsigned long wifiStart   = 0;
const unsigned long WIFI_TIMEOUT = 10000;  // 10 seconds max

// === Send state — ensures UDP fires after WiFi is ready ===
bool pendingUDPSend = false;

// === Heartbeat ===
unsigned long lastHeartbeat           = 0;
const unsigned long heartbeatInterval = 5000;

// =====================================================
// Modbus callback
// =====================================================
bool modbusCallback(Modbus::ResultCode event, uint16_t transactionId, void* data) {
  if (event == Modbus::EX_SUCCESS) {
    Serial.println("✅ Modbus Write Confirmed");
  } else {
    Serial.print("❌ Modbus Error: 0x");
    Serial.println(event, HEX);
  }
  modbusWriteDone = true;
  return true;
}

// =====================================================
// LEDs
// =====================================================
void turnOffAllLEDs() {
  digitalWrite(YELLOW_LED, HIGH);
  digitalWrite(GREEN_LED,  HIGH);
  digitalWrite(RED_LED,    HIGH);
}

// NON-BLOCKING: just turns the correct LED on and records the time.
// The loop() checks when 3 s have passed and turns it off.
void startLED(float value) {
  turnOffAllLEDs();
  if (value < 13.0) {
    Serial.println("🔴 RED LED ON (<13)");
    digitalWrite(RED_LED, LOW);
  } else if (value > 16.0 && value <= 18.0) {
    Serial.println("🟢 GREEN LED ON (16-18)");
    digitalWrite(GREEN_LED, LOW);
  } else {
    Serial.println("🔴 RED LED ON (out of range)");
    digitalWrite(RED_LED, LOW);
  }
  ledActive = true;
  ledOnTime = millis();
}

// Called every loop() — turns LED off after LED_DURATION ms
void handleLEDTimer() {
  if (ledActive && (millis() - ledOnTime >= LED_DURATION)) {
    turnOffAllLEDs();
    ledActive = false;
    Serial.println("💡 LED off");
  }
}

// =====================================================
// WiFi — non-blocking state machine
// Call startWiFiConnect() once to kick off.
// Call updateWiFi() every loop() to drive the state.
// =====================================================
void startWiFiConnect() {
  Serial.println("📡 Starting WiFi connect...");
  udpReady    = false;
  wifiState   = WIFI_CONNECTING;
  wifiStart   = millis();

  WiFi.disconnect(true);
  delay(100);   // short one-time hardware settle — acceptable
  WiFi.config(local_IP, gateway, subnet);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

// Returns true once WiFi is confirmed up (or failed).
// Must be called every loop() while wifiState == WIFI_CONNECTING.
bool updateWiFi() {
  if (wifiState != WIFI_CONNECTING) return false;

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi Connected!");
    Serial.print("IP: "); Serial.println(WiFi.localIP());

    delay(200);   // short socket-layer settle — acceptable

    if (udp.begin(UDP_PORT)) {
      udpReady = true;
      Serial.println("🛰 UDP socket ready");
    } else {
      Serial.println("❌ UDP begin failed");
    }

    wifiState = WIFI_CONNECTED_OK;
    return true;
  }

  if (millis() - wifiStart > WIFI_TIMEOUT) {
    Serial.println("\n❌ WiFi timeout!");
    wifiState = WIFI_FAILED;
    return true;   // done (failed)
  }

  // Still waiting — print a dot every 500 ms
  static unsigned long lastDot = 0;
  if (millis() - lastDot > 500) {
    lastDot = millis();
    Serial.print(".");
  }

  return false;   // still in progress
}

// =====================================================
// UDP Send — with retry and full error reporting
// =====================================================
void sendUDP(float value) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ UDP skip: WiFi not connected");
    return;
  }
  if (!udpReady) {
    Serial.println("⚠️ UDP skip: socket not ready");
    return;
  }

  char msg[20];
  snprintf(msg, sizeof(msg), "%.2f", value);

  const int MAX_RETRIES = 3;
  for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    Serial.print("📤 UDP attempt ");
    Serial.print(attempt);
    Serial.print("/");
    Serial.print(MAX_RETRIES);
    Serial.print(" → ");
    Serial.println(msg);

    if (!udp.beginPacket(UDP_IP, UDP_PORT)) {
      Serial.println("   ❌ beginPacket failed");
      delay(200);
      continue;
    }

    int written = udp.write((uint8_t*)msg, strlen(msg));
    Serial.print("   bytes: "); Serial.println(written);

    if (udp.endPacket()) {
      Serial.println("   ✅ UDP sent");
      return;
    } else {
      Serial.println("   ❌ endPacket failed");
      delay(200);
    }
  }

  Serial.println("❌ UDP failed after all retries");
}

// =====================================================
// Heartbeat — Serial only, no UDP
// =====================================================
void sendHeartbeat() {
  unsigned long now = millis();
  if (now - lastHeartbeat < heartbeatInterval) return;
  lastHeartbeat = now;

  Serial.println("===== HEARTBEAT =====");
  Serial.print("WiFi    : "); Serial.println(WiFi.status() == WL_CONNECTED ? "UP" : "DOWN");
  Serial.print("udpReady: "); Serial.println(udpReady      ? "yes" : "no");
  Serial.print("flow    : "); Serial.println(waterFlowing  ? "yes" : "no");
  Serial.print("sent    : "); Serial.println(sent          ? "yes" : "no");
  Serial.print("elapsed : "); Serial.println(elapsedSeconds, 2);
  Serial.print("wifiSt  : "); Serial.println(wifiState);
  Serial.print("ledActive:"); Serial.println(ledActive     ? "yes" : "no");
  Serial.println("=====================");
}

// =====================================================
// Modbus write — blocking with callback + timeout
// (this is short: <3 s, Modbus needs task() called fast)
// =====================================================
void writeModbus(uint16_t value) {
  modbusWriteDone = false;

  digitalWrite(MAX485_DE, HIGH);
  digitalWrite(MAX485_RE, HIGH);

  modbus.writeHreg(SLAVE_ID, REG_ADDRESS, value, modbusCallback);

  unsigned long timeout = millis();
  while (!modbusWriteDone) {
    modbus.task();
    delay(10);
    if (millis() - timeout > 3000) {
      Serial.println("⚠️ Modbus Write Timeout");
      break;
    }
  }

  digitalWrite(MAX485_DE, LOW);
  digitalWrite(MAX485_RE, LOW);
}

// =====================================================
// Setup
// =====================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n🚀 SYSTEM START");

  pinMode(YELLOW_LED, OUTPUT);
  pinMode(GREEN_LED,  OUTPUT);
  pinMode(RED_LED,    OUTPUT);
  turnOffAllLEDs();

  // Boot indicator — only blocking delay in the whole program
  digitalWrite(YELLOW_LED, LOW);
  delay(2000);
  digitalWrite(YELLOW_LED, HIGH);

  // RS485
  pinMode(MAX485_DE, OUTPUT);
  pinMode(MAX485_RE, OUTPUT);
  digitalWrite(MAX485_RE, LOW);
  digitalWrite(MAX485_DE, LOW);

  // Modbus
  modbusSerial.begin(9600, SERIAL_8N1, 32, 33);
  modbus.begin(&modbusSerial, MAX485_RE);
  modbus.master();
  Serial.println("🟡 Modbus Ready");

  pinMode(irSensorDigitalPin, INPUT);

  // Reset Modbus register to 0 at boot
  Serial.println("🔄 Resetting Modbus to 0...");
  writeModbus(0);
  Serial.println("🔄 Modbus Reset Done");
}

// =====================================================
// Loop — fully non-blocking
// =====================================================
void loop() {
  modbus.task();
  sendHeartbeat();
  handleLEDTimer();     // turns LED off after 3 s non-blocking

  // Drive the WiFi state machine while connecting
  if (wifiState == WIFI_CONNECTING) {
    bool done = updateWiFi();

    if (done) {
      // WiFi finished (success or fail) — now send UDP if pending
      if (pendingUDPSend) {
        pendingUDPSend = false;
        if (wifiState == WIFI_CONNECTED_OK) {
          sendUDP(elapsedSeconds);
        } else {
          Serial.println("❌ WiFi failed — UDP not sent");
        }
        sent = true;
        Serial.println("🏁 Cycle complete — reset to run again");
      }
    }
    return;   // don't read IR while WiFi is connecting
  }

  if (sent) return;  // HALT until hard reset

  int irValue = digitalRead(irSensorDigitalPin);
  unsigned long currentTime = millis();

  // --- FLOW START ---
  if (irValue == LOW) {
    if (!isSteady && (currentTime - lastSteadyTime > debounceDelay)) {
      isSteady     = true;
      waterFlowing = true;
      startTime    = currentTime;
      Serial.println("💧 FLOW START");
    }
    lastSteadyTime = currentTime;
  }

  // --- FLOW STOP ---
  else {
    if (isSteady && (currentTime - lastSteadyTime > debounceDelay)) {
      isSteady = false;

      if (waterFlowing) {
        waterFlowing   = false;
        elapsedTime   += (currentTime - startTime);
        elapsedSeconds = elapsedTime / 1000.0;

        Serial.print("⏱ Time: ");
        Serial.println(elapsedSeconds, 2);

        readyToSend = true;
      }
    }
  }

  // --- SEND PROCESS ---
  if (readyToSend && !sent) {
    readyToSend = false;

    uint16_t modbusValue = (uint16_t)(elapsedSeconds * 100);

    // 1. Write Modbus (blocking but short — needs fast task() calls)
    Serial.print("📤 Writing Modbus: ");
    Serial.println(modbusValue);
    writeModbus(modbusValue);

    // 2. Start LED non-blocking (returns immediately)
    startLED(elapsedSeconds);

    // 3. Kick off WiFi connect non-blocking
    //    UDP will fire automatically once WiFi comes up (see loop above)
    pendingUDPSend = true;
    startWiFiConnect();
  }
}


