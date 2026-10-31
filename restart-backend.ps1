$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskUrl = 'http://127.0.0.1:8787'

function Get-ConfiguredAppMode {
    $taskEnvRoot = if ($env:TEMU_DATA_ROOT) { $env:TEMU_DATA_ROOT } else { $taskRoot }
    $taskEnvPath = Join-Path $taskEnvRoot '.env'
    if (-not (Test-Path -LiteralPath $taskEnvPath)) { return 'demo' }
    $taskModeLine = Get-Content -LiteralPath $taskEnvPath | Where-Object { $_ -match '^\s*APP_MODE\s*=' } | Select-Object -Last 1
    if (-not $taskModeLine) { return 'demo' }
    $taskMode = (($taskModeLine -split '=', 2)[1]).Trim().Trim('"', "'").ToLowerInvariant()
    if ($taskMode -notin @('demo', 'live')) { throw "APP_MODE must be demo or live, received: $taskMode" }
    return $taskMode
}

function Backup-TaskDatabases {
    $taskBackupData = if ($env:TEMU_DATA_ROOT) { $env:TEMU_DATA_ROOT } else { $taskRoot }
    & $taskPython -3 (Join-Path $taskRoot 'scripts/backup_local_databases.py') --data-root $taskBackupData --label before-restart --version $taskBackupVersion
    if ($LASTEXITCODE -ne 0) { throw 'Consistent backup verification failed. Backend was not upgraded.' }
}

function Get-TaskPortOwners {
    # Get-NetTCPConnection is preferred, but can be unavailable on a damaged
    # or locked-down Windows networking provider. netstat is a read-only
    # fallback so the restart launcher does not mistake a stale listener for
    # a free port.
    try {
        $taskOwners = @(Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
        if ($taskOwners.Count) { return @($taskOwners) }
    } catch { }
    $taskOwners = @()
    foreach ($taskLine in @(netstat -ano -p tcp 2>$null)) {
        if ($taskLine -match '^\s*TCP\s+\S+:8787\s+\S+\s+LISTENING\s+(\d+)\s*$') {
            $taskOwners += [int]$Matches[1]
        }
    }
    return @($taskOwners | Select-Object -Unique)
}

function Wait-TaskProcessExit([int]$taskProcessId) {
    for ($taskWait = 0; $taskWait -lt 20; $taskWait++) {
        if (-not (Get-Process -Id $taskProcessId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

try {
    $taskPython = (Get-Command py.exe -ErrorAction Stop).Source
    $taskBackupVersion = 'unknown'
    $taskAppMode = Get-ConfiguredAppMode
    $taskProcessIds = @(Get-TaskPortOwners)
    if ($taskProcessIds.Count -gt 1) { throw 'Multiple processes own port 8787. Please close the old backend window manually.' }
    if ($taskProcessIds.Count -eq 1) {
        $taskProcessId = $taskProcessIds[0]
        $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $taskProcessId" -ErrorAction Stop
        if ($taskProcess.Name -notmatch '^pythonw?\.exe$' -or
            $taskProcess.CommandLine -notmatch '(?i)(^|[\\/"\s])server\.py($|["\s])') {
            throw 'Port 8787 belongs to another application. No process was stopped.'
        }
        $taskResponsive = $false
        try {
            $taskHealth = Invoke-RestMethod "$taskUrl/api/health" -TimeoutSec 3
            $taskBackupVersion = $taskHealth.version
            if ($null -eq $taskHealth.miaoShouConfigured) { throw 'Port response is not the workbench.' }
            $taskAccounts = (Invoke-RestMethod "$taskUrl/api/miaoshou-accounts" -TimeoutSec 3).data.accounts
            $taskActivity = $null
            if ($taskHealth.sharedAutomation) {
                $taskActivity = (Invoke-RestMethod "$taskUrl/api/automation/shared/drain-check" -TimeoutSec 3).data
                if ($null -eq $taskActivity.activeTransfers) {
                    throw 'Cannot verify shared task activity. No process was stopped.'
                }
                if ($taskActivity.drained -ne $true) { throw 'Pause automatic processing and wait for all active requests before restarting.' }
                $taskPaused = $taskActivity.paused -eq $true
                if ($null -eq $taskActivity.paused -and ($taskActivity.activeTransfers -or $taskActivity.editing -or $taskActivity.publishing)) {
                    throw 'The current backend cannot verify paused work. Wait for queues to finish before restarting.'
                }
                if ($taskActivity.editing -or $taskActivity.publishing -or $taskActivity.inFlightTransfers -or $taskActivity.autoTasksBusy -or $taskActivity.reloading) {
                    throw 'Tasks are still actively saving or publishing. Pause and wait for the current save to stop before restarting.'
                }
                # Collection/order sync is read-only against MiaoShou and its
                # local import writes are idempotent. HourlySync records an
                # interrupted run at startup, so it is safe to let a paused
                # controlled restart stop a long scan rather than delaying an
                # urgent backend update for thousands of read-only pages.
                if ($taskActivity.activeTransfers -and -not $taskPaused) {
                    throw 'Tasks are queued in the shared scheduler. Pause automatic processing before restarting.'
                }
            }
            # ``activity`` above counts queued/running/publishing work from
            # every physical pool directly. Do not load every historical job
            # just to restart: large accounts can have thousands of completed
            # records and make that read exceed the safety probe timeout.
            # The paused aggregate still blocks active remote writes while
            # allowing durable queued work to survive the controlled restart.
            $taskResponsive = $true
        } catch {
            if ($_.Exception.Message -match 'Tasks are still running|Cannot verify shared') { throw }
            throw 'Cannot verify that all account and shared tasks are idle. No process was stopped. Check the backend before restarting.'
        }
        # Recheck ownership immediately before stopping the verified Python service.
        $taskOwner = @(Get-TaskPortOwners)
        if ($taskOwner.Count -ne 1 -or $taskOwner[0] -ne $taskProcessId) { throw 'Port ownership changed. Please retry.' }
        if ($taskResponsive) {
            Write-Host "Stopping old workbench process $taskProcessId..."
        } else {
            Write-Host "Stopping unresponsive verified workbench process $taskProcessId..."
        }
        Stop-Process -Id $taskProcessId -ErrorAction Stop
        if (-not (Wait-TaskProcessExit $taskProcessId)) { throw 'The old workbench did not exit within 5 seconds. Close its Python process in Task Manager, then run this file again.' }
    }
    Backup-TaskDatabases
    $env:HOST = '127.0.0.1'
    $env:PORT = '8787'
    # Load the explicit local setting for this child process so an inherited
    # shell APP_MODE cannot silently keep the workbench in a different mode.
    $env:APP_MODE = $taskAppMode
    Write-Host 'Starting the updated backend...'
    $taskStarted = Start-Process -FilePath $taskPython -ArgumentList @('-3', 'server.py') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru
    $taskVerified = $false
    for ($taskAttempt = 0; $taskAttempt -lt 60; $taskAttempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $taskHealth = Invoke-RestMethod "$taskUrl/api/health" -TimeoutSec 1
            if ($taskHealth.version -eq '1.2.190' -and $taskHealth.concurrentEditing -eq $true -and $taskHealth.mode -eq $taskAppMode) {
                $taskVerified = $true
                break
            }
        } catch { }
        if ($taskStarted.HasExited) { break }
    }
    if (-not $taskVerified) { throw "Startup could not be verified in APP_MODE=$taskAppMode. Check .env and run: py -3 server.py" }
    Write-Host "Backend 1.2.190 is ready in APP_MODE=$taskAppMode. Paused queues survive a controlled restart and resume on request." -ForegroundColor Green
    exit 0
} catch {
    if ($_.Exception.Message -match '拒绝访问|Access is denied') {
        Write-Host 'The verified old backend cannot be stopped by this Windows account. In Task Manager, end its Python process, then run restart-backend.cmd again.' -ForegroundColor Red
    } else {
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
    exit 1
}
