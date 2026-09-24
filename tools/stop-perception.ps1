<# .SYNOPSIS Stop only the perception-service processes created by start-perception.ps1. #>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifestPath = Join-Path $repoRoot 'runtime\perception\processes.json'

if (-not (Test-Path $manifestPath -PathType Leaf)) {
    Write-Host 'No perception launcher manifest exists; nothing was stopped.'
    exit 0
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$entries = @($manifest.processes)
[array]::Reverse($entries)

foreach ($entry in $entries) {
    $pidValue = [int]$entry.pid
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        Write-Host "[$($entry.name)] already stopped"
        continue
    }
    if (-not $processInfo.CommandLine -or
        $processInfo.CommandLine -notlike "*$($entry.scriptPath)*" -or
        $processInfo.CreationDate.ToUniversalTime().Ticks -ne [long]$entry.creationTicks) {
        Write-Warning "[$($entry.name)] PID $pidValue no longer matches its recorded command; skipped."
        continue
    }
    Stop-Process -Id $pidValue -ErrorAction Stop
    Write-Host "[$($entry.name)] stopped PID $pidValue"
}

Remove-Item -LiteralPath $manifestPath -Force
