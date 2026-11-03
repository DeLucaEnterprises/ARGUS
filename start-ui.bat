@echo off
REM ARGUS - UI v2. Double-click this in the argus folder. No terminal needed.
REM Installs Flask into the venv if needed, then starts the UI.
REM The browser opens on its own at http://127.0.0.1:5000.
REM Leave this window open while you use ARGUS. Close it (or Ctrl+C) to stop.
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo ERROR: .venv not found in this folder.
  echo You must be in C:\Users\markj\Downloads\argus-starter\argus, not in argus-starter.
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install flask
.venv\Scripts\python src\app.py
pause
