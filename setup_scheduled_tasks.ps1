# AsimutBooker - Task Scheduler Setup Script
# Run as Administrator to install one non-overlapping repeated task.

$ErrorActionPreference = "Stop"

$TaskName = "AsimutBooker_Recurring"
$ScriptPath = Join-Path $PSScriptRoot "run_booker.bat"
$PythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$WorkingDir = $PSScriptRoot
$FirstRunTime = "07:13"
$RepeatMinutes = 15
# 07:13 + 59 repetitions = 21:58. The next repetition (22:13) is excluded.
$RepeatDurationIso = "PT14H46M"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "AsimutBooker Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    Write-Error "run_booker.bat was not found at $ScriptPath"
    exit 1
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    Write-Error "The isolated Python runtime is missing at $PythonPath"
    exit 1
}

& $PythonPath -c "import playwright, yaml, book_week; assert book_week._CONFIG_ERROR is None, book_week._CONFIG_ERROR"
if ($LASTEXITCODE -ne 0) {
    throw "The isolated Python runtime could not import the booking application (exit code $LASTEXITCODE)."
}

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 14) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

$CmdPath = Join-Path $env:SystemRoot "System32\cmd.exe"
$ActionArguments = '/d /c ""{0}" --scheduled"' -f $ScriptPath
$Action = New-ScheduledTaskAction `
    -Execute $CmdPath `
    -Argument $ActionArguments `
    -WorkingDirectory $WorkingDir

$Trigger = New-ScheduledTaskTrigger -Daily -At $FirstRunTime
$Repetition = New-CimInstance `
    -ClientOnly `
    -Namespace "Root/Microsoft/Windows/TaskScheduler" `
    -ClassName "MSFT_TaskRepetitionPattern" `
    -Property @{
        Interval = "PT${RepeatMinutes}M"
        Duration = $RepeatDurationIso
        StopAtDurationEnd = $false
    }
$Trigger.Repetition = $Repetition

Write-Host "Configuring AC/DC wake timers and plugged-in lid behavior..." -ForegroundColor Yellow
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
if ($LASTEXITCODE -ne 0) {
    throw "Failed to enable AC wake timers (powercfg exit code $LASTEXITCODE)."
}
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
if ($LASTEXITCODE -ne 0) {
    throw "Failed to enable battery wake timers (powercfg exit code $LASTEXITCODE)."
}
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set the plugged-in lid action (powercfg exit code $LASTEXITCODE)."
}
powercfg /setactive SCHEME_CURRENT
if ($LASTEXITCODE -ne 0) {
    throw "Failed to activate the updated power plan (powercfg exit code $LASTEXITCODE)."
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath "\" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "AsimutBooker automatic booking every 15 minutes from 07:13 through 21:58." `
    -Force |
    Out-Null

$RegisteredTask = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction Stop
if (
    -not $RegisteredTask.Settings.WakeToRun -or
    -not $RegisteredTask.Settings.StartWhenAvailable -or
    $RegisteredTask.Settings.MultipleInstances -ne "IgnoreNew"
) {
    throw "The task was registered but its wake/recovery settings were not retained."
}
$RegisteredTrigger = $RegisteredTask.Triggers[0]
if (
    $RegisteredTrigger.Repetition.Interval -ne "PT15M" -or
    $RegisteredTrigger.Repetition.Duration -ne $RepeatDurationIso
) {
    throw "The task was registered without the expected repetition window."
}
$RegisteredAction = $RegisteredTask.Actions[0]
if ($RegisteredAction.Execute -ne $CmdPath -or $RegisteredAction.Arguments -notlike "*--scheduled*") {
    throw "The task was registered without the expected scheduled launcher action."
}

# Remove obsolete task shapes only after the replacement task is registered and
# verified. A dependency or power-policy failure therefore leaves any existing
# working schedule untouched.
Write-Host "Removing obsolete AsimutBooker tasks from the Task Scheduler root..." -ForegroundColor Yellow
$legacyTasks = @(
    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object {
            $_.TaskPath -eq "\" -and (
                $_.TaskName -eq "AsimutBooker" -or
                $_.TaskName -match '^AsimutBooker_[0-2][0-9][0-5][0-9]$' -or
                $_.TaskName -match '^AsimutBooker-[0-2][0-9][0-5][0-9]$'
            )
        }
)
foreach ($legacyTask in $legacyTasks) {
    Unregister-ScheduledTask `
        -TaskName $legacyTask.TaskName `
        -TaskPath $legacyTask.TaskPath `
        -Confirm:$false
    Write-Host "  Removed obsolete task: $($legacyTask.TaskName)" -ForegroundColor Gray
}

Write-Host "Created: $TaskName" -ForegroundColor Green
Write-Host "  Repeats every 15 minutes from 07:13 through 21:58." -ForegroundColor White
Write-Host "  Runs headless, requests wake on AC or battery, catches up after a missed start, and ignores overlapping starts." -ForegroundColor White
Write-Host "  Actual wake-from-sleep still depends on Windows, firmware, and hardware support." -ForegroundColor White

Write-Host "Setup complete. No further input is required." -ForegroundColor Green
