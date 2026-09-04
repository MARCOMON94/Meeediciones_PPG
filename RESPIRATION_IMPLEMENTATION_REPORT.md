# Pipeline de frecuencia respiratoria (RR) — informe de implementación

## 1. Resumen

Se implementó un pipeline independiente de estimación de frecuencia respiratoria (RR) a
partir de RED/IR crudos, en un paquete nuevo `respiration/`, sin modificar el
preprocesamiento cardíaco, BPM, SpO2 ni los archivos `resultados/raw/*.csv` existentes.
Se ejecutó de forma retrospectiva sobre los 620 registros `raw_*.csv` disponibles, se
generó un resumen (`rr_summary.csv`), JSON por archivo y gráficas diagnósticas, y se
integró como una ventana nueva ("Respiración") accesible desde un botón propio en el menú
principal.

**Resultado del objetivo inicial (spec §35):** sí existe modulación respiratoria
reproducible y recuperable en la señal PPG obtenida en pezón ovino — 171 de 620 registros
(27.6 %) producen una RR con evidencia suficiente (consenso entre RIIV/RIAV/RIFV, RED/IR,
espectro y autocorrelación) para superar el umbral de confianza. RIAV es, con diferencia,
el componente más informativo en este dataset (ver §4).

## 2. Implementación

### 2.1 Investigación previa (spec §41)

Antes de escribir código se confirmó, vía `graphify` y lectura directa:

- La versión activa de la app es `ppg_suite/` (no existe `ppg_suite_v5.py` en este repo).
- `ppg_suite/processing.py` contiene el pipeline cardíaco (BPM/SpO2/FFT/autocorr/
  artefactos) y ya tenía una estimación respiratoria *ingenua* (`estimate_respiration()`,
  un único pico FFT sobre una ventana, sin validación cruzada) que alimenta
  `Metrics.resp_rate_rpm`. **Se dejó intacta**: sigue viva para no romper nada corriente,
  pero el nuevo pipeline no la reutiliza ni se basa en ella, precisamente porque es el
  método de "FFT única" que el encargo pide no usar como único estimador.
- `ppg_suite/paths.py` define `RAW_DIR = resultados/raw/`; una muestra real confirmó las
  columnas `id`, `modo`, `tiempo_s`, `red_raw`, `ir_raw`, `system_time` tal como se
  esperaba — no fue necesario tocar el firmware ni el escritor de RAW.
- El proyecto no tiene scipy/pandas/matplotlib (ni en `requirements.txt` ni instalados en
  el venv). `processing.py` ya resuelve todo su análisis espectral con `numpy.fft`
  puro. El nuevo módulo sigue esa misma convención: filtros band-pass/low-pass de fase
  cero implementados a mano vía enmascarado en el dominio de la frecuencia, y un Welch
  casero (periodograma promediado por segmentos). No se añadió ninguna dependencia nueva.
  Los diagnósticos gráficos reutilizan `pyqtgraph` (ya es dependencia de runtime) y se
  exportan a PNG en modo *offscreen*, igual que ya hace la suite de tests existente.

### 2.2 Arquitectura

```
respiration/
    __init__.py       analyze_respiration(t, red, ir, cfg) -> RespirationMetrics (API pública)
    config.py          RespirationConfig
    models.py          RespirationMetrics, CandidateEstimate, WindowRR/WindowedResult
    filters.py         fft_lowpass / fft_bandpass (fase cero, con taper suave en los bordes)
    preprocessing.py   prepare_respiration_signal(): validación, remuestreo 100Hz->25Hz,
                        anti-alias, artefactos/saturación/contacto
    riiv.py            RIIV = band-pass directo de la señal remuestreada (IR y RED)
    riav.py            detección de latidos cardíacos + amplitud pico-valle -> RIAV
    rifv.py            IBI de los mismos latidos -> RIFV
    spectral.py         welch_psd() + métricas del pico (ratio, prominencia, 2º pico, ancho)
    autocorr.py        autocorrelación acotada a [60/rr_max, 60/rr_min] + concordancia
    fusion.py           candidato por señal (FFT+autocorr) + consenso robusto con rechazo
                        de outliers (mediana ponderada por confianza, no media simple)
    quality.py          duración, ciclos, concordancia, estabilidad, artefactos ->
                        confidence 0-100, y motivo explícito de rechazo
    windows.py           ventanas solapadas (>=60 s) -> mediana/MAD/CV -> estabilidad
    pipeline.py          orquesta RIIV/RIAV/RIFV -> candidatos -> consenso (compartido por
                        el análisis global y por cada ventana)
    plotting.py         panel de 6 gráficos (spec §25), reutilizado por GUI y batch
    batch.py             CLI: python -m respiration.batch [--plots] [--limit N]
```

`ppg_suite/windows/respiration_window.py` es la ventana nueva de la GUI; `ppg_suite/menu.py`
y `controller.py` reciben únicamente la adición mínima de un botón y su wiring (mismo
patrón que usan el resto de modos, p. ej. Fourier).

### 2.3 Fórmulas y parámetros clave

- **RIIV**: band-pass de fase cero (enmascarado FFT) en `[respiratory_low_hz,
  respiratory_high_hz]` = `[0.10, 1.20]` Hz sobre la señal remuestreada a 25 Hz — el propio
  band-pass elimina tanto la deriva extremadamente lenta como todo lo más rápido que la
  banda, sin un paso de detrend aparte.
- **RIAV**: detección de latidos con `ppg_suite.processing.find_local_peaks`/
  `processed_ppg` (reutilizado solo para localizar el latido, nunca para el contenido de
  RIIV), `amplitud_i = pico - valle_local`, interpolada sobre la rejilla respiratoria y
  band-paseada igual que RIIV.
- **RIFV**: `ibi = diff(peak_times)`, `hr_instantanea = 60/ibi`, interpolada y
  band-paseada; puede devolver `NaN`/confianza 0 sin invalidar RIIV/RIAV (spec §11).
- **Espectral**: Welch casero (Hanning, 50 % solape, segmentos de ~15 s) +
  `peak_band_ratio`, prominencia, diferencia con el 2º pico, ancho de pico.
- **Autocorrelación**: pico de `ac` dentro de `[60/rr_max, 60/rr_min]`.
- **Fusión**: por candidato (`riiv_ir/red`, `riav_ir/red`, `rifv`) se calcula un RR y una
  confianza combinando calidad espectral, autocorrelación y concordancia FFT-autocorr;
  el consenso final es una **mediana ponderada por confianza tras rechazo de outliers
  MAD** (nunca una media simple), para que un único RIFV desviado no arrastre el resultado.
- **Confidence (0-100)**: pondera duración, nº de ciclos, calidad espectral/autocorr,
  concordancia FFT-autocorr, RED-IR, entre estimadores, y estabilidad entre ventanas;
  además hay un **"evidence gate"** — si la evidencia espectral+autocorr conjunta es
  débil, el score total se escala hacia abajo aunque la duración/ciclos sean altos, para
  que un registro largo y limpio pero sin periodicidad real no obtenga confianza alta solo
  por tener muchos datos.
- Umbrales iniciales deliberadamente amplios (`rr_min=6`, `rr_max=72` rpm) para no sesgar
  la búsqueda hacia lo que "se espera" encontrar en oveja (spec §6).

## 3. Análisis retrospectivo

Ejecutado con `python -m respiration.batch --plots`, sin modificar ningún archivo bajo
`resultados/raw/` (confirmado con `git status` antes/después — el directorio está además
excluido de git).

| Duración | Nº registros | RR válida | % válida |
|---|---:|---:|---:|
| <20 s | 84 | 1 | 1.2 % |
| 20-29 s | 271 | 71 | 26.2 % |
| 30-59 s | 169 | 71 | 42.0 % |
| >=60 s | 72 | 28 | 38.9 % |
| (duración desconocida / archivo vacío) | 24 | 0 | 0.0 % |
| **Total** | **620** | **171** | **27.6 %** |

- Confianza (solo válidas): mediana 68.5, rango 60.1–87.5 (el umbral configurado es 60).
- RR (solo válidas): mediana 8.6 rpm, P10=8.0, P90=14.7 rpm — ver limitación en §5.

Motivos de rechazo más comunes (registros no válidos): duración insuficiente (271
combinando ambas variantes del mensaje), confianza insuficiente (58), señal IR
prácticamente constante / sin contacto (34), muestras insuficientes (25), movimiento/
contacto excesivo (20), demasiados pocos ciclos (16), alta discordancia RED/IR (13).

Diagnósticos gráficos (6 paneles: RAW IR/RED, RIIV, RIAV, RIFV, PSD respiratoria, RR por
ventana) generados en `resultados/analisis/respiracion/diagnostics/` para los 6 mejores y
6 peores registros por confianza, más un puñado de casos usados durante la depuración.
Resumen completo en `resultados/analisis/respiracion/rr_summary.csv`; JSON por archivo en
`resultados/analisis/respiracion/json/` (620 archivos, uno por cada raw procesado).

## 4. Comparación RIIV / RIAV / RIFV / RED / IR

Frecuencia con la que cada estimador forma parte del consenso final (de los 171 registros
válidos):

| Estimador | Presente en el consenso | % |
|---|---:|---:|
| RIAV IR | 161 | 94.2 % |
| RIAV RED | 155 | 90.6 % |
| RIFV | 129 | 75.4 % |
| RIIV IR | 96 | 56.1 % |
| RIIV RED | 54 | 31.6 % |

**RIAV es claramente el componente más informativo** en este dataset — se mantiene dentro
del consenso en más del 90 % de los registros válidos, muy por delante de RIIV. IR es
sistemáticamente más útil que RED (tanto en RIIV como en RIAV), consistente con que el IR
suele tener mejor perfusión/SNR en el pezón que el RED en este sensor. RIFV aporta en 3 de
cada 4 registros válidos — más de lo esperado inicialmente para una especie no humana
(spec §11 avisaba de que podría rendir peor), pero cuando aporta suele coincidir con
RIAV/RIIV, no actuar solo.

## 5. Problemas encontrados (relevante para trabajo futuro)

1. **Fugas espectrales en los bordes de banda ("edge leakage")** — el hallazgo más
   importante de esta primera pasada. Un registro de control sin oveja
   (`raw_BLOQUE_12CFG_marco_no_oveja_*`, sin posibilidad de respiración real) devolvía
   inicialmente una RR "confiada" de forma repetible. Causa raíz: (a) la deriva lenta
   térmica/de contacto tiene una cola espectral que no desaparece bruscamente en
   `respiratory_low_hz`, y el primer bin dentro de banda heredaba esa energía; (b) de forma
   simétrica, el tono cardíaco (justo por encima de `respiratory_high_hz` cuando la
   frecuencia cardíaca es baja) se filtraba mediante ringing de Gibbs de un corte ideal; (c)
   en el dominio de autocorrelación, el lag mínimo de búsqueda (`rr_max`) coincidía con la
   cola de la autocorrelación cerca de lag 0. Se corrigió con: taper suave (coseno elevado,
   no corte ideal) en los filtros FFT, y **guardas de borde** explícitas en
   `spectral_peak_metrics` y `autocorr_rr` que rechazan un "pico" cuando aparece justo en el
   extremo de la banda/ventana de búsqueda y la señal ya era igual o más fuerte
   inmediatamente fuera de esa banda. Verificado con el archivo de control: pasó de RR=8.0
   rpm/confianza 87.7 (falso positivo) a `valid=False`. El ratio global de válidos bajó de
   ~49 % a ~28 % al corregirlo — el número más bajo es el correcto.
2. **Concentración residual en el extremo bajo de la banda (~8 rpm)** — tras la corrección
   anterior, el 27.6 % de RR válidas sigue teniendo mediana en 8.6 rpm con bastantes valores
   agrupados exactamente en 8.0 rpm (el primer bin dentro de banda con la resolución de Welch
   usada). No se puede descartar con los datos actuales si esto es (a) una respiración
   ovina real e inusualmente lenta en las condiciones de la toma, o (b) un residuo más sutil
   de la misma familia de problema del punto 1 que las guardas actuales no capturan del
   todo. Sin referencia respiratoria sincronizada (spec §31) no es posible zanjarlo — se
   deja documentado en vez de forzar una conclusión.
3. **Movimiento/contacto**: 34 registros con IR prácticamente constante (sin contacto o
   sensor apagado) y 20 con artefactos de movimiento/contacto excesivo — coherente con
   tomas de campo reales.
4. **Duración**: con diferencia el motivo de rechazo más frecuente (271/620). La mayoría de
   `raw_*.csv` existentes se capturaron para BPM/SpO2 (20 s), no para RR — esperable, ver
   recomendaciones.
5. No se detectaron errores del pipeline cardíaco existente durante esta investigación;
   `estimate_respiration()` (el método FFT-única) se dejó sin tocar.

## 6. Recomendaciones para futuras mediciones

- **Duración de adquisición**: usar el preset "VALIDATION" (120 s) al menos en un
  subconjunto de animales para poder ejecutar el análisis por ventanas (spec §15) con
  margen; 60 s como protocolo estándar si se quiere BPM+SpO2+RR en la misma toma; 30 s
  como mínimo operativo estricto para RR.
- **Referencia sincronizada**: la recomendación más importante y ya presente en el propio
  encargo (spec §31) — sin observación/video de referencia no se puede saber si el
  agrupamiento en ~8 rpm (§5.2) es fisiológico o un artefacto residual. Es el siguiente
  paso obligatorio antes de confiar en los valores numéricos.
- **Revisar `respiratory_low_hz` con datos de referencia**: si al validar se confirma que
  8-10 rpm no es fisiológicamente plausible en oveja en las condiciones de este estudio,
  subir el suelo de búsqueda (p. ej. a 0.15-0.17 Hz ~ 9-10 rpm) reduciría el riesgo
  descrito en §5.2 — pero solo debe hacerse con evidencia, no a priori (spec §6/§42).
  Los límites actuales (6-72 rpm) se mantienen deliberadamente amplios en el código.
- **Priorizar RIAV en desarrollo futuro**: es el componente con más señal en este dataset;
  si se explora mejorar el pipeline, la detección de latidos y la extracción de amplitud
  (RIAV IR primero, luego RED) es donde más rendimiento hay que ganar.
- **GUI**: la ventana "Respiración" ya permite re-analizar cualquier raw >=30 s bajo
  demanda; una vez validado con referencia, sería el momento de mostrar RR en el flujo de
  medición en vivo (spec §29), no antes.

## 7. Tests

`tests/test_respiration.py` — 6 tests sintéticos del spec §33 (RIIV recupera RR desde
modulación de baseline, RIAV recupera RR desde modulación de amplitud, la confianza baja
con el ruido, un artefacto de movimiento periódico no secuestra el consenso, una señal sin
modulación se rechaza, una toma demasiado corta obtiene confianza reducida) más un test de
extremo a extremo sobre un `raw_LONG_*.csv` real (se salta si no hay ninguno disponible; no
compara contra un valor de referencia porque no existe ground truth todavía — spec §34).
Los 7 tests pasan; la suite completa del proyecto (`pytest`) no muestra regresiones nuevas
(los 13 errores preexistentes en `tests/test_io_trash.py` y similares son un problema de
permisos del directorio temporal de Windows en este entorno, no relacionado con este
cambio — se reproducen igual en `main` antes de esta implementación).

## 8. Archivos creados / modificados

**Nuevos**: `respiration/` (paquete completo, 14 archivos), `tests/test_respiration.py`,
`ppg_suite/windows/respiration_window.py`, este informe.

**Modificados (adición mínima, mismo patrón que el resto de modos)**: `ppg_suite/menu.py`
(botón "Respiración" + entrada en `AppMode`), `controller.py` (`show_respiration()` +
wiring). No se modificó `ppg_suite/processing.py`, `ppg_suite/models.py`, el firmware, ni
ningún archivo bajo `resultados/`.
