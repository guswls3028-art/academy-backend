@echo off
chcp 65001 >nul
title Academy Local Dev (Backend + Frontend)
color 0B

REM 이 배치 파일이 있는 폴더(backend)에서 run-dev-single.ps1 실행
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-dev-single.ps1"
pause
