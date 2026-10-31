"""Exercise the runner barriers before any real file read/network request occurs."""
import os
from pathlib import Path
import socket
import sqlite3
import unittest

import server


@unittest.skipUnless(os.getenv('TEMU_TEST_MODE') == '1', 'Run through tests/run_tests.py')
class TestRunnerIsolation(unittest.TestCase):
    def test_import_uses_disposable_root_and_no_real_ai_config(self):
        self.assertNotEqual(server.DATA_ROOT, server.ROOT)
        self.assertEqual(server.DEEPSEEK_PROFILES.path.parent, Path(os.environ['TEMU_DATA_ROOT']))
        self.assertFalse(server.DeepSeekClient(1).configured)

    def test_production_secrets_and_sqlite_are_rejected_before_access(self):
        for name in ('.env', 'deepseek_profiles.json'):
            with self.assertRaisesRegex(RuntimeError, 'Production configuration'):
                (server.ROOT / name).read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Production configuration'):
            sqlite3.connect(server.ROOT / 'automation_pool.sqlite3')

    def test_non_loopback_network_is_rejected_before_connect(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(RuntimeError, 'External network'):
                connection.connect(('203.0.113.1', 443))
