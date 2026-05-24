#include <Wire.h>
#include "MAX30105.h"

MAX30105 sensor;

// Firmware compatible con la app Python mtest_modular.
// Protocolo esperado por Python:
//   STATUS
//   CONFIG RED=31 IR=31 AVG=1 RATE=100 WIDTH=411 ADC=16384 SKIP=10 DEBUG=0
//   START_CONTINUOUS
//   STOP
// Datos enviados durante streaming:
//   micros,red,ir

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
bool sensorOk = false;
bool streaming = false;
uint16_t skipRemaining = 0;
String rxLine = "";

bool validAvg(uint16_t v) {
  return v == 1 || v == 2 || v == 4 || v == 8 || v == 16 || v == 32;
}

bool validRate(uint16_t v) {
  return v == 50 || v == 100 || v == 200 || v == 400 || v == 800 || v == 1000 || v == 1600 || v == 3200;
}

bool validWidth(uint16_t v) {
  return v == 69 || v == 118 || v == 215 || v == 411;
}

bool validAdc(uint16_t v) {
  return v == 2048 || v == 4096 || v == 8192 || v == 16384;
}

void printConfig() {
  Serial.print(F("CFG RED=")); Serial.print(cfg.red);
  Serial.print(F(" IR=")); Serial.print(cfg.ir);
  Serial.print(F(" AVG=")); Serial.print(cfg.avg);
  Serial.print(F(" RATE=")); Serial.print(cfg.rate);
  Serial.print(F(" WIDTH=")); Serial.print(cfg.width);
  Serial.print(F(" ADC=")); Serial.print(cfg.adc);
  Serial.print(F(" SKIP=")); Serial.print(cfg.skip);
  Serial.print(F(" DEBUG=")); Serial.println(cfg.debug ? 1 : 0);
}

void applySensorConfig() {
  if (!sensorOk) return;

  // ledMode = 2: RED + IR. Necesario para SpO2 estimada.
  const byte ledMode = 2;
  sensor.setup(cfg.ir, cfg.avg, ledMode, cfg.rate, cfg.width, cfg.adc);
  sensor.setPulseAmplitudeRed(cfg.red);
  sensor.setPulseAmplitudeIR(cfg.ir);
  sensor.setPulseAmplitudeGreen(0);
  sensor.clearFIFO();
}

int readValueAfterKey(const String &line, const String &key, int fallback) {
  int pos = line.indexOf(key + "=");
  if (pos < 0) return fallback;
  pos += key.length() + 1;
  int end = line.indexOf(' ', pos);
  if (end < 0) end = line.length();
  return line.substring(pos, end).toInt();
}

void handleConfig(const String &line) {
  int red = readValueAfterKey(line, "RED", cfg.red);
  int ir = readValueAfterKey(line, "IR", cfg.ir);
  int avg = readValueAfterKey(line, "AVG", cfg.avg);
  int rate = readValueAfterKey(line, "RATE", cfg.rate);
  int width = readValueAfterKey(line, "WIDTH", cfg.width);
  int adc = readValueAfterKey(line, "ADC", cfg.adc);
  int skip = readValueAfterKey(line, "SKIP", cfg.skip);
  int debug = readValueAfterKey(line, "DEBUG", cfg.debug ? 1 : 0);

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
  printConfig();
}

void handleCommand(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line == "STATUS") {
    if (sensorOk) Serial.println(F("READY"));
    else Serial.println(F("ERR_SENSOR_NOT_FOUND"));
    printConfig();
    return;
  }

  if (line.startsWith("CONFIG")) {
    handleConfig(line);
    return;
  }

  if (line == "START_CONTINUOUS") {
    if (!sensorOk) {
      Serial.println(F("ERR_SENSOR_NOT_FOUND"));
      return;
    }
    sensor.clearFIFO();
    skipRemaining = cfg.skip;
    streaming = true;
    Serial.println(F("OK_START_CONTINUOUS"));
    return;
  }

  if (line == "STOP") {
    streaming = false;
    Serial.println(F("OK_STOP"));
    return;
  }

  Serial.print(F("WARN_UNKNOWN_CMD "));
  Serial.println(line);
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLine.length() > 0) {
        handleCommand(rxLine);
        rxLine = "";
      }
    } else {
      if (rxLine.length() < 160) rxLine += c;
    }
  }
}

void streamSamples() {
  if (!streaming || !sensorOk) return;

  sensor.check();
  while (sensor.available()) {
    uint32_t red = sensor.getRed();
    uint32_t ir = sensor.getIR();
    sensor.nextSample();

    if (skipRemaining > 0) {
      skipRemaining--;
      continue;
    }

    Serial.print(micros());
    Serial.print(',');
    Serial.print(red);
    Serial.print(',');
    Serial.println(ir);
  }
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  delay(200);

  sensorOk = sensor.begin(Wire, I2C_SPEED_FAST);
  if (!sensorOk) {
    Serial.println(F("ERR_SENSOR_NOT_FOUND"));
    return;
  }

  applySensorConfig();
  Serial.println(F("READY"));
  printConfig();
}

void loop() {
  readSerialCommands();
  streamSamples();
}
