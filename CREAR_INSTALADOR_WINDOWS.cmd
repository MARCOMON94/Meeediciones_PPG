@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_windows_release.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo La creacion del instalador fallo con codigo %EXIT_CODE%.
)

echo.
pause
exit /b %EXIT_CODE%

