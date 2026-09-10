@echo off
REM Quick start script for Rowdy's Den Members App (Windows)
REM This script sets up and starts the members app integrated with den_server

echo.
echo 🚀 Rowdy's Den Members App — Integrated with den_server
echo ==================================================
echo.

REM Check if Python 3 is available
python3 --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python 3 is not installed. Please install Python 3.8 or later.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python3 --version') do set PYTHON_VERSION=%%i
echo ✅ Python 3 found: %PYTHON_VERSION%

REM Install requirements if needed
if not exist "venv" (
    echo.
    echo 📦 Installing dependencies...
    pip install -r requirements.txt
)

REM Setup demo data (optional)
if "%1"=="--fresh" (
    echo.
    echo 🗑️  Clearing old demo data...
    if exist ".devdata\snapshot.json" (
        del .devdata\snapshot.json
    )
)

echo.
echo 🎬 Starting den_server...
echo ==================================================
echo.
echo 📱 Members app will be available at:
echo    🌐 http://localhost:8000/members
echo.
echo 📊 API docs at:
echo    🌐 http://localhost:8000/docs
echo.
echo Test logins (AUTH_DEV_MODE=true):
echo    📧 ravi@example.com
echo    📧 arjun@example.com
echo.
echo Press Ctrl+C to stop
echo ==================================================
echo.

REM Start the server
set MONGODB_URI=mongomock://demo
set AUTH_DEV_MODE=true
set PORT=8000
python3 run.py

pause
