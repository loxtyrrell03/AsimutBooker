# AsimutBooker - Task Scheduler Setup Script
# Run this script as Administrator to create scheduled tasks
# Usage: Right-click PowerShell -> Run as Administrator -> .\setup_scheduled_tasks.ps1

$ErrorActionPreference = "Stop"

# Configuration
$TaskName = "AsimutBooker"
$ScriptPath = Join-Path $PSScriptRoot "run_booker.bat"
$WorkingDir = $PSScriptRoot

# Times to run (every 30 minutes from 07:30 to 22:00)
$RunTimes = @(
    "07:30", "08:00", "08:30", "09:00", "09:30",
    "10:00", "10:30", "11:00", "11:30", "12:00",
    "12:30", "13:00", "13:30", "14:00", "14:30",
    "15:00", "15:30", "16:00", "16:30", "17:00",
    "17:30", "18:00", "18:30", "19:00", "19:30",
    "20:00", "20:30", "21:00", "21:30", "22:00"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "AsimutBooker Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    exit 1
}

# Check if script exists
if (-not (Test-Path $ScriptPath)) {
    Write-Host "ERROR: run_booker.bat not found at $ScriptPath" -ForegroundColor Red
    exit 1
}

Write-Host "Script path: $ScriptPath" -ForegroundColor Green
Write-Host "Working directory: $WorkingDir" -ForegroundColor Green
Write-Host ""

# Remove existing tasks
Write-Host "Removing existing AsimutBooker tasks..." -ForegroundColor Yellow
Get-ScheduledTask | Where-Object { $_.TaskName -like "AsimutBooker*" } | ForEach-Object {
    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
    Write-Host "  Removed: $($_.TaskName)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Creating scheduled tasks..." -ForegroundColor Yellow

# Create action
$Action = New-ScheduledTaskAction -Execute $ScriptPath -WorkingDirectory $WorkingDir

# Create settings
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Create principal (run whether user is logged on or not requires password)
# For simplicity, we'll run only when logged on
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$count = 0
foreach ($time in $RunTimes) {
    $TaskFullName = "${TaskName}_$($time.Replace(':', ''))"

    # Create trigger for this specific time, daily
    $Trigger = New-ScheduledTaskTrigger -Daily -At $time

    # Register the task
    Register-ScheduledTask `
        -TaskName $TaskFullName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "AsimutBooker automatic room booking at $time" | Out-Null

    $count++
    Write-Host "  Created: $TaskFullName (runs at $time daily, wakes PC)" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Created $count scheduled tasks." -ForegroundColor White
Write-Host ""
Write-Host "IMPORTANT NOTES:" -ForegroundColor Yellow
Write-Host "1. Tasks are set to WAKE your PC from sleep" -ForegroundColor White
Write-Host "2. Tasks run daily at each scheduled time" -ForegroundColor White
Write-Host "3. Your PC must be plugged in (not on battery only)" -ForegroundColor White
Write-Host "4. Hibernate mode may not wake reliably - use Sleep instead" -ForegroundColor White
Write-Host ""
Write-Host "To view tasks: Open Task Scheduler -> Task Scheduler Library" -ForegroundColor Gray
Write-Host "To remove all tasks: Run this script again or delete manually" -ForegroundColor Gray
Write-Host ""

# Verify wake timers are enabled
Write-Host "Checking power settings..." -ForegroundColor Yellow
$wakeTimers = powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>$null
if ($wakeTimers -match "0x00000000") {
    Write-Host ""
    Write-Host "Enabling wake timers..." -ForegroundColor Yellow
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
    powercfg /setactive SCHEME_CURRENT
    Write-Host "Wake timers enabled." -ForegroundColor Green
} else {
    Write-Host "Wake timers already enabled." -ForegroundColor Green
}

# Configure lid close action to "Do nothing" when plugged in
Write-Host ""
Write-Host "Configuring lid close action..." -ForegroundColor Yellow
Write-Host "Setting lid close to 'Do nothing' when plugged in..." -ForegroundColor Gray

# LIDACTION: 0 = Do nothing, 1 = Sleep, 2 = Hibernate, 3 = Shut down
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

Write-Host "Lid close action set to 'Do nothing' (when plugged in)." -ForegroundColor Green
Write-Host ""
Write-Host "NOTE: Your laptop will now stay awake with the lid closed" -ForegroundColor Yellow
Write-Host "      (only when plugged into power)." -ForegroundColor Yellow
Write-Host ""
Write-Host "To revert this later, run:" -ForegroundColor Gray
Write-Host "  powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1" -ForegroundColor Cyan

Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
