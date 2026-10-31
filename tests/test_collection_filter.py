"""Snapshot filtering and upgrades use disposable SQLite files, never server."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from collection_cache import CollectionReadCache


class CollectionFilterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'cache.sqlite3'
        self.cache = CollectionReadCache(self.path)

    def legacy_snapshot(self, *, corrupt=False):
        path = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('''CREATE TABLE collection_full_snapshots (
                scope TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                completed_at REAL NOT NULL, total_items INTEGER NOT NULL,
                last_failed_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE collection_snapshot_members (
                scope TEXT NOT NULL, generation INTEGER NOT NULL, position INTEGER NOT NULL,
                detail_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(scope,generation,detail_id))''')
            db.execute("INSERT INTO collection_full_snapshots VALUES ('old',7,123,502,456,'retained failure')")
            rows = [{'collectBoxDetailId': str(9007199254740993 + i),
                     'subAppAccountId': ' 9007199254740995 ', 'title': f'冬季 Straße {i}'} for i in range(502)]
            db.executemany('INSERT INTO collection_snapshot_members VALUES (?,?,?,?,?)',
                           [('old', 7, i, row['collectBoxDetailId'],
                             '{broken' if corrupt and i == 501 else json.dumps(row)) for i, row in enumerate(rows)])
        return path, rows

    def test_legacy_backfill_is_complete_and_reopening_does_not_reparse_rows(self):
        path, rows = self.legacy_snapshot()
        cache = CollectionReadCache(path)
        result = cache.filtered_listing('old', '9007199254740995', 11, 50, title_keyword='STRASSE')
        self.assertEqual(result['rows'], rows[500:])
        self.assertEqual(result['total'], 502)
        self.assertEqual(result['snapshot'], {'generation': 7, 'completedAt': 123.0,
                         'totalItems': 502, 'failedAt': 456.0, 'error': 'retained failure'})
        with patch('collection_cache.json.loads', side_effect=AssertionError('backfilled twice')):
            reopened = CollectionReadCache(path)
        self.assertEqual(reopened.filtered_listing('old', detail_id=rows[-1]['collectBoxDetailId'])['rows'], rows[-1:])

    def test_failed_backfill_rolls_back_columns_and_rows_then_can_retry(self):
        path, rows = self.legacy_snapshot(corrupt=True)
        with self.assertRaises(ValueError):
            CollectionReadCache(path)
        with closing(sqlite3.connect(path)) as db, db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(collection_snapshot_members)')}
            self.assertNotIn('person_id', columns)
            self.assertNotIn('title_fold', columns)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM collection_snapshot_members').fetchone()[0], 502)
            self.assertEqual(db.execute('SELECT generation FROM collection_full_snapshots').fetchone()[0], 7)
            db.execute('UPDATE collection_snapshot_members SET payload=? WHERE position=501', (json.dumps(rows[-1]),))
        self.assertEqual(CollectionReadCache(path).filtered_listing('old', title_keyword='straße')['total'], 502)

    def test_partial_schema_is_backfilled_atomically(self):
        path, _ = self.legacy_snapshot()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("ALTER TABLE collection_snapshot_members ADD COLUMN person_id TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE collection_snapshot_members SET person_id='stale'")
        cache = CollectionReadCache(path)
        self.assertEqual(cache.filtered_listing('old', '9007199254740995', title_keyword='straße')['total'], 502)
        self.assertEqual(cache.filtered_listing('old', 'stale')['total'], 0)

    def test_unicode_literal_substring_and_combined_filters_preserve_semantics(self):
        rows = [
            {'collectBoxDetailId': 1, 'subAppAccountId': ' 11 ', 'title': '冬季手套 Straße Σίσυφος'},
            {'collectBoxDetailId': 2, 'subAppAccountId': 22, 'title': '紫色手套 STRASSE σίσυφοσ'},
            {'collectBoxDetailId': 3, 'subAppAccountId': 11, 'title': '100%_C++ [冬季]'},
            {'collectBoxDetailId': 4, 'title': None},
            {'collectBoxDetailId': '92233720368547758080', 'subAppAccountId': '9007199254740995', 'title': 'İSTANBUL'},
        ]
        self.cache.store_listing('a', rows)
        for keyword, expected in [(' STRASSE ', [1, 2]), ('手套', [1, 2]), ('ΣΊΣΥΦΟΣ', [1, 2]),
                                  ('%_', [3]), ('_', [3]), ('[冬季]', [3]), ('.*', []),
                                  ('i\u0307stanbul', ['92233720368547758080']), ('missing', [])]:
            with self.subTest(keyword=keyword):
                result = self.cache.filtered_listing('a', title_keyword=keyword)
                self.assertEqual([row['collectBoxDetailId'] for row in result['rows']], expected)
                self.assertEqual(result['total'], len(expected))
        self.assertEqual(self.cache.filtered_listing('a', '11', title_keyword='手套')['rows'], rows[:1])
        self.assertEqual(self.cache.filtered_listing('a', '22', detail_id='1')['total'], 0)
        self.assertEqual(self.cache.filtered_listing('a', '9007199254740995', detail_id=rows[-1]['collectBoxDetailId'])['rows'], rows[-1:])

    def test_only_current_page_json_is_parsed(self):
        rows = [{'collectBoxDetailId': i, 'subAppAccountId': str(i % 2), 'title': '手套'} for i in range(1, 146)]
        self.cache.store_listing('a', rows)
        with patch('collection_cache.json.loads', wraps=json.loads) as decode:
            result = self.cache.filtered_listing('a', '1', 3, 7, title_keyword='手套')
        self.assertEqual(result['total'], 73)
        self.assertEqual([row['collectBoxDetailId'] for row in result['rows']], list(range(29, 42, 2)))
        self.assertEqual(decode.call_count, 7)

    def test_huge_pages_and_sizes_never_overflow_sqlite(self):
        rows = [{'collectBoxDetailId': i, 'subAppAccountId': '11'} for i in range(1, 4)]
        self.cache.store_listing('a', rows)
        for page, size, expected in [(10 ** 100, 50, []), (2, 10 ** 100, []),
                                     (1, 10 ** 100, rows), (0, 2, rows[:2]), (-5, 2, rows[:2])]:
            with self.subTest(page=page, size=size):
                result = self.cache.filtered_listing('a', '11', page, size)
                self.assertEqual(result['rows'], expected)
                self.assertEqual(result['total'], 3)

    def test_missing_and_empty_snapshots_remain_distinct(self):
        self.assertIsNone(self.cache.filtered_listing('a'))
        self.cache.store_listing('a', [])
        with patch('collection_cache.json.loads', side_effect=AssertionError('empty page parsed')):
            result = self.cache.filtered_listing('a', '11', 10 ** 100)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['snapshot']['totalItems'], 0)

    def test_only_current_generation_and_scope_members_are_returned(self):
        self.cache.store_listing('a', [{'collectBoxDetailId': 1, 'subAppAccountId': '11'}])
        self.cache.store_listing('a', [{'collectBoxDetailId': 2, 'subAppAccountId': '11'}])
        self.cache.store_listing('other', [{'collectBoxDetailId': 3, 'subAppAccountId': '11'}])
        self.cache.get('a', 'products:1', lambda: {'data': {'detailList': [{'collectBoxDetailId': 4}]}}, force=True)
        with closing(self.cache._connect()) as db, db:
            db.execute('INSERT INTO collection_snapshot_members '
                       '(scope,generation,position,detail_id,payload,person_id,title_fold) VALUES (?,?,?,?,?,?,?)',
                       ('a', 1, 0, '999', json.dumps({'collectBoxDetailId': 999}), '11', ''))
        result = self.cache.filtered_listing('a', '11')
        self.assertEqual([row['collectBoxDetailId'] for row in result['rows']], [2])
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['snapshot']['generation'], 2)

    def test_failed_snapshot_replacement_retains_previous_complete_generation(self):
        original = [{'collectBoxDetailId': 1, 'subAppAccountId': '11', 'title': 'old'}]
        self.cache.store_listing('a', original)
        with closing(self.cache._connect()) as db, db:
            db.execute("CREATE TRIGGER reject_member BEFORE INSERT ON collection_snapshot_members "
                       "WHEN NEW.detail_id='3' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.cache.store_listing('a', [{'collectBoxDetailId': 2}, {'collectBoxDetailId': 3}])
        self.cache.mark_listing_refresh_failed('a', 'fixture failure')
        result = self.cache.filtered_listing('a', '11')
        self.assertEqual(result['rows'], original)
        self.assertEqual(result['snapshot']['generation'], 1)
        self.assertEqual(result['snapshot']['error'], 'fixture failure')
        self.assertEqual(self.cache._read('a', 'products:1')[0]['data']['detailList'], original)

    def test_snapshot_switch_between_queries_cannot_mix_generation_count_and_page(self):
        with closing(self.cache._connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
        for pause_before in ('SELECT COUNT(*)', 'SELECT payload'):
            with self.subTest(pause_before=pause_before):
                original = [{'collectBoxDetailId': i, 'subAppAccountId': '11'} for i in (1, 2, 3)]
                self.cache.store_listing('a', original)
                previous = self.cache.snapshot_info('a')
                ready, finished = Event(), Event()
                errors = []

                class PausedReader(sqlite3.Connection):
                    def execute(connection, sql, parameters=()):
                        if sql.startswith(pause_before):
                            ready.set()
                            if not finished.wait(3):
                                raise AssertionError('snapshot writer did not finish')
                        return super().execute(sql, parameters)

                def write_snapshot():
                    try:
                        if not ready.wait(3):
                            raise AssertionError('snapshot reader did not reach pause')
                        self.cache.store_listing('a', [{'collectBoxDetailId': 99, 'subAppAccountId': '11'}])
                    except Exception as error:
                        errors.append(error)
                    finally:
                        finished.set()

                writer = Thread(target=write_snapshot)
                writer.start()
                try:
                    def reader():
                        return sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True,
                                               timeout=3, factory=PausedReader)
                    with patch.object(self.cache, '_connect_reader', reader):
                        result = self.cache.filtered_listing('a', '11', 2, 2)
                finally:
                    ready.set()
                    writer.join(4)
                self.assertFalse(writer.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(result['snapshot'], previous)
                self.assertEqual(result['total'], 3)
                self.assertEqual(result['rows'], original[2:])
                current = self.cache.filtered_listing('a', '11')
                self.assertEqual([row['collectBoxDetailId'] for row in current['rows']], [99])

    def test_person_and_detail_queries_use_indexes(self):
        self.cache.store_listing('a', [{'collectBoxDetailId': i, 'subAppAccountId': str(i % 10),
                                       'title': '手套'} for i in range(100)])
        original = self.cache._connect_reader
        for filters, index in [({'person_id': '1'}, 'collection_snapshot_member_person'),
                               ({'person_id': '1', 'title_keyword': '手套'}, 'collection_snapshot_member_person'),
                               ({'title_keyword': '手套'}, 'collection_snapshot_member_title'),
                               ({'detail_id': '11'}, 'sqlite_autoindex_collection_snapshot_members_1')]:
            with self.subTest(filters=filters):
                queries = []

                def reader():
                    db = original()
                    db.set_trace_callback(queries.append)
                    return db

                with patch.object(self.cache, '_connect_reader', reader):
                    self.cache.filtered_listing('a', **filters)
                selects = [query for query in queries if query.startswith(('SELECT COUNT(*)', 'SELECT payload'))]
                self.assertEqual(len(selects), 2)
                with closing(self.cache._connect_reader()) as db:
                    for sql in selects:
                        plan = ' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql))
                        self.assertIn(index, plan)
                        self.assertNotIn('USE TEMP B-TREE', plan)
                        if sql.startswith('SELECT COUNT(*)'):
                            self.assertIn('COVERING INDEX', plan)

    def test_filter_reader_is_read_only(self):
        with closing(self.cache._connect_reader()) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM collection_snapshot_members')


if __name__ == '__main__':
    unittest.main()
