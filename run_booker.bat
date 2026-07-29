@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0run_booker.ps1" %*
set "BOOKER_EXIT=%ERRORLEVEL%"

endlocal & exit /b %BOOKER_EXIT%
