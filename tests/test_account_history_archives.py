"""Account removal covers hot/cold history using disposable databases only."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from account_history import clear_history
from account_slots import AccountSlotStore
from automation_pool import AutomationPool
from event_archive import archive_path, archive_task_events


class AccountHistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.slots = AccountSlotStore(self.root / 'slots.sqlite3')
        self.slots.history_cleared_ids()
        self.pools = self.make_pools(3)
        self.other_pools = self.make_pools(2)
        self.orders = self.root / 'order_sync.account-3.sqlite3'
        self.other_orders = self.root / 'order_sync.account-2.sqlite3'
        for path in (self.orders, self.other_orders):
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE synced_orders (order_id INTEGER PRIMARY KEY)')
                db.execute('INSERT INTO synced_orders VALUES (7)')
        self.shared = self.root / 'shared_automation.sqlite3'
        with closing(sqlite3.connect(self.shared)) as db, db:
            db.executescript('''
                CREATE TABLE shared_batches (batch_id TEXT PRIMARY KEY);
                CREATE TABLE shared_transfers (transfer_id TEXT PRIMARY KEY,batch_id TEXT,payload TEXT);
                CREATE TABLE shared_job_origins (account_id INTEGER,job_id TEXT);
                CREATE TABLE shared_copies (target_account INTEGER,detail_id INTEGER);
                INSERT INTO shared_batches VALUES ('mixed');
                INSERT INTO shared_transfers VALUES ('removed','mixed','{"sourceAccountId":3,"targetAccountId":1}');
                INSERT INTO shared_transfers VALUES ('retained','mixed','{"sourceAccountId":2,"targetAccountId":1}');
                INSERT INTO shared_job_origins VALUES (3,'removed'),(2,'retained');
                INSERT INTO shared_copies VALUES (3,123),(2,123);
            ''')
        self.backups = self.root / 'backups'
        self.retire = Mock()

    def make_pools(self, account):
        pools = []
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=100)).isoformat()
        for number in (1, 2, 3):
            suffix = '' if number == 1 else f'.pool-{number}'
            pool = AutomationPool(self.root / f'automation_pool.account-{account}{suffix}.sqlite3')
            for kind, status in (('old', 'done'), ('recent', 'needs_review')):
                job_id = f'{account}-{number}-{kind}'
                pool.put_item(123, 'site', 0, {'title': f'fixture {account}/{number}'})
                pool.start_job(job_id, 123, 7, 'US')
                pool.update_job(job_id, 'complete' if status == 'done' else 'save', status, kind)
            with closing(pool._connect()) as db, db:
                db.execute('UPDATE automation_jobs SET created_at=?,updated_at=? WHERE job_id=?',
                           (old, old, f'{account}-{number}-old'))
                db.execute('UPDATE automation_job_events SET created_at=? WHERE job_id=?',
                           (old, f'{account}-{number}-old'))
            report = archive_task_events([pool.path], now=now)
            self.assertEqual(report['errors'], [])
            self.assertGreater(report['deletedEvents'], 0)
            pools.append(pool)
        return pools

    @staticmethod
    def dump(path):
        with closing(sqlite3.connect(path)) as db:
            return '\n'.join(db.iterdump())

    @staticmethod
    def rows(path, query):
        with closing(sqlite3.connect(path)) as db:
            return db.execute(query).fetchall()

    def target_paths(self):
        return [self.slots.path, self.orders, self.shared,
                *[path for pool in self.pools for path in (pool.path, archive_path(pool.path))]]

    def clear(self):
        clear_history(3, self.slots, self.pools[0].path, self.orders, self.shared,
                      self.backups, self.retire, extra_pool_paths=[pool.path for pool in self.pools[1:]])

    def test_three_cold_pools_are_backed_up_cleared_and_recoverable_with_idempotent_marker(self):
        before = {(pool.path.name, kind): pool.get_job(f'3-{number}-{kind}')
                  for number, pool in enumerate(self.pools, 1) for kind in ('old', 'recent')}
        others = [self.other_orders, *[path for pool in self.other_pools
                                      for path in (pool.path, archive_path(pool.path))]]
        other_before = {path: self.dump(path) for path in others}
        self.clear()
        backups = list(self.backups.iterdir())
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        # main plus 3 hot + 3 cold + orders + shared: only eight ATTACHes.
        self.assertEqual({path.name for path in backup.iterdir()}, {path.name for path in self.target_paths()})
        for number, pool in enumerate(self.pools, 1):
            self.assertEqual(pool.list_jobs(), [])
            self.assertEqual(pool.list_items(), [])
            cold = archive_path(pool.path)
            self.assertTrue(cold.is_file())
            self.assertEqual(self.rows(cold, 'SELECT * FROM automation_job_events'), [])
            self.assertEqual(self.rows(cold, "SELECT value FROM event_archive_metadata WHERE key='source_file'"),
                             [(pool.path.name,)])
            restored = AutomationPool(backup / pool.path.name)
            for kind in ('old', 'recent'):
                self.assertEqual(restored.get_job(f'3-{number}-{kind}'), before[pool.path.name, kind])
        self.assertEqual(self.rows(self.orders, 'SELECT * FROM synced_orders'), [])
        self.assertEqual(self.rows(self.shared, 'SELECT transfer_id FROM shared_transfers'), [('retained',)])
        self.assertEqual(self.rows(self.shared, 'SELECT * FROM shared_batches'), [('mixed',)])
        self.assertEqual(self.rows(self.shared, 'SELECT * FROM shared_job_origins'), [(2, 'retained')])
        self.assertEqual(self.rows(self.shared, 'SELECT * FROM shared_copies'), [(2, 123)])
        self.assertEqual({path: self.dump(path) for path in others}, other_before)
        self.assertEqual(self.slots.history_cleared_ids(), {3})
        marker = self.rows(self.slots.path, 'SELECT account_id,backup_path FROM account_history_cleanup')
        self.assertEqual(marker, [(3, str(backup))])
        self.clear()
        self.assertEqual(list(self.backups.iterdir()), backups)
        self.assertEqual(self.rows(self.slots.path, 'SELECT account_id,backup_path FROM account_history_cleanup'), marker)
        self.assertEqual(self.retire.call_count, 2)

    def test_wrong_source_archive_prevents_credentials_and_any_history_changes(self):
        cold = archive_path(self.pools[1].path)
        with closing(sqlite3.connect(cold)) as db, db:
            db.execute("UPDATE event_archive_metadata SET value='automation_pool.account-2.pool-2.sqlite3'")
        before = {path: self.dump(path) for path in self.target_paths()}
        with self.assertRaisesRegex(ValueError, '不属于'):
            self.clear()
        self.assertEqual({path: self.dump(path) for path in self.target_paths()}, before)
        self.retire.assert_not_called()
        self.assertEqual(self.slots.history_cleared_ids(), set())

    def test_missing_source_metadata_is_rejected_before_credentials_are_retired(self):
        cold = archive_path(self.pools[0].path)
        with closing(sqlite3.connect(cold)) as db, db:
            db.execute('DROP TABLE event_archive_metadata')
        before = {path: self.dump(path) for path in self.target_paths()}
        with self.assertRaisesRegex(ValueError, '缺少'):
            self.clear()
        self.assertEqual({path: self.dump(path) for path in self.target_paths()}, before)
        self.retire.assert_not_called()

    def test_late_failure_rolls_back_hot_cold_orders_shared_and_marker_then_can_retry(self):
        with closing(sqlite3.connect(self.shared)) as db, db:
            db.execute("CREATE TRIGGER fixture_failure BEFORE DELETE ON shared_transfers "
                       "BEGIN SELECT RAISE(ABORT, 'fixture cleanup failure'); END")
        before = {path: self.dump(path) for path in self.target_paths()}
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture cleanup failure'):
            self.clear()
        self.assertEqual({path: self.dump(path) for path in self.target_paths()}, before)
        self.assertEqual(self.slots.history_cleared_ids(), set())
        self.retire.assert_called_once_with()
        backup = next(self.backups.iterdir())
        for pool in self.pools:
            self.assertEqual(self.dump(backup / archive_path(pool.path).name), before[archive_path(pool.path)])
        with closing(sqlite3.connect(self.shared)) as db, db:
            db.execute('DROP TRIGGER fixture_failure')
        self.clear()
        self.assertEqual(self.slots.history_cleared_ids(), {3})
        for pool in self.pools:
            self.assertEqual(self.rows(archive_path(pool.path), 'SELECT * FROM automation_job_events'), [])

    def test_metadata_is_revalidated_inside_cleanup_transaction(self):
        cold = archive_path(self.pools[2].path)
        before = {pool.path: self.dump(pool.path) for pool in self.pools}
        cold_before = self.rows(cold, 'SELECT * FROM automation_job_events')
        def change_source():
            with closing(sqlite3.connect(cold)) as db, db:
                db.execute("UPDATE event_archive_metadata SET value='different.sqlite3'")
        self.retire.side_effect = change_source
        with self.assertRaisesRegex(ValueError, '不属于'):
            self.clear()
        self.assertEqual({pool.path: self.dump(pool.path) for pool in self.pools}, before)
        self.assertEqual(self.rows(cold, 'SELECT * FROM automation_job_events'), cold_before)
        self.assertEqual(self.slots.history_cleared_ids(), set())
        backup = next(self.backups.iterdir())
        self.assertEqual(self.rows(backup / cold.name, "SELECT value FROM event_archive_metadata WHERE key='source_file'"),
                         [(self.pools[2].path.name,)])

    def test_existing_cold_is_cleared_even_when_its_hot_file_is_absent(self):
        pool = self.pools[2]
        pool.path.rename(pool.path.with_suffix('.fixture-kept'))
        cold = archive_path(pool.path)
        before = self.dump(cold)
        self.clear()
        self.assertFalse(pool.path.exists())
        self.assertTrue(cold.is_file())
        self.assertEqual(self.rows(cold, 'SELECT * FROM automation_job_events'), [])
        backup = next(self.backups.iterdir())
        self.assertEqual(self.dump(backup / cold.name), before)
        self.assertEqual(self.slots.history_cleared_ids(), {3})

    def test_absent_archives_are_not_created_by_cleanup(self):
        for pool in self.pools:
            cold = archive_path(pool.path)
            cold.rename(cold.with_suffix('.fixture-kept'))
        self.clear()
        for pool in self.pools:
            self.assertFalse(archive_path(pool.path).exists())
        self.assertEqual(self.slots.history_cleared_ids(), {3})

    def test_empty_archive_from_failed_creation_is_backed_up_and_left_empty(self):
        cold = archive_path(self.pools[0].path)
        cold.rename(cold.with_suffix('.fixture-kept'))
        with closing(sqlite3.connect(cold)):
            pass
        self.clear()
        self.assertTrue(cold.is_file())
        self.assertEqual(self.rows(cold, 'SELECT name FROM sqlite_master'), [])
        backup = next(self.backups.iterdir())
        self.assertTrue((backup / cold.name).is_file())
        self.assertEqual(self.rows(backup / cold.name, 'SELECT name FROM sqlite_master'), [])
        self.assertEqual(self.slots.history_cleared_ids(), {3})

    def test_wrong_archive_schema_is_rejected_before_retiring_credentials(self):
        cold = archive_path(self.pools[0].path)
        with closing(sqlite3.connect(cold)) as db, db:
            db.execute('ALTER TABLE automation_job_events RENAME TO unexpected_history')
        before = {path: self.dump(path) for path in self.target_paths()}
        with self.assertRaisesRegex(ValueError, '缺少'):
            self.clear()
        self.assertEqual({path: self.dump(path) for path in self.target_paths()}, before)
        self.retire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
