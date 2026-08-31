param(
    [string]$AllowedLogin = "",
    [int]$HttpsPort = 10443
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$TaskName = "AsimutBooker_Phone"
$BackendPort = 8794
$WorkingDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$PythonPath = Join-Path $WorkingDir ".venv\Scripts\python.exe"
$PythonwPath = Join-Path $WorkingDir ".venv\Scripts\pythonw.exe"
$ServerPath = Join-Path $WorkingDir "phone_server.py"
$ConfigPath = Join-Path $WorkingDir "data\phone_server_config.json"
$PhoneDir = Join-Path $WorkingDir "phone"
$BuildPath = Join-Path $PhoneDir "dist-phone\index.html"
$TailscalePath = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
$VerifyPath = Join-Path $WorkingDir "verify_phone_deployment.ps1"
$CodexCommand = Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue

foreach ($RequiredPath in @($PythonPath, $PythonwPath, $ServerPath, $TailscalePath, $VerifyPath)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "Required phone-app dependency is missing: $RequiredPath"
    }
}
if ($null -eq $CodexCommand -or -not (Test-Path -LiteralPath $CodexCommand.Source -PathType Leaf)) {
    throw "The Codex CLI executable was not found. Run phone setup from Codex."
}
$CodexPath = [string]$CodexCommand.Source
$BasePythonwPath = (& $PythonPath -c "import pathlib, sys; print(pathlib.Path(sys._base_executable).with_name('pythonw.exe'))").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $BasePythonwPath -PathType Leaf)) {
    throw "The virtual environment's base windowless Python runtime could not be resolved."
}
$AllowedPythonwPaths = @($PythonwPath, $BasePythonwPath)

$TailnetStatus = (& $TailscalePath status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $null -eq $TailnetStatus.Self) {
    throw "Tailscale status could not be read."
}
$DnsName = ([string]$TailnetStatus.Self.DNSName).TrimEnd(".").ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($DnsName) -or -not $DnsName.EndsWith(".ts.net")) {
    throw "Tailscale did not report a trusted MagicDNS hostname."
}
if ([string]::IsNullOrWhiteSpace($AllowedLogin)) {
    $SelfUserId = [string]$TailnetStatus.Self.UserID
    $Owner = @($TailnetStatus.User.PSObject.Properties.Value | Where-Object {
        [string]$_.ID -eq $SelfUserId
    })
    if ($Owner.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$Owner[0].LoginName)) {
        throw "The exact current Tailscale login could not be derived."
    }
    $AllowedLogin = [string]$Owner[0].LoginName
}
$PublicOrigin = "https://${DnsName}:$HttpsPort"

$NodeCandidates = @()
$NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($null -ne $NodeCommand) {
    $NodeCandidates += $NodeCommand.Source
}
$NodeCandidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$NodePath = @($NodeCandidates | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_ -PathType Leaf)
} | Select-Object -First 1)
if ($NodePath.Count -ne 1) {
    throw "The Node runtime used to build the phone shell was not found."
}
if (-not (Test-Path -LiteralPath (Join-Path $PhoneDir "node_modules\vite\bin\vite.js") -PathType Leaf)) {
    throw "Phone dependencies are missing. Open this repository in Codex once, then rerun setup."
}

$Commit = (& git -C $WorkingDir rev-parse --short=12 HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Commit)) {
    throw "The phone build version could not be derived from Git."
}
$OldBuildVersion = $env:ASIMUT_PHONE_VERSION
try {
    $env:ASIMUT_PHONE_VERSION = $Commit
    & $NodePath[0] (Join-Path $PhoneDir "node_modules\vite\bin\vite.js") build --config (Join-Path $PhoneDir "vite.static.config.ts")
    if ($LASTEXITCODE -ne 0) {
        throw "The phone shell build failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:ASIMUT_PHONE_VERSION = $OldBuildVersion
}
if (-not (Test-Path -LiteralPath $BuildPath -PathType Leaf)) {
    throw "The phone shell build did not produce index.html."
}
& $PythonPath (Join-Path $WorkingDir "tools\verify_phone_build.py") --dist (Join-Path $PhoneDir "dist-phone") --expected-version $Commit
if ($LASTEXITCODE -ne 0) {
    throw "The built phone shell failed its offline/install verification."
}

& $PythonPath -c "import assistant_runtime, phone_api, phone_server"
if ($LASTEXITCODE -ne 0) {
    throw "The isolated Python runtime could not import the phone service."
}
& $PythonPath (Join-Path $WorkingDir "phone_configure.py") --allowed-login $AllowedLogin --public-origin $PublicOrigin --codex-executable $CodexPath --path $ConfigPath
if ($LASTEXITCODE -ne 0) {
    throw "The private phone configuration could not be written."
}
& $PythonPath $ServerPath --config $ConfigPath --check-config
if ($LASTEXITCODE -ne 0) {
    throw "The private phone configuration did not pass strict validation."
}

$Occupied = @(Get-NetTCPConnection -State Listen -LocalPort $BackendPort -ErrorAction SilentlyContinue)
if ($Occupied.Count -gt 0) {
    $Owned = $false
    foreach ($Listener in $Occupied) {
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction Stop
        if (
            $AllowedPythonwPaths -icontains [string]$Process.ExecutablePath -and
            [string]$Process.CommandLine -like "*phone_server.py*"
        ) {
            $Owned = $true
        } else {
            throw "Loopback port $BackendPort is already owned by another process."
        }
    }
    if ($Owned) {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction SilentlyContinue
        $Deadline = [DateTime]::UtcNow.AddSeconds(12)
        do {
            Start-Sleep -Milliseconds 250
            $Occupied = @(Get-NetTCPConnection -State Listen -LocalPort $BackendPort -ErrorAction SilentlyContinue)
        } while ($Occupied.Count -gt 0 -and [DateTime]::UtcNow -lt $Deadline)
        if ($Occupied.Count -gt 0) {
            throw "The existing exact phone service did not stop cleanly."
        }
    }
}

$CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentUser = $CurrentIdentity.Name
$CurrentSid = $CurrentIdentity.User.Value
$Arguments = '"{0}" --config "{1}"' -f $ServerPath, $ConfigPath
$Action = New-ScheduledTaskAction -Execute $PythonwPath -Argument $Arguments -WorkingDirectory $WorkingDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath "\" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Private tailnet-only Asimut Booker phone companion" `
    -Force | Out-Null

$Registered = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction Stop
if (
    @($Registered.Actions).Count -ne 1 -or
    $Registered.Actions[0].Execute -ine $PythonwPath -or
    $Registered.Actions[0].Arguments -cne $Arguments -or
    $Registered.Actions[0].WorkingDirectory -ine $WorkingDir -or
    [string]$Registered.Principal.LogonType -ne "Interactive" -or
    [string]$Registered.Principal.RunLevel -ne "Limited"
) {
    throw "The phone startup task did not match its exact launcher contract."
}
$RegisteredAccount = New-Object Security.Principal.NTAccount([string]$Registered.Principal.UserId)
$RegisteredSid = $RegisteredAccount.Translate([Security.Principal.SecurityIdentifier]).Value
if ($RegisteredSid -ne $CurrentSid) {
    throw "The phone startup task was registered for the wrong Windows identity."
}

Start-ScheduledTask -TaskName $TaskName -TaskPath "\"
$Healthy = $false
for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/healthz" -Headers @{ Host = "${DnsName}:$HttpsPort" } -TimeoutSec 2
        if ($Health.status -eq "ok" -and $Health.version -eq $Commit) {
            $Healthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $Healthy) {
    throw "The phone service did not become healthy on exact loopback."
}

$ServeBefore = @(& $TailscalePath serve status)
if ($LASTEXITCODE -ne 0) {
    throw "Existing Tailscale Serve routes could not be read."
}
& $TailscalePath serve --bg "--https=$HttpsPort" "http://127.0.0.1:$BackendPort"
if ($LASTEXITCODE -ne 0) {
    throw "The tailnet-only HTTPS route could not be installed."
}
$ServeAfter = @(& $TailscalePath serve status)
foreach ($ExistingLine in $ServeBefore) {
    if (-not [string]::IsNullOrWhiteSpace($ExistingLine) -and $ServeAfter -notcontains $ExistingLine) {
        throw "An existing Tailscale Serve route changed during phone setup."
    }
}

& $VerifyPath -ExpectedVersion $Commit
if ($LASTEXITCODE -ne 0) {
    throw "Phone deployment verification failed."
}

Write-Host "Asimut Assistant is ready at $PublicOrigin/" -ForegroundColor Green
Write-Host "On iPhone: open it in Safari, Share, then Add to Home Screen." -ForegroundColor Cyan
