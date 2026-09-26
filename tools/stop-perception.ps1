<# .SYNOPSIS Stop this repository's perception services and verify their ports are free. #>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifestPath = Join-Path $repoRoot 'runtime\perception\processes.json'
$services = @(
    [pscustomobject]@{ Name = 'home'; Port = 8765; Script = 'apps\home-service\src\server.py'; Venv = '.venv\Scripts\python.exe' },
    [pscustomobject]@{ Name = 'vision'; Port = 8766; Script = 'apps\vision-service\src\main.py'; Venv = '.venv-vision\Scripts\python.exe' },
    [pscustomobject]@{ Name = 'voice'; Port = 8767; Script = 'apps\voice-service\src\loop.py'; Venv = '.venv\Scripts\python.exe' },
    [pscustomobject]@{ Name = 'console'; Port = 8770; Script = 'apps\perception-console\src\main.py'; Venv = '.venv\Scripts\python.exe' }
)

function Test-ServiceCommand {
    param($Process, $Service)
    if ($null -eq $Process -or $Process.Name -notmatch '^pythonw?\.exe$' -or -not $Process.CommandLine) { return $false }
    $absoluteScript = Join-Path $repoRoot $Service.Script
    return $Process.CommandLine.Contains($absoluteScript) -or $Process.CommandLine.Contains($Service.Script)
}

function Test-RepositoryProcess {
    param($Process, $Service)
    if (-not (Test-ServiceCommand -Process $Process -Service $Service)) { return $false }
    $absoluteScript = Join-Path $repoRoot $Service.Script
    $venvPython = Join-Path $repoRoot $Service.Venv
    return $Process.CommandLine.Contains($absoluteScript) -or $Process.CommandLine.Contains($venvPython)
}

$stopped = [System.Collections.Generic.HashSet[int]]::new()

# A Windows Python venv launcher can create a second python.exe that owns the
# listening socket. Inspect both generations and repeat for exit races.
for ($pass = 0; $pass -lt 3; $pass++) {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $byId = @{}
    foreach ($processInfo in $processes) { $byId[[int]$processInfo.ProcessId] = $processInfo }
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in 8765, 8766, 8767, 8770 })
    $targets = @{}

    foreach ($service in $services) {
        foreach ($processInfo in $processes) {
            if (Test-RepositoryProcess -Process $processInfo -Service $service) {
                $targets[[int]$processInfo.ProcessId] = $service.Name
            }
        }

        # A manual launch may use a relative script path and system Python.
        # Only claim that process when it owns this service's known port.
        foreach ($listener in $listeners | Where-Object { $_.LocalPort -eq $service.Port }) {
            $portPid = [int]$listener.OwningProcess
            if ($byId.ContainsKey($portPid) -and (Test-ServiceCommand -Process $byId[$portPid] -Service $service)) {
                $targets[$portPid] = $service.Name
            }
        }
    }

    # Include the child of a repository venv launcher, even with a relative path.
    foreach ($processInfo in $processes) {
        $parentPid = [int]$processInfo.ParentProcessId
        if (-not $targets.ContainsKey($parentPid)) { continue }
        $service = $services | Where-Object { $_.Name -eq $targets[$parentPid] } | Select-Object -First 1
        if (Test-ServiceCommand -Process $processInfo -Service $service) {
            $targets[[int]$processInfo.ProcessId] = $service.Name
        }
    }

    if ($targets.Count -eq 0) { break }
    $ordered = @($targets.Keys | Sort-Object { if ($byId.ContainsKey($_) -and $targets.ContainsKey([int]$byId[$_].ParentProcessId)) { 0 } else { 1 } })
    foreach ($targetPid in $ordered) {
        try {
            Stop-Process -Id $targetPid -Force -ErrorAction Stop
            if ($stopped.Add($targetPid)) { Write-Host "[$($targets[$targetPid])] stopped PID $targetPid" }
        }
        catch {
            if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
                Write-Warning "[$($targets[$targetPid])] could not stop PID $targetPid`: $($_.Exception.Message)"
            }
        }
    }
    Start-Sleep -Milliseconds 400
}

$remaining = @()
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8765, 8766, 8767, 8770 })
foreach ($service in $services) {
    foreach ($listener in $listeners | Where-Object { $_.LocalPort -eq $service.Port }) {
        $portPid = [int]$listener.OwningProcess
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $portPid" -ErrorAction SilentlyContinue
        $remaining += "  $($service.Port) <- PID $portPid $($processInfo.Name)"
    }
}

if ($remaining.Count -gt 0) {
    Write-Error "Perception ports are still occupied after stopping $($stopped.Count) process(es):`n$($remaining -join "`n")"
    exit 1
}

Remove-Item -LiteralPath $manifestPath -Force -ErrorAction SilentlyContinue
Write-Host "Stopped $($stopped.Count) process(es). Ports 8765/8766/8767/8770 are free."
