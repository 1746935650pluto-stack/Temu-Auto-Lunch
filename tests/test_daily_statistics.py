from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from daily_statistics import build_report, local_day, parameters
from order_sync import OrderSyncStore, extract_package_rows


class DailyStatisticsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.accounts = [{'id': 1, 'name': 'one'}, {'id': 2, 'name': 'two'}]
        self.query = {'start': ['2026-11-08'], 'end': ['2026-11-09']}
        self.primary = self.root / 'automation_pool.sqlite3'
        self.orders = self.root / 'order_sync.sqlite3'

    def pool(self, name='automation_pool.sqlite3'):
        path = self.root / name
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE automation_items(detail_id,created_at);'
                             'CREATE TABLE automation_jobs(job_id,created_at);'
                             'CREATE TABLE automation_job_events(event_id,job_id,step,status,created_at);')
        return path

    def report(self, query=None):
        return build_report(self.accounts, self.primary, self.orders, query or self.query)

    def order(self, key, **kwargs):
        return {'packageId': key, 'orderNo': f'order-{key}', 'shopId': 'shop',
                'orderTime': '2026-11-09 00:00:00', 'amount': '10.10', 'currency': 'USD', **kwargs}

    def test_zero_rows_include_idle_accounts_and_dates_without_creating_databases(self):
        result = self.report()
        self.assertEqual(len(result['rows']), 4)
        self.assertEqual(result['totals']['orders'], 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_midnight_retry_shard_and_archive_overlap_deduplicate(self):
        path = self.pool()
        with closing(sqlite3.connect(path)) as db, db:
            db.executemany('INSERT INTO automation_items VALUES (?,?)', [(1, '2026-11-08T16:00:00+00:00'), (2, '2026-11-09T16:00:00+00:00')])
            db.execute('INSERT INTO automation_jobs VALUES (?,?)', ('job', '2026-11-08T15:59:59+00:00'))
            db.executemany('INSERT INTO automation_job_events VALUES (?,?,?,?,?)', [
                (1, 'job', 'save', 'done', '2026-11-08T15:59:59+00:00'),
                (2, 'job', 'publish', 'done', '2026-11-08T16:00:00+00:00'),
                (3, 'job', 'publish', 'done', '2026-11-08T17:00:00+00:00'),
                (4, 'job', 'save', 'needs_review', '2026-11-08T17:00:00+00:00'),
                (5, 'job', 'publish', 'needs_review', '2026-11-08T17:00:00+00:00')])
        cold = self.root / 'automation_pool.archive.sqlite3'
        with closing(sqlite3.connect(cold)) as db, db:
            db.executescript('CREATE TABLE event_archive_metadata(key,value); CREATE TABLE automation_job_events(event_id,job_id,step,status,created_at);')
            db.execute('INSERT INTO event_archive_metadata VALUES (?,?)', ('source_file', path.name))
            db.execute('INSERT INTO automation_job_events VALUES (?,?,?,?,?)', (2, 'job', 'publish', 'done', '2026-11-08T16:00:00+00:00'))
        shard = self.pool('automation_pool.pool-2.sqlite3')
        with closing(sqlite3.connect(shard)) as db, db:
            db.execute('INSERT INTO automation_job_events VALUES (?,?,?,?,?)', (2, 'job', 'publish', 'done', '2026-11-08T16:00:00+00:00'))
        result = self.report()
        today = next(r for r in result['rows'] if r['date'] == '2026-11-09' and r['accountId'] == 1)
        self.assertEqual([today[k] for k in ('collected', 'saved', 'submitted', 'editFailed', 'publishFailed')], [1, 0, 1, 1, 1])
        self.assertEqual(result['totals']['saved'], 1)
        self.assertEqual(result['totals']['tasks'], 1)

    def test_same_job_and_order_ids_in_different_accounts_are_independent(self):
        for account, filename in ((1, 'automation_pool.sqlite3'), (2, 'automation_pool.account-2.sqlite3')):
            path = self.pool(filename)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('INSERT INTO automation_job_events VALUES (?,?,?,?,?)', (1, 'same', 'save', 'done', '2026-11-09T00:00:00+00:00'))
            store = OrderSyncStore(self.orders if account == 1 else self.root / 'order_sync.account-2.sqlite3')
            store.upsert([self.order(1)])
        self.assertEqual(self.report()['totals']['saved'], 2)
        self.assertEqual(self.report()['totals']['orders'], 2)
        self.assertEqual(self.report({**self.query, 'accountId': ['2']})['totals']['orders'], 1)

    def test_order_snapshot_replacement_preserves_history_and_repeat_sync_is_idempotent(self):
        store = OrderSyncStore(self.orders)
        store.replace_snapshot([self.order(1, orderTime='2026-11-08 23:59:59')])
        for _ in range(2):
            store.replace_snapshot([self.order(2)])
        self.assertEqual(len(store.list()), 1)
        self.assertEqual(self.report()['totals']['orders'], 2)
        self.assertEqual(self.report()['totals']['amounts'], {'USD': '20.20'})

    def test_split_packages_and_currencies_quantity_and_invalid_amounts(self):
        store = OrderSyncStore(self.orders)
        store.upsert([self.order(1, itemDetails=[{'skuId': 'A', 'quantity': 3}]),
                      self.order(2, orderNo='order-1', itemDetails=[{'quantity': 2}]),
                      self.order(3, currency='EUR', amount='0.20'),
                      self.order(4, amount='NaN'), self.order(5, amount='1', currency='')])
        total = self.report()['totals']
        self.assertEqual((total['orders'], total['packages'], total['quantity']), (4, 5, 5))
        self.assertEqual(total['amounts'], {'EUR': '0.20', 'USD': '10.10'})
        self.assertEqual((total['quantityMissing'], total['amountMissing']), (3, 2))

    def test_unknown_order_date_excluded_and_order_number_fallback_disclosed(self):
        OrderSyncStore(self.orders).upsert([self.order(1, orderTime='bad'), self.order(2, orderNo='')])
        result = self.report()
        self.assertEqual(result['totals']['orders'], 1)
        self.assertEqual(len(result['warnings']), 2)

    def test_old_order_store_is_seeded_on_upgrade(self):
        store = OrderSyncStore(self.orders)
        store.upsert([self.order(1)])
        with closing(sqlite3.connect(self.orders)) as db, db:
            db.execute('DROP TABLE order_statistics_history')
        OrderSyncStore(self.orders).replace_snapshot([self.order(2)])
        self.assertEqual(self.report()['totals']['orders'], 2)

    def test_vendor_quantity_survives_normalization(self):
        rows = extract_package_rows({'data': {'orderPackageList': [{'opOrderPackageId': 1,
            'items': [{'platformSkuId': 's', 'quantity': 7}]}]}})
        store = OrderSyncStore(self.orders)
        store.upsert(rows)
        self.assertEqual(store.list()[0]['itemDetails'][0]['quantity'], 7)

    def test_later_incomplete_snapshot_keeps_known_order_date_and_amount(self):
        store = OrderSyncStore(self.orders)
        store.replace_snapshot([self.order(1)])
        store.replace_snapshot([self.order(1, orderTime='', amount='', currency='')])
        result = self.report()['totals']
        self.assertEqual(result['orders'], 1)
        self.assertEqual(result['amounts'], {'USD': '10.10'})

    def test_order_date_formats_are_beijing_independent_of_host_timezone(self):
        for value in ('2026-11-09 00:00:00', '2026-11-08T16:00:00Z', '2026-11-09T00:00:00+08:00', 1794153600, 1794153600000):
            self.assertEqual(local_day(value), '2026-11-09')
        self.assertIsNone(local_day('bad'))

    def test_invalid_ranges_unknown_accounts_and_duplicate_parameters_rejected(self):
        for query in ({'start': ['bad']}, {'start': ['2026-01-01'], 'end': ['2026-11-09']},
                      {'accountId': ['3']}, {'accountId': ['1','2']}, {'unused': ['x']},
                      {'start': ['2026-11-10'], 'end': ['2026-11-09']}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parameters(query, self.accounts)

    def test_wrong_archive_is_not_silently_included(self):
        self.pool()
        with closing(sqlite3.connect(self.root / 'automation_pool.archive.sqlite3')) as db, db:
            db.execute('CREATE TABLE other(x)')
        with self.assertRaises(ValueError):
            self.report()
