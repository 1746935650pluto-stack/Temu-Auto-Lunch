from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from daily_statistics import build_report, shop_report
from order_sync import OrderSyncStore


class ShopStatisticsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name); self.path = self.root / 'order_sync.sqlite3'
        self.accounts = [{'id': 1, 'name': 'one'}, {'id': 2, 'name': 'two'}]
        self.query = {'accountId': ['1'], 'start': ['2026-11-10'], 'end': ['2026-11-10']}

    def order(self, key, **values):
        return {'packageId': key, 'orderNo': f'order-{key}', 'shopId': 'a', 'shopName': 'Alpha',
                'orderTime': '2026-11-10 00:00:00', 'itemDetails': [{'quantity': 2}], **values}

    def report(self, **query):
        return shop_report(self.accounts, self.path, {**self.query, **query})

    def test_account_and_shop_id_isolation_and_split_package_deduplication(self):
        OrderSyncStore(self.path).upsert([self.order(1), self.order(2, orderNo='order-1'),
                                        self.order(3, shopId='b', shopName='Beta', orderNo='order-1')])
        OrderSyncStore(self.root / 'order_sync.account-2.sqlite3').upsert([self.order(1, shopName='Other account')])
        report = self.report(); self.assertEqual(report['totals']['orders'], 2)
        self.assertEqual(report['totals']['packages'], 3); self.assertEqual(report['totals']['quantity'], 6)
        self.assertEqual({row['shopName'] for row in report['shops']}, {'Alpha', 'Beta'})
        other = self.report(accountId=['2'])
        self.assertEqual(other['totals']['orders'], 1); self.assertEqual(other['shops'][0]['shopName'], 'Other account')

    def test_beijing_midnight_and_zero_stores_in_range(self):
        OrderSyncStore(self.path).upsert([self.order(1, orderTime='2026-11-09T15:59:59Z'),
                                        self.order(2, shopId='b', shopName='Beta', orderTime='2026-11-09T16:00:00Z')])
        report = self.report(); self.assertEqual(report['totals']['orders'], 1)
        self.assertEqual({row['shopId']: row['orders'] for row in report['shops']}, {'a': 0, 'b': 1})

    def test_history_exceeds_current_list_limit_and_matches_daily_total(self):
        store = OrderSyncStore(self.path); store.upsert([self.order(index) for index in range(1007)])
        self.assertEqual(len(store.list()), 1000)
        self.assertEqual(self.report()['totals']['orders'], 1007)
        daily = build_report(self.accounts, self.root / 'missing.sqlite3', self.path, self.query)
        self.assertEqual(daily['totals']['orders'], self.report()['totals']['orders'])

    def test_history_shop_names_survive_snapshot_replacement(self):
        store = OrderSyncStore(self.path); store.replace_snapshot([self.order(1)])
        store.replace_snapshot([self.order(2, shopId='b', shopName='Beta')])
        store.replace_snapshot([self.order(2, shopId='b', shopName='')])
        self.assertEqual({row['shopName'] for row in self.report()['shops']}, {'Alpha', 'Beta'})
        self.assertEqual(self.report()['totals']['orders'], 2)

    def test_old_history_migration_backfills_names_and_report_remains_readonly(self):
        store = OrderSyncStore(self.path); store.upsert([self.order(1)])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE order_statistics_history DROP COLUMN shop_name')
        before = self.path.read_bytes()
        self.assertEqual(self.report()['shops'][0]['shopName'], 'Alpha')
        self.assertEqual(self.path.read_bytes(), before)
        OrderSyncStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT shop_name FROM order_statistics_history').fetchone()[0], 'Alpha')

    def test_same_name_distinct_ids_and_missing_ids_do_not_merge_named_shops(self):
        OrderSyncStore(self.path).upsert([self.order(1, shopName='Same'), self.order(2, shopId='b', shopName='Same'),
                                        self.order(3, shopId='', shopName='Named one', orderNo='repeat'),
                                        self.order(4, shopId='', shopName='Named two', orderNo='repeat')])
        report = self.report(); self.assertEqual(len(report['shops']), 4)
        self.assertEqual(len({row['displayName'] for row in report['shops']}), 4)
        self.assertEqual(report['totals']['orders'], 4)
        self.assertEqual(build_report(self.accounts, self.root/'missing.sqlite3', self.path, self.query)['totals']['orders'], 4)
        self.assertTrue(report['warnings'])

    def test_missing_dates_numbers_and_quantities_are_disclosed_without_amount_fields(self):
        OrderSyncStore(self.path).upsert([self.order(1, orderTime='bad'), self.order(2, orderNo='', itemDetails=[]),
                                        self.order(3, shopId='', shopName='')])
        report = self.report(); self.assertEqual(report['totals']['orders'], 2)
        self.assertEqual(report['totals']['quantityMissing'], 1); self.assertEqual(len(report['warnings']), 3)
        self.assertNotIn('amounts', report['totals']); self.assertNotIn('amountMissing', report['totals'])

    def test_empty_account_does_not_create_database_and_invalid_filters_reject(self):
        self.assertEqual(self.report()['shops'], []); self.assertFalse(self.path.exists())
        for query in ({'accountId': ['all']}, {'accountId': ['3']}, {'accountId': ['1','2']},
                      {'start': ['bad']}, {'start': ['2026-01-01']}, {'extra': ['x']}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.report(**query)
