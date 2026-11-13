"""Execute the restart script with all process, HTTP and backup operations mocked."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import unittest


class RestartPreflightTests(unittest.TestCase):
    def run_case(self, scenario):
        shell = shutil.which('powershell.exe') or shutil.which('pwsh')
        if not shell:
            self.skipTest('PowerShell unavailable')
        source = (Path(__file__).resolve().parents[1] / 'restart-backend.ps1').read_text(encoding='utf-8-sig')
        mocks = r'''
$global:scenario = '__SCENARIO__'
$global:paused = $global:scenario -eq 'already-paused'
$global:started = $false
$global:stopped = $false
$global:trace = @()
function Trace($value) {
    $global:trace += $value
    ConvertTo-Json -InputObject @($global:trace) | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'trace.json') -Encoding UTF8
}
function Get-ConfiguredAppMode { return 'live' }
function Get-TaskPortOwners { return @(12345) }
function Get-Command { return [pscustomobject]@{Source='mock-python'} }
function Get-CimInstance { return [pscustomobject]@{Name='python.exe';CommandLine='python server.py'} }
function Start-Sleep { }
function Wait-TaskProcessExit { return $true }
function Backup-TaskDatabases {
    Trace 'backup'
    if ($global:scenario -eq 'backup-failure') { throw 'fixture backup verification failed' }
}
function Stop-Process { Trace 'stop'; $global:stopped=$true }
function Start-Process { Trace 'start'; $global:started=$true; return [pscustomobject]@{HasExited=$true} }
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if ($Uri.EndsWith('/pause')) {
        $global:paused = [bool]($Body | ConvertFrom-Json).paused
        Trace ('pause:'+$global:paused.ToString().ToLowerInvariant())
        return @{data=@{paused=$global:paused}}
    }
    if ($Uri.EndsWith('/health')) {
        $version=if($global:started){'__VERSION__'}else{'1.2.202'}
        return @{version=$version;runtimeVersion=$version;resourceVersion='__VERSION__';pendingUpdate=(-not $global:started);
                 miaoShouConfigured=$true;sharedAutomation=$true;concurrentEditing=$true;mode='live'}
    }
    if ($Uri.EndsWith('/drain-check')) {
        $busy=$global:scenario -eq 'busy'
        return @{data=@{activeTransfers=0;paused=$global:paused;drained=($global:paused -and -not $busy);
                        running=[int]$busy;editing=$busy;publishing=0;inFlightTransfers=0;publishWorker=$false;
                        autoTasksBusy=$false;reloading=$false;syncing=($global:scenario -eq 'read-sync')}}
    }
    throw 'Unexpected HTTP request in fixture'
}
'''.replace('__SCENARIO__', scenario).replace('__VERSION__', re.search(r"runtimeVersion -eq '([0-9.]+)'", source).group(1))
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'restart-fixture.ps1'
            script.write_text(source.replace('\ntry {', '\n' + mocks + '\ntry {', 1), encoding='utf-8-sig')
            result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                    capture_output=True, timeout=20)
            trace_path = Path(folder) / 'trace.json'
            trace = json.loads(trace_path.read_text(encoding='utf-8-sig')) if trace_path.exists() else []
            return result.returncode, trace, result.stdout.decode(errors='replace')

    def test_idle_unpaused_automatically_pauses_backs_up_and_resumes(self):
        code, trace, _ = self.run_case('idle')
        self.assertEqual(code, 0)
        self.assertEqual(trace, ['pause:true', 'backup', 'stop', 'start', 'pause:false'])

    def test_existing_pause_is_preserved(self):
        code, trace, _ = self.run_case('already-paused')
        self.assertEqual(code, 0)
        self.assertEqual(trace, ['backup', 'stop', 'start'])

    def test_backup_failure_keeps_backend_and_restores_original_state(self):
        code, trace, output = self.run_case('backup-failure')
        self.assertEqual(code, 1)
        self.assertEqual(trace, ['pause:true', 'backup', 'pause:false'])
        self.assertIn('fixture backup verification failed', output)

    def test_busy_writer_never_stops_or_backs_up(self):
        code, trace, output = self.run_case('busy')
        self.assertEqual(code, 1)
        self.assertEqual(trace, ['pause:true', 'pause:false'])
        self.assertIn('Active requests did not drain', output)

    def test_read_sync_keeps_existing_safe_restart_policy(self):
        code, trace, output = self.run_case('read-sync')
        self.assertEqual(code, 0)
        self.assertEqual(trace, ['pause:true', 'backup', 'stop', 'start', 'pause:false'])
        self.assertIn('Read-only synchronization is active', output)


if __name__ == '__main__':
    unittest.main()
