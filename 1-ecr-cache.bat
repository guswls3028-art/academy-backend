@echo off
title ECR - 캐시 기반 (push)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0go.ps1" %*
if errorlevel 1 echo [오류] 위 메시지 확인.
echo.
pause
