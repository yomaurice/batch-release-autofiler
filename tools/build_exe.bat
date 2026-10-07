@echo off
rem Build the standalone app (no Python needed on the target PC) into build_out\BatchRelease.zip
rem Run from anywhere; needs the project's .venv (start.bat creates it).
cd /d "%~dp0.."
.venv\Scripts\python -m pip install -q pyinstaller || goto :error
.venv\Scripts\pyinstaller gui.py --name BatchRelease --onedir --windowed --noconfirm --clean ^
    --collect-data customtkinter --collect-submodules keyring --add-data "%CD%\config.example.yaml;." ^
    --exclude-module pytest --exclude-module _pytest ^
    --distpath build_out\dist --workpath build_out\work --specpath build_out || goto :error
.venv\Scripts\python -c "import shutil; shutil.make_archive('build_out/BatchRelease', 'zip', 'build_out/dist', 'BatchRelease')" || goto :error
echo.
echo Built: build_out\BatchRelease.zip
exit /b 0

:error
echo Build failed.
exit /b 1
