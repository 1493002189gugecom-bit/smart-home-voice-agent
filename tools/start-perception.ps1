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
    [ValidateRange(5, 300)]
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
    Write-Warning 'Perception UI is not built. Run npm install and npm run build in apps\perception-console\web first.'
    exit 2
}

if (-not (Test-Path $python -PathType Leaf)) {
    Write-Warning "Required Python interpreter not found: $python"
    exit 2
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

$env:PYTHONIOENCODING = 'utf-8'

function Test-ListeningPort {
    param([int]$Port)
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-ServicePortOwner {
    param($Service)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Service.Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $listener) { return $false }
    $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($null -eq $ownerInfo -or $ownerInfo.Name -notmatch '^pythonw?\.exe$' -or -not $ownerInfo.CommandLine) { return $false }
    $relativeScript = $Service.Script.Substring($repoRoot.Length + 1)
    return $ownerInfo.CommandLine.Contains($Service.Script) -or $ownerInfo.CommandLine.Contains($relativeScript)
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
        Arguments = @(); Health = 'http://127.0.0.1:8770/health'
    }
)

$owned = @(Get-OwnedProcesses)
$failures = @()
foreach ($service in $services) {
    $launchClock = [System.Diagnostics.Stopwatch]::StartNew()
    $runKind = 'warm'
    $launchOutcome = 'ok'
    $launchError = $null
    try {
    $ownedEntry = $owned | Where-Object { $_.name -eq $service.Name } | Select-Object -First 1
    if ($null -ne $ownedEntry -and (Test-OwnedEntry -Entry $ownedEntry)) {
        Write-Host "[$($service.Name)] waiting for existing launcher process (PID $($ownedEntry.pid))"
        $serviceDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while (-not ((Test-Endpoint -Uri $service.Health) -and (Test-ServicePortOwner -Service $service))) {
            if ((Get-Date) -gt $serviceDeadline) {
                throw "[$($service.Name)] did not become ready within $TimeoutSeconds seconds on port $($service.Port)."
            }
            Start-Sleep -Milliseconds 500
        }
        Write-Host "[$($service.Name)] ready on 127.0.0.1:$($service.Port)"
        continue
    }
    if ((Test-Endpoint -Uri $service.Health) -and (Test-ServicePortOwner -Service $service)) {
        Write-Host "[$($service.Name)] ready on 127.0.0.1:$($service.Port) (existing process)"
        continue
    }
    if (Test-ListeningPort -Port $service.Port) {
        throw "[$($service.Name)] port $($service.Port) is occupied, but it is not a ready service from this project. Run tools\stop-perception.ps1 and inspect the port owner."
    }
    $runKind = 'cold'
    if (-not (Test-Path $service.Python -PathType Leaf)) {
        throw "[$($service.Name)] Python interpreter not found: $($service.Python)"
    }
    if (-not (Test-Path $service.Script -PathType Leaf)) {
        throw "[$($service.Name)] service script not found: $($service.Script)"
    }
    if ($service.Name -eq 'home') {
        $env:HOME_SERVICE_BACKEND = $HomeBackend
        if ($HomeBackend -eq 'ha') {
            $haEnv = Join-Path $repoRoot 'runtime\home-assistant\ha.env'
            if (-not (Test-Path $haEnv -PathType Leaf)) {
                throw "Home Assistant credentials are missing: $haEnv"
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
        throw "[$($service.Name)] failed to start: $($_.Exception.Message)"
    }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        throw "[$($service.Name)] process exited before it could be recorded."
    }
    $owned += [pscustomobject]@{
        name = $service.Name
        pid = $process.Id
        scriptPath = $service.Script
        creationTicks = $processInfo.CreationDate.ToUniversalTime().Ticks
    }
    Save-Manifest -Processes $owned
    Write-Host "[$($service.Name)] started (PID $($process.Id)); waiting for health"
    $serviceDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not ((Test-Endpoint -Uri $service.Health) -and (Test-ServicePortOwner -Service $service))) {
        if ((Get-Date) -gt $serviceDeadline) {
            throw "[$($service.Name)] did not become ready within $TimeoutSeconds seconds on port $($service.Port)."
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "[$($service.Name)] ready on 127.0.0.1:$($service.Port)"
    }
    catch {
        # A model, device or credential fault must not prevent the console from
        # starting and showing which service needs attention.
        $failures += [pscustomobject]@{ Name = $service.Name; Message = $_.Exception.Message }
        $launchOutcome = 'error'
        $launchError = 'startup_failed'
        Write-Warning "[$($service.Name)] $($_.Exception.Message)"
    }
    finally {
        $launchClock.Stop()
        # The reporter accepts only numeric timing and a fixed error code.
        try {
            & $python (Join-Path $repoRoot 'tools\perception-report.py') record `
                --component launcher --stage ("start_" + $service.Name) `
                --outcome $launchOutcome --duration-ms $launchClock.Elapsed.TotalMilliseconds `
                --run-kind $runKind --error-code $(if ($launchError) { $launchError } else { 'none' }) 2>$null | Out-Null
        }
        catch { Write-Verbose 'Diagnostic recording was unavailable.' }
    }
}

Save-Manifest -Processes @(Get-OwnedProcesses)

$consoleReady = (Test-Endpoint -Uri 'http://127.0.0.1:8770/health') -and `
    (Test-ServicePortOwner -Service ($services | Where-Object { $_.Name -eq 'console' }))
if ($consoleReady) {
    Write-Host 'Perception center is ready at http://127.0.0.1:8770/'
    try {
        $statusResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:8770/api/status' -UseBasicParsing -TimeoutSec 8
        $statusBody = $statusResponse.Content | ConvertFrom-Json
        if ($null -eq $statusBody.services) { throw 'component status missing' }
        foreach ($service in $services | Where-Object { $_.Name -ne 'console' }) {
            $component = $statusBody.services.($service.Name)
            if ($null -eq $component) { throw "component status missing: $($service.Name)" }
            if ($null -ne $component -and $component.state -ne 'up' -and
                @($failures | Where-Object { $_.Name -eq $service.Name }).Count -eq 0) {
                $failures += [pscustomobject]@{ Name = $service.Name; Message = $component.error_code }
                Write-Warning "[$($service.Name)] needs attention: $($component.error_code)"
            }
        }
    }
    catch {
        $failures += [pscustomobject]@{ Name = 'status'; Message = 'status_unavailable' }
        Write-Warning 'Perception console is online, but component status could not be read.'
    }
}
else {
    Write-Warning 'Perception console is unavailable on 127.0.0.1:8770; review the console failure above.'
}
if ($consoleReady -and -not $NoWindow) {
    $consoleUrl = 'http://127.0.0.1:8770/'
    $browserCandidates = @(@(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
    ) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) })
    try {
        if ($browserCandidates.Count -gt 0) {
            Start-Process -FilePath $browserCandidates[0] -ArgumentList @('--new-window', "--app=$consoleUrl") `
                -WindowStyle Normal -ErrorAction Stop | Out-Null
        }
        else {
            Start-Process -FilePath $consoleUrl -ErrorAction Stop | Out-Null
        }
        Write-Host "Requested a visible perception console window: $consoleUrl"
    }
    catch {
        $failures += [pscustomobject]@{ Name = 'window'; Message = 'window_open_failed' }
        Write-Warning "Could not open the perception console window: $($_.Exception.Message). Open $consoleUrl manually."
    }
}
if ($failures.Count -gt 0) {
    Write-Warning "$($failures.Count) service(s) need attention. Open the console Service page for component status."
}
if (-not $consoleReady) { exit 2 }
if ($failures.Count -gt 0) { exit 1 }
exit 0
