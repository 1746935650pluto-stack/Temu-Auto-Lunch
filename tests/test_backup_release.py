"""Only synthetic databases and release files are used in these tests."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.backup_local_databases import create_backup, retention_preview, verify_backup
from scripts.package_release import create_release


class BackupReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_sqlite_backup_includes_committed_wal_and_verifies_corruption(self):
        with closing(sqlite3.connect(self.root / 'pool.sqlite3')) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE rows (id INTEGER)')
            connection.execute('INSERT INTO rows VALUES (7)'); connection.commit()
            result = create_backup(self.root, version='test', pinned=True)
        folder = Path(result['path'])
        self.assertTrue(result['verified'])
        with closing(sqlite3.connect(folder / 'pool.sqlite3')) as connection:
            self.assertEqual(connection.execute('SELECT id FROM rows').fetchone(), (7,))
        self.assertEqual(retention_preview(self.root)['backups'][0]['action'], 'keep-pinned')
        with (folder / 'pool.sqlite3').open('ab') as stream:
            stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            verify_backup(folder)

    def test_retention_is_preview_only_keeps_ten_pinned_and_unknown_backups(self):
        backups = self.root / 'backups'; backups.mkdir()
        for index in range(13):
            folder = backups / str(index); folder.mkdir()
            manifest = {'formatVersion': 1, 'status': 'complete', 'createdAt': f'2026-10-{index + 1:02d}', 'pinned': index == 0}
            (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (backups / 'legacy').mkdir()
        preview = retention_preview(self.root)
        self.assertEqual(preview['deleted'], 0)
        self.assertEqual(sum(row['action'] == 'keep-recent' for row in preview['backups']), 10)
        self.assertEqual(sum(row['action'] == 'candidate-after-verification' for row in preview['backups']), 2)
        self.assertEqual(sum(row['action'] == 'keep-pinned' for row in preview['backups']), 1)
        self.assertEqual(len(list(backups.iterdir())), 14)

    def test_release_allowlist_excludes_credentials_and_databases(self):
        (self.root / 'app.js').write_text('console.log("fixture")', encoding='utf-8')
        (self.root / '.env').write_text('SYNTHETIC_SECRET=not-a-real-key', encoding='utf-8')
        (self.root / 'deepseek_profiles.json').write_text('{}', encoding='utf-8')
        (self.root / 'pool.sqlite3').write_bytes(b'not a database')
        output = self.root / 'release.zip'
        with patch('scripts.package_release.RELEASE_FILES', ('app.js',)):
            self.assertTrue(create_release(output, self.root)['verified'])
            with self.assertRaises(FileExistsError):
                create_release(output, self.root)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {'app.js', 'release-manifest.json'})

    def test_manifest_cannot_reference_outside_backup(self):
        folder = self.root / 'backup'; folder.mkdir()
        (folder / 'manifest.json').write_text(json.dumps({'formatVersion': 1, 'status': 'complete',
            'databases': [{'name': '../other.sqlite3'}]}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            verify_backup(folder)


if __name__ == '__main__':
    unittest.main()
