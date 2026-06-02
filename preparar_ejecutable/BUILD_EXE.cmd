@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"

set "ROOT=%~dp0"
set "SRC=%ROOT%source"
set "VENV=%ROOT%.build_venv"

echo ============================================
echo Construyendo ejecutable Windows de mtestv2
echo ============================================
echo.

cd /d "%ROOT%"

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] No se encontro Python.
    echo Instala Python 3 y marca "Add Python to PATH".
    goto FIN
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [INFO] Creando entorno de build en:
    echo "%VENV%"
    %PYTHON_CMD% -m venv "%VENV%"
    if not exist "%VENV%\Scripts\python.exe" goto ERROR_VENV
)

echo [INFO] Instalando dependencias de empaquetado...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
if %errorlevel% neq 0 goto ERROR_PIP

"%VENV%\Scripts\python.exe" -m pip install -r "%SRC%\requirements.txt" pyinstaller
if %errorlevel% neq 0 goto ERROR_PIP

echo.
echo [INFO] Generando mtestv2.exe...
cd /d "%SRC%"
"%VENV%\Scripts\python.exe" -m PyInstaller --clean mtestv2.spec
if %errorlevel% neq 0 goto ERROR_BUILD

echo.
echo ============================================
echo BUILD COMPLETADA
echo ============================================
echo Ejecutable:
echo "%SRC%\dist\mtestv2.exe"
echo.
echo Al ejecutarse, los resultados se guardaran en:
echo "%%USERPROFILE%%\Documents\mtestv2\resultados"
echo.
goto FIN

:ERROR_VENV
echo [ERROR] No se pudo crear el entorno de build.
goto FIN

:ERROR_PIP
echo [ERROR] Fallo instalando dependencias. Revisa conexion a internet.
goto FIN

:ERROR_BUILD
echo [ERROR] Fallo PyInstaller generando el ejecutable.
goto FIN

:FIN
echo.
pause
endlocal
