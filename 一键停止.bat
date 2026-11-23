@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1" %*
set "EXIT_CODE=%errorlevel%"
if "%~1"=="" pause
exit /b %EXIT_CODE%
