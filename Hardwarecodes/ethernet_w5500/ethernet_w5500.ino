// ==============================================================================
// Walvoil Viscosity Flow Measurement System — W5500 SPI Ethernet Firmware
// Target MCU: ESP32 + WIZnet W5500 Ethernet Module + MAX485 Modbus + IR Flow Sensor
//
// Network: Static IP / Gateway / Subnet / DNS
// Target Destination: wifi30 on UDP Port 5002
// ==============================================================================

#include <SPI.h>
#include <Ethernet.h>
#include <EthernetUdp.h>
#include <ModbusRTU.h>

// ==============================================================================
// 1. PIN DEFINITIONS
// ==============================================================================
// --- W5500 SPI Ethernet Pins ---
#define W5500_MISO 12
#define W5500_MOSI 13
#define W5500_SCK  14
#define W5500_CS   15
#define W5500_RST  25

// --- MAX485 Modbus RTU Pins ---
#define MAX485_DE  4
#define MAX485_RE  4
#define MODBUS_RX_PIN 32
#define MODBUS_TX_PIN 33

// --- IR Flow Sensor Pin (Digital) ---
const int irSensorDigitalPin = 27;

// --- Status LED Indicator ---
const int LED_PIN = 18;  // Solid on boot, 200ms blink on UDP telemetry send

// ==============================================================================
// 2. NETWORK & UDP CONFIGURATION (STATIC IP & W5500)
// ==============================================================================
// Enter a MAC address for your controller below.
// Newer Ethernet shields have a MAC address printed on a sticker on the shield.
byte mac[] = { 0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0xED };

// Static Network Settings (Configure to match your plant network subnet)
IPAddress local_IP(192, 168, 161, 130);      // Static IP of this ESP32 station
IPAddress gateway(192, 168, 161, 1);         // Network Gateway
IPAddress subnet(255, 255, 255, 0);       // Subnet Mask
IPAddress primaryDNS(192, 168, 160, 54);      // Local / Primary DNS resolver
IPAddress secondaryDNS(8, 8, 8, 8);        // Secondary DNS

// Destination Host & UDP Port
// Target Host: Fully Qualified Domain Name (FQDN) resolved dynamically via corporate DNS
// No hardcoded IP is used; DHCP dynamic IP shifts for WFI30 are automatically resolved by DNS.
const char* TARGET_HOST = "WFI30.Walvoil.co.in";
const unsigned int TARGET_UDP_PORT = 5002;      // UDP Port 5002 as requested
const unsigned int LOCAL_UDP_PORT  = 5002;      // Local listening port

const int DEVICE_ID = 120;                     // Device Station Identifier

// Ethernet UDP instance
EthernetUDP udp;
bool udpStarted = false;

// ==============================================================================
// 3. MODBUS & JUMBO DISPLAY CONFIGURATION
// ==============================================================================
HardwareSerial modbusSerial(2); 
ModbusRTU modbus;

const uint8_t  SLAVE_ID    = 1;  // Jumbo Display Modbus Slave ID
const uint16_t REG_ADDRESS = 1;  // Jumbo Display Register Address (2 registers for float)

// ==============================================================================
// 4. FLOW TIMING & HEARTBEAT STATE VARIABLES
// ==============================================================================
unsigned long lastSteadyTime = 0;
const unsigned long debounceDelay = 500;  // 500ms debounce
bool isSteady        = false;
bool waterFlowing    = false;
unsigned long startTime   = 0;
unsigned long elapsedTime = 0;
bool readyToSend     = false;
bool sent            = false;
float elapsedSeconds = 0.0;

// Heartbeat interval (UDP PING sent periodically to keep dashboard ONLINE)
unsigned long lastHeartbeat = 0;
const unsigned long heartbeatInterval = 5000; // 5000ms = 5 seconds

// ==============================================================================
// 5. HELPER FUNCTIONS
// ==============================================================================

// Hardware reset pulse for W5500
void resetW5500() {
  pinMode(W5500_RST, OUTPUT);
  digitalWrite(W5500_RST, LOW);
  delay(50);
  digitalWrite(W5500_RST, HIGH);
  delay(200); // Allow W5500 PLL to stabilize
}

// Convert float to 2 Modbus registers (IEEE-754 32-bit format)
void floatToModbusRegisters(float value, uint16_t *registers) {
  uint8_t bytes[4];
  memcpy(bytes, &value, sizeof(float));

  registers[0] = (bytes[1] << 8) | bytes[0];
  registers[1] = (bytes[3] << 8) | bytes[2];
}

// Transmit float value to Jumbo Display via Modbus RTU
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

// Send periodic Heartbeat packet to destination
void sendHeartbeatUDP() {
  if (!udpStarted) return;
  
  char message[24];
  snprintf(message, sizeof(message), "%d,PING", DEVICE_ID);

  // Send packet to domain "WFI30.Walvoil.co.in" on port 5002
  // EthernetUDP queries corporate DNS live, handling dynamic IP changes automatically
  int beginOk = udp.beginPacket(TARGET_HOST, TARGET_UDP_PORT);
  if (beginOk) {
    udp.write((const uint8_t*)message, strlen(message));
    udp.endPacket();
  } else {
    Serial.print("⚠️ [HEARTBEAT DNS FAIL] Could not resolve host: ");
    Serial.println(TARGET_HOST);
  }
}

// Send final measured viscosity seconds to destination
void sendUDP(float value) {
  if (!udpStarted) return;

  char message[32];
  snprintf(message, sizeof(message), "%d,%.2f", DEVICE_ID, value);

  // Try dynamic DNS resolution with quick retry
  int beginOk = 0;
  for (int retry = 0; retry < 3; retry++) {
    beginOk = udp.beginPacket(TARGET_HOST, TARGET_UDP_PORT);
    if (beginOk) break;
    delay(100);
  }

  if (beginOk) {
    udp.write((const uint8_t*)message, strlen(message));
    if (udp.endPacket()) {
      Serial.print("📤 [ETHERNET UDP SENT] -> ");
      Serial.print(TARGET_HOST);
      Serial.print(":");
      Serial.print(TARGET_UDP_PORT);
      Serial.print(" | Payload: ");
      Serial.println(message);

      // Status LED indicator blink (200ms)
      digitalWrite(LED_PIN, HIGH);
      delay(200);
      digitalWrite(LED_PIN, LOW);
    } else {
      Serial.println("❌ [ETHERNET UDP FAIL] Packet transmission failed.");
    }
  } else {
    Serial.print("❌ [ETHERNET UDP FAIL] Could not resolve dynamic host: ");
    Serial.println(TARGET_HOST);
  }
}

// ==============================================================================
// 6. ARDUINO SETUP
// ==============================================================================
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println("\n=======================================================");
  Serial.println("  WALVOIL VISCOSITY STATION — W5500 ETHERNET FIRMWARE");
  Serial.println("=======================================================");

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // LED ON during boot

  // Configure MAX485 Control Pins
  pinMode(MAX485_DE, OUTPUT);
  pinMode(MAX485_RE, OUTPUT);
  digitalWrite(MAX485_RE, LOW);
  digitalWrite(MAX485_DE, LOW);

  // Initialize Modbus Serial
  modbusSerial.begin(9600, SERIAL_8N1, MODBUS_RX_PIN, MODBUS_TX_PIN);
  modbus.begin(&modbusSerial, MAX485_RE);
  modbus.master();
  modbus.task();

  // Configure IR Sensor Pin
  pinMode(irSensorDigitalPin, INPUT);

  // Hardware Reset W5500 Module
  Serial.println("🔄 Initializing W5500 Hardware Reset...");
  resetW5500();

  // Initialize Custom SPI Bus with W5500 Pin Assignment
  // SPI.begin(SCK, MISO, MOSI, SS)
  SPI.begin(W5500_SCK, W5500_MISO, W5500_MOSI, W5500_CS);

  // Configure Ethernet CS pin
  Ethernet.init(W5500_CS);

  // Initialize Ethernet with Static IP configuration
  Serial.println("🔌 Connecting W5500 with Static IP Configuration...");
  Ethernet.begin(mac, local_IP, primaryDNS, gateway, subnet);

  // Check Ethernet Hardware Status
  if (Ethernet.hardwareStatus() == EthernetNoHardware) {
    Serial.println("❌ ERROR: W5500 Ethernet shield was not found! Check wiring.");
  } else if (Ethernet.linkStatus() == LinkOFF) {
    Serial.println("⚠️ WARNING: Ethernet cable is not connected (Link OFF).");
  } else {
    Serial.println("✅ Ethernet Link UP!");
  }

  Serial.print("   Station Static IP : "); Serial.println(Ethernet.localIP());
  Serial.print("   Subnet Mask       : "); Serial.println(Ethernet.subnetMask());
  Serial.print("   Gateway           : "); Serial.println(Ethernet.gatewayIP());
  Serial.print("   DNS Server        : "); Serial.println(Ethernet.dnsServerIP());
  Serial.print("   Target Destination: "); Serial.print(TARGET_HOST);
  Serial.print(" on UDP Port "); Serial.println(TARGET_UDP_PORT);

  // Start UDP Listener / Transmitter
  udpStarted = udp.begin(LOCAL_UDP_PORT);
  if (udpStarted) {
    Serial.print("✅ Ethernet UDP listening on port ");
    Serial.println(LOCAL_UDP_PORT);
  } else {
    Serial.println("❌ Failed to bind local Ethernet UDP port.");
  }

  // Clear Jumbo Display to 0.00 at boot
  writeDisplayValue(0.0);

  digitalWrite(LED_PIN, LOW); // LED OFF after successful setup
  Serial.println("🚀 System Initialized - Ready for viscosity measurement.\n");
}

// ==============================================================================
// 7. ARDUINO MAIN LOOP
// ==============================================================================
void loop() {
  // Service Modbus background tasks
  modbus.task();

  unsigned long now = millis();

  // Check Ethernet Link Status periodically
  static unsigned long lastLinkCheck = 0;
  if (now - lastLinkCheck >= 10000) {
    lastLinkCheck = now;
    if (Ethernet.linkStatus() == LinkOFF) {
      Serial.println("⚠️ [ETHERNET WARNING] Cable disconnected / Link OFF");
    }
  }

  // Periodic Heartbeat to keep dashboard ONLINE
  if (now - lastHeartbeat >= heartbeatInterval) {
    lastHeartbeat = now;
    sendHeartbeatUDP();
    Serial.println("\n--- HEARTBEAT (ETHERNET UDP PING SENT) ---");
    Serial.print("Target Host: "); Serial.print(TARGET_HOST);
    Serial.print(" | Port: "); Serial.println(TARGET_UDP_PORT);
    Serial.print("Flowing    : "); Serial.println(waterFlowing ? "YES" : "NO");
    Serial.print("Sent       : "); Serial.println(sent ? "YES" : "NO");
    Serial.println("-------------------------------------------\n");
  }

  // Once test is completed and sent, wait for next measurement cycle
  if (sent) {
    delay(10);
    return;
  }

  int irValue = digitalRead(irSensorDigitalPin);

  // IR Sensor Timing Logic (Active LOW: 0 = Flow Detected, 1 = Flow Stopped)
  if (irValue == 0) {
    if (!isSteady && (now - lastSteadyTime > debounceDelay)) {
      isSteady = true;
      waterFlowing = true;
      startTime = now;
      Serial.println("💧 Flow detected. Timer started...");
    }
    lastSteadyTime = now;
  } else {
    if (isSteady && (now - lastSteadyTime > debounceDelay)) {
      isSteady = false;
      if (waterFlowing) {
        waterFlowing = false;
        elapsedSeconds = (now - startTime) / 1000.0;
        Serial.print("🚫 Flow stopped! Measured Time: ");
        Serial.print(elapsedSeconds, 2);
        Serial.println(" seconds");

        // 1. Modbus Write to Jumbo Display immediately
        bool modbusSuccess = writeDisplayValue(elapsedSeconds);
        if (modbusSuccess) {
          Serial.println("✅ Modbus Write OK (Display Updated).");
        } else {
          Serial.println("❌ Modbus Write Failed.");
        }

        readyToSend = true;
      }
    }
  }

  // 2. Ethernet UDP Transmission Sequence
  if (readyToSend && !sent) {
    if (Ethernet.linkStatus() == LinkON || Ethernet.linkStatus() == Unknown) {
      sendUDP(elapsedSeconds);
      sent = true;
      readyToSend = false;
      Serial.println("🏁 Measurement Sequence Complete via Ethernet.");
    } else {
      static unsigned long lastWaitMsg = 0;
      if (now - lastWaitMsg > 3000) {
        Serial.println("⏳ Waiting for Ethernet Link to send data...");
        lastWaitMsg = now;
      }
    }
  }

  delay(10);
}

