@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHONW=%PROJECT_DIR%.venv\Scripts\pythonw.exe"
set "GUI=%PROJECT_DIR%gui.py"

if not exist "%PYTHONW%" (
    echo AsimutBooker cannot start because its Python environment is missing:
    echo   %PYTHONW%
    echo.
    echo From this folder, create and install it with:
    echo   py -3.12 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e .
    echo.
    pause
    exit /b 2
)

if not exist "%GUI%" (
    echo AsimutBooker cannot start because gui.py is missing:
    echo   %GUI%
    pause
    exit /b 3
)

start "AsimutBooker" /D "%PROJECT_DIR%" "%PYTHONW%" "%GUI%"
if errorlevel 1 (
    echo Failed to launch the AsimutBooker control panel.
    pause
    exit /b 1
)

exit /b 0
