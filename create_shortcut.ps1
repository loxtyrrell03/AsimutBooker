[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$PythonwPath = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$GuiPath = Join-Path $ProjectRoot "gui.py"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "AsimutBooker.lnk"

if (-not (Test-Path -LiteralPath $PythonwPath -PathType Leaf)) {
    throw "Project Python not found: $PythonwPath`nCreate .venv and install the project first."
}
if (-not (Test-Path -LiteralPath $GuiPath -PathType Leaf)) {
    throw "Control panel not found: $GuiPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PythonwPath
$shortcut.Arguments = "`"$GuiPath`""
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.Description = "AsimutBooker Control Panel"

$iconPath = Join-Path $ProjectRoot "assets\icon.ico"
if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
    $shortcut.IconLocation = $iconPath
}

$shortcut.Save()
Write-Host "Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
