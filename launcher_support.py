"""Read-only identity probes and non-destructive workbench startup."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

BASE_URL = 'http://127.0.0.1:8787'


def workbench_id(root, data_root):
    value = '|'.join(str(Path(p).resolve()).casefold() for p in (root, data_root))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def configured_mode(data_root):
    mode = 'demo'
    path = Path(data_root) / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            match = re.match(r'^\s*APP_MODE\s*=\s*(.*?)\s*$', line)
            if match:
                mode = match[1].strip().strip('\"\'').lower()
    if mode not in ('live', 'demo'):
        raise RuntimeError('本机 APP_MODE 配置无效，未启动新后台。')
    return mode


def process_owns_project(pid, root):
    if type(pid) is not int or not 0 < pid < 2 ** 31 or os.name != 'nt':
        return False
    command = ('[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '
        f'Get-CimInstance Win32_Process -Filter "ProcessId = {pid}" | '
        'Select-Object Name,CommandLine | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
        capture_output=True, encoding='utf-8-sig', timeout=8,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        process = json.loads(result.stdout)
        return (process.get('Name', '').lower() in ('python.exe', 'pythonw.exe')
                and str(Path(root).resolve() / 'server.py').casefold()
                in str(process.get('CommandLine', '')).casefold())
    except (TypeError, ValueError):
        return False


def health(root, data_root):
    # This is strictly a loopback probe. Inherited proxy settings must not
    # route a local health request through an external proxy.
    try:
        with build_opener(ProxyHandler({})).open(BASE_URL + '/api/health', timeout=2) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise RuntimeError('本机端口返回了非预期数据。')
        value = json.loads(raw)
    except (OSError, URLError, TimeoutError):
        return None
    except (TypeError, ValueError) as error:
        raise RuntimeError('8787 端口不是有效的工作台后台，未启动新进程。') from error
    if not isinstance(value, dict) or not value.get('sharedAutomation') or not value.get('concurrentEditing'):
        raise RuntimeError('8787 端口被其他应用占用，未修改或停止该应用。')
    identity = value.get('workbenchId')
    if identity:
        verified = identity == workbench_id(root, data_root)
    else:
        verified = process_owns_project(value.get('processId'), root)
    if not verified:
        raise RuntimeError('8787 端口属于另一套工作台，请先确认原后台；未启动重复进程。')
    return value


@contextmanager
def startup_lock(data_root, timeout=40):
    path = Path(data_root) / 'logs' / 'launcher.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        started = time.monotonic()
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - started >= timeout:
                    raise RuntimeError('另一个启动器仍在启动后台，请稍后重试。')
                time.sleep(0.2)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)


def port_available():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', 8787))
            return True
        except OSError:
            return False


def ensure_backend(root, *, startup_timeout=40):
    root = Path(root).resolve()
    if not (root / 'server.py').is_file():
        raise RuntimeError('找不到工作台 server.py，请从完整程序目录启动。')
    data_root = Path(os.getenv('TEMU_DATA_ROOT') or root).resolve()
    existing = health(root, data_root)
    if existing:
        return {'url': BASE_URL + '/', 'reused': True, **existing}
    with startup_lock(data_root, startup_timeout):
        existing = health(root, data_root)
        if existing:
            return {'url': BASE_URL + '/', 'reused': True, **existing}
        process = None
        if port_available():
            stamp = time.strftime('%Y%m%d-%H%M%S')
            logs = data_root / 'logs'
            environment = {**os.environ, 'HOST': '127.0.0.1', 'PORT': '8787',
                           'APP_MODE': configured_mode(data_root), 'PYTHONUNBUFFERED': '1'}
            python = Path(sys.executable)
            if python.name.lower() == 'pythonw.exe':
                python = python.with_name('python.exe')
            with (logs / f'launcher-{stamp}.out.log').open('ab') as out, \
                 (logs / f'launcher-{stamp}.err.log').open('ab') as err:
                process = subprocess.Popen([str(python), str(root / 'server.py')],
                    cwd=root, env=environment, stdout=out, stderr=err,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
        # An occupied but slow port may be a backend already starting. Wait;
        # never kill it, reset pause state or start competing queue consumers.
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            existing = health(root, data_root)
            if existing:
                return {'url': BASE_URL + '/', 'reused': process is None, **existing}
            if process is not None and process.poll() is not None:
                raise RuntimeError('后台未能启动，请查看 logs/launcher 日志；原队列未修改。')
            time.sleep(0.35)
        raise RuntimeError('后台仍在启动或响应较慢；未停止进程，也未重复启动，请稍后重试。')
