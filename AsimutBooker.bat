@echo off
REM AsimutBooker GUI Launcher
REM Double-click this file to open the control panel

cd /d "%~dp0"

REM Activate virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Launch GUI (pythonw for no console window)
start "" pythonw gui.py
