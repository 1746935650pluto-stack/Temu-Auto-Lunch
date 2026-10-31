"""Run the Python suite in a disposable data directory with external I/O denied.

Usage: py -3 tests/run_tests.py [--pattern test_*.py] [--timeout 600] [-v]
The parent owns the temporary directory and bounds the entire child lifetime,
including a test that leaves a worker or HTTP request hanging.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'


class ImmediateFailureResult(unittest.TextTestResult):
    """Keep long runs diagnosable even when a later test reaches the deadline."""
    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.stream.writeln('\nFAIL: ' + test.id())
        self.stream.writeln(self.failures[-1][1])

    def addError(self, test, error):
        super().addError(test, error)
        self.stream.writeln('\nERROR: ' + test.id())
        self.stream.writeln(self.errors[-1][1])

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.stream.writeln('\nFAILED SUBTEST: ' + subtest.id())
            self.stream.writeln(self._exc_info_to_string(error, subtest))


def _loopback(address):
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0])
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install_io_guard():
    """Block real credentials/databases and non-local network before imports."""
    source_root = ROOT.resolve()

    def audit(event, args):
        if event == 'socket.connect' and not _loopback(args[1]):
            raise RuntimeError('External network is disabled in the test runner; inject a fake client')
        if event == 'socket.getaddrinfo' and args[0] not in ('localhost', '127.0.0.1', '::1', None):
            try:
                local = ipaddress.ip_address(str(args[0])).is_loopback
            except ValueError:
                local = False
            if not local:
                raise RuntimeError('External DNS is disabled in the test runner')
        if event not in ('open', 'sqlite3.connect') or not args or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        value = os.fsdecode(args[0])
        if value == ':memory:' or value.startswith('file::memory:'):
            return
        if event == 'sqlite3.connect' and value.startswith('file:'):
            from urllib.parse import unquote, urlsplit
            value = unquote(urlsplit(value).path)
            if os.name == 'nt' and value.startswith('/') and len(value) > 2 and value[2] == ':':
                value = value[1:]
        path = Path(value).resolve()
        if path.is_relative_to(source_root) and (path.name.startswith('.env') or path.name == 'deepseek_profiles.json'
                                                or '.sqlite3' in path.name):
            raise RuntimeError('Production configuration/database access is disabled in tests; use TEMU_DATA_ROOT')

    sys.addaudithook(audit)


def stop_workers(baseline):
    backend = sys.modules.get('server')
    if backend is not None:
        services = [getattr(backend, 'AUTOMATION_WATCHDOG', None)]
        for name in ('COLLECTION_SYNCS', 'ORDER_AUTO_SYNCS', 'AUTOMATIC_TASKS'):
            services.extend(getattr(backend, name, {}).values())
        for service in services:
            event = getattr(service, 'stop_event', None)
            if event is not None:
                event.set()
        executors = [getattr(backend, name, None) for name in ('_title_executor', '_pool_import_executor')]
        runtime = getattr(backend, 'RUNTIME', None)
        executors.extend(getattr(lane, 'imports', None) for lane in getattr(runtime, 'lanes', {}).values())
        for executor in executors:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
    deadline = time.monotonic() + 5
    for thread in threading.enumerate():
        if thread.ident not in baseline and thread is not threading.current_thread():
            thread.join(max(0, deadline - time.monotonic()))
    return [thread.name for thread in threading.enumerate()
            if thread.ident not in baseline and thread is not threading.current_thread()]


def child(args):
    if not os.environ.get('TEMU_DATA_ROOT') or os.environ.get('TEMU_TEST_MODE') != '1':
        raise RuntimeError('Use the parent test runner to establish isolated storage')
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(TESTS), str(ROOT)]
    install_io_guard()
    baseline = {thread.ident for thread in threading.enumerate()}
    loader = unittest.TestLoader()
    if args.filter:
        loader.testNamePatterns = [f'*{value}*' for value in args.filter]
    files = sorted(TESTS.glob(args.pattern))[args.shard_index::args.shard_count]
    suite = unittest.TestSuite(loader.discover(str(TESTS), pattern=path.name) for path in files)
    if not suite.countTestCases():
        print('No tests matched the requested pattern/filter.', file=sys.stderr)
        return 2
    print(f'Running {suite.countTestCases()} tests (shard {args.shard_index + 1}/{args.shard_count}) '
          'with isolated storage and no external network.', flush=True)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1, resultclass=ImmediateFailureResult).run(suite)
    leaks = stop_workers(baseline)
    if leaks:
        print('ERROR: workers did not stop: ' + ', '.join(leaks), file=sys.stderr, flush=True)
        # Parent cleans the disposable directory after the process is gone.
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)
    return 0 if result.wasSuccessful() else 1


def run_shard(args, index, count):
    with tempfile.TemporaryDirectory(prefix='temu-tests-') as directory:
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(('MIAOSHOU_', 'DEEPSEEK_', 'OPENAI_', 'COMFYUI_', 'TEMU_'))}
        environment.update(TEMU_DATA_ROOT=directory, TEMU_TEST_MODE='1', APP_MODE='demo', PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, str(Path(__file__).resolve()), '--child', '--pattern', args.pattern,
                   '--shard-index', str(index), '--shard-count', str(count)]
        for value in args.filter or []:
            command.extend(['--filter', value])
        if args.verbose:
            command.append('--verbose')
        try:
            code = subprocess.run(command, cwd=ROOT, env=environment, timeout=args.timeout).returncode
            print(f'Shard {index + 1}/{count} finished with exit code {code}.', flush=True)
            return code
        except subprocess.TimeoutExpired:
            print(f'Test process stopped after {args.timeout} seconds; no production data was used.', file=sys.stderr)
            return 124


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern', default='test_*.py')
    parser.add_argument('--filter', action='append', help='Run matching test method names; may be repeated')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--jobs', type=int, default=1, help='Independent test processes, 1 to 4')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--shard-index', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--shard-count', type=int, default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args)
    if args.timeout <= 0 or not 1 <= args.jobs <= 4:
        parser.error('--timeout must be positive and --jobs must be between 1 and 4')
    count = min(args.jobs, len(list(TESTS.glob(args.pattern))))
    if not count:
        parser.error('No test files matched --pattern')
    if count == 1:
        return run_shard(args, 0, 1)
    with ThreadPoolExecutor(max_workers=count) as executor:
        results = list(executor.map(lambda index: run_shard(args, index, count), range(count)))
    return 124 if 124 in results else (1 if any(results) else 0)


if __name__ == '__main__':
    raise SystemExit(main())
