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
    -Disable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 14) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentUser = $CurrentIdentity.Name
$CurrentUserSid = $CurrentIdentity.User.Value
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

function Assert-RegisteredTaskContract {
    param(
        [Parameter(Mandatory)]
        $RegisteredTask,

        [Parameter(Mandatory)]
        [bool]$ExpectedEnabled,

        [Parameter(Mandatory)]
        [ValidateSet("Disabled", "Ready")]
        [string]$ExpectedState
    )

    if (
        [bool]$RegisteredTask.Settings.Enabled -ne $ExpectedEnabled -or
        [string]$RegisteredTask.State -cne $ExpectedState
    ) {
        throw "The task was registered without the expected enabled state '$ExpectedEnabled' and runtime state '$ExpectedState'."
    }

    if (@($RegisteredTask.Actions).Count -ne 1) {
        throw "The task was registered without exactly one launcher action."
    }
    $RegisteredAction = $RegisteredTask.Actions[0]
    if (
        $RegisteredAction.Execute -ine $CmdPath -or
        $RegisteredAction.Arguments -cne $ActionArguments -or
        $RegisteredAction.WorkingDirectory -ine $WorkingDir
    ) {
        throw "The task was registered without the exact scheduled launcher contract."
    }

    if (@($RegisteredTask.Triggers).Count -ne 1) {
        throw "The task was registered without exactly one daily trigger."
    }
    $RegisteredTrigger = $RegisteredTask.Triggers[0]
    if (
        $RegisteredTrigger.CimClass.CimClassName -ne "MSFT_TaskDailyTrigger" -or
        -not $RegisteredTrigger.Enabled -or
        $RegisteredTrigger.DaysInterval -ne 1 -or
        $RegisteredTrigger.Repetition.Interval -ne "PT15M" -or
        $RegisteredTrigger.Repetition.Duration -ne $RepeatDurationIso -or
        $RegisteredTrigger.Repetition.StopAtDurationEnd
    ) {
        throw "The task was registered without the exact daily repetition contract."
    }

    try {
        $RegisteredStart = [DateTimeOffset]::Parse(
            [string]$RegisteredTrigger.StartBoundary,
            [Globalization.CultureInfo]::InvariantCulture
        )
    } catch {
        throw "The task was registered with an unreadable daily start boundary."
    }
    $RegisteredLocalTime = $RegisteredStart.ToLocalTime().ToString(
        "HH:mm",
        [Globalization.CultureInfo]::InvariantCulture
    )
    if ($RegisteredLocalTime -ne $FirstRunTime) {
        throw "The task was registered without the exact 07:13 local start time."
    }

    if (
        -not $RegisteredTask.Settings.WakeToRun -or
        -not $RegisteredTask.Settings.StartWhenAvailable -or
        -not $RegisteredTask.Settings.RunOnlyIfNetworkAvailable -or
        $RegisteredTask.Settings.DisallowStartIfOnBatteries -or
        $RegisteredTask.Settings.StopIfGoingOnBatteries -or
        $RegisteredTask.Settings.MultipleInstances -ne "IgnoreNew" -or
        $RegisteredTask.Settings.ExecutionTimeLimit -ne "PT14M" -or
        $RegisteredTask.Settings.RestartCount -ne 1 -or
        $RegisteredTask.Settings.RestartInterval -ne "PT1M"
    ) {
        throw "The task was registered without the exact wake, recovery, network, battery, overlap, or runtime settings."
    }

    try {
        $RegisteredPrincipalName = New-Object Security.Principal.NTAccount(
            [string]$RegisteredTask.Principal.UserId
        )
        $RegisteredUserSid = $RegisteredPrincipalName.Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    } catch {
        throw "The registered task principal could not be resolved to a Windows SID."
    }
    if (
        $RegisteredUserSid -ne $CurrentUserSid -or
        [string]$RegisteredTask.Principal.LogonType -ne "Interactive" -or
        [string]$RegisteredTask.Principal.RunLevel -ne "Limited"
    ) {
        throw "The task was registered without the exact current-user interactive limited principal."
    }
}

function ConvertTo-DisabledTaskXml {
    param(
        [Parameter(Mandatory)]
        [string]$TaskXml
    )

    [xml]$Document = $TaskXml
    $TaskNamespace = $Document.DocumentElement.NamespaceURI
    if ([string]::IsNullOrWhiteSpace($TaskNamespace)) {
        throw "The existing task XML does not declare the Task Scheduler namespace."
    }

    $NamespaceManager = New-Object System.Xml.XmlNamespaceManager($Document.NameTable)
    $NamespaceManager.AddNamespace("task", $TaskNamespace)
    $SettingsNode = $Document.SelectSingleNode(
        "/task:Task/task:Settings",
        $NamespaceManager
    )
    if ($null -eq $SettingsNode) {
        throw "The existing task XML does not contain a settings element."
    }
    $EnabledNode = $Document.SelectSingleNode(
        "/task:Task/task:Settings/task:Enabled",
        $NamespaceManager
    )
    if ($null -eq $EnabledNode) {
        $EnabledNode = $Document.CreateElement("Enabled", $TaskNamespace)
        $SettingsNode.AppendChild($EnabledNode) | Out-Null
    }

    $EnabledNode.InnerText = "false"
    return $Document.OuterXml
}

function Get-ReplacementTaskExact {
    $MatchingTasks = @(
        Get-ScheduledTask -ErrorAction Stop |
            Where-Object {
                $_.TaskPath -eq "\" -and
                $_.TaskName -ieq $TaskName
            }
    )
    if ($MatchingTasks.Count -gt 1) {
        throw "Task Scheduler returned more than one exact replacement task."
    }
    if ($MatchingTasks.Count -eq 0) {
        return $null
    }
    return $MatchingTasks[0]
}

function Remove-ReplacementTaskFailClosed {
    $Task = Get-ReplacementTaskExact
    if ($null -eq $Task) {
        return
    }

    try {
        Disable-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction Stop | Out-Null
    } catch {
        # Still attempt unregistering: absence is the authoritative safe state.
    }
    try {
        Unregister-ScheduledTask `
            -TaskName $TaskName `
            -TaskPath "\" `
            -Confirm:$false `
            -ErrorAction Stop
    } catch {
        # The exact absence check below decides whether cleanup succeeded.
    }

    $RemainingTask = Get-ReplacementTaskExact
    if ($null -ne $RemainingTask) {
        throw "The unverified replacement task could not be disabled and removed."
    }
}

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

$PreviousTask = Get-ReplacementTaskExact
$PreviousTaskDisabledXml = $null
$PreviousTaskCanBeReenabled = $false
if ($null -ne $PreviousTask) {
    $PreviousTaskXml = Export-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath "\" `
        -ErrorAction Stop
    $PreviousTaskDisabledXml = ConvertTo-DisabledTaskXml -TaskXml $PreviousTaskXml

    if (
        [bool]$PreviousTask.Settings.Enabled -and
        [string]$PreviousTask.State -ceq "Ready"
    ) {
        try {
            Assert-RegisteredTaskContract `
                -RegisteredTask $PreviousTask `
                -ExpectedEnabled $true `
                -ExpectedState "Ready"
            $PreviousTaskCanBeReenabled = $true
        } catch {
            # An invalid prior definition may be restored only in a disabled state.
        }
    }
}

$ReplacementRegistrationStarted = $false
try {
    $ReplacementRegistrationStarted = $true
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

    $RegisteredTask = Get-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath "\" `
        -ErrorAction Stop
    Assert-RegisteredTaskContract `
        -RegisteredTask $RegisteredTask `
        -ExpectedEnabled $false `
        -ExpectedState "Disabled"

    Enable-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath "\" `
        -ErrorAction Stop |
        Out-Null
    $RegisteredTask = Get-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath "\" `
        -ErrorAction Stop
    Assert-RegisteredTaskContract `
        -RegisteredTask $RegisteredTask `
        -ExpectedEnabled $true `
        -ExpectedState "Ready"

    # Remove obsolete task shapes only after the replacement has passed its
    # disabled and enabled readbacks. Any removal failure enters the same
    # fail-closed rollback path, preventing overlapping schedules.
    Write-Host "Removing obsolete AsimutBooker tasks from the Task Scheduler root..." -ForegroundColor Yellow
    $legacyTasks = @(
        Get-ScheduledTask -ErrorAction Stop |
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
            -Confirm:$false `
            -ErrorAction Stop
        Write-Host "  Removed obsolete task: $($legacyTask.TaskName)" -ForegroundColor Gray
    }

    $remainingLegacyTasks = @(
        Get-ScheduledTask -ErrorAction Stop |
            Where-Object {
                $_.TaskPath -eq "\" -and (
                    $_.TaskName -eq "AsimutBooker" -or
                    $_.TaskName -match '^AsimutBooker_[0-2][0-9][0-5][0-9]$' -or
                    $_.TaskName -match '^AsimutBooker-[0-2][0-9][0-5][0-9]$'
                )
            }
    )
    if ($remainingLegacyTasks.Count -ne 0) {
        throw "One or more obsolete AsimutBooker tasks remained after cleanup."
    }

    $RegisteredTask = Get-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath "\" `
        -ErrorAction Stop
    Assert-RegisteredTaskContract `
        -RegisteredTask $RegisteredTask `
        -ExpectedEnabled $true `
        -ExpectedState "Ready"
} catch {
    $InstallFailure = $_
    $RollbackFailures = [Collections.Generic.List[string]]::new()

    if ($ReplacementRegistrationStarted) {
        try {
            Remove-ReplacementTaskFailClosed
        } catch {
            $RollbackFailures.Add($_.Exception.Message)
        }
    }

    if ($RollbackFailures.Count -eq 0 -and $null -ne $PreviousTaskDisabledXml) {
        try {
            Register-ScheduledTask -TaskName $TaskName -TaskPath "\" -Xml $PreviousTaskDisabledXml -Force -ErrorAction Stop | Out-Null
            $RestoredTask = Get-ScheduledTask `
                -TaskName $TaskName `
                -TaskPath "\" `
                -ErrorAction Stop
            if (
                [bool]$RestoredTask.Settings.Enabled -or
                [string]$RestoredTask.State -cne "Disabled"
            ) {
                throw "The previous task definition was not restored in a disabled state."
            }

            if ($PreviousTaskCanBeReenabled) {
                Assert-RegisteredTaskContract `
                    -RegisteredTask $RestoredTask `
                    -ExpectedEnabled $false `
                    -ExpectedState "Disabled"
                Enable-ScheduledTask `
                    -TaskName $TaskName `
                    -TaskPath "\" `
                    -ErrorAction Stop |
                    Out-Null
                $RestoredTask = Get-ScheduledTask `
                    -TaskName $TaskName `
                    -TaskPath "\" `
                    -ErrorAction Stop
                Assert-RegisteredTaskContract `
                    -RegisteredTask $RestoredTask `
                    -ExpectedEnabled $true `
                    -ExpectedState "Ready"
            }
        } catch {
            $RollbackFailures.Add($_.Exception.Message)
            try {
                Remove-ReplacementTaskFailClosed
            } catch {
                $RollbackFailures.Add($_.Exception.Message)
            }
        }
    }

    if ($RollbackFailures.Count -ne 0) {
        throw (
            "Scheduled task setup failed: {0} Rollback also failed: {1}" -f
            $InstallFailure.Exception.Message,
            ($RollbackFailures -join "; ")
        )
    }
    throw $InstallFailure
}

Write-Host "Created: $TaskName" -ForegroundColor Green
Write-Host "  Repeats every 15 minutes from 07:13 through 21:58." -ForegroundColor White
Write-Host "  Runs headless, requests wake on AC or battery, catches up after a missed start, and ignores overlapping starts." -ForegroundColor White
Write-Host "  Actual wake-from-sleep still depends on Windows, firmware, and hardware support." -ForegroundColor White

Write-Host "Setup complete. No further input is required." -ForegroundColor Green
