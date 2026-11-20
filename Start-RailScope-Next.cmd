@echo off
setlocal
set "RAILSCOPE_NEXT_PACKS=%~dp0data\processed\railscope-next\packs"
set "RAILSCOPE_NEXT_EXE=%~dp0railscope-next\target\release\railscope-next.exe"
if not exist "%RAILSCOPE_NEXT_EXE%" set "RAILSCOPE_NEXT_EXE=%~dp0railscope-next\target\debug\railscope-next.exe"
if not exist "%RAILSCOPE_NEXT_EXE%" (
  echo RailScope Next has not been built. See railscope-next\README.md.
  exit /b 1
)
start "" "%RAILSCOPE_NEXT_EXE%"
