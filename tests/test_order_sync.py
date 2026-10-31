import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path

from order_sync import OrderSyncStore, extract_package_rows


class OrderSyncStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = OrderSyncStore(Path(self.directory.name) / 'orders.sqlite3')

    def test_extracts_and_saves_minimal_package_data(self):
        rows = extract_package_rows({'data': {'packageList': [{
            'packageId': 7, 'packageNo': 'MS-7', 'orderNo': 'TEMU-1',
            'shopId': 9, 'packageStatus': '待处理', 'trackingNo': 'TRACK-1',
            'orderAmount': '12.50', 'currency': 'USD', 'productName': 'Example',
        }]}})
        self.assertEqual(self.store.upsert(rows), 1)
        order = self.store.list()[0]
        self.assertEqual(order['packageNo'], 'MS-7')
        self.assertEqual(order['orderNo'], 'TEMU-1')
        self.assertEqual(order['trackingNo'], 'TRACK-1')
        self.assertEqual(self.store.summary()['count'], 1)

    def test_rejects_unrecognized_list_shape(self):
        with self.assertRaisesRegex(ValueError, '列表'):
            extract_package_rows({'data': {'unknown': []}})

    def test_item_specs_and_sku_ids_survive_save_update_and_snapshot(self):
        payload = {'data': {'orderPackageList': [{
            'opOrderPackageId': 10, 'appPackageNo': 'MS-10',
            'items': [
                {'title': 'Gloves', 'skuSubName': 'Purple / S', 'platformSkuId': '19706490306'},
                {'title': 'Gloves', 'skuSubName': 'Black / M', 'platformSkuId': '19706490307'},
            ],
        }]}}
        rows = extract_package_rows(payload)
        for save in (self.store.upsert, self.store.upsert, self.store.replace_snapshot):
            self.assertEqual(save(rows), 1)
            details = self.store.list()[0]['itemDetails']
            self.assertEqual(details, [
                {'title': 'Gloves', 'specification': 'Purple / S', 'skuId': '19706490306'},
                {'title': 'Gloves', 'specification': 'Black / M', 'skuId': '19706490307'},
            ])

    def test_migrates_existing_orders_without_item_details(self):
        self.store.upsert([{'packageId': 1, 'orderNo': 'PO-existing'}])
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute('ALTER TABLE synced_orders DROP COLUMN item_details')
        migrated = OrderSyncStore(self.store.path).list()[0]
        self.assertEqual(migrated['orderNo'], 'PO-existing')
        self.assertEqual(migrated['itemDetails'], [])

    def test_snapshot_replaces_stale_orders_and_maps_pending_status(self):
        self.store.upsert([{'packageId': 1, 'packageNo': 'STALE'}])
        count = self.store.replace_snapshot([{
            'opOrderPackageId': 2, 'appPackageNo': 'MS-2', 'platformOrderSn': 'NEW-2',
            'appPackageStatus': 'pending',
        }])
        self.assertEqual(count, 1)
        orders = self.store.list()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['packageNo'], 'MS-2')
        self.assertEqual(orders[0]['status'], '待处理')

    def test_lists_orders_by_ordered_time_not_last_updated_time(self):
        self.store.upsert([
            {'packageId': 1, 'packageNo': 'OLDER', 'orderedAt': '2026-10-14 20:00:00', 'updatedAtRemote': '2026-10-15 19:00:00'},
            {'packageId': 2, 'packageNo': 'NEWER', 'orderedAt': '2026-10-15 10:00:00', 'updatedAtRemote': '2026-10-15 11:00:00'},
        ])
        self.assertEqual([order['packageNo'] for order in self.store.list()], ['NEWER', 'OLDER'])

    def test_extracts_official_miaoshou_package_list_without_consignee(self):
        rows = extract_package_rows({'data': {
            'orderPackageList': [{
                'opOrderPackageId': 9, 'appPackageNo': 'MS-9', 'shopId': 3, 'shopNick': '妙手测试店铺',
                'appPackageStatus': 'wait_seller_send', 'appPackageStatusText': '待发货',
                'logisticsNo': 'TRACK-9',
                'orderInfo': {
                    'platformOrderSn': 'TEMU-9', 'orderAmount': 8.5,
                    'currency': 'USD', 'gmtOrderStart': '2026-10-14 09:00:00',
                    'gmtOrderModified': '2026-10-14 10:00:00',
                },
                'items': [{'title': 'Example product', 'picUrl': 'https://example.test/item.jpg'}],
                'consigneeInfo': {'city': 'must not be saved'},
            }]
        }})
        self.assertEqual(self.store.upsert(rows), 1)
        order = self.store.list()[0]
        self.assertEqual(order['packageNo'], 'MS-9')
        self.assertEqual(order['orderNo'], 'TEMU-9')
        self.assertEqual(order['status'], '待发货')
        self.assertEqual(order['itemSummary'], 'Example product')
        self.assertEqual(order['shopName'], '妙手测试店铺')
        self.assertEqual(order['imageUrl'], 'https://example.test/item.jpg')
