@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" %*
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [CodePoet] Launcher exited with code %EXIT_CODE%.
    if "%~1"=="" pause
)
exit /b %EXIT_CODE%
