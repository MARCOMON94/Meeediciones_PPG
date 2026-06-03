@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

REM =====================================================
REM Lanzador mtestv2
REM Lee la ruta del proyecto desde un archivo .env
REM El .env debe estar en la misma carpeta que este .cmd
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
set "AUTO_UPDATE_GIT=1"

REM Comprobar que existe .env
if not exist "%ENV_FILE%" (
    echo [ERROR] No se encuentra el archivo .env junto al lanzador.
    echo Ruta esperada:
    echo "%ENV_FILE%"
    echo.
    echo Crea un archivo llamado .env en la misma carpeta que este lanzador.
    echo.
    echo Ejemplo de contenido:
    echo PROJECT_DIR=C:\RUTA\A\mtestv2
    echo PYTHON_REL=.venv\Scripts\python.exe
    echo MAIN_FILE=main.py
    echo AUTO_UPDATE_GIT=0
    echo.
    goto FIN
)

REM Leer .env
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /I "%%A"=="PROJECT_DIR" set "PROJECT_DIR=%%B"
    if /I "%%A"=="PYTHON_REL" set "PYTHON_REL=%%B"
    if /I "%%A"=="MAIN_FILE" set "MAIN_FILE=%%B"
    if /I "%%A"=="AUTO_UPDATE_GIT" set "AUTO_UPDATE_GIT=%%B"
)

REM Validar PROJECT_DIR
if "%PROJECT_DIR%"=="" (
    echo [ERROR] PROJECT_DIR no esta definido en el archivo .env.
    echo.
    echo Ejemplo de contenido del .env:
    echo PROJECT_DIR=C:\RUTA\A\mtestv2
    echo PYTHON_REL=.venv\Scripts\python.exe
    echo MAIN_FILE=main.py
    echo AUTO_UPDATE_GIT=0
    echo.
    goto FIN
)

REM Quitar posibles comillas si alguien las puso en el .env
set "PROJECT_DIR=%PROJECT_DIR:"=%"
set "PYTHON_REL=%PYTHON_REL:"=%"
set "MAIN_FILE=%MAIN_FILE:"=%"
set "AUTO_UPDATE_GIT=%AUTO_UPDATE_GIT:"=%"

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
set "REQUIREMENTS_PATH=%PROJECT_DIR%\requirements.txt"

REM Asegurar UTF-8 tambien dentro de Python
set "PYTHONUTF8=1"
REM Evitar que Git haga preguntas interactivas y bloquee el arranque.
set "GIT_TERMINAL_PROMPT=0"
set "GIT_ASKPASS=echo"

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
    echo Si hace falta crearlo, desde la carpeta del proyecto ejecuta:
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

REM Actualizar repositorio si Git esta disponible
if /I "%AUTO_UPDATE_GIT%"=="0" (
    echo [INFO] Actualizacion por Git desactivada en .env ^(AUTO_UPDATE_GIT=0^).
    echo.
) else if exist "%PROJECT_DIR%\.git" (
    where git >nul 2>nul
    if not errorlevel 1 (
        echo [INFO] Comprobando si hay cambios locales antes de actualizar...
        git -c gc.auto=0 -c maintenance.auto=false diff --quiet >nul 2>nul
        set "GIT_DIRTY_WORKTREE=!errorlevel!"
        git -c gc.auto=0 -c maintenance.auto=false diff --cached --quiet >nul 2>nul
        set "GIT_DIRTY_INDEX=!errorlevel!"

        if not "!GIT_DIRTY_WORKTREE!"=="0" (
            echo.
            echo [WARN] Hay cambios locales en el repositorio. Se omite actualizacion automatica.
            echo [WARN] El programa arrancara con la version local para no tocar archivos de este ordenador.
            echo.
        ) else (
            if not "!GIT_DIRTY_INDEX!"=="0" (
                echo.
                echo [WARN] Hay cambios preparados en Git. Se omite actualizacion automatica.
                echo [WARN] El programa arrancara con la version local.
                echo.
            ) else (
                echo [INFO] Actualizando repositorio sin preguntas interactivas...
                git -c gc.auto=0 -c maintenance.auto=false pull --ff-only --no-edit <nul
                if errorlevel 1 (
                    echo.
                    echo [WARN] No se pudo actualizar automaticamente con Git.
                    echo [WARN] El programa continuara con la version local.
                    echo [WARN] Si Git tenia una pregunta de y/n, queda evitada para no bloquear el arranque.
                    echo.
                ) else (
                    echo [OK] Repositorio actualizado.
                    echo.
                )
            )
        )
    ) else (
        echo [WARN] Git no esta disponible en PATH. Se omite actualizacion automatica.
        echo.
    )
) else (
    echo [WARN] Esta carpeta no parece ser un repositorio Git. Se omite git pull.
    echo.
)

REM Comprobar dependencias principales e instalarlas si faltan
echo [INFO] Comprobando dependencias Python...
"%PYTHON_EXE%" -c "import numpy, serial, PyQt6, pyqtgraph; print('OK_IMPORTS')" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Faltan dependencias o alguna no importa correctamente.

    if exist "%REQUIREMENTS_PATH%" (
        echo [INFO] Instalando dependencias desde requirements.txt...
        "%PYTHON_EXE%" -m pip install -r "%REQUIREMENTS_PATH%"
    ) else (
        echo [WARN] No existe requirements.txt. Instalando dependencias basicas...
        "%PYTHON_EXE%" -m pip install numpy pyserial PyQt6 pyqtgraph
    )

    if errorlevel 1 (
        echo [ERROR] Fallo instalando dependencias.
        echo.
        echo Revisa la conexion a internet o ejecuta instalarmtestv2.cmd.
        goto FIN
    )

    echo [INFO] Reintentando comprobacion de dependencias...
    "%PYTHON_EXE%" -c "import numpy, serial, PyQt6, pyqtgraph; print('OK_IMPORTS')" >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Las dependencias siguen sin importar correctamente.
        goto FIN
    )
)
echo [OK] Dependencias listas.
echo.

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
