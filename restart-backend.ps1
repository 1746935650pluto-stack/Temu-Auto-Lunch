$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskUrl = 'http://127.0.0.1:8787'
$taskOriginalPaused = $null
$taskPauseChanged = $false
$taskStopped = $false
$taskBackupDone = $false

function Set-TaskPause([bool]$paused) {
    $body = @{paused=$paused; confirmation='SET_SHARED_AUTOMATION_PAUSE'} | ConvertTo-Json
    Invoke-RestMethod "$taskUrl/api/automation/shared/pause" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 10 | Out-Null
}

function Wait-TaskDrain {
    for ($taskDrainAttempt = 0; $taskDrainAttempt -lt 90; $taskDrainAttempt++) {
        $state = (Invoke-RestMethod "$taskUrl/api/automation/shared/drain-check" -TimeoutSec 5).data
        if ($null -eq $state.activeTransfers -or $null -eq $state.paused -or $null -eq $state.drained) {
            throw 'Cannot verify shared task activity; backend has not been stopped.'
        }
        if ($state.drained -eq $true -and -not ($state.running -or $state.editing -or $state.publishing -or $state.inFlightTransfers -or $state.publishWorker -or $state.autoTasksBusy -or $state.reloading)) {
            if ($state.syncing) { Write-Host 'Read-only synchronization is active. Completed snapshots remain; interrupted scans will be retried by scheduled synchronization.' }
            return $state
        }
        if ($taskDrainAttempt % 5 -eq 0) {
            Write-Host "Waiting for active requests: running=$($state.running), publishing=$($state.publishing), copies=$($state.inFlightTransfers), scheduler=$($state.editing), automatic=$($state.autoTasksBusy)."
        }
        Start-Sleep -Seconds 1
    }
    throw 'Active requests did not drain in time. Backend was not stopped; try again after current work finishes.'
}

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
            $taskActivity = $null
            if ($taskHealth.sharedAutomation) {
                $taskActivity = (Invoke-RestMethod "$taskUrl/api/automation/shared/drain-check" -TimeoutSec 3).data
                if ($null -eq $taskActivity.activeTransfers) {
                    throw 'Cannot verify shared task activity. No process was stopped.'
                }
                if ($null -eq $taskActivity.paused) { throw 'Cannot verify shared pause state.' }
                $taskOriginalPaused = [bool]$taskActivity.paused
                if (-not $taskOriginalPaused) {
                    # Mark before the request so a lost response still restores
                    # the original state if preflight later fails.
                    $taskPauseChanged = $true
                    Write-Host 'Pausing new task claims for a controlled restart...'
                    Set-TaskPause $true
                }
                $taskActivity = Wait-TaskDrain
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
            throw "Restart preflight failed: $($_.Exception.Message)"
        }
        # Keep the verified backend alive if the backup fails.
        Write-Host 'Creating and verifying a consistent database backup...'
        Backup-TaskDatabases
        $taskBackupDone = $true
        if ($taskHealth.sharedAutomation) { $taskActivity = Wait-TaskDrain }
        # Recheck ownership immediately before stopping the verified Python service.
        $taskOwner = @(Get-TaskPortOwners)
        if ($taskOwner.Count -ne 1 -or $taskOwner[0] -ne $taskProcessId) { throw 'Port ownership changed. Please retry.' }
        if ($taskResponsive) {
            Write-Host "Stopping old workbench process $taskProcessId..."
        } else {
            Write-Host "Stopping unresponsive verified workbench process $taskProcessId..."
        }
        Stop-Process -Id $taskProcessId -ErrorAction Stop
        $taskStopped = $true
        if (-not (Wait-TaskProcessExit $taskProcessId)) { throw 'The old workbench did not exit within 5 seconds. Close its Python process in Task Manager, then run this file again.' }
    }
    if (-not $taskBackupDone) { Backup-TaskDatabases }
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
            if ($taskHealth.runtimeVersion -eq '1.2.205' -and $taskHealth.resourceVersion -eq '1.2.205' -and $taskHealth.pendingUpdate -eq $false -and $taskHealth.concurrentEditing -eq $true -and $taskHealth.mode -eq $taskAppMode) {
                $taskVerified = $true
                break
            }
        } catch { }
        # py.exe may exit after handing off to python.exe; verify the actual
        # listener rather than treating launcher exit as backend failure.
    }
    if (-not $taskVerified) { throw "Startup could not be verified in APP_MODE=$taskAppMode. Check .env and run: py -3 server.py" }
    if ($taskPauseChanged) {
        Set-TaskPause $false
        $taskPauseChanged = $false
    }
    Write-Host "Backend 1.2.205 is ready in APP_MODE=$taskAppMode. Original queue pause state restored." -ForegroundColor Green
    exit 0
} catch {
    if ($taskPauseChanged -and -not $taskStopped) {
        try { Set-TaskPause $false; $taskPauseChanged = $false }
        catch { Write-Host 'Could not restore automatic processing; it remains paused. Resume it after checking the backend.' -ForegroundColor Yellow }
    }
    if ($_.Exception.Message -match '拒绝访问|Access is denied') {
        Write-Host 'The verified old backend cannot be stopped by this Windows account. In Task Manager, end its Python process, then run restart-backend.cmd again.' -ForegroundColor Red
    } else {
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
    exit 1
}
