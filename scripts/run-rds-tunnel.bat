@echo off
chcp 65001 >nul
title RDS Tunnel
color 0E

echo ========================================
echo   RDS SSH Tunnel
echo ========================================
echo.
echo This tunnel connects localhost:5433 to RDS via EC2
echo Keep this window open while using the backend.
echo.
echo Press Ctrl+C to stop the tunnel.
echo.

cd /d C:\academy
powershell -ExecutionPolicy Bypass -File "scripts\setup_rds_tunnel.ps1"

pause
