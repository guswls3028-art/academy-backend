@echo off
chcp 65001 >nul
title Academy Backend (Local)
color 0A

echo ========================================
echo   Academy Backend - Local Development
echo ========================================
echo.

cd /d C:\academy

REM 가상환경 활성화 (있는 경우)
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo Starting Django server...
echo Backend URL: http://localhost:8000
echo.

python manage.py runserver 0.0.0.0:8000

pause
