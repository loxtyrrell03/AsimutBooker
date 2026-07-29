[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AdditionalArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogsRoot = Join-Path $ProjectRoot "logs"
$DayDirectory = Join-Path $LogsRoot (Get-Date -Format "yyyy-MM-dd")
$runId = "{0}-p{1}-{2}" -f (Get-Date -Format "HH-mm-ss-fff"), $PID, ([guid]::NewGuid().ToString("N").Substring(0, 8))
$LogPath = Join-Path $DayDirectory "$runId.log"
$SummaryPath = Join-Path $LogsRoot "scheduler.log"
$startedAt = Get-Date
$exitCode = 1

try {
    New-Item -ItemType Directory -Path $DayDirectory -Force | Out-Null
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Project Python not found: $PythonPath"
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONUNBUFFERED = "1"

    $header = @(
        "timestamp=$($startedAt.ToString('o'))",
        "run_id=$runId",
        "python=$PythonPath",
        "command=python -m asimut_booker.cli run --headless --scheduled",
        ("-" * 72)
    )
    $header | Set-Content -LiteralPath $LogPath -Encoding UTF8

    $pythonArguments = @(
        "-u",
        "-m",
        "asimut_booker.cli",
        "run",
        "--headless",
        "--scheduled"
    )
    if ($AdditionalArguments) {
        $pythonArguments += $AdditionalArguments
    }

    & $PythonPath @pythonArguments 2>&1 |
        Tee-Object -LiteralPath $LogPath -Append
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 1
    }
}
catch {
    $exitCode = 1
    $message = "wrapper_error=$($_.Exception.Message)"
    $message | Add-Content -LiteralPath $LogPath -Encoding UTF8
    Write-Host $message -ForegroundColor Red
}
finally {
    $finishedAt = Get-Date
    $durationSeconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    $footer = @(
        ("-" * 72),
        "finished_at=$($finishedAt.ToString('o'))",
        "duration_seconds=$durationSeconds",
        "exit_code=$exitCode"
    )
    $footer | Add-Content -LiteralPath $LogPath -Encoding UTF8
    $relativeLog = $LogPath.Substring($ProjectRoot.Length).TrimStart("\")
    $summary = "$($finishedAt.ToString('o')) run_id=$runId exit_code=$exitCode duration_seconds=$durationSeconds log=$relativeLog"
    $summary | Add-Content -LiteralPath $SummaryPath -Encoding UTF8
}

exit $exitCode
