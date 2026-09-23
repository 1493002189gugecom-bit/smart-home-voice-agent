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

foreach ($required in @($python, $visionPython)) {
    if (-not (Test-Path $required -PathType Leaf)) { throw "Required Python interpreter not found: $required" }
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

if ($HomeBackend -eq 'ha') {
    $haEnv = Join-Path $repoRoot 'runtime\home-assistant\ha.env'
    if (-not (Test-Path $haEnv -PathType Leaf)) {
        throw "Home Assistant credentials are missing: $haEnv. Use -HomeBackend memory only for local simulation."
    }
    $env:HOME_SERVICE_BACKEND = 'ha'
    $env:HOME_ASSISTANT_URL = 'http://127.0.0.1:8123'
    $env:HA_ENV_FILE = $haEnv
    $env:HA_OPERATION_DB = Join-Path $repoRoot 'runtime\home-assistant\operations.sqlite3'
}
else {
    $env:HOME_SERVICE_BACKEND = 'memory'
}
$env:PYTHONIOENCODING = 'utf-8'

function Test-ListeningPort {
    param([int]$Port)
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-Endpoint {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch { return $false }
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
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$entry.pid)" -ErrorAction SilentlyContinue
        if ($null -ne $processInfo -and $processInfo.CommandLine -like "*$($entry.commandContains)*") {
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
    if (Test-Endpoint -Uri $service.Health) {
        Write-Host "[$($service.Name)] using healthy existing service on 127.0.0.1:$($service.Port)"
        continue
    }
    if (Test-ListeningPort -Port $service.Port) {
        throw "[$($service.Name)] port $($service.Port) is occupied, but its health endpoint did not respond."
    }
    if (-not (Test-Path $service.Script -PathType Leaf)) { throw "Service script not found: $($service.Script)" }

    $stdout = Join-Path $runtimeRoot "$($service.Name).out.log"
    $stderr = Join-Path $runtimeRoot "$($service.Name).err.log"
    $argumentList = @($service.Script) + $service.Arguments
    $process = Start-Process -FilePath $service.Python -ArgumentList $argumentList `
        -WorkingDirectory $repoRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -WindowStyle Hidden -PassThru
    $owned += [pscustomobject]@{
        name = $service.Name
        pid = $process.Id
        commandContains = [IO.Path]::GetFileName($service.Script)
        startedAt = [DateTimeOffset]::Now.ToString('o')
    }
    Save-Manifest -Processes $owned
    Write-Host "[$($service.Name)] started (PID $($process.Id))"
}

Save-Manifest -Processes $owned

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while (-not (Test-Endpoint -Uri 'http://127.0.0.1:8770/api/status')) {
    if ((Get-Date) -gt $deadline) {
        throw "Perception console did not become ready. Inspect logs under $runtimeRoot."
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
