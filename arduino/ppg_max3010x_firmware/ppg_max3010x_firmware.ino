#include <Wire.h>
#include "MAX30105.h"

MAX30105 sensor;

// ======================================================
// Firmware mtestv2
// MAX3010x + NTC A0
//
// Comandos desde Python:
//   STATUS
//   CONFIG RED=31 IR=31 AVG=1 RATE=100 WIDTH=411 ADC=16384 SKIP=10 DEBUG=0
//   CONFIG_TEMP VCC=3.30 RFIX=10000 RN=10000 BETA=3435 OFFSET=0.0 ADCBITS=12
//   START_CONTINUOUS
//   START_TEMP
//   STOP
//
// Datos enviados:
//   micros,red,ir,tempC,tempRaw
// ======================================================


// ---------- MAX3010x ----------
struct SensorConfig {
  uint8_t red = 31;
  uint8_t ir = 31;
  uint8_t avg = 1;
  uint16_t rate = 100;
  uint16_t width = 411;
  uint16_t adc = 16384;
  uint16_t skip = 10;
  bool debug = false;
};

SensorConfig cfg;


// ---------- NTC TEMPERATURA ----------
const int PIN_TEMP = A0;

// Montaje asumido:
// 3.3V ---- NTC ---- A0 ---- R_FIJA ---- GND
struct TempConfig {
  float vcc = 3.30;
  float rFixed = 10000.0;
  float rNominal = 10000.0;
  float tNominalK = 298.15;  // 25 ºC
  float beta = 3435.0;
  float offsetC = 0.0;

#if defined(ARDUINO_ARCH_AVR)
  uint8_t adcBits = 10;
#else
  uint8_t adcBits = 12;
#endif
};

TempConfig tempCfg;


// ---------- ESTADO ----------
bool sensorOk = false;
bool streaming = false;
bool tempOnlyMode = false;
uint16_t skipRemaining = 0;
String rxLine = "";

unsigned long lastTempOnlyMs = 0;
const unsigned long TEMP_ONLY_PERIOD_MS = 100;


// ======================================================
// Validaciones MAX3010x
// ======================================================

bool validAvg(uint16_t v) {
  return v == 1 || v == 2 || v == 4 || v == 8 || v == 16 || v == 32;
}

bool validRate(uint16_t v) {
  return v == 50 || v == 100 || v == 200 || v == 400 ||
         v == 800 || v == 1000 || v == 1600 || v == 3200;
}

bool validWidth(uint16_t v) {
  return v == 69 || v == 118 || v == 215 || v == 411;
}

bool validAdc(uint16_t v) {
  return v == 2048 || v == 4096 || v == 8192 || v == 16384;
}


// ======================================================
// Helpers de lectura de comandos
// ======================================================

int readIntAfterKey(const String &line, const String &key, int fallback) {
  int pos = line.indexOf(key + "=");
  if (pos < 0) return fallback;

  pos += key.length() + 1;
  int end = line.indexOf(' ', pos);
  if (end < 0) end = line.length();

  return line.substring(pos, end).toInt();
}

float readFloatAfterKey(const String &line, const String &key, float fallback) {
  int pos = line.indexOf(key + "=");
  if (pos < 0) return fallback;

  pos += key.length() + 1;
  int end = line.indexOf(' ', pos);
  if (end < 0) end = line.length();

  return line.substring(pos, end).toFloat();
}


// ======================================================
// Configuración sensor
// ======================================================

void printSensorConfig() {
  Serial.print(F("CFG RED=")); Serial.print(cfg.red);
  Serial.print(F(" IR=")); Serial.print(cfg.ir);
  Serial.print(F(" AVG=")); Serial.print(cfg.avg);
  Serial.print(F(" RATE=")); Serial.print(cfg.rate);
  Serial.print(F(" WIDTH=")); Serial.print(cfg.width);
  Serial.print(F(" ADC=")); Serial.print(cfg.adc);
  Serial.print(F(" SKIP=")); Serial.print(cfg.skip);
  Serial.print(F(" DEBUG=")); Serial.println(cfg.debug ? 1 : 0);
}

void printTempConfig() {
  Serial.print(F("TEMP_CFG VCC=")); Serial.print(tempCfg.vcc, 4);
  Serial.print(F(" RFIX=")); Serial.print(tempCfg.rFixed, 1);
  Serial.print(F(" RN=")); Serial.print(tempCfg.rNominal, 1);
  Serial.print(F(" BETA=")); Serial.print(tempCfg.beta, 1);
  Serial.print(F(" OFFSET=")); Serial.print(tempCfg.offsetC, 3);
  Serial.print(F(" ADCBITS=")); Serial.println(tempCfg.adcBits);
}

void applySensorConfig() {
  if (!sensorOk) return;

  // ledMode = 2: RED + IR, necesario para SpO2 estimada.
  const byte ledMode = 2;

  sensor.setup(cfg.ir, cfg.avg, ledMode, cfg.rate, cfg.width, cfg.adc);
  sensor.setPulseAmplitudeRed(cfg.red);
  sensor.setPulseAmplitudeIR(cfg.ir);
  sensor.setPulseAmplitudeGreen(0);
  sensor.clearFIFO();
}

void applyTempConfig() {
#if defined(ARDUINO_ARCH_SAMD) || defined(ARDUINO_ARCH_ESP32) || defined(ARDUINO_ARCH_MBED)
  analogReadResolution(tempCfg.adcBits);
#endif
}

void handleConfig(const String &line) {
  int red = readIntAfterKey(line, "RED", cfg.red);
  int ir = readIntAfterKey(line, "IR", cfg.ir);
  int avg = readIntAfterKey(line, "AVG", cfg.avg);
  int rate = readIntAfterKey(line, "RATE", cfg.rate);
  int width = readIntAfterKey(line, "WIDTH", cfg.width);
  int adc = readIntAfterKey(line, "ADC", cfg.adc);
  int skip = readIntAfterKey(line, "SKIP", cfg.skip);
  int debug = readIntAfterKey(line, "DEBUG", cfg.debug ? 1 : 0);

  cfg.red = constrain(red, 0, 255);
  cfg.ir = constrain(ir, 0, 255);
  cfg.avg = validAvg(avg) ? avg : 1;
  cfg.rate = validRate(rate) ? rate : 100;
  cfg.width = validWidth(width) ? width : 411;
  cfg.adc = validAdc(adc) ? adc : 16384;
  cfg.skip = constrain(skip, 0, 200);
  cfg.debug = debug != 0;

  applySensorConfig();

  Serial.println(F("OK_CONFIG"));
  printSensorConfig();
}

void handleTempConfig(const String &line) {
  tempCfg.vcc = readFloatAfterKey(line, "VCC", tempCfg.vcc);
  tempCfg.rFixed = readFloatAfterKey(line, "RFIX", tempCfg.rFixed);
  tempCfg.rNominal = readFloatAfterKey(line, "RN", tempCfg.rNominal);
  tempCfg.beta = readFloatAfterKey(line, "BETA", tempCfg.beta);
  tempCfg.offsetC = readFloatAfterKey(line, "OFFSET", tempCfg.offsetC);
  tempCfg.adcBits = constrain(readIntAfterKey(line, "ADCBITS", tempCfg.adcBits), 8, 16);

  if (tempCfg.vcc < 1.0) tempCfg.vcc = 3.30;
  if (tempCfg.rFixed < 100.0) tempCfg.rFixed = 10000.0;
  if (tempCfg.rNominal < 100.0) tempCfg.rNominal = 10000.0;
  if (tempCfg.beta < 1000.0) tempCfg.beta = 3435.0;

  applyTempConfig();

  Serial.println(F("OK_CONFIG_TEMP"));
  printTempConfig();
}


// ======================================================
// Temperatura
// ======================================================

uint32_t adcMaxValue() {
  if (tempCfg.adcBits >= 31) return 2147483647UL;
  return (1UL << tempCfg.adcBits) - 1UL;
}

uint16_t leerTempRaw() {
  return analogRead(PIN_TEMP);
}

float leerTemperaturaC(uint16_t raw) {
  float adcMax = (float)adcMaxValue();
  if (adcMax <= 0) adcMax = 4095.0;

  float vOut = ((float)raw * tempCfg.vcc) / adcMax;

  if (vOut >= tempCfg.vcc - 0.001) vOut = tempCfg.vcc - 0.001;
  if (vOut <= 0.001) vOut = 0.001;

  // Montaje:
  // 3.3V ---- NTC ---- A0 ---- R_FIJA ---- GND
  float rTerm = tempCfg.rFixed * ((tempCfg.vcc / vOut) - 1.0);

  if (rTerm <= 0.0) return NAN;

  float logR = log(rTerm / tempCfg.rNominal);
  float tempC = (1.0 / ((1.0 / tempCfg.tNominalK) + (logR / tempCfg.beta))) - 273.15;

  return tempC + tempCfg.offsetC;
}

void printDataLine(uint32_t red, uint32_t ir) {
  uint16_t rawTemp = leerTempRaw();
  float tempC = leerTemperaturaC(rawTemp);

  Serial.print(micros());
  Serial.print(",");
  Serial.print(red);
  Serial.print(",");
  Serial.print(ir);
  Serial.print(",");
  if (isnan(tempC)) Serial.print("nan");
  else Serial.print(tempC, 2);
  Serial.print(",");
  Serial.println(rawTemp);
}


// ======================================================
// Comandos
// ======================================================

void handleCommand(const String &cmd) {
  if (cmd.length() == 0) return;

  if (cmd == "STATUS") {
    Serial.println(sensorOk ? F("STATUS SENSOR_OK") : F("STATUS SENSOR_ERROR"));
    printSensorConfig();
    printTempConfig();
    return;
  }

  if (cmd.startsWith("CONFIG_TEMP")) {
    handleTempConfig(cmd);
    return;
  }

  if (cmd.startsWith("CONFIG")) {
    handleConfig(cmd);
    return;
  }

  if (cmd == "START_CONTINUOUS" || cmd == "START") {
    if (!sensorOk) {
      Serial.println(F("ERR_SENSOR_NOT_READY"));
      return;
    }

    tempOnlyMode = false;
    streaming = true;
    skipRemaining = cfg.skip;
    sensor.clearFIFO();

    Serial.println(F("OK_START_CONTINUOUS"));
    return;
  }

  if (cmd == "START_TEMP") {
    tempOnlyMode = true;
    streaming = true;
    lastTempOnlyMs = 0;

    Serial.println(F("OK_START_TEMP"));
    return;
  }

  if (cmd == "STOP") {
    streaming = false;
    tempOnlyMode = false;
    skipRemaining = 0;

    if (sensorOk) sensor.clearFIFO();

    Serial.println(F("OK_STOP"));
    return;
  }

  Serial.print(F("WARN_UNKNOWN_CMD "));
  Serial.println(cmd);
}


// ======================================================
// Setup / Loop
// ======================================================

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 5000);

  applyTempConfig();

  Wire.begin();
  delay(50);

  if (!sensor.begin(Wire, I2C_SPEED_FAST)) {
    sensorOk = false;
    Serial.println(F("ERROR_SENSOR"));
  } else {
    sensorOk = true;
    applySensorConfig();
  }

  Serial.println(F("READY"));
  printSensorConfig();
  printTempConfig();
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      rxLine.trim();
      if (rxLine.length() > 0) {
        handleCommand(rxLine);
      }
      rxLine = "";
    } else {
      rxLine += c;
      if (rxLine.length() > 180) rxLine = "";
    }
  }

  if (!streaming) return;

  if (tempOnlyMode) {
    unsigned long nowMs = millis();

    if (nowMs - lastTempOnlyMs >= TEMP_ONLY_PERIOD_MS) {
      lastTempOnlyMs = nowMs;
      printDataLine(0, 0);
    }

    return;
  }

  if (!sensorOk) return;

  sensor.check();

  while (sensor.available()) {
    uint32_t red = sensor.getRed();
    uint32_t ir = sensor.getIR();

    sensor.nextSample();

    if (skipRemaining > 0) {
      skipRemaining--;
      continue;
    }

    printDataLine(red, ir);
  }
}