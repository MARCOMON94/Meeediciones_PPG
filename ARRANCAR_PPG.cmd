@echo off
setlocal EnableExtensions

REM =====================================================
REM Lanzador mtestv2
REM Lee la ruta del proyecto desde un archivo .env
REM El .env debe estar en la misma carpeta que este .bat
REM =====================================================

set "ENV_FILE=%~dp0.env"

echo ============================================
echo Arrancando programa de medicion desarrollado en conjunto con Triple M...
echo ============================================
echo.

REM Valores por defecto
set "PROJECT_DIR="
set "PYTHON_REL=.venv\Scripts\python.exe"
set "MAIN_FILE=main.py"

REM Comprobar que existe .env
if not exist "%ENV_FILE%" (
    echo [ERROR] No se encuentra el archivo .env junto al lanzador.
    echo Ruta esperada:
    echo "%ENV_FILE%"
    echo.
    goto FIN
)

REM Leer .env
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /I "%%A"=="PROJECT_DIR" set "PROJECT_DIR=%%B"
    if /I "%%A"=="PYTHON_REL" set "PYTHON_REL=%%B"
    if /I "%%A"=="MAIN_FILE" set "MAIN_FILE=%%B"
)

REM Validar PROJECT_DIR
if "%PROJECT_DIR%"=="" (
    echo [ERROR] PROJECT_DIR no esta definido en el archivo .env.
    echo Ejemplo:
    echo PROJECT_DIR=C:\Users\julia\OneDrive\Desktop\tesis\mtestv2
    echo.
    goto FIN
)

REM Quitar posibles comillas si alguien las puso en el .env
set "PROJECT_DIR=%PROJECT_DIR:"=%"
set "PYTHON_REL=%PYTHON_REL:"=%"
set "MAIN_FILE=%MAIN_FILE:"=%"

REM Comprobar carpeta del proyecto
if not exist "%PROJECT_DIR%" (
    echo [ERROR] La carpeta del proyecto no existe:
    echo "%PROJECT_DIR%"
    echo.
    echo Revisa PROJECT_DIR en el archivo .env.
    goto FIN
)

cd /d "%PROJECT_DIR%"

REM Construir rutas
set "PYTHON_EXE=%PROJECT_DIR%\%PYTHON_REL%"
set "MAIN_PATH=%PROJECT_DIR%\%MAIN_FILE%"

REM Comprobar Python del entorno virtual
if not exist "%PYTHON_EXE%" (
    echo [ERROR] No se encuentra Python del entorno virtual:
    echo "%PYTHON_EXE%"
    echo.
    echo Posibles causas:
    echo - No existe la carpeta .venv
    echo - El entorno virtual esta en otra ruta
    echo - No se ha creado el entorno virtual en este ordenador
    echo.
    echo Si hace falta crearlo, desde la carpeta del proyecto:
    echo python -m venv .venv
    echo .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    goto FIN
)

REM Comprobar main.py
if not exist "%MAIN_PATH%" (
    echo [ERROR] No se encuentra el archivo principal:
    echo "%MAIN_PATH%"
    echo.
    echo Revisa MAIN_FILE en el archivo .env.
    goto FIN
)

echo [OK] Carpeta del proyecto:
echo "%PROJECT_DIR%"
echo.
echo [OK] Python:
echo "%PYTHON_EXE%"
echo.
echo [OK] Archivo principal:
echo "%MAIN_PATH%"
echo.

"%PYTHON_EXE%" "%MAIN_PATH%"

echo.
echo ============================================
echo Programa cerrado.
echo ============================================

:FIN
echo.
echo Pulsa una tecla para salir.
pause >nul
endlocal