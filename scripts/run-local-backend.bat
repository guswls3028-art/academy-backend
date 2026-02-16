@echo off
chcp 65001 >nul
title Academy Backend (Local)
color 0A

echo ========================================
echo   Academy Backend - Local Development
echo ========================================
echo.

cd /d C:\academy
if errorlevel 1 (
    echo ERROR: Cannot change to C:\academy directory
    pause
    exit /b 1
)

REM 가상환경 활성화 (있는 경우)
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
    if errorlevel 1 (
        echo ERROR: Failed to activate virtual environment
        pause
        exit /b 1
    )
) else (
    echo WARNING: Virtual environment not found at venv\Scripts\activate.bat
)

echo.
echo Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python is not available
    pause
    exit /b 1
)

echo.
echo Checking Django...
python -c "import django; print('Django version:', django.get_version())" 2>&1
if errorlevel 1 (
    echo ERROR: Django is not installed or not available
    pause
    exit /b 1
)

echo.
echo Database connection: Direct RDS (public access enabled)
echo No SSH tunnel needed.

echo.
echo Starting Django server...
echo Backend URL: http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo.

python manage.py runserver 0.0.0.0:8000

if errorlevel 1 (
    echo.
    echo ========================================
    echo   Server stopped with error!
    echo ========================================
    echo.
    echo Check the error messages above.
    echo.
)

pause
