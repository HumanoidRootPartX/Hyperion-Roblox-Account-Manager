@echo off
REM Build Hyperion Account Manager into a single .exe
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  ".venv\Scripts\python.exe" -m playwright install chromium
)
".venv\Scripts\python.exe" -m pip install pyinstaller==6.11.1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm "hyperion_am.spec"
echo.
echo Build complete:  dist\Hyperion Account Manager.exe
