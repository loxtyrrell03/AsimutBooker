[CmdletBinding()]
param(
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$StartTime = "07:30",

    [ValidatePattern('^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')]
    [string]$EndTime = "22:15",

    [ValidateRange(15, 15)]
    [int]$IntervalMinutes = 15,

    [ValidateRange(0, 600)]
    [int]$LeadSeconds = 120,

    [ValidateRange(5, 120)]
    [int]$ExecutionTimeLimitMinutes = 12,

    [switch]$Disabled,

    [switch]$ValidateOnly,

    [switch]$NoPause
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "AsimutBooker"
$ProjectRoot = $PSScriptRoot
$WrapperPath = Join-Path $ProjectRoot "run_booker.ps1"
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Convert-MinutesToIsoDuration {
    param([Parameter(Mandatory = $true)][int]$Minutes)
    $hours = [math]::Floor($Minutes / 60)
    $remainingMinutes = $Minutes % 60
    if ($hours -gt 0 -and $remainingMinutes -gt 0) {
        return "PT${hours}H${remainingMinutes}M"
    }
    if ($hours -gt 0) {
        return "PT${hours}H"
    }
    return "PT${remainingMinutes}M"
}

function Escape-Xml {
    param([AllowEmptyString()][string]$Value)
    return [System.Security.SecurityElement]::Escape($Value)
}

try {
    if (-not $ValidateOnly) {
        if (-not (Test-Path -LiteralPath $WrapperPath -PathType Leaf)) {
            throw "Scheduler wrapper not found: $WrapperPath"
        }
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
            throw "Project Python not found: $PythonPath`nCreate .venv and install the project before scheduling it."
        }
    }

    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $startClock = [datetime]::ParseExact($StartTime, "HH:mm", $culture)
    $endClock = if ($EndTime -eq "24:00") {
        $startClock.Date.AddDays(1)
    } else {
        [datetime]::ParseExact($EndTime, "HH:mm", $culture)
    }
    if ($endClock -le $startClock) {
        throw "EndTime must be later than StartTime on the same day."
    }

    # One daily calendar trigger repeats every 15 minutes. This avoids the old
    # burst of dozens of independent tasks when Windows resumes from sleep.
    $firstTarget = (Get-Date).Date.Add($startClock.TimeOfDay)
    $triggerBoundary = $firstTarget.AddSeconds(-$LeadSeconds)
    $windowMinutes = [int](($endClock - $startClock).TotalMinutes)
    # Include the final target without keeping the trigger active for another interval.
    $repetitionDuration = Convert-MinutesToIsoDuration ($windowMinutes + 1)
    $executionLimit = Convert-MinutesToIsoDuration $ExecutionTimeLimitMinutes

    $powerShellPath = (Get-Command "powershell.exe" -ErrorAction Stop).Source
    $actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$WrapperPath`""
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $author = $userId
    $registeredAt = (Get-Date).ToString("s")
    $taskEnabledText = if ($Disabled) { "false" } else { "true" }

    $escapedPowerShell = Escape-Xml $powerShellPath
    $escapedArguments = Escape-Xml $actionArguments
    $escapedRoot = Escape-Xml $ProjectRoot
    $escapedUser = Escape-Xml $userId
    $escapedAuthor = Escape-Xml $author
    $startBoundaryText = $triggerBoundary.ToString("yyyy-MM-dd'T'HH:mm:ss")

    $taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$registeredAt</Date>
    <Author>$escapedAuthor</Author>
    <Description>AsimutBooker single-instance booking coordinator. Targets $StartTime-$EndTime every $IntervalMinutes minutes and starts $LeadSeconds seconds early.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT${IntervalMinutes}M</Interval>
        <Duration>$repetitionDuration</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$startBoundaryText</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$escapedUser</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>$taskEnabledText</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>$executionLimit</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$escapedPowerShell</Command>
      <Arguments>$escapedArguments</Arguments>
      <WorkingDirectory>$escapedRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

    # Force a well-formed XML parse even during normal installation so quoting
    # or path characters cannot register a malformed task definition.
    $null = [xml]$taskXml
    if ($ValidateOnly) {
        Write-Host "Scheduled task definition is valid." -ForegroundColor Green
        Write-Host "  First trigger:       $startBoundaryText"
        Write-Host "  Repetition:          every $IntervalMinutes minutes for $repetitionDuration"
        Write-Host "  Instance policy:    IgnoreNew"
        exit 0
    }

    # Register the replacement first. If registration fails, the existing
    # schedule remains intact.
    Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force | Out-Null

    # Remove only the obsolete per-time tasks after the canonical task exists.
    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like "AsimutBooker_*" } |
        ForEach-Object {
            Unregister-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -Confirm:$false
        }

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop

    Write-Host "AsimutBooker scheduler installed successfully." -ForegroundColor Green
    Write-Host "  Task name:          $TaskName"
    Write-Host "  Target window:      $StartTime - $EndTime"
    Write-Host "  Cadence:            every $IntervalMinutes minutes"
    Write-Host "  Trigger lead:       $LeadSeconds seconds"
    Write-Host "  Instance policy:    $($task.Settings.MultipleInstances)"
    Write-Host "  Wake from sleep:    $($task.Settings.WakeToRun)"
    Write-Host "  Enabled:            $(-not $Disabled)"
    Write-Host "  Next run:           $($info.NextRunTime)"
    Write-Host ""
    Write-Host "The task uses the current interactive Windows account." -ForegroundColor Yellow
    Write-Host "It runs while this account remains signed in, including on the lock screen."
    exit 0
}
catch {
    Write-Error $_
    if (-not $NoPause -and $Host.Name -notlike "*ServerRemoteHost*") {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
    exit 1
}
