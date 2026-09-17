@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0desktop\Run-RailScope.ps1" -NoPause
if errorlevel 1 pause
