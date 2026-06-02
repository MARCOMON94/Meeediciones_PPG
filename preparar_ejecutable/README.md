# Preparar ejecutable Windows

Esta carpeta contiene una copia preparada para generar una version portable de `mtestv2` para Windows sin tocar el proyecto principal.

## Que contiene

- `BUILD_EXE.cmd`: script para crear el ejecutable.
- `source/`: copia del proyecto adaptada para empaquetar con PyInstaller.
- `source/mtestv2.spec`: configuracion de PyInstaller.

## Que no se sube a Git

Los artefactos pesados o generados quedan ignorados:

- `.build_venv/`
- `source/build/`
- `source/dist/`
- `entrega/`
- `*.zip`
- `*.exe`

## Como generar el ejecutable

Desde Windows, ejecutar:

```bat
preparar_ejecutable\BUILD_EXE.cmd
```

El ejecutable se genera dentro de:

```txt
preparar_ejecutable\source\dist\mtestv2.exe
```

En la version empaquetada, los resultados se guardan en:

```txt
%USERPROFILE%\Documents\mtestv2\resultados
```

Los PDFs exportables se guardan en:

```txt
%USERPROFILE%\Documents\mtestv2\resultados\documentos_generados
```
