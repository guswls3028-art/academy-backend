@echo off
title EC2 배포 (docker compose)
cd /d C:\academy
powershell -ExecutionPolicy Bypass -File "%~dp0deploy.ps1"
echo.
pause
