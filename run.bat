@echo off
cd /d "%~dp0"

echo ================================================
echo   BatMUD CN - Web Client + Translation
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.9+
    pause
    exit /b 1
)

echo [1/2] Installing dependencies...
pip install -r requirements.txt -q 2>nul
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 2
)

echo [2/2] Starting server...
echo.
python -m src.main
pause
