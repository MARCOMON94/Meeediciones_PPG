# mtestv2

Aplicacion de escritorio para capturar, analizar y documentar senales PPG con sensor MAX3010x, lectura de temperatura NTC, firmware Arduino y una interfaz PyQt6 pensada para trabajo de campo.

El proyecto combina adquisicion en tiempo real, configuracion del sensor, analisis de pulso, generacion de informes y empaquetado portable para Windows. Esta preparado para publicarse como portfolio sin incluir resultados experimentales ni datos de medicion.

## Que hace

- Captura senales RED/IR desde un MAX3010x conectado por Arduino.
- Registra temperatura NTC junto a cada medicion con termometros A0-A3.
- Permite seleccionar especie/animal: oveja, cabra o vaca.
- En oveja/cabra usa sensores izquierda/derecha; en vaca usa FLT, FRT, RLT y RRT.
- La temperatura final se guarda como el maximo del primer golpe de calor, no como media.
- Permite modos de medicion rapida, test de campo, reajustes, solo temperatura y bloques de configuracion.
- Calcula metricas de calidad, BPM, SpO2 estimada, perfusion, artefactos y saturacion.
- Incluye analisis experimental con Fourier, autocorrelacion e Hilbert para comparar configuraciones.
- Genera CSV, JSON, graficas, capturas y PDFs de informe.
- Estadisticas permite revisar sesiones, marcar raws de varias sesiones y preparar un ZIP en el Escritorio para correo.
- Puede empaquetarse como `.exe` portable para uso en Windows sin entorno de desarrollo visible.

## Tecnologias

- Python
- PyQt6
- pyqtgraph
- NumPy
- pyserial
- bleak para conexion BLE con Arduino Nano 33 IoT
- sounddevice
- Arduino / C++
- PyInstaller para el ejecutable portable

## Estructura del proyecto

```txt
main.py                         # punto de entrada
controller.py                   # controlador de ventanas
requirements.txt                # dependencias Python
ARRANCAR_PPG.cmd                # lanzador principal en Windows
instalarmtestv2.cmd             # instalacion inicial
actualizaciones/                # notas visibles desde la interfaz
arduino/
  ppg_max3010x_firmware/
    ppg_max3010x_firmware.ino   # firmware para Arduino
ppg_suite/
  paths.py                      # rutas, resultados y logging
  utils.py                      # utilidades generales
  models.py                     # dataclasses de configuracion y metricas
  processing.py                 # BPM, FFT, autocorrelacion, SpO2 y artefactos
  widgets.py                    # widgets reutilizables
  menu.py                       # menu inicial
  windows/
    measurement_window.py       # base de campo/test
    real_window.py              # medicion de campo
    test_window.py              # test de campo
    temperature_window.py       # solo temperatura
    reajustes_window.py         # medicion larga y diagnostico
    scheduled_window.py         # configuraciones y experimento 3M
    relations_window.py         # estadisticas y explorador de sesiones
    fourier_window.py           # analisis Fourier + Hilbert
preparar_ejecutable/
  BUILD_EXE.cmd                 # build del ejecutable portable
  source/                       # copia adaptada para PyInstaller
```

## Instalacion de desarrollo

Requisitos:

- Windows
- Python 3
- Git, opcional para actualizacion automatica
- Arduino con el firmware del proyecto cargado

Desde la raiz del repositorio:

```bat
instalarmtestv2.cmd
```

El instalador crea el entorno virtual, instala dependencias y prepara un archivo `.env` local si no existe.

Ejemplo de `.env`:

```txt
PROJECT_DIR=C:\ruta\a\mtestv2
PYTHON_REL=.venv\Scripts\python.exe
MAIN_FILE=main.py
AUTO_UPDATE_GIT=1
```

## Arranque

La forma recomendada en Windows es:

```bat
ARRANCAR_PPG.cmd
```

El lanzador:

- Lee la configuracion local desde `.env`.
- Entra en `PROJECT_DIR`.
- Intenta actualizar con `git pull --ff-only` si `AUTO_UPDATE_GIT=1`.
- Comprueba dependencias.
- Ejecuta `main.py`.

Tambien se puede lanzar manualmente:

```bat
.venv\Scripts\python.exe main.py
```

## Modos principales

- Medicion de campo: toma rapida con la interfaz minima.
- Test de campo: captura con notas, parametros y graficas diagnosticas.
- Solo temperatura: registro NTC sin PPG.
- Reajustes: medicion larga con controles completos, diagnostico Arduino y snapshots.
- Configuraciones: ejecucion de bloques de parametros del sensor.
- Experimento 3M: busqueda adaptativa de una configuracion util del sensor con ranking final.
- Estadisticas: explorador de sesiones, raws, procesados, informes, graficas, capturas y preparacion de ZIP para correo.
- Analisis Fourier + Hilbert: comparacion de raws para estudiar que configuracion separa mejor el pulso.

## Datos generados

Durante el uso normal, la aplicacion escribe resultados en:

```txt
resultados/
```

Esa carpeta esta ignorada por Git porque puede contener datos experimentales, capturas, informes y archivos pesados. Si se clona el proyecto desde cero, la carpeta se crea durante la ejecucion cuando sea necesaria.

Subcarpetas habituales:

- `raw/`: datos crudos.
- `processed/`: datos procesados.
- `sessions/`: resumenes de sesiones.
- `reports/`: JSON y tablas derivadas.
- `documentos_generados/`: informes PDF.
- `figures/`: graficas.
- `screenshots/`: capturas.
- `configs/`: configuraciones aplicadas y `animal_profiles.json` con configuraciones MAX3010x predefinidas por especie.
- `logs/`: logs de ejecucion.

## Animales, sensores y temperatura

Las pantallas de recogida permiten elegir `Oveja`, `Cabra` o `Vaca`.

- Oveja y cabra usan posiciones `RT` y `LT`.
- Vaca usa posiciones `FLT`, `FRT`, `RLT` y `RRT`.
- Los canales fisicos son siempre `A0`, `A1`, `A2` y `A3`.
- Por defecto, oveja/cabra usan `A0 derecha / A1 izquierda`.
- Por defecto, vaca usa `A0 FRT / A1 FLT / A2 RRT / A3 RLT`.
- La asignacion puede cambiarse desde la app antes de capturar.
- Oveja, cabra y vaca pueden guardar una configuracion MAX3010x predefinida por especie. El boton aparece bajo el bloque del sensor en las pantallas de toma normal, temperatura y reajustes; en Configuraciones/3M el bloque MAX3010x es solo orientativo porque cambia por fila.
- Si ya existe una configuracion previa para esa especie, la app muestra la configuracion antigua y pide confirmacion antes de sustituirla.
- El boton `Mostrar resultados` de las pantallas de recogida cierra la ventana actual y abre `Estadisticas`.
- Las recogidas guardan anotaciones de inicio y anotaciones finales. En Configuraciones/3M, cada cambio de configuracion permite anotar lo ocurrido en el tramo que acaba de terminar.

La temperatura final de sesiones y Estadisticas es el maximo independiente de cada termometro/posicion durante el primer golpe de calor. La app ignora el primer segundo de estabilizacion y busca el maximo en los 5 segundos siguientes; las medias antiguas se mantienen solo como referencia tecnica.

## Firmware Arduino

El firmware esta en:

```txt
arduino/ppg_max3010x_firmware/ppg_max3010x_firmware.ino
```

Configuracion recomendada para Arduino Nano 33 IoT:

- Placa en Arduino IDE: `Arduino SAMD Boards` -> `Arduino Nano 33 IoT`.
- Librerias Arduino desde `Sketch` -> `Include Library` -> `Manage Libraries...`:
  - `ArduinoBLE` by Arduino, necesaria para `ArduinoBLE.h` y Bluetooth BLE.
  - `SparkFun MAX3010x Pulse and Proximity Sensor Library`, necesaria para `MAX30105.h`.
- Conexiones: MAX3010x por I2C en `SDA/A4` y `SCL/A5`.
- Termometros NTC: `A0`, `A1`, `A2`, `A3`. Cada entrada debe tener divisor `3.3V -> NTC -> Ax -> resistencia fija 10k -> GND`.
- Para oveja/cabra basta usar A0/A1. Para vaca se pueden usar los cuatro termometros y asignarlos a `FLT/FRT/RLT/RRT`.
- ADC de temperatura: `12 bits`, ya configurado por defecto en placas SAMD como Nano 33 IoT.
- Bluetooth: el Nano 33 IoT tiene BLE mediante el modulo u-blox NINA-W102. Este firmware activa un servicio BLE `mtestv2 Nano33IoT` cuando compila como Nano 33 IoT. La app puede conectarse desde el selector de puertos con `BLE Nano 33 IoT mtestv2`.
- BLE no aparece como puerto COM clasico. La app usa `bleak` y caracteristicas GATT propias para enviar comandos y recibir lineas. Por limite de ancho de banda BLE, USB Serial sigue siendo la opcion con mas muestras por segundo; por BLE se notifican datos a ritmo limitado para mantener estabilidad.

Formato de datos actual del firmware:

```txt
micros,red,ir,tempA0C,tempA0Raw,tempA1C,tempA1Raw,tempA2C,tempA2Raw,tempA3C,tempA3Raw
```

La app sigue aceptando raws y firmware antiguos con solo A0/A1.

La aplicacion se comunica por serie mediante comandos de texto como:

- `STATUS`
- `CONFIG RED=... IR=... AVG=... RATE=... WIDTH=... ADC=... SKIP=... DEBUG=...`
- `CONFIG_TEMP VCC=... RFIX=... RN=... BETA=... OFFSET=... ADCBITS=...`
- `START_CONTINUOUS`
- `START_TEMP`
- `STOP`
- `DIAGNOSTICO`

Antes de iniciar una toma, el software verifica que la configuracion confirmada por Arduino coincide con la solicitada.

## Ejecutable portable

Para generar una version portable:

```bat
preparar_ejecutable\BUILD_EXE.cmd
```

El script prepara un entorno temporal, instala dependencias de build, usa PyInstaller y genera el ejecutable en:

```txt
preparar_ejecutable\source\dist\mtestv2.exe
```

En la version portable, los resultados se guardan fuera del repositorio:

```txt
%USERPROFILE%\Documents\mtestv2\resultados
```

## Notas para publicacion

- `.env`, entornos virtuales, builds, ejecutables y `resultados/` no se versionan.
- El repositorio contiene el codigo, scripts de arranque, firmware y documentacion tecnica.
- Los datos reales de medicion deben mantenerse fuera del historial publico.
