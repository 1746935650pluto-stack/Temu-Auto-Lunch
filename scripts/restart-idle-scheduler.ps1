param([Parameter(Mandatory=$true)][int]$ExpectedProcessId,
      [Parameter(Mandatory=$true)][switch]$ResumeConfirmed)
$ErrorActionPreference = 'Stop'
if (-not $ResumeConfirmed) { throw 'Explicit approval to resume queued remote saves is required.' }
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskUrl = 'http://127.0.0.1:8787'
function Assert-IdleScheduler {
    $taskHealth = Invoke-RestMethod "$taskUrl/api/health" -TimeoutSec 5
    if ($taskHealth.processId -ne $ExpectedProcessId -or -not $taskHealth.sharedAutomation) { throw 'Backend identity changed.' }
    $taskActivity = (Invoke-RestMethod "$taskUrl/api/automation/shared/activity" -TimeoutSec 5).data
    if ($null -eq $taskActivity.editing -or $taskActivity.editing -or $taskActivity.publishing -or
        $null -eq $taskActivity.activeTransfers -or $taskActivity.activeTransfers -ne 0) { throw 'A worker is active; no restart allowed.' }
    & py -3 (Join-Path $PSScriptRoot 'check_idle_queue.py')
    if ($LASTEXITCODE -ne 0) { throw 'Queue idle verification failed.' }
}
Assert-IdleScheduler
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ExpectedProcessId"
if ($taskProcess.Name -notmatch '^pythonw?\.exe$' -or $taskProcess.CommandLine -notmatch '(?i)(^|[\\/"\s])server\.py($|["\s])') { throw 'Unexpected listener process.' }
$taskMode = (Invoke-RestMethod "$taskUrl/api/health" -TimeoutSec 5).mode
if ($taskMode -notin @('live','demo')) { throw 'Unknown running mode.' }
& py -3 (Join-Path $PSScriptRoot 'backup_local_databases.py') --label v1.2.118
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; nothing stopped.' }
Start-Sleep -Seconds 2
Assert-IdleScheduler
Stop-Process -Id $ExpectedProcessId
Wait-Process -Id $ExpectedProcessId -Timeout 10 -ErrorAction SilentlyContinue
$taskLogs = Join-Path $taskRoot 'logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$env:APP_MODE = $taskMode
$env:HOST = '127.0.0.1'
$env:PORT = '8787'
$env:PYTHONUNBUFFERED = '1'
$taskStarted = Start-Process -FilePath $taskProcess.ExecutablePath -ArgumentList 'server.py' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs "backend-$taskStamp.out.log") -RedirectStandardError (Join-Path $taskLogs "backend-$taskStamp.err.log")
for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $taskHealth = Invoke-RestMethod "$taskUrl/api/health" -TimeoutSec 2
        if ($taskHealth.processId -eq $taskStarted.Id -and $taskHealth.version -eq '1.2.118' -and $taskHealth.mode -eq $taskMode) {
            $taskHealth | ConvertTo-Json -Compress
            exit 0
        }
    } catch { }
}
throw 'New backend not verified; inspect local logs. Do not repeatedly restart.'
