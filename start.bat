@echo off
rem Double-click to open the Batch Release Autofiler window
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo First run: creating the Python environment...
    py -3 -m venv .venv || goto :error
)
rem Install packages on the first run, and again whenever requirements.txt changed (e.g. after git pull)
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
    echo Installing Python packages, this takes a minute...
    .venv\Scripts\python -m pip install -q -r requirements.txt || goto :error
    copy /y requirements.txt .venv\requirements.installed >nul
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
start "" .venv\Scripts\pythonw.exe gui.py
exit /b 0

:error
echo Setup failed - make sure Python 3.11+ is installed (py launcher) and the network is reachable.
pause
