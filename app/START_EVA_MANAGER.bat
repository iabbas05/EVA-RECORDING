@echo off
if not exist "%~dp0EVA.exe" (
  echo EVA.exe is missing. Extract the complete EVA release and run Install_EVA.bat.
  pause
  exit /b 1
)
start "EVA" "%~dp0EVA.exe"
