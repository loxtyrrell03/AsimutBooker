@echo off
setlocal
REM AsimutBooker GUI Launcher
REM Double-click this file to open the control panel

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Prefer the repository's isolated Python without relying on activation state.
if exist ".venv\Scripts\pythonw.exe" (
    set "PYTHONW_EXE=%~dp0.venv\Scripts\pythonw.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHONW_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    set "PYTHONW_EXE=pythonw.exe"
)

REM Launch GUI (pythonw for no console window)
start "" "%PYTHONW_EXE%" "%~dp0gui.py"
