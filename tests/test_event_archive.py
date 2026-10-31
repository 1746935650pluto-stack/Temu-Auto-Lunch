"""Archival, recovery, merged reads and backups use only disposable data."""
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Thread
import unittest
from unittest.mock import patch

import event_archive
from event_archive import (archive_path, archive_task_events, preview_event_archives,
                           read_events_for_jobs, read_job_events)
from scripts import archive_task_events as archive_cli
from scripts import backup_local_databases as backups


NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=100)).isoformat()
RECENT = (NOW - timedelta(days=1)).isoformat()


class EventArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'automation_pool.sqlite3'
        self.create_pool(self.path)

    @staticmethod
    def create_pool(path):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE automation_jobs (job_id TEXT PRIMARY KEY,status TEXT NOT NULL,'
                       'step TEXT NOT NULL,updated_at TEXT NOT NULL)')
            db.execute('''CREATE TABLE automation_job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES automation_jobs(job_id) ON DELETE CASCADE,
                step TEXT NOT NULL,status TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL)''')
            db.execute('CREATE INDEX events_job_idx ON automation_job_events(job_id,event_id)')

    def add_job(self, job='success', *, path=None, status='done', step='complete', updated_at=OLD, events=None):
        path = path or self.path
        events = events if events is not None else [('prepare', 'running', '开始', OLD),
                                                   ('complete', 'done', '完成', updated_at)]
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('INSERT INTO automation_jobs VALUES (?,?,?,?)', (job, status, step, updated_at))
            db.executemany('INSERT INTO automation_job_events(job_id,step,status,message,created_at) VALUES (?,?,?,?,?)',
                           [(job, *event) for event in events])

    def rows(self, path=None):
        with closing(sqlite3.connect(path or self.path)) as db:
            return db.execute('SELECT event_id,job_id,step,status,message,created_at FROM automation_job_events ORDER BY event_id').fetchall()

    def execute(self, paths=None, **kwargs):
        return archive_task_events(paths or [self.path], now=NOW, **kwargs)

    def test_preview_counts_ranges_and_has_no_writes_or_archive_creation(self):
        self.add_job()
        self.add_job('recent', updated_at=RECENT)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = preview_event_archives([self.path], now=NOW)
        self.assertTrue(result['previewOnly'])
        self.assertEqual((result['jobs'], result['events']), (1, 2))
        self.assertEqual(result['pools'][0]['oldestCompletedAt'], OLD)
        self.assertEqual(result['pools'][0]['newestEventAt'], OLD)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(archive_path(self.path).exists())

    def test_only_explicit_old_complete_success_is_archived(self):
        self.add_job()
        for status in ('queued', 'running', 'failed', 'needs_review', 'needs_input',
                       'ready_publish', 'interrupted', 'canceled', 'unknown'):
            self.add_job(status, status=status)
        self.add_job('wrong-step', step='publish')
        self.add_job('recent', updated_at=RECENT)
        self.add_job('boundary', updated_at=(NOW - timedelta(days=90)).isoformat())
        self.add_job('bad-time', updated_at='not-a-date')
        self.add_job('no-zone', updated_at='2026-01-01T00:00:00')
        self.add_job('no-events', events=[])
        self.add_job('uncertain-last', events=[('complete', 'needs_review', '不确定', OLD)])
        self.add_job('running-complete', events=[('complete', 'running', '不确定', OLD)])
        self.add_job('recent-event', events=[('prepare', 'running', '新事件', RECENT), ('complete', 'done', '完成', OLD)])
        self.add_job('bad-event-time', events=[('complete', 'done', '完成', 'bad')])
        self.add_job('naive-event-time', events=[('complete', 'done', '完成', '2026-01-01T00:00:00')])
        initial_jobs = 21
        report = self.execute()
        self.assertEqual(report['errors'], [])
        self.assertEqual((report['jobs'], report['archivedEvents'], report['deletedEvents']), (1, 2, 2))
        self.assertEqual({row[1] for row in self.rows(archive_path(self.path))}, {'success'})
        self.assertNotIn('success', {row[1] for row in self.rows()})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM automation_jobs').fetchone()[0], initial_jobs)

    def test_offset_dates_are_compared_as_instants(self):
        when = (NOW - timedelta(days=91)).astimezone(timezone(timedelta(hours=8))).isoformat()
        self.add_job(updated_at=when, events=[('complete', 'done', '完成', when)])
        result = self.execute()
        self.assertEqual(result['deletedEvents'], 1)
        self.assertEqual(result['pools'][0]['oldestCompletedAt'], (NOW - timedelta(days=91)).isoformat())

    def test_copy_error_does_not_delete_hot_rows(self):
        self.add_job()
        original = self.rows()
        with patch('event_archive._copy_and_verify', side_effect=sqlite3.OperationalError('synthetic disk failure')):
            result = self.execute()
        self.assertEqual(result['deletedEvents'], 0)
        self.assertTrue(result['errors'])
        self.assertEqual(self.rows(), original)

    def fail_delete(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON automation_job_events "
                       "BEGIN SELECT RAISE(ABORT,'synthetic delete failure'); END")

    def test_committed_copy_delete_failure_is_deduplicated_and_idempotently_recovered(self):
        self.add_job()
        original = self.rows()
        self.fail_delete()
        failed = self.execute()
        self.assertTrue(failed['errors'])
        self.assertEqual((failed['archivedEvents'], failed['deletedEvents']), (2, 0))
        self.assertEqual(self.rows(), original)
        self.assertEqual(self.rows(archive_path(self.path)), original)
        self.assertEqual(len(read_job_events(self.path, 'success')), 2)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TRIGGER fail_delete')
        recovered = self.execute()
        self.assertEqual(recovered['errors'], [])
        self.assertEqual((recovered['archivedEvents'], recovered['recoveredEvents'], recovered['deletedEvents']), (0, 2, 2))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows(archive_path(self.path)), original)
        again = self.execute()
        self.assertEqual((again['archivedEvents'], again['deletedEvents']), (0, 0))

    def test_content_or_id_conflict_aborts_before_hot_deletion(self):
        self.add_job()
        self.fail_delete()
        self.execute()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TRIGGER fail_delete')
        with closing(sqlite3.connect(archive_path(self.path))) as db, db:
            db.execute("UPDATE automation_job_events SET message='different-content' WHERE event_id=1")
        result = self.execute()
        self.assertTrue(result['errors'])
        self.assertIn('校验失败', result['errors'][0]['error'])
        self.assertEqual(result['deletedEvents'], 0)
        self.assertEqual(len(self.rows()), 2)

    def test_later_delete_chunk_failure_rolls_back_entire_hot_batch(self):
        events = [('prepare', 'running', f'event {i}', OLD) for i in range(500)]
        events.append(('complete', 'done', 'complete', OLD))
        self.add_job(events=events)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_last_delete BEFORE DELETE ON automation_job_events "
                       "WHEN OLD.event_id=501 BEGIN SELECT RAISE(ABORT,'late deletion failure'); END")
        failed = self.execute()
        self.assertTrue(failed['errors'])
        self.assertEqual(failed['deletedEvents'], 0)
        self.assertEqual(len(self.rows()), 501)
        self.assertEqual(len(self.rows(archive_path(self.path))), 501)
        self.assertEqual(len(read_job_events(self.path, 'success')), 501)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TRIGGER fail_last_delete')
        recovered = self.execute()
        self.assertEqual(recovered['errors'], [])
        self.assertEqual((recovered['archivedEvents'], recovered['recoveredEvents'], recovered['deletedEvents']), (0, 501, 501))

    def test_late_copy_verification_failure_rolls_back_earlier_cold_inserts(self):
        events = [('prepare', 'running', f'event {i}', OLD) for i in range(500)]
        events.append(('complete', 'done', 'complete', OLD))
        self.add_job(events=events)
        # Seed only the conflicting last ID; the first verification chunk must
        # be rolled back when a later chunk discovers the content mismatch.
        with closing(sqlite3.connect(archive_path(self.path))) as db, db:
            db.execute('CREATE TABLE event_archive_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute("INSERT INTO event_archive_metadata VALUES ('source_file',?)", (self.path.name,))
            db.execute('CREATE TABLE automation_job_events (event_id INTEGER PRIMARY KEY,job_id TEXT,'
                       'step TEXT,status TEXT,message TEXT,created_at TEXT)')
            db.execute('INSERT INTO automation_job_events VALUES (501,?,?,?,?,?)',
                       ('success', 'complete', 'done', 'conflict', OLD))
        failed = self.execute()
        self.assertTrue(failed['errors'])
        self.assertEqual(len(self.rows()), 501)
        self.assertEqual(len(self.rows(archive_path(self.path))), 1)

    def test_empty_archive_left_by_failed_creation_does_not_hide_hot_events(self):
        self.add_job()
        with closing(sqlite3.connect(archive_path(self.path))):
            pass
        self.assertEqual(len(read_job_events(self.path, 'success')), 2)
        self.assertEqual(self.execute()['deletedEvents'], 2)

    def test_source_identity_mismatch_is_rejected_for_execution_and_reads(self):
        self.add_job()
        self.fail_delete()
        self.execute()
        with closing(sqlite3.connect(archive_path(self.path))) as db, db:
            db.execute("UPDATE event_archive_metadata SET value='another.sqlite3'")
        self.assertTrue(self.execute()['errors'])
        self.assertEqual(len(self.rows()), 2)
        with self.assertRaisesRegex(ValueError, '不属于'):
            read_job_events(self.path, 'success')

    def test_status_is_rechecked_under_write_lock(self):
        self.add_job()
        original = event_archive._candidate_ids

        def change_after_preview(db, cutoff):
            ids = original(db, cutoff)
            with closing(sqlite3.connect(self.path)) as writer, writer:
                writer.execute("UPDATE automation_jobs SET status='running'")
            return ids

        with patch('event_archive._candidate_ids', change_after_preview):
            result = self.execute()
        self.assertEqual(result['deletedEvents'], 0)
        self.assertFalse(archive_path(self.path).exists())
        self.assertEqual(len(self.rows()), 2)

    def test_two_concurrent_executions_copy_and_delete_each_event_once(self):
        self.add_job()
        barrier = Barrier(2)
        original = event_archive._candidate_ids
        results = []

        def candidates(db, cutoff):
            ids = original(db, cutoff)
            barrier.wait(timeout=3)
            return ids

        with patch('event_archive._candidate_ids', candidates):
            workers = [Thread(target=lambda: results.append(self.execute())) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(not result['errors'] for result in results))
        self.assertEqual(sum(result['deletedEvents'] for result in results), 2)
        self.assertEqual(len(self.rows(archive_path(self.path))), 2)

    def test_pool_failure_does_not_rollback_other_pool_and_ids_stay_local(self):
        other = self.root / 'automation_pool.account-2.sqlite3'
        self.create_pool(other)
        self.add_job()
        self.add_job('other-success', path=other)
        self.fail_delete()
        result = self.execute([self.path, other])
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['deletedEvents'], 2)
        self.assertEqual(read_job_events(self.path, 'success')[0]['event_id'], 1)
        self.assertEqual(read_job_events(other, 'other-success')[0]['event_id'], 1)
        self.assertEqual(read_job_events(self.path, 'other-success'), [])

    def test_hot_and_cold_reads_keep_event_ids_order_and_new_retry_events(self):
        self.add_job()
        self.add_job('unarchived', status='running')
        self.execute()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE automation_jobs SET status='running',step='prepare',updated_at=? WHERE job_id='success'", (RECENT,))
            db.execute("INSERT INTO automation_job_events(job_id,step,status,message,created_at) VALUES ('success','prepare','running','retry',?)", (RECENT,))
            db.row_factory = sqlite3.Row
            result = read_events_for_jobs(self.path, ['success', 'unarchived', 'missing'], connection=db)
        self.assertEqual([row['event_id'] for row in result['success']], [1, 2, 5])
        self.assertEqual([row['event_id'] for row in result['unarchived']], [3, 4])
        self.assertEqual(result['missing'], [])
        self.assertEqual(self.execute()['deletedEvents'], 0)

    def test_read_handles_move_between_hot_and_cold_query(self):
        self.add_job()
        original = event_archive.archive_path
        moved = []

        def move_once(path):
            if not moved:
                moved.append(True)
                self.assertEqual(self.execute()['deletedEvents'], 2)
            return original(path)

        with patch('event_archive.archive_path', move_once):
            result = read_job_events(self.path, 'success')
        self.assertEqual([row['event_id'] for row in result], [1, 2])

    def test_backup_includes_cold_db_created_after_source_enumeration(self):
        self.add_job()
        original = backups.read_only
        moved = []

        def move_before_hot_backup(path):
            if path == self.path and not moved:
                moved.append(True)
                self.assertEqual(self.execute()['deletedEvents'], 2)
            return original(path)

        with patch('scripts.backup_local_databases.read_only', move_before_hot_backup):
            result = backups.create_backup(self.root, version='fixture')
        folder = Path(result['path'])
        self.assertEqual(result['databaseCount'], 2)
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual([row['name'] for row in manifest['databases']],
                         ['automation_pool.sqlite3', 'automation_pool.archive.sqlite3'])
        self.assertEqual(len(read_job_events(folder / self.path.name, 'success')), 2)
        self.assertTrue(backups.verify_backup(folder)['verified'])

    def test_backup_existing_cold_file_always_follows_hot_snapshot(self):
        self.add_job()
        self.execute()
        self.add_job('second')
        original = backups.read_only
        calls = []

        def record(path):
            if path.parent == self.root:
                calls.append(path.name)
                if path == self.path:
                    self.execute()
            return original(path)

        with patch('scripts.backup_local_databases.read_only', record):
            result = backups.create_backup(self.root, version='fixture')
        self.assertEqual(calls, ['automation_pool.sqlite3', 'automation_pool.archive.sqlite3'])
        self.assertEqual(len(read_job_events(Path(result['path']) / self.path.name, 'second')), 2)

    def test_cli_is_preview_by_default_and_ignores_archives(self):
        self.add_job()
        with closing(sqlite3.connect(archive_path(self.path))):
            pass
        with patch.object(archive_cli, 'preview_event_archives', return_value={'previewOnly': True}) as preview, \
                patch.object(archive_cli, 'archive_task_events') as execute, redirect_stdout(io.StringIO()):
            self.assertEqual(archive_cli.main(['--data-root', str(self.root)]), 0)
        preview.assert_called_once_with([self.path], retention_days=90)
        execute.assert_not_called()

    def test_retention_and_path_validation_never_create_missing_source(self):
        for days in (6, 0, True, '90', 36501):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.execute(retention_days=days)
        missing = self.root / 'missing.sqlite3'
        with self.assertRaises(ValueError):
            self.execute([missing])
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            archive_task_events([self.path], now=datetime(2026, 1, 1))

    def test_preview_digest_detects_equal_count_job_replacement_and_content_changes(self):
        self.add_job()
        original = preview_event_archives([self.path], now=NOW)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE automation_jobs SET job_id='replacement'")
            db.execute("UPDATE automation_job_events SET job_id='replacement'")
        replaced = preview_event_archives([self.path], now=NOW)
        self.assertEqual(original['events'], replaced['events'])
        self.assertEqual(original['pools'][0]['oldestEventAt'], replaced['pools'][0]['oldestEventAt'])
        self.assertNotEqual(original['scopeDigest'], replaced['scopeDigest'])
        denied = self.execute(expected_preview=original)
        self.assertTrue(denied['scopeConflict'])
        self.assertEqual(denied['errors'][0]['code'], 'scope_conflict')
        self.assertEqual(denied['deletedEvents'], 0)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE automation_job_events SET message='modified' WHERE event_id=1")
        changed = preview_event_archives([self.path], now=NOW)
        self.assertNotEqual(replaced['scopeDigest'], changed['scopeDigest'])
        self.assertTrue(self.execute(expected_preview=replaced)['scopeConflict'])
        self.assertFalse(archive_path(self.path).exists())

    def test_approved_execution_rejects_changed_freeze_time_or_pool_before_writes(self):
        self.add_job()
        preview = preview_event_archives([self.path], now=NOW)
        with self.assertRaises(event_archive.ArchiveScopeConflict):
            archive_task_events([self.path], now=NOW + timedelta(seconds=1), expected_preview=preview)
        other = self.root / 'automation_pool.account-2.sqlite3'
        self.create_pool(other)
        with self.assertRaises(event_archive.ArchiveScopeConflict):
            self.execute([other], expected_preview=preview)
        self.assertFalse(archive_path(self.path).exists())
        self.assertFalse(archive_path(other).exists())

    def test_approved_execution_rechecks_content_before_first_batch(self):
        self.add_job()
        preview = preview_event_archives([self.path], now=NOW)
        original = event_archive._connect
        writable_calls = []

        def mutate_between_locks(path, *, writable=False):
            if writable:
                writable_calls.append(path)
                if len(writable_calls) == 2:
                    with closing(sqlite3.connect(self.path)) as db, db:
                        db.execute("UPDATE automation_job_events SET message='late-change' WHERE event_id=1")
            return original(path, writable=writable)

        with patch('event_archive._connect', mutate_between_locks):
            result = self.execute(expected_preview=preview)
        self.assertTrue(result['scopeConflict'])
        self.assertEqual(result['deletedEvents'], 0)
        self.assertEqual(len(self.rows()), 2)
        self.assertFalse(archive_path(self.path).exists())

    def test_approved_scope_never_expands_after_scope_check(self):
        self.add_job()
        preview = preview_event_archives([self.path], now=NOW)
        original = event_archive._connect
        writable_calls = []

        def add_after_freeze(path, *, writable=False):
            if writable:
                writable_calls.append(path)
                if len(writable_calls) == 2:
                    self.add_job('new-eligible')
            return original(path, writable=writable)

        with patch('event_archive._connect', add_after_freeze):
            result = self.execute(expected_preview=preview)
        self.assertFalse(result['scopeConflict'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deletedEvents'], 2)
        self.assertEqual({row[1] for row in self.rows()}, {'new-eligible'})
        self.assertEqual({row[1] for row in self.rows(archive_path(self.path))}, {'success'})

    def test_scope_conflict_preserves_report_of_prior_committed_batches(self):
        for job in ('a', 'b', 'c'):
            self.add_job(job)
        preview = preview_event_archives([self.path], now=NOW)
        original = event_archive._connect
        writable_calls = []

        def change_second_batch(path, *, writable=False):
            if writable:
                writable_calls.append(path)
                if len(writable_calls) == 3:
                    with closing(sqlite3.connect(self.path)) as db, db:
                        db.execute("UPDATE automation_job_events SET message='late-change' WHERE job_id='b'")
            return original(path, writable=writable)

        with patch('event_archive._connect', change_second_batch):
            result = self.execute(expected_preview=preview, batch_jobs=1)
        self.assertTrue(result['scopeConflict'])
        self.assertEqual(result['errors'][0]['code'], 'scope_conflict')
        self.assertEqual((result['jobs'], result['archivedEvents'], result['deletedEvents']), (1, 2, 2))
        self.assertEqual({row[1] for row in self.rows()}, {'b', 'c'})
        self.assertEqual({row[1] for row in self.rows(archive_path(self.path))}, {'a'})


class EventArchivePoolIntegrationTests(unittest.TestCase):
    def test_archived_details_and_list_keep_events_and_generated_title(self):
        from automation_pool import AutomationPool
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'automation_pool.sqlite3'
            pool = AutomationPool(path)
            job_id = 'a' * 32
            pool.put_item(12, 'site', 0, {'title': 'original'})
            pool.start_job(job_id, 12, 34, 'US')
            pool.update_job(job_id, 'titles', 'done', '中英文标题已生成：中文「归档后标题」；英文「Archived title」；尚未写入妙手')
            pool.update_job(job_id, 'complete', 'done', 'complete')
            with closing(pool._connect()) as db, db:
                db.execute('UPDATE automation_jobs SET created_at=?,updated_at=?', (OLD, OLD))
                db.execute('UPDATE automation_job_events SET created_at=?', (OLD,))
            before = pool.get_job(job_id)
            report = archive_task_events([path], now=NOW)
            self.assertEqual(report['errors'], [])
            self.assertEqual(report['deletedEvents'], len(before['events']))
            self.assertEqual(pool.get_job(job_id), before)
            self.assertEqual(pool.list_jobs(), [before])
            self.assertEqual(before['title'], '归档后标题')
            # Existing deletion visibility remains enforced by the main record.
            pool.delete_job_records([job_id])
            self.assertIsNone(pool.get_job(job_id))
            self.assertEqual(pool.list_jobs(), [])


if __name__ == '__main__':
    unittest.main()
