from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import launcher_support as launcher


class ClientLauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'server.py').write_text('# synthetic server', encoding='utf-8')
        env = patch.dict(os.environ, {'TEMU_DATA_ROOT': str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.ready = {'sharedAutomation': True, 'concurrentEditing': True, 'processId': 123,
                      'runtimeVersion': '1.2.179', 'workbenchId': launcher.workbench_id(self.root, self.root)}

    def test_reuses_running_backend_without_lock_spawn_or_queue_mutation(self):
        with patch.object(launcher, 'health', return_value=self.ready), \
             patch.object(launcher, 'startup_lock') as lock, patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.ensure_backend(self.root)
        self.assertTrue(result['reused']); self.assertEqual(result['processId'], 123)
        lock.assert_not_called(); spawn.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.root / 'server.py'])

    def test_occupied_slow_port_waits_instead_of_spawning_or_killing(self):
        with patch.object(launcher, 'health', side_effect=[None, None, None, self.ready]), \
             patch.object(launcher, 'startup_lock', return_value=nullcontext()), \
             patch.object(launcher, 'port_available', return_value=False), \
             patch.object(launcher.time, 'sleep'), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.ensure_backend(self.root)
        self.assertTrue(result['reused']); spawn.assert_not_called()

    def test_first_start_uses_absolute_server_python_and_existing_config_without_editing_it(self):
        envfile = self.root / '.env'; envfile.write_text('APP_MODE=demo\nOTHER_SETTING=keep\n', encoding='utf-8')
        process = Mock(); process.poll.return_value = None
        with patch.object(launcher, 'health', side_effect=[None, None, self.ready]), \
             patch.object(launcher, 'port_available', return_value=True), \
             patch.dict(os.environ, {'APP_MODE': 'live', 'UNCHANGED_FIXTURE': 'keep'}), \
             patch.object(launcher.subprocess, 'Popen', return_value=process) as spawn:
            result = launcher.ensure_backend(self.root)
        command = spawn.call_args.args[0]; options = spawn.call_args.kwargs
        self.assertEqual(command[1], str(self.root / 'server.py'))
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertEqual(options['env']['APP_MODE'], 'demo')
        self.assertEqual(options['env']['UNCHANGED_FIXTURE'], 'keep')
        self.assertEqual(envfile.read_text(encoding='utf-8'), 'APP_MODE=demo\nOTHER_SETTING=keep\n')
        self.assertFalse(result['reused'])

    def test_foreign_backend_identity_is_rejected(self):
        response = Mock(); response.read.return_value=json.dumps({**self.ready,'workbenchId':'other'}).encode()
        response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        with patch.object(launcher, 'build_opener') as opener:
            opener.return_value.open.return_value=response
            with self.assertRaisesRegex(RuntimeError, '另一套'):
                launcher.health(self.root, self.root)

    def test_invalid_mode_fails_closed_and_identity_separates_data_directories(self):
        (self.root / '.env').write_text('APP_MODE=invalid', encoding='utf-8')
        with self.assertRaises(RuntimeError):launcher.configured_mode(self.root)
        self.assertNotEqual(launcher.workbench_id(self.root,self.root),launcher.workbench_id(self.root,self.root/'other'))


if __name__ == '__main__':unittest.main()
