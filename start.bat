@echo off
REM Hyperion Account Manager launcher
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: creating virtual environment...
  py -3.12 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  echo Installing the login browser - one-time, ~130MB...
  ".venv\Scripts\python.exe" -m playwright install chromium
)
".venv\Scripts\python.exe" main.py
