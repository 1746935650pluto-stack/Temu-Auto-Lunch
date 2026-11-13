"""Whole-box import uses isolated databases and synthetic vendor reads only."""
import os
import unittest
from threading import Barrier
from unittest.mock import patch

import server
import hourly_sync
import test_collection_accounts as fixtures
from hourly_sync import HourlySync


class HourlySyncTests(unittest.TestCase):
    request = fixtures.CollectionAccountTests.request
    stop_server = fixtures.CollectionAccountTests.stop_server

    def setUp(self):
        fixtures.CollectionAccountTests.setUp(self)
        for key in ('SHARED_SERVICES', 'COLLECTION_SYNCS', 'COLLECTION_READ_CACHES', 'ORDER_AUTO_SYNCS'):
            mocked = patch.object(server, key, {})
            mocked.start(); self.addCleanup(mocked.stop)
        self.now = 1000
        self.service = HourlySync(server, server.COLLECTION_PEOPLE.store.path, clock=lambda: self.now)
        server.COLLECTION_SYNCS[self.service.path] = self.service
        self.addCleanup(lambda: self.service._finish() if self.service.running else None)

    def run_account(self, account=2):
        accounts = self.service.start_sync(account, launch=False)
        self.service._run(accounts, False)
        return self.service.snapshot(account)['result']

    def test_full_pagination_skips_existing_and_managed_copies_without_overwrite(self):
        pool = server.automation_pool(2)
        for detail_id in range(1, 500):
            pool.put_item(detail_id, 'site', 0, {'title': 'existing snapshot'})
        server.automation_pool(1).put_item(501, 'site', 0, {'title': 'other account'})
        service = server.shared_automation()
        service.accounts()
        with service.db() as db:
            db.execute("INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,'ready',NULL,'')",
                       ('known-copy', 1, service.scope(1), 888, 2, service.scope(2), 500))
        calls = []
        def post(client, path, payload):
            calls.append((client.account_id, path, payload))
            if path == server.HALF_BOX_LIST:
                ids = range(1, 501) if payload['pageNo'] == 1 else [501]
                data = {'detailList': [{'collectBoxDetailId': i, 'title': f'item {i}', 'subAppAccountId': '11'} for i in ids]}
            elif path == server.HALF_SITE_DETAIL:
                data = {'siteCollectItemInfo': self.item}
            else:
                raise AssertionError('unexpected vendor call: ' + path)
            return {'result': 'success', 'code': 'success', 'data': data}
        with patch.object(server.MiaoShouClient, 'post', post):
            result = self.run_account()
        self.assertEqual((result['total'], result['added'], result['skipped'], result['replicas']), (501, 1, 499, 1))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c[0] == 2 for c in calls))
        self.assertEqual(pool.get_item(1)['title'], 'existing snapshot')
        self.assertEqual(server.automation_pool(1).get_item(501)['title'], 'other account')
        self.assertEqual(pool.list_jobs(), [])
        cached = server.collection_read_cache()._read(service.scope(2), 'products:50:11')[0]
        self.assertEqual([r['collectBoxDetailId'] for r in cached['data']['detailList']], [501])
        self.assertEqual(server.collection_read_cache()._read(service.scope(2), 'page-summary')[0]['totalItems'], 501)
        self.assertEqual(server.COLLECTION_PEOPLE.ownership(service.scope(2))[1]['501'], '11')

    def test_detail_failure_isolated_and_second_run_retries_only_missing(self):
        reads = []
        fail = [True]
        def post(client, path, payload):
            if path == server.HALF_BOX_LIST:
                data = {'detailList': [{'collectBoxDetailId': i} for i in (1, 2)]}
            else:
                reads.append(payload['detailId'])
                if payload['detailId'] == 2 and fail[0]:
                    raise RuntimeError('detail unavailable')
                data = {'siteCollectItemInfo': self.item}
            return {'result': 'success', 'code': 'success', 'data': data}
        with patch.object(server.MiaoShouClient, 'post', post):
            result = self.run_account()
            self.assertEqual((result['status'], result['added'], result['failed']), ('partial', 1, 1))
            self.assertEqual(result['failures'][0]['detailId'], 2)
            fail[0] = False
            result = self.run_account()
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(reads.count(1), 1)
        self.assertEqual(result['status'], 'completed')

    def test_failed_scan_preserves_cache_and_never_imports_partial_listing(self):
        scope = server.shared_automation().scope(2)
        cache = server.collection_read_cache()
        cache.store_listing(scope, [{'collectBoxDetailId': 900}])
        with patch.object(server.BackgroundAutomationWorker, '_vendor', return_value={'data': {}}):
            result = self.run_account()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(server.automation_pool(2).item_ids(), set())
        self.assertEqual(cache._read(scope, 'products:50:1')[0]['data']['detailList'][0]['collectBoxDetailId'], 900)

    def test_schedule_persists_toggle_due_time_and_runs_once_after_idle(self):
        self.assertFalse(self.service.tick(launch=False))
        self.now += 3600
        with patch.object(server.shared_automation(), 'activity', return_value={'editing': True, 'publishing': False, 'activeTransfers': 0}):
            self.assertFalse(self.service.tick(launch=False))
        with patch.object(server.shared_automation(), 'activity', return_value={'editing': False, 'publishing': False, 'activeTransfers': 0}):
            accounts = self.service.tick(launch=False)
        self.assertEqual([a['id'] for a in accounts], [1, 2])
        self.assertFalse(self.service.tick(launch=False))
        self.service._run(accounts, True)
        self.assertFalse(self.service.tick(launch=False))
        for account in (1, 2):
            result = self.service.snapshot(account)['result']
            self.assertIsNone(result['orders'])
            self.assertEqual(result['added'], 1)
        self.service.set_enabled(False)
        reopened = HourlySync(server, self.service.path, clock=lambda: self.now)
        self.assertFalse(reopened.snapshot()['enabled'])
        self.assertFalse(reopened.tick(launch=False))
        self.assertTrue(all(path in {server.HALF_BOX_LIST, server.HALF_SITE_DETAIL, server.MIAOSHOU_PACKAGE_LIST_PATH}
                            for _, path, _ in self.calls))

    def test_orders_only_schedule_runs_every_five_minutes_without_importing_collection(self):
        service = HourlySync(server, self.service.path.with_name('order-auto-sync-test.sqlite3'),
                             interval=300, clock=lambda: self.now, orders_only=True)
        self.now += 300
        accounts = service.tick(launch=False)
        self.assertEqual([account['id'] for account in accounts], [1, 2])
        service._run(accounts, True)
        self.assertTrue(all(service.snapshot(account)['result']['status'] == 'completed' for account in (1, 2)))
        self.assertTrue(all(service.snapshot(account)['result']['orders'] == 1 for account in (1, 2)))
        self.assertTrue(all(path == server.MIAOSHOU_PACKAGE_LIST_PATH for _, path, _ in self.calls))

    def test_credential_rotation_blocks_import_and_hides_old_status(self):
        accounts = self.service.start_sync(2, launch=False)
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_SECRET': 'rotated'}):
            self.service._run(accounts, False)
            self.assertIsNone(self.service.snapshot(2)['result'])
        self.assertEqual(self.service.snapshot(2)['result']['status'], 'failed')
        self.assertEqual(server.automation_pool(2).item_ids(), set())
        self.assertEqual(self.calls, [])

    def test_sync_never_allows_remote_write_and_invalid_orders_preserve_snapshot(self):
        account = server.shared_automation().check_account(2)
        worker = self.service._worker(account)
        with self.assertRaisesRegex(ValueError, '同步只允许读取'):
            worker._vendor(server.HALF_SITE_SAVE, {})
        self.assertEqual(self.calls, [])
        store = server.order_store(2)
        store.replace_snapshot([{'orderNo': 'keep-existing-order'}])
        self.service.start_sync(2, launch=False)
        with patch.object(server.BackgroundAutomationWorker, '_vendor', return_value={'data': {'orderPackageList': None}}):
            self.service._orders(account)
        self.assertIn('保留原有订单', self.service.snapshot(2)['result']['orderError'])
        self.assertEqual(store.list()[0]['orderNo'], 'keep-existing-order')

    def test_malformed_shop_isolated_from_other_products(self):
        original = server.MiaoShouClient.post
        def post(client, path, payload):
            if path == server.HALF_BOX_LIST:
                return {'result': 'success', 'code': 'success', 'data': {'detailList': [
                    {'collectBoxDetailId': 1, 'editModel': 'shop', 'collectBoxDetailShopList': [{'shopId': 'invalid'}]},
                    {'collectBoxDetailId': 2, 'editModel': 'site'}]}}
            return original(client, path, payload)
        with patch.object(server.MiaoShouClient, 'post', post):
            result = self.run_account()
        self.assertEqual((result['added'], result['failed'], result['processed']), (1, 1, 2))
        self.assertEqual(server.automation_pool(2).item_ids(), {2})

    def test_restart_reports_interruption_and_duplicate_start_is_rejected(self):
        accounts = self.service.start_sync(2, launch=False)
        with self.assertRaisesRegex(ValueError, '已有同步任务'):
            self.service.start_sync(2, launch=False)
        self.service._finish()
        reopened = HourlySync(server, self.service.path, clock=lambda: self.now)
        self.assertEqual(reopened.snapshot(2)['result']['status'], 'interrupted')
        self.assertFalse(reopened.running)
        self.assertEqual(server.ACCOUNT_MUTATIONS_ACTIVE, 0)

    def test_sync_blocks_account_removal_until_worker_finishes(self):
        self.service.start_sync(2, launch=False)
        with server.ACCOUNT_CONFIG_LOCK:
            with self.assertRaises(server.CredentialConfigError):
                server.require_idle_configuration(2)
        self.assertTrue(server.shared_automation().activity()['syncing'])
        self.service._finish()
        self.assertFalse(server.shared_automation().activity()['syncing'])

    def test_http_explicit_account_same_origin_and_settings_validation(self):
        headers = {'X-Miaoshou-Account-Id': '2'}
        with patch.object(hourly_sync, 'Thread') as thread:
            status, body = self.request('POST', '/api/collection-sync', {}, {**headers, 'Origin': 'http://example.com'})
            self.assertEqual(status, 403)
            thread.assert_not_called()
            self.assertEqual(self.request('POST', '/api/collection-sync/settings', {'enabled': 'false'}, headers)[0], 400)
            status, body = self.request('POST', '/api/collection-sync', {}, headers)
            self.assertEqual(status, 202)
            self.assertEqual(body['data']['accountId'], 2)
            thread.return_value.start.assert_called_once()
        self.assertEqual(self.request('GET', '/api/collection-sync', headers=headers)[1]['data']['accountId'], 2)
        self.assertEqual(self.calls, [])



    def order_service(self):
        return HourlySync(server, self.service.path.with_name('orders-test.sqlite3'),
                          interval=300, clock=lambda: self.now, orders_only=True)

    def test_auto_orders_parallel_single_page_merge_and_cache_invalidation(self):
        service = self.order_service()
        for account in (1, 2):
            server.order_store(account).upsert([{'packageId': 999, 'orderNo': 'old'}])
        barrier = Barrier(2)
        calls = []
        def read(client, path, payload):
            self.assertEqual(path, server.MIAOSHOU_PACKAGE_LIST_PATH)
            calls.append((client.account_id, payload.copy()))
            barrier.wait(timeout=3)
            return {'result': 'success', 'code': 'success', 'data': {'total': 5000, 'packageList': [
                {'packageId': i, 'orderNo': str(i)} for i in range(100)]}}
        accounts = service.start_sync(None, launch=False)
        with patch.object(server.MiaoShouClient, 'post', read), \
             patch.object(server.DAILY_STATISTICS, 'invalidate') as invalidate:
            service._run(accounts, True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(p['page'] == 1 and p['pageSize'] == 100 for _, p in calls))
        self.assertEqual(sorted(c.args[0] for c in invalidate.call_args_list), [1, 2], service.results)
        for account in (1, 2):
            result = service.snapshot(account)['result']
            self.assertEqual((result['status'], result['orders'], result['orderPages'], result['orderReceived']),
                             ('completed', 100, 1, 100))
            self.assertEqual(server.order_store(account).summary()['count'], 101)
        self.assertFalse(service.running)
        self.assertEqual(server.ACCOUNT_MUTATIONS_ACTIVE, 0)

    def test_orders_due_during_editing_do_not_overlap_another_round(self):
        service = self.order_service()
        self.now += 300
        with patch.object(server.shared_automation(), 'activity', return_value={
                'editing': True, 'publishing': True, 'activeTransfers': 1}):
            accounts = service.tick(launch=False)
        self.assertEqual(len(accounts), 2)
        self.assertFalse(service.tick(launch=False))
        service._run(accounts, True)
        self.assertEqual(service.next_run, self.now + 300)
        self.assertFalse(service.tick(launch=False))

    def test_busy_account_is_skipped_others_finish_without_overwriting(self):
        service = self.order_service()
        server.order_store(2).upsert([{'packageId': 999, 'orderNo': 'old'}])
        accounts = service.start_sync(None, launch=False)
        with server.ORDER_SYNC_LOCKS[2]:
            service._run(accounts, True)
        self.assertEqual(service.snapshot(1)['result']['status'], 'completed')
        self.assertIn('本轮已跳过', service.snapshot(2)['result']['orderError'])
        self.assertEqual(server.order_store(2).summary()['count'], 1)

    def test_interval_upgrade_clamps_old_deadline_preserves_disabled(self):
        path = self.service.path.with_name('upgrade-orders.sqlite3')
        old = HourlySync(server, path, interval=1800, clock=lambda: self.now, orders_only=True)
        old.set_enabled(False)
        new = HourlySync(server, path, interval=300, clock=lambda: self.now, orders_only=True)
        self.assertEqual(new.next_run, self.now + 300)
        self.assertFalse(new.enabled)
        new.set_enabled(True)
        self.assertFalse(new.tick(launch=False))
        self.assertEqual(self.service.interval, 3600)

    def test_hourly_collection_no_longer_duplicates_order_fetches(self):
        accounts = self.service.start_sync(None, launch=False)
        with patch.object(self.service, '_orders') as orders, patch.object(self.service, '_collection'):
            self.service._run(accounts, True)
        orders.assert_not_called()

    def test_auto_status_route_is_readonly_and_scope_filtered(self):
        service = self.order_service()
        with patch.object(server, 'order_auto_sync', return_value=service):
            status, body = self.request('GET', '/api/orders/auto-sync')
            self.assertEqual(status, 200)
            self.assertEqual(body['data']['intervalSeconds'], 300)
            self.assertEqual({a['accountId'] for a in body['data']['accounts']}, {1, 2})
            self.assertTrue(all(a['orders'] is None for a in body['data']['accounts']))
            self.assertEqual(self.request('GET', '/api/orders/auto-sync',
                             headers={'Origin': 'https://example.test'})[0], 403)
        self.assertEqual(self.calls, [])
