"""Account isolation tests; all remote requests use synthetic responses."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server
from order_sync import OrderSyncStore


class OrderAccountTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, value in (
            ('ORDER_SYNC_STORE', OrderSyncStore(root / 'order_sync.sqlite3')),
            ('ACCOUNT_SLOT_STORE', server.AccountSlotStore(root / 'slots.sqlite3')),
            ('AUTOMATION_POOL', server.AutomationPool(root / 'automation_pool.sqlite3')),
            ('ACCOUNT_POOLS', {}),
            ('DATA_ROOT', root),
            ('DEEPSEEK_PROFILES', server.DeepSeekProfileStore(root / 'deepseek_profiles.json')),
            ('MIAOSHOU_PACKAGE_LIST_PATH', '/open/v1/order/package/fetch/search_package_list'),
            ('MIAOSHOU_PACKAGE_LIST_PAYLOAD', '{"page":1}'),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {
            'APP_MODE': 'live', 'MIAOSHOU_APP_KEY': 'test-one', 'MIAOSHOU_APP_SECRET': 'secret-one',
            'MIAOSHOU_ACCOUNT_2_APP_KEY': 'test-two', 'MIAOSHOU_ACCOUNT_2_APP_SECRET': 'secret-two',
        }, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.AppHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None,
                               {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_storage_is_separate_with_identical_record_ids_and_legacy_history_preserved(self):
        server.ORDER_SYNC_STORE.upsert([{'packageId': 7, 'orderNo': 'account-one', 'shopName': 'shop-one'}])
        server.order_store(2).upsert([{'packageId': 7, 'orderNo': 'account-two', 'shopName': 'shop-two'}])
        for account_id, expected in ((1, 'account-one'), (2, 'account-two')):
            status, body = self.request('GET', f'/api/orders?accountId={account_id}')
            self.assertEqual(status, 200)
            self.assertEqual(body['data']['accountId'], account_id)
            self.assertEqual(body['data']['orders'][0]['orderNo'], expected)
            self.assertEqual(body['data']['summary']['count'], 1)
        self.assertIs(server.order_store(1), server.ORDER_SYNC_STORE)
        self.assertEqual(self.request('GET', '/api/orders')[1]['data']['accountId'], 1)

    def test_sync_uses_selected_credentials_and_only_replaces_selected_snapshot(self):
        server.ORDER_SYNC_STORE.upsert([{'packageId': 7, 'orderNo': 'keep-one'}])
        calls = []

        def fake_post(client, path, payload):
            calls.append((client.app_key, path, payload['page']))
            return {'result': 'success', 'code': 'success', 'data': {'orderPackageList': [{
                'opOrderPackageId': 7, 'orderInfo': {'platformOrderSn': 'new-two'},
                'shopNick': 'second-shop', 'items': [{'title': 'item', 'skuSubName': 'Blue / S', 'platformSkuId': '123'}],
            }]}}

        with patch.object(server.MiaoShouClient, 'post', fake_post), patch.object(server.time, 'sleep'):
            status, body = self.request('POST', '/api/orders/sync', {'accountId': 2})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [('test-two', server.MIAOSHOU_PACKAGE_LIST_PATH, 1)])
        self.assertEqual(body['data']['accountId'], 2)
        self.assertEqual(body['data']['orders'][0]['orderNo'], 'new-two')
        self.assertEqual(server.ORDER_SYNC_STORE.list()[0]['orderNo'], 'keep-one')

    def test_unknown_or_unconfigured_account_never_falls_back_to_primary(self):
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_KEY': '', 'MIAOSHOU_ACCOUNT_2_APP_SECRET': ''}), \
             patch.object(server.MiaoShouClient, 'post') as remote:
            status, body = self.request('GET', '/api/orders?accountId=2')
            self.assertEqual(status, 200)
            self.assertFalse(body['data']['syncConfigured'])
            self.assertEqual(body['data']['orders'], [])
            self.assertFalse(server.MiaoShouClient(2).configured)
            for account_id in (2, 99, 0, True, '1.5', '../1', None):
                self.assertEqual(self.request('POST', '/api/orders/sync', {'accountId': account_id})[0], 400)
            self.assertEqual(self.request('GET', '/api/orders?accountId=99')[0], 400)
            remote.assert_not_called()

    def test_failure_and_concurrent_sync_preserve_records_and_release_lock(self):
        store = server.order_store(2)
        store.upsert([{'packageId': 8, 'orderNo': 'preserved'}])
        with patch.object(server.AppHandler, '_account_vendor', side_effect=RuntimeError('test failure')):
            self.assertEqual(self.request('POST', '/api/orders/sync', {'accountId': 2})[0], 502)
        self.assertFalse(server.ORDER_SYNC_LOCKS[2].locked())
        self.assertEqual(store.list()[0]['orderNo'], 'preserved')
        server.ORDER_SYNC_LOCKS[2].acquire()
        try:
            with patch.object(server.MiaoShouClient, 'post') as remote:
                self.assertEqual(self.request('POST', '/api/orders/sync', {'accountId': 2})[0], 409)
                remote.assert_not_called()
        finally:
            server.ORDER_SYNC_LOCKS[2].release()

    def test_account_directory_changes_are_shared_with_orders_and_cross_origin_is_blocked(self):
        status, body = self.request('POST', '/api/miaoshou-accounts', {'confirmation': 'ADD_EMPTY_COLLECTION'})
        self.assertEqual(status, 201)
        account_id = body['data']['id']
        self.assertEqual(self.request('GET', f'/api/orders?accountId={account_id}')[1]['data']['orders'], [])
        self.assertEqual(self.request('DELETE', f'/api/miaoshou-accounts/{account_id}', {'confirmation': 'DELETE_EMPTY_COLLECTION'})[0], 200)
        self.assertEqual(self.request('GET', f'/api/orders?accountId={account_id}')[0], 400)
        for method, path, body in (('GET', '/api/orders?accountId=1', None), ('POST', '/api/orders/sync', {'accountId': 1})):
            self.assertEqual(self.request(method, path, body, {'Origin': 'https://example.com'})[0], 403)

    def test_account_with_cached_orders_is_not_an_empty_deletable_slot(self):
        server.order_store(2).upsert([{'packageId': 1, 'orderNo': 'preserve'}])
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_KEY': '', 'MIAOSHOU_ACCOUNT_2_APP_SECRET': ''}):
            status, body = self.request('GET', '/api/miaoshou-accounts')
            self.assertFalse(next(a for a in body['data']['accounts'] if a['id'] == 2)['deletable'])
            self.assertEqual(self.request('DELETE', '/api/miaoshou-accounts/2', {'confirmation': 'DELETE_EMPTY_COLLECTION'})[0], 400)
            self.assertEqual(server.order_store(2).list()[0]['orderNo'], 'preserve')


if __name__ == '__main__':
    unittest.main()
