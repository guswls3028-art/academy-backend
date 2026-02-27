@echo off
chcp 65001 >nul
title Academy Local Dev (Backend + Frontend)
color 0B

echo ========================================
echo   Academy Local Development
echo   Backend + Frontend
echo ========================================
echo.

REM 8000, 5174, 5175, 5176 포트 사용 중인 프로세스 모두 종료
echo [CLEANUP] Stopping any process on ports 8000, 5174, 5175, 5176...
powershell -NoProfile -Command "$ports=8000,5174,5175,5176; foreach ($p in $ports) { Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue | ForEach-Object { if ($_.OwningProcess) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } } }"
timeout /t 1 /nobreak >nul
echo.

cd /d C:\academy

REM 가상환경 활성화 (있는 경우)
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM 백엔드를 백그라운드로 실행
echo Starting Backend server...
start /b python manage.py runserver 0.0.0.0:8000 >nul 2>&1

REM 잠시 대기 (백엔드가 시작될 시간)
timeout /t 3 /nobreak >nul

REM 프론트엔드 실행 (포그라운드 - 이 창에서 실행)
echo Starting Frontend server...
echo.
echo ========================================
echo   Servers running
echo ========================================
echo.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:5174
echo.
echo Press Ctrl+C to stop both servers
echo.

cd /d C:\academyfront
pnpm dev

REM 프론트엔드가 종료되면 백엔드도 종료
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Academy Local Dev*" >nul 2>&1
