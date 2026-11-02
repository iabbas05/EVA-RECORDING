@echo off
title EVA Install / Update
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_update.ps1" -RecordingLayout streamlabs_game
if errorlevel 1 (
  echo EVA update failed. The previous app was preserved or restored. Read the error above.
  pause
  exit /b 1
)
echo EVA is ready. Open the EVA desktop shortcut.
pause
