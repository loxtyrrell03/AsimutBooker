@echo off
REM AsimutBooker - Run booking script
REM This batch file is called by Task Scheduler

cd /d "%~dp0"

REM Create logs directory structure: logs/YYYY-MM-DD/
set "TODAY=%date:~10,4%-%date:~4,2%-%date:~7,2%"
set "LOGDIR=logs\%TODAY%"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Generate timestamp for log filename: HH-MM-SS
set "TIMESTAMP=%time:~0,2%-%time:~3,2%-%time:~6,2%"
REM Remove leading space from hour if present
set "TIMESTAMP=%TIMESTAMP: =0%"
set "LOGFILE=%LOGDIR%\%TIMESTAMP%.log"

REM Activate virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Run the booking script (headless for scheduled runs) and capture output
REM %* passes any additional arguments (e.g., --target-time 10:00)
echo [%date% %time%] Starting scheduled booking run... > "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
python book_week.py --headless %* >> "%LOGFILE%" 2>&1
echo ============================================================ >> "%LOGFILE%"
echo [%date% %time%] Booking run completed >> "%LOGFILE%"

REM Also append summary to main scheduler log
echo [%date% %time%] Run completed - see %LOGFILE% >> logs\scheduler.log
