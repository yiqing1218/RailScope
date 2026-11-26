@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0railscope-next\Start-RailScope.ps1"
if not errorlevel 1 exit /b 0
if /i not "%~1"=="--no-pause" pause
exit /b 1
