# NGBot GUI のデスクトップショートカットを作成するスクリプト
# 実行方法: PowerShell から `powershell -ExecutionPolicy Bypass -File create_shortcut.ps1`

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "NGBot.lnk"
$TargetPath = Join-Path $ProjectDir "run_ngbot_gui.bat"
$IconPath = Join-Path $ProjectDir "NGBot.ico"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.WindowStyle = 7   # 7 = Minimized (バッチ自体は最小化、pythonwはGUI窓のみ)
$Shortcut.Description = "NGBot 音声分析 GUI"
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}
$Shortcut.Save()

Write-Host "デスクトップショートカット作成: $ShortcutPath"
Write-Host "起動対象: $TargetPath"
