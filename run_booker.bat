@echo off
setlocal
REM AsimutBooker - Run booking script
REM This batch file is called by Task Scheduler

cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

REM Create logs directory structure: logs/YYYY-MM-DD/
REM Use PowerShell to get date in correct format (works regardless of locale)
for /f "usebackq" %%i in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd'"`) do set "TODAY=%%i"
set "LOGDIR=logs\%TODAY%"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Generate timestamp for log filename: HH-MM-SS
for /f "usebackq" %%i in (`powershell -NoProfile -Command "Get-Date -Format 'HH-mm-ss'"`) do set "TIMESTAMP=%%i"
set "LOGFILE=%LOGDIR%\%TIMESTAMP%.log"

REM Scheduled runs must use the verified repository runtime; never fall back globally.
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

REM Run the booking script (headless for scheduled runs) and capture output
REM %* passes scheduler-specific arguments (normally --scheduled).
echo [%date% %time%] Starting scheduled booking run... > "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
if not exist "%PYTHON_EXE%" (
    echo ERROR: Required isolated Python runtime is missing: %PYTHON_EXE% >> "%LOGFILE%"
    echo [%date% %time%] Run exited 10 - see %LOGFILE% >> logs\scheduler.log
    exit /b 10
)

"%PYTHON_EXE%" -c "import playwright, yaml, book_week; assert book_week._CONFIG_ERROR is None, book_week._CONFIG_ERROR" >> "%LOGFILE%" 2>&1
set "RUNTIME_CHECK_EXIT=%ERRORLEVEL%"
if not "%RUNTIME_CHECK_EXIT%"=="0" (
    echo ERROR: Isolated runtime health check failed with exit code %RUNTIME_CHECK_EXIT%. >> "%LOGFILE%"
    echo [%date% %time%] Run exited %RUNTIME_CHECK_EXIT% - see %LOGFILE% >> logs\scheduler.log
    exit /b %RUNTIME_CHECK_EXIT%
)

"%PYTHON_EXE%" book_week.py --headless %* >> "%LOGFILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo ============================================================ >> "%LOGFILE%"
echo [%date% %time%] Booking run completed with exit code %EXIT_CODE% >> "%LOGFILE%"

REM Also append summary to main scheduler log
echo [%date% %time%] Run exited %EXIT_CODE% - see %LOGFILE% >> logs\scheduler.log

exit /b %EXIT_CODE%
