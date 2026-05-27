@echo off
setlocal EnableExtensions

echo ============================================
echo Instalador mtestv2
echo ============================================
echo.

REM Ir a la carpeta donde esta este instalador
cd /d "%~dp0"

echo [INFO] Carpeta actual:
echo "%CD%"
echo.

REM Buscar Python
set "PYTHON_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    )
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] No se ha encontrado Python instalado.
    echo.
    echo Instala Python 3 desde:
    echo https://www.python.org/downloads/
    echo.
    echo IMPORTANTE:
    echo Marca la casilla "Add Python to PATH" durante la instalacion.
    echo.
    goto FIN
)

echo [OK] Python encontrado:
%PYTHON_CMD% --version
echo.

REM Crear entorno virtual si no existe
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creando entorno virtual .venv...
    %PYTHON_CMD% -m venv .venv

    if not exist ".venv\Scripts\python.exe" (
        echo [ERROR] No se pudo crear el entorno virtual.
        goto FIN
    )
) else (
    echo [OK] Ya existe entorno virtual .venv
)

echo.

REM Actualizar pip
echo [INFO] Actualizando pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip

if %errorlevel% neq 0 (
    echo [ERROR] Fallo actualizando pip.
    goto FIN
)

echo.

REM Instalar dependencias
if exist "requirements.txt" (
    echo [INFO] Instalando dependencias desde requirements.txt...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
) else (
    echo [WARN] No existe requirements.txt.
    echo [INFO] Instalando dependencias basicas manualmente...
    ".venv\Scripts\python.exe" -m pip install numpy pyserial PyQt6 pyqtgraph
)

if %errorlevel% neq 0 (
    echo [ERROR] Fallo instalando dependencias.
    echo.
    echo Posibles causas:
    echo - No hay internet
    echo - Python esta mal instalado
    echo - pip esta bloqueado
    echo - Antivirus o permisos de Windows haciendo de villano mediocre
    goto FIN
)

echo.

REM Crear .env si no existe
if not exist ".env" (
    echo [INFO] Creando archivo .env para este ordenador...

    > ".env" echo PROJECT_DIR=%CD%
    >> ".env" echo PYTHON_REL=.venv\Scripts\python.exe
    >> ".env" echo MAIN_FILE=main.py

    echo [OK] .env creado.
) else (
    echo [OK] Ya existe archivo .env. No se modifica.
)

echo.

REM Comprobar main.py
if not exist "main.py" (
    echo [ERROR] No se encuentra main.py en esta carpeta.
    echo.
    echo Este instalador debe estar dentro de la carpeta principal de mtestv2.
    goto FIN
)

REM Prueba rapida de imports
echo [INFO] Comprobando librerias principales...
".venv\Scripts\python.exe" -c "import numpy, serial, PyQt6, pyqtgraph; print('OK_IMPORTS')"

if %errorlevel% neq 0 (
    echo [ERROR] Alguna libreria no se importa correctamente.
    goto FIN
)

echo.
echo ============================================
echo INSTALACION COMPLETADA
echo ============================================
echo.
echo Ahora puedes ejecutar:
echo ARRANCAR_PPG.cmd
echo.
echo O manualmente:
echo .venv\Scripts\python.exe main.py
echo.

:FIN
echo.
echo Pulsa una tecla para salir.
pause >nul
endlocal