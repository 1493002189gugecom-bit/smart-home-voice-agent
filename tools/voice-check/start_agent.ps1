<#
.SYNOPSIS
  Start the whole voice-agent chain: Home Assistant stack, home-service, voice loop.

.DESCRIPTION
  Order matters, so this script enforces it:
    1. Docker stack (Home Assistant + Mosquitto + simulator) is up.
    2. home-service runs with the Home Assistant backend on loopback.
    3. The voice loop starts with the agent enabled.

  Home Assistant must already hold the broker credentials: run
  tools\ha-check\setup_mqtt.py once (or after a credential rotation).

.EXAMPLE
  .\tools\voice-check\start_agent.ps1
  .\tools\voice-check\start_agent.ps1 -Text         # type instead of speaking
  .\tools\voice-check\start_agent.ps1 -NoAgent      # voice loop without the LLM
  .\tools\voice-check\start_agent.ps1 -SkipStack    # stack already running
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [int]$ServicePort = 8765,
    [switch]$SkipStack,
    [switch]$NoAgent,
    [switch]$Text,
    [ValidateSet('hybrid', 'wake')]
    [string]$ListeningMode,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is empty inside a param() default value in Windows PowerShell 5.1,
# so the repository root is resolved in the body instead.
if (-not $RepoRoot) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RepoRoot = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
}
# Windows PowerShell 5.1 decodes child-process output with the console codepage,
# which mangles the Chinese this project prints. Pairing PYTHONIOENCODING with a
# UTF-8 console reader keeps the wake-word prompt and replies readable.
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$RepoRoot = (Resolve-Path $RepoRoot).Path
$runtime = Join-Path $RepoRoot 'runtime\home-assistant'
$composeEnv = Join-Path $runtime 'compose.env'
$haEnv = Join-Path $runtime 'ha.env'
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$serviceScript = Join-Path $RepoRoot 'apps\home-service\src\server.py'
$loopScript = Join-Path $RepoRoot 'apps\voice-service\src\loop.py'
$serviceLog = Join-Path $runtime 'home-service.log'

function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    # Windows PowerShell 5.1 promotes native stderr to a terminating error while
    # $ErrorActionPreference is 'Stop', and docker writes progress to stderr.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command @Arguments 2>&1 | Out-Null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

foreach ($required in @($python, $serviceScript, $loopScript)) {
    if (-not (Test-Path $required)) {
        throw "required file not found: $required"
    }
}
if (-not (Test-Path $composeEnv)) {
    throw "missing $composeEnv - run .\tools\ha-check\prepare_stack.ps1 first."
}
if (-not (Test-Path $haEnv)) {
    throw "missing $haEnv - save HOME_ASSISTANT_TOKEN there first."
}

if (-not $SkipStack) {
    Write-Host '[1/4] starting the Home Assistant stack ...'
    $exit = Invoke-Native -Command 'docker' -Arguments @(
        'compose', '--env-file', $composeEnv,
        '-f', 'D:\dac\docker-compose.yml',
        '-f', (Join-Path $RepoRoot 'infra\home-assistant\compose.override.yaml'),
        'up', '-d'
    )
    if ($exit -ne 0) { throw "docker compose up failed (exit $exit)" }
}
else {
    Write-Host '[1/4] stack start skipped'
}

function Test-HomeService {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$ServicePort/health" -TimeoutSec 5 -UseBasicParsing
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

Write-Host '[2/4] starting home-service (Home Assistant backend) ...'
if (Test-HomeService) {
    Write-Host "      already listening on 127.0.0.1:$ServicePort"
}
else {
    $env:HOME_SERVICE_BACKEND = 'ha'
    $env:HOME_ASSISTANT_URL = 'http://127.0.0.1:8123'
    $env:HA_ENV_FILE = $haEnv
    $env:HA_OPERATION_DB = Join-Path $runtime 'operations.sqlite3'
    Start-Process -FilePath $python -ArgumentList @($serviceScript, '--port', "$ServicePort") `
        -RedirectStandardOutput $serviceLog -RedirectStandardError "$serviceLog.err" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(45)
    while (-not (Test-HomeService)) {
        if ((Get-Date) -gt $deadline) {
            throw "home-service did not become healthy; see $serviceLog and $serviceLog.err"
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "      healthy on 127.0.0.1:$ServicePort"
}

Write-Host '[3/4] checking Home Assistant and the discovered devices ...'
Push-Location $RepoRoot
try {
    $env:HA_ENV_FILE = $haEnv
    $probe = & $python (Join-Path $RepoRoot 'tools\ha-check\connection.py') 2>&1
    Write-Host "      $probe"

    $wakeScript = Join-Path $RepoRoot 'tools\voice-check\wake_words.py'
    $words = (& $python $wakeScript) -join ''
    Write-Host ''
    Write-Host "      唤醒词：$words" -ForegroundColor Yellow
    Write-Host '      默认可以直接说“卧室好热”“屋里太暗了”，也可使用唤醒词连续对话。'
    Write-Host '      房间不明确时会追问；-ListeningMode wake 可恢复仅唤醒词模式。'
    Write-Host '      也可以整句说“我出门了”“我回来了”“我要睡觉了”。'
    Write-Host '      说“没事了”或静默 20 秒结束会话。Ctrl+C 退出。'
    Write-Host ''

    if ($CheckOnly) {
        Write-Host '[4/4] check-only: not starting the voice loop'
        return 0
    }

    if ($Text) {
        Write-Host '[4/4] starting text mode (type instead of speaking) ...'
    }
    else {
        Write-Host '[4/4] starting the voice loop ...'
    }
    $loopArguments = @($loopScript)
    if (-not $NoAgent) { $loopArguments += '--agent' }
    if ($Text) { $loopArguments += '--text' }
    if ($ListeningMode) { $loopArguments += @('--listening-mode', $ListeningMode) }
    & $python @loopArguments
    return $LASTEXITCODE
}
finally {
    Pop-Location
}
