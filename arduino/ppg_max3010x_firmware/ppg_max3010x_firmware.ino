#include <Wire.h>
#include "MAX30105.h"

MAX30105 sensor;

// ======================================================
// Firmware mtestv2
// MAX3010x + NTC A0/A1
//
// Comandos desde Python:
//   STATUS
//   CONFIG RED=63 IR=63 AVG=4 RATE=800 WIDTH=411 ADC=16384 SKIP=50 DEBUG=0
//   CONFIG_TEMP VCC=3.30 RFIX=10000 RN=10000 BETA=3435 OFFSET=0.0 ADCBITS=12
//   START_CONTINUOUS
//   START_TEMP
//   STOP
//
// Datos enviados:
//   micros,red,ir,tempA0C,tempA0Raw,tempA1C,tempA1Raw
// ======================================================


// ---------- MAX3010x ----------
struct SensorConfig {
  uint8_t red = 63;
  uint8_t ir = 63;
  uint8_t avg = 4;
  uint16_t rate = 800;
  uint16_t width = 411;
  uint16_t adc = 16384;
  uint16_t skip = 50;
  bool debug = false;
};

SensorConfig cfg;


// ---------- NTC TEMPERATURA ----------
const int PIN_TEMP_A0 = A0;
const int PIN_TEMP_A1 = A1;

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
  cfg.avg = validAvg(avg) ? avg : 4;
  cfg.rate = validRate(rate) ? rate : 800;
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

uint16_t leerTempRaw(int pin) {
  return analogRead(pin);
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
  uint16_t rawTempA0 = leerTempRaw(PIN_TEMP_A0);
  uint16_t rawTempA1 = leerTempRaw(PIN_TEMP_A1);
  float tempA0C = leerTemperaturaC(rawTempA0);
  float tempA1C = leerTemperaturaC(rawTempA1);

  Serial.print(micros());
  Serial.print(",");
  Serial.print(red);
  Serial.print(",");
  Serial.print(ir);
  Serial.print(",");
  if (isnan(tempA0C)) Serial.print("nan");
  else Serial.print(tempA0C, 2);
  Serial.print(",");
  Serial.print(rawTempA0);
  Serial.print(",");
  if (isnan(tempA1C)) Serial.print("nan");
  else Serial.print(tempA1C, 2);
  Serial.print(",");
  Serial.println(rawTempA1);
}

// ======================================================
// Diagnostico manual
// No se ejecuta solo. Solo responde al comando DIAGNOSTICO.
// No modifica setup(), loop(), streaming ni formato CSV.
// ======================================================

const byte MAX3010X_ADDR = 0x57;

void printHexAddress(byte address) {
  Serial.print(F("0x"));
  if (address < 16) Serial.print("0");
  Serial.print(address, HEX);
}

bool pingI2C(byte address) {
  Wire.beginTransmission(address);
  byte error = Wire.endTransmission();
  return error == 0;
}

bool scanI2CForMax3010x() {
  bool maxFound = false;
  byte count = 0;

  Serial.println(F("I2C_ESCANEO_INICIO"));

  for (byte address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    byte error = Wire.endTransmission();

    if (error == 0) {
      count++;

      Serial.print(F("I2C_ENCONTRADO "));
      printHexAddress(address);

      if (address == MAX3010X_ADDR) {
        Serial.print(F(" MAX3010X"));
        maxFound = true;
      } else {
        Serial.print(F(" OTRO"));
      }

      Serial.println();
    }
  }

  Serial.print(F("I2C_TOTAL "));
  Serial.println(count);

  if (maxFound) {
    Serial.println(F("I2C_RESULTADO OK_MAX3010X_ENCONTRADO"));
  } else {
    Serial.println(F("I2C_RESULTADO ERROR_MAX3010X_NO_ENCONTRADO"));
    Serial.println(F("I2C_ACCION Revisar VCC/GND/SDA/SCL. El MAX3010x debe aparecer en 0x57."));
  }

  Serial.println(F("I2C_ESCANEO_FIN"));

  return maxFound;
}

void diagnosticoMax3010x(bool maxFound) {
  Serial.println(F("MAX3010X_INICIO"));

  Serial.print(F("MAX3010X_DIRECCION_ESPERADA "));
  printHexAddress(MAX3010X_ADDR);
  Serial.println();

  Serial.print(F("MAX3010X_I2C "));
  Serial.println(maxFound ? F("CONECTADO") : F("NO_DETECTADO"));

  Serial.print(F("MAX3010X_SENSOR_OK "));
  Serial.println(sensorOk ? 1 : 0);

  if (!maxFound) {
    Serial.println(F("MAX3010X_RESULTADO ERROR_NO_DETECTADO"));
    Serial.println(F("MAX3010X_EXPLICACION El Arduino no ve el sensor en el bus I2C."));
    Serial.println(F("MAX3010X_ACCION Revisar cableado: VCC a 3V3, GND a GND, SDA a SDA/A4, SCL a SCL/A5."));
  }
  else if (!sensorOk) {
    Serial.println(F("MAX3010X_RESULTADO ERROR_INICIALIZACION"));
    Serial.println(F("MAX3010X_EXPLICACION El sensor aparece en I2C, pero sensor.begin() fallo al arrancar."));
    Serial.println(F("MAX3010X_ACCION Reiniciar Arduino. Si persiste, probar cables cortos o I2C_SPEED_STANDARD."));
  }
  else {
    sensor.check();

    int available = sensor.available();

    Serial.print(F("MAX3010X_FIFO_MUESTRAS "));
    Serial.println(available);

    if (available > 0) {
      uint32_t red = sensor.getRed();
      uint32_t ir = sensor.getIR();

      Serial.print(F("MAX3010X_RED "));
      Serial.println(red);

      Serial.print(F("MAX3010X_IR "));
      Serial.println(ir);

      Serial.println(F("MAX3010X_RESULTADO OK_CONECTADO_Y_LEYENDO"));
    } else {
      Serial.println(F("MAX3010X_RESULTADO OK_CONECTADO_SIN_MUESTRAS_AUN"));
      Serial.println(F("MAX3010X_ACCION Si no aparecen datos al hacer START_CONTINUOUS, revisar posicion del dedo/sensor o configuracion."));
    }
  }

  Serial.println(F("MAX3010X_FIN"));
}

void diagnosticoPinTemperatura(int pin, const __FlashStringHelper *label) {
  const byte muestras = 20;

  pinMode(pin, INPUT);
  delay(5);

  uint32_t rawSum = 0;
  uint16_t rawMin = 65535;
  uint16_t rawMax = 0;

  for (byte i = 0; i < muestras; i++) {
    uint16_t raw = leerTempRaw(pin);
    rawSum += raw;
    if (raw < rawMin) rawMin = raw;
    if (raw > rawMax) rawMax = raw;
    delay(2);
  }

  uint16_t rawAvg = rawSum / muestras;

  pinMode(pin, INPUT_PULLUP);
  delay(20);

  uint32_t rawPullupSum = 0;
  for (byte i = 0; i < muestras; i++) {
    rawPullupSum += leerTempRaw(pin);
    delay(2);
  }

  uint16_t rawPullupAvg = rawPullupSum / muestras;

  pinMode(pin, INPUT);
  delay(5);

  uint32_t adcMax = adcMaxValue();
  float tempC = leerTemperaturaC(rawAvg);
  uint16_t variacion = rawMax - rawMin;
  uint16_t diferenciaPullup = rawPullupAvg > rawAvg ? rawPullupAvg - rawAvg : rawAvg - rawPullupAvg;

  Serial.print(F("TEMP_SENSOR "));
  Serial.println(label);

  Serial.print(F("TEMP_RAW_AVG "));
  Serial.print(rawAvg);
  Serial.print(F("/"));
  Serial.println(adcMax);

  Serial.print(F("TEMP_RAW_MIN "));
  Serial.println(rawMin);

  Serial.print(F("TEMP_RAW_MAX "));
  Serial.println(rawMax);

  Serial.print(F("TEMP_RAW_PULLUP "));
  Serial.println(rawPullupAvg);

  Serial.print(F("TEMP_DIF_PULLUP "));
  Serial.println(diferenciaPullup);

  Serial.print(F("TEMP_C "));
  if (isnan(tempC)) Serial.println(F("nan"));
  else Serial.println(tempC, 2);

  if (diferenciaPullup > adcMax / 4) {
    Serial.print(F("TEMP_RESULTADO ERROR_FLOTANTE "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION Cambia mucho al activar pullup interno. Probablemente no hay divisor NTC-resistencia conectado."));
  }
  else if (rawAvg <= 5) {
    Serial.print(F("TEMP_RESULTADO ERROR_CASI_0V "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION La entrada esta casi a GND."));
  }
  else if (rawAvg >= adcMax - 5) {
    Serial.print(F("TEMP_RESULTADO ERROR_CASI_VCC "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION La entrada esta casi a VCC."));
  }
  else if (variacion > adcMax / 10) {
    Serial.print(F("TEMP_RESULTADO AVISO_INESTABLE "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION La lectura varia demasiado. Puede haber mala conexion o pin flotando."));
  }
  else if (isnan(tempC)) {
    Serial.print(F("TEMP_RESULTADO ERROR_CALCULO_NAN "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION El calculo de temperatura no es valido."));
  }
  else if (tempC < -10.0 || tempC > 80.0) {
    Serial.print(F("TEMP_RESULTADO AVISO_TEMPERATURA_RARA "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION La temperatura calculada esta fuera de rango razonable para una prueba normal."));
  }
  else {
    Serial.print(F("TEMP_RESULTADO OK_PROBABLEMENTE_CONECTADA "));
    Serial.println(label);
    Serial.println(F("TEMP_EXPLICACION La lectura parece estable y no reacciona como un pin flotante."));
  }
}

void diagnosticoTemperatura() {
  Serial.println(F("TEMP_INICIO"));

  diagnosticoPinTemperatura(PIN_TEMP_A0, F("NTC_A0"));
  diagnosticoPinTemperatura(PIN_TEMP_A1, F("NTC_A1"));
  Serial.println(F("TEMP_NOTA Para deteccion fiable, cada entrada debe tener su divisor NTC-resistencia: 3.3V -> NTC -> Ax -> resistencia fija 10k -> GND."));
  Serial.println(F("TEMP_FIN"));
  return;

  const byte muestras = 20;

  // Lectura normal
  pinMode(PIN_TEMP_A0, INPUT);
  delay(5);

  uint32_t rawSum = 0;
  uint16_t rawMin = 65535;
  uint16_t rawMax = 0;

  for (byte i = 0; i < muestras; i++) {
    uint16_t raw = leerTempRaw(PIN_TEMP_A0);

    rawSum += raw;

    if (raw < rawMin) rawMin = raw;
    if (raw > rawMax) rawMax = raw;

    delay(2);
  }

  uint16_t rawAvg = rawSum / muestras;

  // Prueba con pullup interno para detectar pin flotante.
  // Si A0 no esta conectado a nada, al activar pullup suele subir mucho.
  pinMode(PIN_TEMP_A0, INPUT_PULLUP);
  delay(20);

  uint32_t rawPullupSum = 0;

  for (byte i = 0; i < muestras; i++) {
    rawPullupSum += leerTempRaw(PIN_TEMP_A0);
    delay(2);
  }

  uint16_t rawPullupAvg = rawPullupSum / muestras;

  // Volvemos a dejarlo como entrada normal para no afectar al resto del programa.
  pinMode(PIN_TEMP_A0, INPUT);
  delay(5);

  uint32_t adcMax = adcMaxValue();
  float tempC = leerTemperaturaC(rawAvg);

  uint16_t variacion = rawMax - rawMin;
  uint16_t diferenciaPullup;

  if (rawPullupAvg > rawAvg) diferenciaPullup = rawPullupAvg - rawAvg;
  else diferenciaPullup = rawAvg - rawPullupAvg;

  Serial.println(F("TEMP_SENSOR NTC_A0"));

  Serial.print(F("TEMP_RAW_AVG "));
  Serial.print(rawAvg);
  Serial.print(F("/"));
  Serial.println(adcMax);

  Serial.print(F("TEMP_RAW_MIN "));
  Serial.println(rawMin);

  Serial.print(F("TEMP_RAW_MAX "));
  Serial.println(rawMax);

  Serial.print(F("TEMP_RAW_PULLUP "));
  Serial.println(rawPullupAvg);

  Serial.print(F("TEMP_DIF_PULLUP "));
  Serial.println(diferenciaPullup);

  Serial.print(F("TEMP_C "));
  if (isnan(tempC)) Serial.println(F("nan"));
  else Serial.println(tempC, 2);

  if (diferenciaPullup > adcMax / 4) {
    Serial.println(F("TEMP_RESULTADO ERROR_A0_FLOTANTE"));
    Serial.println(F("TEMP_EXPLICACION A0 cambia mucho al activar pullup interno. Probablemente no hay divisor NTC-resistencia conectado."));
    Serial.println(F("TEMP_ACCION Conectar: 3.3V -> NTC -> A0 -> resistencia fija 10k -> GND."));
  }
  else if (rawAvg <= 5) {
    Serial.println(F("TEMP_RESULTADO ERROR_A0_CASI_0V"));
    Serial.println(F("TEMP_EXPLICACION A0 esta casi a GND."));
    Serial.println(F("TEMP_ACCION Puede faltar la NTC superior o A0 puede estar unido a GND por la resistencia fija."));
  }
  else if (rawAvg >= adcMax - 5) {
    Serial.println(F("TEMP_RESULTADO ERROR_A0_CASI_VCC"));
    Serial.println(F("TEMP_EXPLICACION A0 esta casi a VCC."));
    Serial.println(F("TEMP_ACCION Puede faltar la resistencia fija a GND o estar mal montado el divisor."));
  }
  else if (variacion > adcMax / 10) {
    Serial.println(F("TEMP_RESULTADO AVISO_A0_INESTABLE"));
    Serial.println(F("TEMP_EXPLICACION La lectura varia demasiado. Puede haber mala conexion o A0 flotando."));
    Serial.println(F("TEMP_ACCION Revisar cables del divisor NTC-resistencia."));
  }
  else if (isnan(tempC)) {
    Serial.println(F("TEMP_RESULTADO ERROR_CALCULO_NAN"));
    Serial.println(F("TEMP_EXPLICACION El calculo de temperatura no es valido."));
    Serial.println(F("TEMP_ACCION Revisar configuracion VCC/RFIX/RN/BETA/ADCBITS."));
  }
  else if (tempC < -10.0 || tempC > 80.0) {
    Serial.println(F("TEMP_RESULTADO AVISO_TEMPERATURA_RARA"));
    Serial.println(F("TEMP_EXPLICACION La temperatura calculada esta fuera de rango razonable para una prueba normal."));
    Serial.println(F("TEMP_ACCION Revisar montaje: 3.3V -> NTC -> A0 -> resistencia fija -> GND."));
  }
  else {
    Serial.println(F("TEMP_RESULTADO OK_PROBABLEMENTE_CONECTADA"));
    Serial.println(F("TEMP_EXPLICACION La lectura parece estable y no reacciona como un pin flotante."));
  }

  Serial.println(F("TEMP_NOTA Para deteccion fiable, debe existir siempre una resistencia fija entre A0 y GND."));
  Serial.println(F("TEMP_FIN"));
}

void diagnosticoEstado() {
  Serial.println(F("ESTADO_INICIO"));

  Serial.print(F("STREAMING "));
  Serial.println(streaming ? 1 : 0);

  Serial.print(F("TEMP_ONLY_MODE "));
  Serial.println(tempOnlyMode ? 1 : 0);

  Serial.print(F("SKIP_REMAINING "));
  Serial.println(skipRemaining);

  Serial.println(F("ESTADO_FIN"));
}

void diagnosticoCompleto() {
  Serial.println(F("DIAGNOSTICO_INICIO"));

  bool maxFound = scanI2CForMax3010x();

  diagnosticoMax3010x(maxFound);
  diagnosticoTemperatura();
  diagnosticoEstado();

  printSensorConfig();
  printTempConfig();

  Serial.println(F("DIAGNOSTICO_FIN"));
}



// ======================================================
// Comandos
// ======================================================

void handleCommand(const String &cmd) {
  if (cmd.length() == 0) return;

  if (cmd == "DIAGNOSTICO") {
    diagnosticoCompleto();
    return;
  }

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
