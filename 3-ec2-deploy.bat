@echo off
title EC2 4대 컨테이너 재생성
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -SkipBuild -StartInstances
if errorlevel 1 echo [오류] deploy.ps1 실패. 키 경로(C:\key) 및 AWS CLI 확인.
echo.
pause
