<#
.SYNOPSIS
  Start the local smart-home perception stack and open its desktop window.

.EXAMPLE
  .\tools\start-perception.ps1
  .\tools\start-perception.ps1 -HomeBackend memory
  .\tools\start-perception.ps1 -NoWindow
#>
[CmdletBinding()]
param(
    [ValidateSet('ha', 'memory')]
    [string]$HomeBackend = 'ha',
    [int]$TimeoutSeconds = 60,
    [switch]$NoWindow
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot 'runtime\perception'
$manifestPath = Join-Path $runtimeRoot 'processes.json'
$frontendIndex = Join-Path $repoRoot 'apps\perception-console\web\dist\index.html'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$visionPython = Join-Path $repoRoot '.venv-vision\Scripts\python.exe'

if (-not (Test-Path $frontendIndex -PathType Leaf)) {
    throw 'Perception UI is not built. Run npm install and npm run build in apps\perception-console\web first.'
}

if (-not (Test-Path $python -PathType Leaf)) { throw "Required Python interpreter not found: $python" }

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

$env:PYTHONIOENCODING = 'utf-8'

function Test-ListeningPort {
    param([int]$Port)
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-Endpoint {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch { return $false }
}

function Test-OwnedEntry {
    param($Entry)
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Entry.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo -or -not $processInfo.CommandLine) { return $false }
    if ($processInfo.CommandLine -notlike "*$($Entry.scriptPath)*") { return $false }
    return $processInfo.CreationDate.ToUniversalTime().Ticks -eq [long]$Entry.creationTicks
}

function Get-OwnedProcesses {
    if (-not (Test-Path $manifestPath -PathType Leaf)) { return @() }
    try {
        $previous = Get-Content $manifestPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-Warning 'The previous perception process manifest is unreadable; ignoring it.'
        return @()
    }

    $live = @()
    foreach ($entry in @($previous.processes)) {
        if (Test-OwnedEntry -Entry $entry) {
            $live += $entry
        }
    }
    return $live
}

function Save-Manifest {
    param([array]$Processes)
    [pscustomobject]@{
        repository = $repoRoot
        updatedAt = [DateTimeOffset]::Now.ToString('o')
        processes = $Processes
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $manifestPath -Encoding UTF8
}

$services = @(
    [pscustomobject]@{
        Name = 'home'; Port = 8765; Python = $python
        Script = Join-Path $repoRoot 'apps\home-service\src\server.py'
        Arguments = @('--port', '8765'); Health = 'http://127.0.0.1:8765/health'
    },
    [pscustomobject]@{
        Name = 'vision'; Port = 8766; Python = $visionPython
        Script = Join-Path $repoRoot 'apps\vision-service\src\main.py'
        Arguments = @(); Health = 'http://127.0.0.1:8766/health'
    },
    [pscustomobject]@{
        Name = 'voice'; Port = 8767; Python = $python
        Script = Join-Path $repoRoot 'apps\voice-service\src\loop.py'
        Arguments = @('--agent'); Health = 'http://127.0.0.1:8767/health'
    },
    [pscustomobject]@{
        Name = 'console'; Port = 8770; Python = $python
        Script = Join-Path $repoRoot 'apps\perception-console\src\main.py'
        Arguments = @(); Health = 'http://127.0.0.1:8770/api/status'
    }
)

$owned = @(Get-OwnedProcesses)
foreach ($service in $services) {
    $ownedEntry = $owned | Where-Object { $_.name -eq $service.Name } | Select-Object -First 1
    if ($null -ne $ownedEntry -and (Test-OwnedEntry -Entry $ownedEntry)) {
        Write-Host "[$($service.Name)] existing launcher-owned process is still starting or running (PID $($ownedEntry.pid))"
        continue
    }
    if (Test-Endpoint -Uri $service.Health) {
        Write-Host "[$($service.Name)] using healthy existing service on 127.0.0.1:$($service.Port)"
        continue
    }
    if (Test-ListeningPort -Port $service.Port) {
        Write-Warning "[$($service.Name)] port $($service.Port) is occupied, but its health endpoint did not respond."
        continue
    }
    if (-not (Test-Path $service.Python -PathType Leaf)) {
        Write-Warning "[$($service.Name)] Python interpreter not found: $($service.Python)"
        continue
    }
    if (-not (Test-Path $service.Script -PathType Leaf)) {
        Write-Warning "[$($service.Name)] service script not found: $($service.Script)"
        continue
    }
    if ($service.Name -eq 'home') {
        $env:HOME_SERVICE_BACKEND = $HomeBackend
        if ($HomeBackend -eq 'ha') {
            $haEnv = Join-Path $repoRoot 'runtime\home-assistant\ha.env'
            if (-not (Test-Path $haEnv -PathType Leaf)) {
                Write-Warning "Home Assistant credentials are missing: $haEnv. Home service was not started."
                continue
            }
            $env:HOME_ASSISTANT_URL = 'http://127.0.0.1:8123'
            $env:HA_ENV_FILE = $haEnv
            $env:HA_OPERATION_DB = Join-Path $repoRoot 'runtime\home-assistant\operations.sqlite3'
        }
    }

    $argumentList = @($service.Script) + $service.Arguments
    try {
        # Services can print transcripts, paths, or credentials; do not persist stdout/stderr.
        $process = Start-Process -FilePath $service.Python -ArgumentList $argumentList `
            -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    }
    catch {
        Write-Warning "[$($service.Name)] failed to start: $($_.Exception.GetType().Name)"
        continue
    }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        Write-Warning "[$($service.Name)] process exited before it could be recorded; inspect its status in the console."
        continue
    }
    $owned += [pscustomobject]@{
        name = $service.Name
        pid = $process.Id
        scriptPath = $service.Script
        creationTicks = $processInfo.CreationDate.ToUniversalTime().Ticks
    }
    Save-Manifest -Processes $owned
    Write-Host "[$($service.Name)] started (PID $($process.Id))"
}

Save-Manifest -Processes $owned

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while (-not (Test-Endpoint -Uri 'http://127.0.0.1:8770/api/status')) {
    if ((Get-Date) -gt $deadline) {
        throw 'Perception console did not become ready. Check the service status and local environment.'
    }
    Start-Sleep -Milliseconds 500
}

Write-Host 'Perception center is ready at http://127.0.0.1:8770/'
if (-not $NoWindow) {
    $browserCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
    ) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) }
    if ($browserCandidates.Count -eq 0) {
        Write-Warning 'Edge or Chrome was not found. Open http://127.0.0.1:8770/ manually.'
    }
    else {
        Start-Process -FilePath $browserCandidates[0] -ArgumentList '--app=http://127.0.0.1:8770/' | Out-Null
    }
}
