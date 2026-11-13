"""Fast imports, progress and report cache use isolated DBs and fake vendors."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import server
from daily_statistics import DailyStatistics
from test_order_accounts import OrderAccountTests


class FastOrderTests(OrderAccountTests):
    def post_sync(self, mode='recent', account=2, request='test_batch'):
        return self.request('POST', '/api/orders/sync', {
            'accountId': account, 'mode': mode, 'requestId': request})

    def vendor(self, total, calls):
        def read(path, payload, *args):
            calls.append(dict(payload))
            start = (payload['page'] - 1) * 100
            return {'data': {'total': total, 'packageList': [
                {'packageId': i, 'orderNo': f'new-{i}', 'orderTime': '2026-11-13'}
                for i in range(start, min(start + 100, total))]}}
        return read

    def test_quick_reads_one_page_merges_history_and_invalidates_after_commit(self):
        store = server.order_store(2)
        store.upsert([{'packageId': 9999, 'orderNo': 'old', 'orderTime': '2026-11-01'}])
        calls = []
        with patch.object(server.AppHandler, '_account_vendor', side_effect=self.vendor(2407, calls)), \
             patch.object(server.DAILY_STATISTICS, 'invalidate') as invalidate:
            status, body = self.post_sync()
        self.assertEqual(status, 200)
        data = body['data']
        self.assertEqual((data['pages'], data['received'], data['imported']), (1, 100, 100))
        self.assertEqual(len(calls), 1)
        self.assertEqual(store.summary()['count'], 101)
        self.assertIn('old', [row['orderNo'] for row in store.list()])
        self.assertIn('更早订单未在本轮刷新', data['message'])
        self.assertNotIn('orders', data)
        invalidate.assert_called_once_with(2)
        progress = self.request('GET', '/api/orders/sync-progress?requestId=test_batch')[1]['data']
        self.assertTrue(progress['supportsModes'])
        self.assertEqual(next(row for row in progress['entries'] if row['accountId'] == 2)['status'], 'completed')
        self.assertEqual(self.request('GET', '/api/orders/sync-progress?requestId=other')[1]['data']['entries'], [])

    def test_history_reads_multiple_pages_and_preserves_existing_history(self):
        server.order_store(2).upsert([{'packageId': 9999, 'orderNo': 'old'}])
        calls = []
        with patch.object(server.AppHandler, '_account_vendor', side_effect=self.vendor(2407, calls)):
            status, body = self.post_sync('history')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['imported'], 2407)
        self.assertEqual(body['data']['pages'], 25)
        self.assertEqual(len(calls), 25)
        self.assertEqual(server.order_store(2).summary()['count'], 2408)

    def test_page_failure_retains_old_data_and_exposes_progress(self):
        store = server.order_store(2)
        store.upsert([{'packageId': 9999, 'orderNo': 'old'}])
        def read(path, payload, *args):
            if payload['page'] == 2:
                raise RuntimeError('page two failed')
            return self.vendor(200, [])(path, payload)
        with patch.object(server.AppHandler, '_account_vendor', side_effect=read), \
             patch.object(server.DAILY_STATISTICS, 'invalidate') as invalidate:
            self.assertEqual(self.post_sync('history')[0], 502)
        invalidate.assert_not_called()
        self.assertEqual(store.summary()['count'], 1)
        entry = next(row for row in self.request('GET', '/api/orders/sync-progress?requestId=test_batch')[1]['data']['entries'] if row['accountId'] == 2)
        self.assertEqual((entry['status'], entry['pages'], entry['received']), ('failed', 1, 100))
        self.assertFalse(server.ORDER_SYNC_LOCKS[2].locked())

    def test_accounts_overlap_but_same_account_cannot_start_twice(self):
        entered1, entered2, release = Event(), Event(), Event()
        def read1(*args):
            entered1.set()
            if not release.wait(2): raise RuntimeError('test release timeout')
            return {'data': {'packageList': []}}
        def read2(*args):
            entered2.set()
            if not release.wait(2): raise RuntimeError('test release timeout')
            return {'data': {'packageList': []}}
        with patch.object(server.AppHandler, '_vendor', side_effect=read1), \
             patch.object(server.AppHandler, '_account_vendor', side_effect=read2), \
             ThreadPoolExecutor(max_workers=2) as executor:
            one = executor.submit(self.post_sync, 'recent', 1)
            two = executor.submit(self.post_sync, 'recent', 2)
            try:
                self.assertTrue(entered1.wait(1)); self.assertTrue(entered2.wait(1))
                self.assertEqual(self.post_sync(account=2, request='duplicate')[0], 409)
                entries = self.request('GET', '/api/orders/sync-progress?requestId=test_batch')[1]['data']['entries']
                self.assertEqual({row['accountId'] for row in entries}, {1, 2})
                self.assertTrue(all(row['status'] == 'running' for row in entries))
            finally:
                release.set()
            self.assertEqual(one.result()[0], 200); self.assertEqual(two.result()[0], 200)

    def test_modes_validation_empty_import_and_progress_origin(self):
        store = server.order_store(2)
        store.upsert([{'packageId': 9999, 'orderNo': 'old'}])
        with patch.object(server.AppHandler, '_account_vendor', side_effect=self.vendor(0, [])) as remote:
            self.assertEqual(self.post_sync('invalid')[0], 400)
            self.assertEqual(self.post_sync(request='bad request')[0], 400)
            remote.assert_not_called()
            status, body = self.post_sync()
            self.assertEqual(status, 200); self.assertEqual(body['data']['imported'], 0)
            self.assertEqual(store.summary()['count'], 1)
        self.assertEqual(self.request('GET', '/api/orders/sync-progress?requestId=test_batch',
                                     headers={'Origin': 'https://example.test'})[0], 403)

    def test_cache_invalidates_affected_account_and_aggregate_only(self):
        service = DailyStatistics()
        accounts = [{'id': 1, 'name': 'one'}, {'id': 2, 'name': 'two'}]
        args = (accounts, server.AUTOMATION_POOL.path, server.ORDER_SYNC_STORE.path)
        with patch('daily_statistics.build_report', side_effect=lambda *args: object()) as build:
            reports = {key: service.report(*args, {'accountId': [key]}) for key in ('1', '2', 'all')}
            service.invalidate(2)
            self.assertIs(service.report(*args, {'accountId': ['1']}), reports['1'])
            self.assertIsNot(service.report(*args, {'accountId': ['2']}), reports['2'])
            self.assertIsNot(service.report(*args, {'accountId': ['all']}), reports['all'])
            self.assertEqual(build.call_count, 5)
