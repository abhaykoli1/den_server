@echo off
REM One-click start for the Rowdy's Den Members App (Windows).
REM   start-members-app.bat          demo mode (in-memory Mongo + seeded club)
REM   start-members-app.bat --fresh  wipe demo data and reseed
REM   start-members-app.bat --prod   real MongoDB from .env
setlocal
cd /d "%~dp0"

set MODE=demo
set FRESH=0
for %%a in (%*) do (
  if "%%a"=="--fresh" set FRESH=1
  if "%%a"=="--prod" set MODE=prod
)

where python >nul 2>&1 || ( echo X python not found - install Python 3.10+ from python.org and tick "Add to PATH" & pause & exit /b 1 )

if not exist ".venv\Scripts\python.exe" (
  echo ^> creating virtualenv .venv ...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -c "import fastapi, motor, mongomock, jinja2, multipart, jwt" >nul 2>&1 || (
  echo ^> installing dependencies ...
  python -m pip install -q --upgrade pip
  pip install -q -r requirements.txt
)

if "%PORT%"=="" set PORT=8000

if "%MODE%"=="demo" (
  set MONGODB_URI=mongomock://demo
  set AUTH_DEV_MODE=true
  if "%FRESH%"=="1" ( echo ^> clearing demo data ... & rmdir /s /q .devdata 2>nul )
  if not exist ".devdata\snapshot.json" (
    echo ^> seeding demo club ...
    python scripts\seed_demo.py
  )
) else (
  if not exist ".env" ( echo X --prod needs a .env with MONGODB_URI and JWT_SECRET & pause & exit /b 1 )
)

echo.
echo   Rowdy's Den - Members App
echo   ---------------------------------------------
echo   Members app   http://localhost:%PORT%/members
echo   Billing API   http://localhost:%PORT%/api
if "%MODE%"=="demo" (
  echo   Demo logins   ravi@example.com  ^|  arjun@example.com
  echo   Reset data    start-members-app.bat --fresh
)
echo   Ctrl+C stops the server
echo   ---------------------------------------------
echo.
start "" http://localhost:%PORT%/members
python run.py
pause
