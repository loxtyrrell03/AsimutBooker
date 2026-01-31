# Create Desktop Shortcut for AsimutBooker GUI
# Run this script once to create the shortcut

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "AsimutBooker.lnk"

# Create WScript Shell object
$WshShell = New-Object -ComObject WScript.Shell

# Create shortcut
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "pythonw.exe"
$Shortcut.Arguments = "`"$ScriptDir\gui.py`""
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.Description = "AsimutBooker Control Panel"

# Try to set icon
$IconPath = Join-Path $ScriptDir "assets\icon.ico"
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}

$Shortcut.Save()

Write-Host "Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
Write-Host ""
Write-Host "You can now launch AsimutBooker from your desktop!" -ForegroundColor Cyan
