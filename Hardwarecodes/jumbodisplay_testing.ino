#include <ModbusRTU.h>

// === MAX485 Pins ===
#define MAX485_DE 4
#define MAX485_RE 4

// === Modbus Configuration ===
HardwareSerial modbusSerial(2);
ModbusRTU modbus;

const uint8_t  SLAVE_ID    = 1;
const uint16_t REG_ADDRESS = 1; // writing 2 registers

// === Timing ===
unsigned long lastSendTime = 0;
const unsigned long interval = 5000; // 5 sec

// === Convert float to Modbus registers ===
void floatToModbusRegisters(float value, uint16_t *registers) {
  uint8_t bytes[4];
  memcpy(bytes, &value, sizeof(float));

  registers[0] = (bytes[1] << 8) | bytes[0];
  registers[1] = (bytes[3] << 8) | bytes[2];
}

void setup() {
  Serial.begin(115200);

  // RS485 control
  pinMode(MAX485_DE, OUTPUT);
  pinMode(MAX485_RE, OUTPUT);
  digitalWrite(MAX485_RE, LOW);
  digitalWrite(MAX485_DE, LOW);

  // UART2 → RX=32, TX=33
  modbusSerial.begin(9600, SERIAL_8N1, 32, 33);
  modbus.begin(&modbusSerial, MAX485_RE);
  modbus.master();

  Serial.println("🚀 System Ready");
}

void loop() {
  modbus.task();

  if (millis() - lastSendTime >= interval) {
    lastSendTime = millis();

    // Generate random float (10.0 to 20.0)
    float randomValue = random(100, 200) / 10.0;

    // Convert to 2 Modbus registers
    uint16_t registers[2];
    floatToModbusRegisters(randomValue, registers);

    // Write to Modbus
    digitalWrite(MAX485_RE, HIGH);
    digitalWrite(MAX485_DE, HIGH);
    bool success = modbus.writeHreg(SLAVE_ID, REG_ADDRESS, registers, 2);
    digitalWrite(MAX485_RE, LOW);
    digitalWrite(MAX485_DE, LOW);

    if (success) {
      Serial.print("📦 Sent Random Float: ");
      Serial.println(randomValue, 2);
    } else {
      Serial.println("❌ Modbus write failed");
    }
  }
}
