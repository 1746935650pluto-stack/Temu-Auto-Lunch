"""Variable collection pagination with isolated SQLite and fake remote reads."""
import os
import unittest
from unittest.mock import patch

import server
import test_automation_routes as fixtures


class CollectionPageSizeTests(unittest.TestCase):
    request = fixtures.AutomationRouteTests.request
    _stop_server = fixtures.AutomationRouteTests._stop_server

    def setUp(self):
        fixtures.AutomationRouteTests.setUp(self)
        environment = patch.dict(os.environ, {'MIAOSHOU_APP_KEY': 'one', 'MIAOSHOU_APP_SECRET': 'one-secret',
            'MIAOSHOU_ACCOUNT_2_APP_KEY': 'two', 'MIAOSHOU_ACCOUNT_2_APP_SECRET': 'two-secret'}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        self.calls = []
        for method, handler in (('_vendor', lambda path, payload: self.read(path, payload, 1)),
                                ('_account_vendor', self.read)):
            mocked = patch.object(server.AppHandler, method, side_effect=handler)
            mocked.start(); self.addCleanup(mocked.stop)

    def read(self, path, payload, account):
        self.assertEqual(path, server.HALF_BOX_LIST)  # Never create, save or publish.
        self.calls.append((account, dict(payload)))
        start = (payload['pageNo'] - 1) * payload['pageSize']
        return {'data': {'detailList': [
            {'collectBoxDetailId': i, 'title': f'account-{account} item-{i}', 'subAppAccountId': str(i % 2 + 1)}
            for i in range(start + 1, min(237, start + payload['pageSize']) + 1)]}}

    def get(self, route):
        status, body = self.request('GET', route)
        self.assertEqual(status, 200, body)
        return body['data']

    def test_sizes_page_boundaries_cache_reuse_and_legacy_default(self):
        for size in (20, 50, 100):
            path = f'/api/products?page=2&pageSize={size}'
            data = self.get(path)
            self.assertEqual(data['pageSize'], size)
            self.assertEqual([row['collectBoxDetailId'] for row in data['data']['detailList']], list(range(size + 1, 2 * size + 1)))
            count = len(self.calls)
            self.assertTrue(self.get(path)['cache']['cached'])
            self.assertEqual(len(self.calls), count)
        self.assertEqual(self.get('/api/products?page=2')['pageSize'], 20)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.get('/api/products?page=3&pageSize=100')['data']['detailList']), 37)
        self.assertEqual(self.get('/api/products?page=4&pageSize=100')['data']['detailList'], [])

    def test_summary_counts_once_and_recalculates_each_size(self):
        for size, pages in ((20, 12), (50, 5), (100, 3), (20, 12)):
            data = self.get(f'/api/products/page-summary?pageSize={size}')
            self.assertEqual((data['totalItems'], data['totalPages'], data['pageSize']), (237, pages, size))
        self.assertEqual(len(self.calls), 2)  # One 500-row page plus the empty next page.
        self.assertTrue(all(payload['pageSize'] == 500 for _, payload in self.calls))

    def test_personnel_name_filters_reuse_complete_local_scan_for_every_size(self):
        for size, pages in ((20, 6), (50, 3), (100, 2)):
            data = self.get(f'/api/products?collectorId=2&titleKeyword=account-1&page=2&pageSize={size}')
            self.assertEqual(data['filter']['status'], 'ready')
            self.assertEqual((data['filter']['totalItems'], data['filter']['totalPages']), (119, pages))
            expected = list(range(1, 238, 2))[size:2 * size]
            self.assertEqual([row['collectBoxDetailId'] for row in data['data']['detailList']], expected)
        self.assertEqual(len(self.calls), 1)

    def test_personnel_filter_uses_persisted_complete_snapshot_without_remote_read(self):
        scope = server.shared_automation().scope(1)
        server.collection_read_cache().store_listing(scope, [
            {'collectBoxDetailId': 901, 'title': 'fixed owner', 'subAppAccountId': '2'},
            {'collectBoxDetailId': 902, 'title': 'other owner', 'subAppAccountId': '1'},
        ])
        self.calls.clear()
        data = self.get('/api/products?collectorId=2&pageSize=50')
        self.assertEqual(data['filter']['source'], 'local_snapshot')
        self.assertEqual([row['collectBoxDetailId'] for row in data['data']['detailList']], [901])
        self.assertEqual(self.calls, [])

    def test_new_snapshot_replaces_old_membership_for_personnel_filter(self):
        scope = server.shared_automation().scope(1)
        cache = server.collection_read_cache()
        cache.store_listing(scope, [{'collectBoxDetailId': 901, 'title': 'old', 'subAppAccountId': '2'}])
        cache.store_listing(scope, [{'collectBoxDetailId': 902, 'title': 'current', 'subAppAccountId': '2'}])
        local = cache.filtered_listing(scope, '2', 1, 50)
        self.assertEqual([row['collectBoxDetailId'] for row in local['rows']], [902])

    def test_same_page_and_size_do_not_mix_account_caches(self):
        for account in (1, 2, 1, 2):
            data = self.get(f'/api/products?accountId={account}&pageSize=100')
            self.assertEqual(data['accountId'], account)
            self.assertTrue(all(row['title'].startswith(f'account-{account} ') for row in data['data']['detailList']))
        self.assertEqual(len(self.calls), 2)

    def test_invalid_or_duplicate_size_rejected_before_any_remote_request(self):
        for route in ('/api/products', '/api/products/page-summary'):
            for invalid in ('0', '-1', '21', '500', '1.5', 'x', '', '50&pageSize=100'):
                self.assertEqual(self.request('GET', f'{route}?pageSize={invalid}')[0], 400)
        self.assertEqual(self.calls, [])

    def test_manual_refresh_only_reloads_requested_size_page(self):
        self.get('/api/products?pageSize=20')
        self.get('/api/products?pageSize=100')
        self.calls.clear()
        data = self.get('/api/products?pageSize=100&refresh=1')
        self.assertFalse(data['cache']['cached'])
        self.assertEqual(self.calls, [(1, {'pageNo': 1, 'pageSize': 100})])
        self.assertTrue(self.get('/api/products?pageSize=20')['cache']['cached'])
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
