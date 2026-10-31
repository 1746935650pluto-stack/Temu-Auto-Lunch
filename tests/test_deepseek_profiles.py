import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from deepseek_profiles import DeepSeekProfileError, DeepSeekProfileStore


class DeepSeekProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DeepSeekProfileStore(Path(self.temp.name) / 'deepseek_profiles.json')
        self.addCleanup(self.temp.cleanup)

    def test_public_profiles_hide_keys_and_assignment_resolves_the_right_key(self):
        self.store.save('nl-api', 'NL 专用 API', 'nl-secret')
        self.store.save('lulu-api', 'LULU 专用 API', 'lulu-secret')
        self.store.assign(1, 'nl-api')
        self.store.assign(4, 'lulu-api')
        public = self.store.public()
        self.assertEqual(public['assignments'], {'1': 'nl-api', '4': 'lulu-api'})
        self.assertNotIn('nl-secret', str(public))
        self.assertEqual(self.store.resolve(1)['apiKey'], 'nl-secret')
        self.assertEqual(self.store.resolve(4)['apiKey'], 'lulu-secret')
        self.assertIsNone(self.store.resolve(2))

    def test_delete_unassigns_accounts_and_rejects_unknown_profiles(self):
        self.store.save('nl-api', 'NL 专用 API', 'nl-secret')
        self.store.assign(1, 'nl-api')
        public = self.store.delete('nl-api')
        self.assertEqual(public['profiles'], [])
        self.assertEqual(public['assignments'], {})
        with self.assertRaisesRegex(DeepSeekProfileError, '不存在'):
            self.store.assign(1, 'nl-api')

    def test_create_never_overwrites_assigned_key_and_replace_requires_existing_id(self):
        self.store.create('nl-api', 'NL', 'first-secret')
        self.store.assign(1, 'nl-api')
        before = self.store.path.read_bytes()
        for create in (self.store.create, self.store.save):
            with self.assertRaisesRegex(DeepSeekProfileError, '已存在'):
                create('nl-api', 'NL changed', 'replacement-secret')
        with self.assertRaisesRegex(DeepSeekProfileError, '不存在'):
            self.store.replace('missing', 'Missing', 'replacement-secret')
        self.assertEqual(self.store.path.read_bytes(), before)
        public = self.store.replace('nl-api', 'NL changed', 'replacement-secret')
        self.assertEqual(public['assignments'], {'1': 'nl-api'})
        self.assertEqual(self.store.resolve(1)['apiKey'], 'replacement-secret')
        self.assertNotIn('replacement-secret', str(public))

    def test_revision_survives_reload_and_migrates_legacy_file_only_on_write(self):
        self.store.path.write_text(json.dumps({'profiles': [], 'assignments': {}}), encoding='utf-8')
        self.assertEqual(self.store.public()['revision'], 0)
        self.assertNotIn('revision', json.loads(self.store.path.read_text()))
        self.assertEqual(self.store.create('nl-api', 'NL', 'synthetic')['revision'], 1)
        self.assertEqual(self.store.assign(1, 'nl-api')['revision'], 2)
        reloaded = DeepSeekProfileStore(self.store.path)
        self.assertEqual(reloaded.public()['revision'], 2)
        self.assertEqual(reloaded.replace('nl-api', 'NL 2', 'synthetic2')['revision'], 3)
        self.assertEqual(reloaded.delete('nl-api')['revision'], 4)

    def test_replace_failure_preserves_key_assignment_and_revision(self):
        self.store.create('nl-api', 'NL', 'synthetic')
        self.store.assign(1, 'nl-api')
        before = self.store.path.read_bytes()
        with patch('deepseek_profiles.os.replace', side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):
                self.store.replace('nl-api', 'NL new', 'synthetic2')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.path.parent.glob('deepseek-profiles-*.tmp')), [])
