@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0go.ps1" %*
if errorlevel 1 (
  echo.
  echo [오류] 위 메시지 확인 후 다시 시도하세요.
)
echo.
pause
