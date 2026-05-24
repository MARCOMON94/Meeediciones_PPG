# mtest modular

Versión modular de `ppg_suite_v5.py`.

## Arranque

```bash
cd mtest_modular
python -m pip install -r requirements.txt
python main.py
```

## Estructura

```txt
main.py                         # arrancador único
controller.py                   # abre/cierra una única ventana activa
ppg_suite/
  paths.py                      # rutas y logging
  utils.py                      # utilidades generales
  models.py                     # dataclasses de configuración/estado/métricas
  processing.py                 # BPM, FFT, autocorrelación, SpO2, artefactos
  widgets.py                    # widgets de configuración
  menu.py                       # menú inicial
  windows/
    measurement_window.py       # ventana base para real/test
    real_window.py              # modo real
    test_window.py              # modo test
    reajustes_window.py         # modo reajustes independiente
arduino/
  ppg_max3010x_firmware/
    ppg_max3010x_firmware.ino   # firmware compatible con el protocolo Python
```

## Cambio principal

Antes, reajustes abría `PPGSuite` y encima `LongModeWindow`. Ahora `ReajustesWindow` es una ventana independiente. Al volver al menú, la ventana activa se cierra, su timer se para y se abre el menú.

## Arduino

El firmware se carga una vez en la placa con Arduino IDE, Arduino CLI o VS Code + PlatformIO/Arduino. Luego Python manda configuración con el comando `CONFIG ...` al conectar y antes de iniciar cada toma.
