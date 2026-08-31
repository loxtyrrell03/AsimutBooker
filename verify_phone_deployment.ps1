param(
    [string]$ExpectedVersion = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$TaskName = "AsimutBooker_Phone"
$BackendPort = 8794
$HttpsPort = 10443
$WorkingDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$PythonwPath = Join-Path $WorkingDir ".venv\Scripts\pythonw.exe"
$PythonPath = Join-Path $WorkingDir ".venv\Scripts\python.exe"
$ServerPath = Join-Path $WorkingDir "phone_server.py"
$ConfigPath = Join-Path $WorkingDir "data\phone_server_config.json"
$BuildDir = Join-Path $WorkingDir "phone\dist-phone"
$TailscalePath = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"

$BasePythonwPath = (& $PythonPath -c "import pathlib, sys; print(pathlib.Path(sys._base_executable).with_name('pythonw.exe'))").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $BasePythonwPath -PathType Leaf)) {
    throw "The virtual environment's base windowless Python runtime could not be resolved."
}
$AllowedPythonwPaths = @($PythonwPath, $BasePythonwPath)

$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if (
    $Config.version -ne 2 -or
    $Config.host -cne "127.0.0.1" -or
    $Config.port -ne $BackendPort -or
    [string]::IsNullOrWhiteSpace([string]$Config.allowed_login) -or
    [string]::IsNullOrWhiteSpace([string]$Config.codex_executable) -or
    -not (Test-Path -LiteralPath ([string]$Config.codex_executable) -PathType Leaf) -or
    [IO.Path]::GetFileName([string]$Config.codex_executable) -ine "codex.exe"
) {
    throw "The phone server configuration is not the exact private contract."
}
$Origin = [Uri][string]$Config.public_origin
if ($Origin.Scheme -cne "https" -or $Origin.Port -ne $HttpsPort -or -not $Origin.Host.EndsWith(".ts.net")) {
    throw "The phone server origin is not the expected private HTTPS origin."
}
$PublicHost = $Origin.Authority

$BuildInfo = Get-Content -LiteralPath (Join-Path $BuildDir "build-info.json") -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) {
    $ExpectedVersion = [string]$BuildInfo.version
}
if ([string]$BuildInfo.version -cne $ExpectedVersion) {
    throw "The deployed phone shell version does not match the expected source version."
}

$Task = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction Stop
$Action = @($Task.Actions)
if (
    $Action.Count -ne 1 -or
    $Action[0].Execute -ine $PythonwPath -or
    $Action[0].Arguments -notlike "*phone_server.py*" -or
    $Action[0].Arguments -notlike "*phone_server_config.json*" -or
    $Action[0].WorkingDirectory -ine $WorkingDir -or
    -not $Task.Settings.Enabled -or
    $Task.Settings.MultipleInstances -ne "IgnoreNew"
) {
    throw "The phone startup task does not match its exact launcher contract."
}

$Listeners = @(Get-NetTCPConnection -State Listen -LocalAddress "127.0.0.1" -LocalPort $BackendPort -ErrorAction Stop)
if ($Listeners.Count -ne 1) {
    throw "The phone service does not own exactly one loopback listener."
}
$Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listeners[0].OwningProcess)" -ErrorAction Stop
if (
    $AllowedPythonwPaths -inotcontains [string]$Process.ExecutablePath -or
    [string]$Process.CommandLine -notlike "*phone_server.py*" -or
    [string]$Process.CommandLine -notlike "*phone_server_config.json*"
) {
    throw "The loopback listener is not the exact phone service process."
}

$Health = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/healthz" -Headers @{ Host = $PublicHost } -TimeoutSec 4
if ($Health.status -ne "ok" -or [string]$Health.version -cne $ExpectedVersion) {
    throw "The loopback health response does not match the deployed build."
}
$Manifest = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/manifest.webmanifest" -Headers @{ Host = $PublicHost } -TimeoutSec 4
if ($Manifest.display -ne "standalone" -or $Manifest.start_url -ne "/" -or @($Manifest.icons).Count -lt 3) {
    throw "The deployed PWA manifest is incomplete."
}
foreach ($Asset in @("/", "/sw.js", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png", "/apple-touch-icon.png")) {
    $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort$Asset" -Headers @{ Host = $PublicHost } -TimeoutSec 4
    if ($Response.StatusCode -ne 200) {
        throw "A required phone asset is unavailable: $Asset"
    }
}

function Get-StatusCode {
    param([Parameter(Mandatory)][scriptblock]$Request)
    try {
        & $Request | Out-Null
        return 200
    } catch {
        if ($null -eq $_.Exception.Response) {
            throw
        }
        return [int]$_.Exception.Response.StatusCode
    }
}

$SensitiveStatus = Get-StatusCode {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort/data/settings.json" -Headers @{ Host = $PublicHost } -TimeoutSec 4
}
if ($SensitiveStatus -ne 404) {
    throw "The static server exposed a repository data path."
}
$AnonymousApiStatus = Get-StatusCode {
    Invoke-WebRequest -UseBasicParsing `
        -Uri "http://127.0.0.1:$BackendPort/api/v1/session" `
        -Method Post `
        -Headers @{ Host = $PublicHost; Origin = [string]$Config.public_origin; "Sec-Fetch-Site" = "same-origin" } `
        -ContentType "application/json" `
        -Body '{"client":"asimut-phone-v1"}' `
        -TimeoutSec 4
}
if ($AnonymousApiStatus -ne 403) {
    throw "The phone API accepted a request without a Tailscale identity."
}

$ServeStatus = @(& $TailscalePath serve status)
if ($LASTEXITCODE -ne 0) {
    throw "Tailscale Serve status could not be read."
}
$ExpectedOriginLine = "https://$PublicHost (tailnet only)"
if ($ServeStatus -notcontains $ExpectedOriginLine -or $ServeStatus -notcontains "|-- / proxy http://127.0.0.1:$BackendPort") {
    throw "The exact tailnet-only phone route is missing."
}
$FunnelStatus = @(& $TailscalePath funnel status)
if ($LASTEXITCODE -ne 0) {
    throw "Tailscale Funnel status could not be read."
}
$UnexpectedFunnelOrigin = @($FunnelStatus | Where-Object {
    ([string]$_).StartsWith("https://$PublicHost", [StringComparison]::OrdinalIgnoreCase) -and
    [string]$_ -cne $ExpectedOriginLine
})
if ($UnexpectedFunnelOrigin.Count -gt 0) {
    throw "The private phone origin was unexpectedly exposed through Funnel."
}

Write-Host "Verified private Asimut phone deployment $ExpectedVersion at $($Config.public_origin)/" -ForegroundColor Green
