"""Owner observations avoid write locks for unchanged cached source evidence."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import collection_people
from collection_people import PersonnelStore, collector_id


class OwnerObservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = PersonnelStore(Path(directory.name) / 'people.sqlite3')
        # Schema migration is deliberately outside the warm observation trace.
        with closing(self.store._connect()):
            pass

    def owner_rows(self, scope='one'):
        with closing(self.store._connect()) as connection:
            return {row['detail_id']: (row['person_id'], row['updated_at'])
                    for row in connection.execute(
                        'SELECT detail_id, person_id, updated_at FROM collection_product_owners WHERE scope=?',
                        (scope,))}

    def traced_connect(self, statements, variable_limit=None):
        original = self.store._connect

        def connect():
            connection = original()
            connection.set_trace_callback(statements.append)
            if variable_limit is not None:
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, variable_limit)
            return connection

        return patch.object(self.store, '_connect', side_effect=connect)

    def assert_read_only(self, statements):
        self.assertFalse([sql for sql in statements if sql.lstrip().split()[0].upper()
                          in {'BEGIN', 'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}], statements)

    def test_unchanged_batch_has_no_begin_or_dml_and_preserves_timestamps(self):
        rows = [{'collectBoxDetailId': 1, 'subAppAccountId': 11},
                {'collectBoxDetailId': 2, 'subAppAccountId': 22}]
        with patch.object(collection_people.time, 'time', return_value=10):
            self.store.observe('one', rows)
            self.store.update_names('one', [{'subAppAccountId': 11, 'name': 'Alice', 'userName': 'login'}])
        before_people, before_owners = self.store.list('one'), self.owner_rows()
        statements = []
        with self.traced_connect(statements), patch.object(collection_people.time, 'time', return_value=20):
            for _ in range(3):
                self.store.observe('one', rows)
        self.assertTrue(any(sql.startswith('SELECT') for sql in statements))
        self.assert_read_only(statements)
        self.assertEqual(self.store.list('one'), before_people)
        self.assertEqual(self.owner_rows(), before_owners)

    def test_changed_and_new_associations_remain_scoped(self):
        rows = [{'collectBoxDetailId': 1, 'subAppAccountId': 11},
                {'collectBoxDetailId': 2, 'subAppAccountId': 22}]
        with patch.object(collection_people.time, 'time', return_value=10):
            self.store.observe('one', rows)
            self.store.observe('two', rows)
        with patch.object(collection_people.time, 'time', return_value=20):
            self.store.observe('one', [
                {'collectBoxDetailId': '0001', 'subAppAccountId': '00033'},
                {'collectBoxDetailId': 2, 'subAppAccountId': 22},
                {'collectBoxDetailId': 3, 'subAppAccountId': 44},
                {'subAppAccountId': 55},
            ])
        self.assertEqual(self.owner_rows(), {'1': ('33', 20), '2': ('22', 10), '3': ('44', 20)})
        self.assertEqual(self.owner_rows('two'), {'1': ('11', 10), '2': ('22', 10)})
        self.assertEqual({row['id'] for row in self.store.list('one')}, {'11', '22', '33', '44', '55'})
        self.assertEqual({row['id'] for row in self.store.list('two')}, {'11', '22'})

    def test_duplicate_ids_use_last_valid_owner_and_parse_each_field_once(self):
        rows = [{'collectBoxDetailId': '0001', 'subAppAccountId': 11},
                {'collectBoxDetailId': 1, 'subAppAccountId': '00022'},
                {'collectBoxDetailId': 1, 'subAppAccountId': True},
                {'collectBoxDetailId': 'invalid', 'subAppAccountId': 33}]
        with patch.object(collection_people, 'collector_id', wraps=collector_id) as normalize:
            self.store.observe('one', iter(rows))
        self.assertEqual(normalize.call_count, 7)
        self.assertEqual(self.store.owners('one'), {'1': '22'})
        self.assertEqual({row['id'] for row in self.store.list('one')}, {'11', '22', '33'})
        statements = []
        with self.traced_connect(statements):
            self.store.observe('one', iter(rows))
        self.assert_read_only(statements)

    def test_backfilled_owner_with_missing_person_is_repaired_without_owner_write(self):
        self.store.preserve_owners('one', [(1, 11)])
        before = self.owner_rows()
        statements = []
        with self.traced_connect(statements):
            self.store.observe('one', [{'collectBoxDetailId': 1, 'subAppAccountId': 11}])
        self.assertEqual(self.owner_rows(), before)
        self.assertEqual([row['id'] for row in self.store.list('one')], ['11'])
        writes = [sql for sql in statements if sql.startswith('INSERT')]
        self.assertEqual(len(writes), 1)
        self.assertIn('INTO people', writes[0])

    def test_deleted_owner_and_person_are_detected_on_later_requests(self):
        rows = [{'collectBoxDetailId': 1, 'subAppAccountId': 11}]
        self.store.observe('one', rows)
        with closing(self.store._connect()) as connection, connection:
            connection.execute('DELETE FROM collection_product_owners WHERE scope=?', ('one',))
        self.store.observe('one', rows)
        self.assertEqual(self.store.owners('one'), {'1': '11'})
        before = self.owner_rows()
        with closing(self.store._connect()) as connection, connection:
            connection.execute('DELETE FROM people WHERE scope=?', ('one',))
        self.store.observe('one', rows)
        self.assertEqual([row['id'] for row in self.store.list('one')], ['11'])
        self.assertEqual(self.owner_rows(), before)

    def test_external_change_is_not_hidden_by_previous_observation(self):
        rows = [{'collectBoxDetailId': 1, 'subAppAccountId': 11}]
        self.store.observe('one', rows)
        with closing(self.store._connect()) as connection, connection:
            connection.execute('UPDATE collection_product_owners SET person_id=?, updated_at=? WHERE scope=?',
                               ('22', 25, 'one'))
        with patch.object(collection_people.time, 'time', return_value=30):
            self.store.observe('one', rows)
        self.assertEqual(self.owner_rows(), {'1': ('11', 30)})

    def test_unchanged_observation_finishes_while_wal_writer_holds_lock(self):
        rows = [{'collectBoxDetailId': 1, 'subAppAccountId': 11}]
        self.store.observe('one', rows)
        done, failures, statements = threading.Event(), [], []

        def observe():
            try:
                self.store.observe('one', rows)
            except BaseException as error:
                failures.append(error)
            finally:
                done.set()

        with closing(self.store._connect()) as writer:
            self.assertEqual(writer.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE people SET name='uncommitted' WHERE scope='one'")
            with self.traced_connect(statements):
                worker = threading.Thread(target=observe)
                worker.start()
                try:
                    completed_with_writer_locked = done.wait(3)
                finally:
                    writer.rollback()
                    worker.join(5)
        self.assertTrue(completed_with_writer_locked, 'Unchanged observation waited for the WAL writer')
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assert_read_only(statements)

    def test_full_batch_is_rechecked_after_acquiring_write_lock(self):
        self.store.observe('one', [{'collectBoxDetailId': 1, 'subAppAccountId': 11},
                                   {'collectBoxDetailId': 2, 'subAppAccountId': 21}])
        statements = []
        database = self.store.path

        class InterleavedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql == 'BEGIN IMMEDIATE':
                    # The first row is repaired concurrently; a previously
                    # matching row and person change before this call locks.
                    with closing(sqlite3.connect(database)) as other, other:
                        other.execute("INSERT INTO people(scope,id) VALUES ('one','22')")
                        other.execute("UPDATE collection_product_owners SET person_id='22', updated_at=77 "
                                      "WHERE scope='one' AND detail_id='1'")
                        other.execute("UPDATE collection_product_owners SET person_id='23', updated_at=88 "
                                      "WHERE scope='one' AND detail_id='2'")
                        other.execute("DELETE FROM people WHERE scope='one' AND id='21'")
                return super().execute(sql, parameters)

        def connect():
            connection = sqlite3.connect(database, factory=InterleavedConnection)
            connection.row_factory = sqlite3.Row
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.store, '_connect', side_effect=connect), \
             patch.object(collection_people.time, 'time', return_value=99):
            self.store.observe('one', [{'collectBoxDetailId': 1, 'subAppAccountId': 22},
                                       {'collectBoxDetailId': 2, 'subAppAccountId': 21}])
        self.assertEqual(self.owner_rows(), {'1': ('22', 77), '2': ('21', 99)})
        self.assertEqual({row['id'] for row in self.store.list('one')}, {'11', '21', '22'})
        owner_writes = [sql for sql in statements if sql.startswith('INSERT INTO collection_product_owners')]
        self.assertEqual(len(owner_writes), 1, statements)
        self.assertEqual(statements.count('BEGIN IMMEDIATE'), 1)

    def test_write_failure_rolls_back_people_and_owners_together(self):
        self.store.observe('one', [{'collectBoxDetailId': 1, 'subAppAccountId': 11}])
        before = self.owner_rows()
        with closing(self.store._connect()) as connection:
            connection.execute("CREATE TRIGGER fail_owner BEFORE INSERT ON collection_product_owners "
                               "WHEN NEW.detail_id='2' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.observe('one', [{'collectBoxDetailId': 1, 'subAppAccountId': 22},
                                       {'collectBoxDetailId': 2, 'subAppAccountId': 33}])
        self.assertEqual(self.owner_rows(), before)
        self.assertEqual([row['id'] for row in self.store.list('one')], ['11'])

    def test_large_batches_stay_below_sqlite_parameter_limit(self):
        rows = [{'collectBoxDetailId': value, 'subAppAccountId': value + 10000}
                for value in range(1, 1204)]
        statements = []
        with self.traced_connect(statements, variable_limit=501):
            self.store.observe('one', rows)
            statements.clear()
            self.store.observe('one', rows)
        self.assert_read_only(statements)
        self.assertEqual(len([sql for sql in statements if sql.startswith('SELECT')]), 6)
        self.assertEqual(len(self.owner_rows()), 1203)
        self.assertEqual(len(self.store.list('one')), 1203)

    def test_empty_and_invalid_identity_batches_do_not_write(self):
        statements = []
        with self.traced_connect(statements):
            self.store.observe('one', [])
            self.store.observe('one', [{'collectBoxDetailId': 1, 'appAccountId': 11},
                                       {'collectBoxDetailId': 2, 'subAppAccountId': False},
                                       {'collectBoxDetailId': 3, 'subAppAccountId': 'invalid'}])
        self.assert_read_only(statements)
        self.assertEqual(self.store.list('one'), [])
        self.assertEqual(self.owner_rows(), {})


if __name__ == '__main__':
    unittest.main()
