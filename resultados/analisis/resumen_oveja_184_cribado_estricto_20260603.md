# Analisis oveja 184 - cribado estricto

Cribado estricto aplicado: se ignoran los 2 primeros segundos y se descartan muestras con outliers robustos RED/IR mas sensibles que el algoritmo actual (z de valor > 5 o z de salto > 6, expandido +/-3 muestras).
El ranking corregido usa la FFT como BPM de decision cuando esta disponible, igual que el experimento 3M, y penaliza discrepancias grandes entre BPM combinado y FFT.

## 20260603_123040
| cfg | config | ref | BPM decision | diff | BPM combinado | calidad | retenido | desc. | PI IR | IQR 15s | score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6 | RED199 IR159 AVG4 ADC16384 | 82.5 | 84.59 | 2.09 | 86.3 | 48.95 | 100.0% | 0.0% | 0.0087 | 3.66 | 71.85 |
| 5 | RED199 IR159 AVG2 ADC16384 | 82.5 | 82.34 | 0.16 | 82.34 | 27.23 | 100.0% | 0.0% | 0.0096 | 0.0 | 69.94 |
| 4 | RED199 IR159 AVG1 ADC16384 | 85.0 | 90.52 | 5.52 | 92.3 | 43.25 | 99.25% | 0.75% | 0.0084 | 0.86 | 68.86 |
| 7 | RED199 IR159 AVG4 ADC8192 | 83.5 | 80.29 | 3.21 | 80.29 | 28.03 | 100.0% | 0.0% | 0.0082 | 0.0 | 67.03 |
| 12 | RED159 IR159 AVG2 ADC16384 | 80.0 | 88.54 | 8.54 | 89.1 | 50.84 | 99.12% | 0.88% | 0.0088 | 4.81 | 64.58 |
| 9 | RED159 IR159 AVG4 ADC16384 | 78.5 | 88.89 | 10.39 | 87.29 | 53.5 | 100.0% | 0.0% | 0.0081 | 7.05 | 61.79 |
| 2 | RED63 IR63 AVG1 ADC16384 | 79.5 | 74.78 | 4.72 | 74.78 | 24.95 | 100.0% | 0.0% | 0.0141 |  | 53.06 |
| 8 | RED199 IR159 AVG2 ADC8192 | 81.0 | 79.29 | 1.71 | 79.29 | 33.15 | 63.84% | 36.16% | 0.008 | 0.0 | 47.37 |

## 20260603_124425
| cfg | config | ref | BPM decision | diff | BPM combinado | calidad | retenido | desc. | PI IR | IQR 15s | score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 11 | RED159 IR159 AVG2 ADC8192 | 77.33 | 72.89 | 4.44 | 171.43 | 48.16 | 97.35% | 2.65% | 0.0903 | 0.0 | 61.89 |
| 2 | RED59 IR47 AVG1 ADC16384 | 71.33 | 72.15 | 0.82 | 72.15 | 27.95 | 99.62% | 0.38% | 0.0669 | 24.65 | 59.26 |
| 12 | RED159 IR159 AVG1 ADC16384 | 72.0 | 70.91 | 1.09 | 70.91 | 23.67 | 100.0% | 0.0% | 0.0721 | 35.69 | 57.75 |
| 5 | RED199 IR159 AVG2 ADC16384 | 70.0 | 64.79 | 5.21 | 171.43 | 53.06 | 100.0% | 0.0% | 0.0867 | 52.32 | 52.3 |
| 10 | RED159 IR159 AVG2 ADC16384 | 73.33 | 89.09 | 15.76 | 171.43 | 50.14 | 99.23% | 0.77% | 0.0602 | 0.0 | 51.65 |
| 7 | RED199 IR159 AVG4 ADC8192 | 70.33 | 71.68 | 1.35 | 171.43 | 47.0 | 92.52% | 7.48% | 0.0587 | 8.04 | 51.56 |
| 8 | RED199 IR159 AVG1 ADC8192 | 71.0 | 68.16 | 2.84 | 68.16 | 33.36 | 71.89% | 28.11% | 0.0741 | 0.0 | 49.6 |
| 9 | RED199 IR159 AVG2 ADC8192 | 66.67 | 70.67 | 4.0 | 171.43 | 40.94 | 98.46% | 1.54% | 0.0714 | 81.92 | 47.78 |

## 20260602_ConVacio
| cfg | config | ref | BPM decision | diff | BPM combinado | calidad | retenido | desc. | PI IR | IQR 15s | score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | RED63 IR63 AVG1 ADC8192 |  | 82.32 |  | 82.44 | 67.58 | 100.0% | 0.0% | 0.0422 | 1.04 | 42.59 |
| 1 | RED31 IR31 AVG1 ADC8192 |  | 74.67 |  | 78.27 | 50.12 | 100.0% | 0.0% | 0.0458 | 0.18 | 39.25 |
| 10 | RED127 IR127 AVG1 ADC16384 |  | 71.4 |  | 71.4 | 32.06 | 100.0% | 0.0% | 0.0437 | 0.0 | 32.86 |
| 7 | RED63 IR63 AVG4 ADC8192 |  | 67.78 |  | 82.86 | 39.58 | 100.0% | 0.0% | 0.0399 | 2.86 | 32.53 |
| 16 | RED95 IR95 AVG4 ADC16384 |  | 87.87 |  | 171.43 | 51.3 | 100.0% | 0.0% | 0.0994 | 0.0 | 29.94 |
| 15 | RED95 IR95 AVG4 ADC8192 |  | 82.85 |  | 171.43 | 51.38 | 100.0% | 0.0% | 0.0965 | 0.0 | 29.91 |
| 14 | RED95 IR95 AVG1 ADC16384 |  | 71.4 |  | 75.57 | 50.63 | 100.0% | 0.0% | 0.0822 | 31.61 | 29.08 |
| 9 | RED127 IR127 AVG1 ADC8192 |  | 73.57 |  | 86.35 | 30.16 | 98.26% | 1.74% | 0.0483 | 6.94 | 24.55 |

## 20260602_SinVacio
| cfg | config | ref | BPM decision | diff | BPM combinado | calidad | retenido | desc. | PI IR | IQR 15s | score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | RED127 IR127 AVG4 ADC16384 |  | 45.38 |  | 171.43 | 57.96 | 100.0% | 0.0% | 0.1904 | 0.0 | 35.38 |
| 16 | RED95 IR95 AVG4 ADC16384 |  | 83.19 |  | 171.43 | 55.76 | 100.0% | 0.0% | 0.2025 | 0.0 | 34.89 |
| 8 | RED63 IR63 AVG4 ADC16384 |  | 45.19 |  | 171.43 | 55.54 | 100.0% | 0.0% | 0.177 | 0.0 | 34.48 |
| 3 | RED31 IR31 AVG4 ADC8192 |  | 47.9 |  | 171.43 | 59.77 | 100.0% | 0.0% | 0.1938 | 5.36 | 30.98 |
| 9 | RED127 IR127 AVG1 ADC8192 |  | 47.59 |  | 140.74 | 38.73 | 100.0% | 0.0% | 0.1705 | 0.0 | 28.57 |
| 1 | RED31 IR31 AVG1 ADC8192 |  | 70.19 |  | 141.27 | 31.59 | 100.0% | 0.0% | 0.1591 | 0.0 | 25.81 |
| 6 | RED63 IR63 AVG1 ADC16384 |  | 47.59 |  | 47.59 | 25.64 | 100.0% | 0.0% | 0.1786 | 10.83 | 25.09 |
| 13 | RED95 IR95 AVG1 ADC8192 |  | 84.36 |  | 132.91 | 42.71 | 100.0% | 0.0% | 0.17 | 6.38 | 23.48 |

## Observaciones automaticas
- Mejor por ranking corregido en 20260603_123040: CFG006 RED199 IR159 AVG4 ADC16384 con score 71.85, BPM decision 84.59, diff 2.09 BPM, retenido 100.0%.
- Mejor por ranking corregido en 20260603_124425: CFG011 RED159 IR159 AVG2 ADC8192 con score 61.89, BPM decision 72.89, diff 4.44 BPM, retenido 97.35%.
- Mejor por ranking corregido en 20260602_ConVacio: CFG005 RED63 IR63 AVG1 ADC8192 con score 42.59, BPM decision 82.32, diff  BPM, retenido 100.0%.
- Mejor por ranking corregido en 20260602_SinVacio: CFG012 RED127 IR127 AVG4 ADC16384 con score 35.38, BPM decision 45.38, diff  BPM, retenido 100.0%.

CSV completo: C:\Users\lol32\Documents\GitHub\mtestv2\resultados\analisis\analisis_oveja_184_cribado_estricto_20260603.csv