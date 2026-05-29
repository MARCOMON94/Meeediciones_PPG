# mtestv2

Aplicacion modular de medicion PPG con sensor MAX3010x, lectura de temperatura NTC y firmware Arduino compatible.

## Arranque recomendado en Windows

Usar:

```bat
ARRANCAR_PPG.cmd
```

El lanzador:

- Lee `.env` para localizar `PROJECT_DIR`, `PYTHON_REL` y `MAIN_FILE`.
- Entra en la carpeta del proyecto definida para cada ordenador.
- Intenta actualizar con `git pull --ff-only`.
- Comprueba las dependencias Python y las instala desde `requirements.txt` si faltan.
- Abre `main.py` con el Python del entorno virtual.

Ejemplo de `.env`:

```txt
PROJECT_DIR=C:\Users\lol32\Documents\GitHub\mtestv2
PYTHON_REL=.venv\Scripts\python.exe
MAIN_FILE=main.py
```

`git pull --ff-only` no borra cambios locales. Si Git no puede actualizar de forma limpia, se detiene y el programa continua con la version local.

## Instalacion

Para preparar un ordenador por primera vez:

```bat
instalarmtestv2.cmd
```

Instala/crea `.venv`, instala dependencias y crea `.env` si no existe.

## Modos de trabajo

- Medicion de campo: toma rapida con la interfaz minima y los datos esenciales.
- Test de campo: toma con notas, parametros desplegables y graficas diagnosticas.
- Solo temperatura: registro NTC sin PPG.
- Reajustes: calibracion larga con controles completos.
- Configuraciones: tabla editable para crear, pegar y ejecutar pruebas de sensor.

El menu incluye un boton `Ultimas actualizaciones` que abre el archivo `actualizaciones/ACTUALIZACIONES_*.txt` mas reciente.

## Datos guardados

Los resultados se guardan dentro de `PROJECT_DIR/resultados/` para separar datos de uso normal y codigo del programa:

- `resultados/raw/`: datos crudos unificados.
- `resultados/processed/`: datos procesados.
- `resultados/sessions/`: resumen global de sesion.
- `resultados/reports/`: resumenes JSON y bloques.
- `resultados/figures/`: graficas.
- `resultados/screenshots/`: capturas de pantalla.
- `resultados/configs/`: configuraciones aplicadas.
- `resultados/logs/`: logs de ejecucion.
- `actualizaciones/`: notas de cambios visibles desde el menu principal.

Los raw incluyen, de forma unificada:

- id/crotal
- modo
- condiciones de medida
- etiqueta de configuracion
- tiempo
- RED/IR raw
- temperatura calculada y raw NTC
- parametros de configuracion del sensor
- estado de confirmacion de configuracion Arduino
- hora del sistema

## Estructura

```txt
main.py                         # arrancador unico
controller.py                   # abre/cierra una unica ventana activa
ppg_suite/
  paths.py                      # rutas desde .env y logging
  utils.py                      # utilidades generales
  models.py                     # dataclasses de configuracion/estado/metricas
  processing.py                 # BPM, FFT, autocorrelacion, SpO2, artefactos
  widgets.py                    # widgets de configuracion
  menu.py                       # menu inicial
  windows/
    measurement_window.py       # ventana base para campo/test
    real_window.py              # modo campo
    test_window.py              # modo test
    temperature_window.py       # modo solo temperatura
    reajustes_window.py         # modo reajustes independiente
    scheduled_window.py         # modo configuraciones con tabla editable
arduino/
  ppg_max3010x_firmware/
    ppg_max3010x_firmware.ino   # firmware compatible con el protocolo Python
```

## Arduino

El firmware se carga una vez en la placa. Python se comunica por serie con comandos de texto:

- `STATUS`
- `CONFIG RED=... IR=... AVG=... RATE=... WIDTH=... ADC=... SKIP=... DEBUG=...`
- `CONFIG_TEMP VCC=... RFIX=... RN=... BETA=... OFFSET=... ADCBITS=...`
- `START_CONTINUOUS`
- `START_TEMP`
- `STOP`
- `DIAGNOSTICO`

Antes de iniciar una toma, la aplicacion verifica la configuracion confirmada por Arduino con la linea `CFG ...`. Si no coincide, muestra aviso y permite aplicar los cambios antes de continuar.
