@echo off
rem Double-click to open the Batch Release Autofiler window
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo First run: creating the Python environment...
    py -m venv .venv || goto :error
    .venv\Scripts\pip install -r requirements.txt || goto :error
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
start "" .venv\Scripts\pythonw.exe gui.py
exit /b 0

:error
echo Setup failed - make sure Python 3.11+ is installed (py launcher).
pause
