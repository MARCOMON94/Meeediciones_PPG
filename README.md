# mtestv2

Aplicacion modular de medicion PPG para Windows con sensor MAX3010x, lectura de temperatura NTC, interfaz PyQt6 y firmware Arduino compatible.

El proyecto esta pensado para dos formas de uso:

- Uso de desarrollo/equipo: se arranca desde el repositorio con entorno virtual, `.env` y actualizacion opcional por Git.
- Uso portable: se genera un `.exe` para otra persona, sin Git, sin Python visible y sin actualizaciones automaticas.

## Requisitos

Para trabajar desde el repositorio:

- Windows.
- Python 3.
- Git, si se quiere usar la actualizacion automatica del lanzador.
- Una placa Arduino con el firmware compatible cargado.

Dependencias Python:

```txt
numpy
pyserial
PyQt6
pyqtgraph
```

## Instalacion inicial

Para preparar un ordenador por primera vez desde la carpeta del proyecto:

```bat
instalarmtestv2.cmd
```

El instalador:

- Busca Python.
- Crea `.venv` si no existe.
- Instala dependencias desde `requirements.txt`.
- Crea `.env` si no existe.
- Comprueba que las librerias principales importan correctamente.

Ejemplo de `.env`:

```txt
PROJECT_DIR=C:\Users\lol32\Documents\GitHub\mtestv2
PYTHON_REL=.venv\Scripts\python.exe
MAIN_FILE=main.py
AUTO_UPDATE_GIT=1
```

## Arranque recomendado

Usar:

```bat
ARRANCAR_PPG.cmd
```

El lanzador:

- Lee `.env` para localizar el proyecto, Python y archivo principal.
- Entra en `PROJECT_DIR`.
- Intenta actualizar el repositorio con `git pull --ff-only` si Git esta disponible.
- La actualizacion se puede desactivar poniendo `AUTO_UPDATE_GIT=0` en `.env`.
- Comprueba dependencias y las instala si faltan.
- Ejecuta `main.py`.

`git pull --ff-only` no borra cambios locales. Si Git no puede actualizar de forma limpia, muestra aviso y continua con la version local. El lanzador evita preguntas interactivas de Git para no quedarse bloqueado en mensajes `y/n`.

Tambien se puede arrancar manualmente:

```bat
.venv\Scripts\python.exe main.py
```

## Modos de trabajo

El menu inicial permite elegir:

- Medicion de campo: toma rapida con la interfaz minima y datos esenciales.
- Test de campo: toma con notas, parametros desplegables y graficas diagnosticas.
- Solo temperatura: registro NTC sin PPG.
- Reajustes: medicion larga con controles completos, diagnostico Arduino y snapshots.
- Configuraciones: tabla editable para crear, pegar y ejecutar bloques de configuracion del sensor.
- Experimento 3M: optimizacion adaptativa para encontrar una configuracion real del sensor en menos de 20 minutos. Entre tramos pide pulsioximetro/fonendo cuando necesita referencia, ignora lecturas 0 o vacias y cambia RED/IR/AVG/ADC segun cercania a BPM manual, pulso PPG, SpO2 usable, PI, artefactos y saturacion. En pantalla muestra la razon de cada decision y al finalizar guarda un JSON tecnico y un PDF final con resumen, ranking, decisiones y mejor candidata.
- Estadisticas: explorador de sesiones, raws, procesados, resumenes, graficas y capturas.
- Analisis experimental de Fourier + Hilbert: comparacion de raws para razonar que configuracion separa mejor el pulso. La lectura principal es `Pulso ref.` frente a BPM estimadas; Fourier, autocorrelacion e Hilbert quedan como apoyo tecnico. Permite exportar un informe PDF con fecha, ranking, procedimiento comparativo, graficas y anexo metodologico.
- El analisis aplica cribado robusto: conserva el raw completo, pero ignora estabilizacion inicial y outliers/saltos RED/IR para que muestras irreales no dominen BPM, Fourier o Hilbert.

El menu tambien incluye `Ultimas actualizaciones`, que abre el archivo mas reciente de:

```txt
actualizaciones/ACTUALIZACIONES_*.txt
```

## Datos guardados

En uso normal desde el repositorio, los resultados se guardan en:

```txt
PROJECT_DIR\resultados
```

Carpetas principales:

- `resultados/raw/`: datos crudos unificados.
- `resultados/processed/`: datos procesados.
- `resultados/sessions/`: resumen global de sesiones.
- `resultados/reports/`: resumenes JSON y bloques de BPM.
- `resultados/documentos_generados/`: PDFs generados para revisar o adjuntar.
- `resultados/documentos_generados/informe_fourier_hilbert_*.pdf`: informes exportados del analisis comparativo.
- `resultados/documentos_generados/informe_experimento_3m_*.pdf`: informe final del Experimento 3M.
- `resultados/figures/`: graficas.
- `resultados/screenshots/`: capturas.
- `resultados/configs/`: configuraciones aplicadas.
- `resultados/logs/`: logs de ejecucion.

Los raw nuevos incluyen tambien las referencias manuales (`pulso_previo`, `pulso_final_pulsio`, `pulso_final_fonendo`) para que `Analisis experimental de Fourier` pueda comparar BPM aunque se copie solo el CSV raw.

Los raws incluyen informacion como:

- id/crotal.
- modo de trabajo.
- condiciones de medida.
- etiqueta de configuracion.
- tiempo.
- RED/IR raw.
- temperatura calculada y raw NTC.
- parametros de configuracion del sensor.
- estado de confirmacion de configuracion Arduino.
- hora del sistema.

## Arduino

El firmware se carga una vez en la placa:

```txt
arduino/ppg_max3010x_firmware/ppg_max3010x_firmware.ino
```

La aplicacion se comunica por serie con comandos de texto:

- `STATUS`
- `CONFIG RED=... IR=... AVG=... RATE=... WIDTH=... ADC=... SKIP=... DEBUG=...`
- `CONFIG_TEMP VCC=... RFIX=... RN=... BETA=... OFFSET=... ADCBITS=...`
- `START_CONTINUOUS`
- `START_TEMP`
- `STOP`
- `DIAGNOSTICO`

Antes de iniciar una toma, la aplicacion verifica la configuracion confirmada por Arduino con la linea `CFG ...`. Si no coincide, muestra aviso y permite aplicar cambios antes de continuar.

## Ejecutable portable para Windows

La carpeta:

```txt
preparar_ejecutable/
```

contiene una copia preparada para generar un `.exe` portable sin modificar el proyecto principal.

Para construirlo:

```bat
preparar_ejecutable\BUILD_EXE.cmd
```

Ese script:

- Crea un entorno temporal de build en `preparar_ejecutable/.build_venv/`.
- Instala dependencias y `pyinstaller`.
- Usa `preparar_ejecutable/source/mtestv2.spec`.
- Genera el ejecutable en `preparar_ejecutable/source/dist/mtestv2.exe`.

En la version empaquetada, los resultados se guardan en:

```txt
%USERPROFILE%\Documents\mtestv2\resultados
```

La version portable no se actualiza automaticamente. Para entregar cambios nuevos hay que generar y pasar un nuevo `.exe`.

Los artefactos pesados quedan ignorados por Git:

- `.build_venv/`
- `source/build/`
- `source/dist/`
- `entrega/`
- `*.exe`
- `*.zip`

## Estructura

```txt
main.py                         # arrancador unico
controller.py                   # controlador de ventanas
requirements.txt                # dependencias Python
ARRANCAR_PPG.cmd                # lanzador de uso normal
instalarmtestv2.cmd             # instalacion inicial
actualizaciones/                # notas visibles desde la interfaz
arduino/
  ppg_max3010x_firmware/
    ppg_max3010x_firmware.ino   # firmware Arduino
ppg_suite/
  paths.py                      # rutas, resultados y logging
  utils.py                      # utilidades generales
  models.py                     # dataclasses de configuracion, estado y metricas
  processing.py                 # BPM, FFT, autocorrelacion, SpO2 y artefactos
  widgets.py                    # widgets reutilizables de configuracion
  menu.py                       # menu inicial
  windows/
    measurement_window.py       # ventana base para campo/test
    real_window.py              # modo medicion de campo
    test_window.py              # modo test de campo
    temperature_window.py       # modo solo temperatura
    reajustes_window.py         # modo reajustes/larga duracion
    scheduled_window.py         # modos configuraciones y Experimento 3M
    relations_window.py         # estadisticas y relacion entre archivos
    fourier_window.py           # analisis experimental de Fourier + Hilbert
preparar_ejecutable/
  BUILD_EXE.cmd                 # build del ejecutable portable
  source/                       # copia adaptada para PyInstaller
```
