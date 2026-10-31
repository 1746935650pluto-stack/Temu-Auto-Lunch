from contextlib import closing
import http.client
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from urllib.parse import urljoin
from scripts.package_release import create_release

class ReleaseRuntimeTests(unittest.TestCase):
    def test_clean_package_starts_and_serves_all_declared_local_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);archive=folder/'release.zip';create_release(archive)
            app=folder/'app';data=folder/'data';data.mkdir()
            with zipfile.ZipFile(archive) as package:package.extractall(app)
            with closing(socket.socket()) as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            env={**os.environ,'TEMU_DATA_ROOT':str(data),'APP_MODE':'demo','PORT':str(port),'HOST':'127.0.0.1',
                 'PYTHONDONTWRITEBYTECODE':'1'}
            with (folder/'stderr.log').open('w') as log:
                process=subprocess.Popen([sys.executable,str(app/'server.py')],cwd=app,env=env,stdout=log,stderr=log)
                try:
                    deadline=time.monotonic()+15
                    while True:
                        try:
                            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
                            conn.request('GET','/');response=conn.getresponse();html=response.read().decode();conn.close()
                            self.assertEqual(response.status,200);break
                        except OSError:
                            if process.poll() is not None or time.monotonic()>deadline:
                                self.fail((folder/'stderr.log').read_text(encoding='utf-8'))
                            time.sleep(.1)
                    assets=set(re.findall(r'(?:src|href)="(/?[^"?#]+\.(?:js|css))(?:\?[^"#]*)?"',html))
                    self.assertIn('/live-status.js',{urljoin('/',a) for a in assets})
                    for asset in assets:
                        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                        conn.request('GET',urljoin('/',asset));response=conn.getresponse();response.read();conn.close()
                        self.assertEqual(response.status,200,asset)
                finally:
                    process.terminate();process.wait(10)
