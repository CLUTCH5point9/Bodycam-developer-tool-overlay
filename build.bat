@echo off
REM Packages overlay_app.py into a standalone exe with PyInstaller.
REM Run this again any time you (or Claude) edit the .py/.json files, to
REM regenerate dist\BodycamOverlay.exe.

pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

pyinstaller --noconfirm --onefile --windowed --name BodycamOverlay ^
    --add-data "families.json;." ^
    --add-data "maps.json;." ^
    --add-data "gamemodes.json;." ^
    --add-data "app_icon.ico;." ^
    --add-data "mod;mod" ^
    --add-data "ue4ss_bundle;ue4ss_bundle" ^
    --hidden-import pystray._win32 ^
    --icon "app_icon.ico" ^
    overlay_app.py

echo.
echo Done. Exe is at dist\BodycamOverlay.exe
