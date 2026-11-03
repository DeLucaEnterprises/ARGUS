@echo off
REM ARGUS - one-time setup for a fresh clone (Windows).
REM Double-click this in the repo folder. No terminal knowledge needed.
REM It creates .venv, installs requirements, creates data folders,
REM and creates .env from .env.example ONLY if .env does not exist yet.
REM It never overwrites an existing .env and never runs the pipeline.
cd /d %~dp0
if not exist src\app.py (
  echo ERROR: src\app.py not found in this folder.
  echo You must run setup.bat from the repo folder itself,
  echo the one that contains src\, web\, sample-data\ and start-ui.bat,
  echo not from its parent folder.
  pause
  exit /b 1
)
echo Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found as "python". Trying "py"...
  py --version >nul 2>&1
  if errorlevel 1 (
    echo Python is not installed or not on PATH.
    echo Install Python 3.11 or newer and tick "Add Python to PATH", then run this again.
    pause
    exit /b 1
  )
  set PY=py
) else (
  set PY=python
)
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  %PY% -m venv .venv
) else (
  echo Virtual environment already exists, reusing it.
)
echo Installing requirements...
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Install failed. Check your internet connection and run setup.bat again.
  pause
  exit /b 1
)
if not exist data\input mkdir data\input
if not exist data\tiles mkdir data\tiles
if not exist data\output mkdir data\output
if not exist .env (
  if exist .env.example (
    copy .env.example .env >nul
    echo Created .env from .env.example.
  )
)
echo.
echo Setup done.
if not exist .env (
  echo Next: create .env from .env.example and add your Meta key.
) else (
  echo Next: open .env in Notepad and make sure META_API_KEY is your real key,
  echo not "your-key-here". Get a key at https://dev.meta.ai/api-keys
)
echo Then double-click start-ui.bat, click USE DEMO PHOTOS, and hit Run pipeline.
echo Leave the start-ui window open while you use ARGUS.
pause
