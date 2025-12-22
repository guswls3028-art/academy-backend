chcp 65001 >nul

@echo off
title Django Auto Runner

echo STEP 1: Activate Virtual Env

call venv\Scripts\activate

python -V
pip -V

echo STEP 2: Run Migrations

python manage.py makemigrations
python manage.py migrate

echo STEP 3: Start Django Server
echo Open: http://127.0.0.1:8000/
echo.

python manage.py runserver

echo.
echo.
echo.
echo Server Stopped
pause
