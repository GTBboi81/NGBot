# Rollback script: restore config.yaml from the latest config.yaml.bak.*
# Usage: .\tests\rollback_model.ps1 [-Force]
#
# 1. Find the most recent config.yaml.bak.YYYYMMDD_HHMMSS
# 2. Save current config.yaml as config.yaml.preroll.<ts>
# 3. Copy the backup over config.yaml
# 4. Print the resulting model_name
#
# NOTE: comments are ASCII-only to avoid Windows PowerShell 5.1 parser issues
# with BOM-less UTF-8 files containing Japanese.

param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$cfg = Join-Path $ProjectRoot 'config.yaml'

if (-not (Test-Path $cfg)) {
    Write-Error ("config.yaml not found: " + $cfg)
    exit 1
}

$backups = Get-ChildItem -Path $ProjectRoot -Filter 'config.yaml.bak.*' -File |
    Sort-Object LastWriteTime -Descending
if (-not $backups) {
    Write-Error 'No config.yaml.bak.* found. Manually set model_name to "elyza3" instead.'
    exit 1
}

$latest = $backups[0]
Write-Host ("Restore source: " + $latest.FullName)
Write-Host ("        (mtime: " + $latest.LastWriteTime + ")")

if (-not $Force) {
    $resp = Read-Host 'Overwrite config.yaml with this backup? (yes/no)'
    if ($resp -ne 'yes') {
        Write-Host 'Cancelled.'
        exit 0
    }
}

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$preroll = ('{0}.preroll.{1}' -f $cfg, $ts)
Copy-Item $cfg $preroll
Write-Host ("Pre-rollback config saved to: " + $preroll)

Copy-Item $latest.FullName $cfg -Force
Write-Host 'OK: rollback done.'

$line = (Get-Content $cfg -Encoding UTF8 | Select-String -Pattern '^\s*model_name:' | Select-Object -First 1).Line
Write-Host ("Current model_name: " + $line)

Write-Host "`nollama list:"
ollama list | Select-String -Pattern 'elyza3|swallow|NAME'
