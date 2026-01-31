@echo off
REM AsimutBooker - Run booking script
REM This batch file is called by Task Scheduler

cd /d "%~dp0"

REM Activate virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Run the booking script (headless for scheduled runs)
python book_week.py --headless

REM Log completion
echo [%date% %time%] Booking run completed >> logs\scheduler.log
