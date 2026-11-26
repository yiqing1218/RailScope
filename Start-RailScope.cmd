@echo off
setlocal
if not defined RAILSCOPE_NEXT_PACKS set "RAILSCOPE_NEXT_PACKS=%~dp0data\processed\railscope-next\packs"
set "RAILSCOPE_NEXT_EXE=%~dp0railscope-next\target\release\railscope-next.exe"
if not exist "%RAILSCOPE_NEXT_EXE%" set "RAILSCOPE_NEXT_EXE=%~dp0railscope-next\target\debug\railscope-next.exe"
if not exist "%RAILSCOPE_NEXT_EXE%" (
  echo RailScope Next has not been built. See "%~dp0railscope-next\README.md".
  if /i not "%~1"=="--no-pause" pause
  exit /b 1
)
start "" /d "%~dp0." "%RAILSCOPE_NEXT_EXE%"
