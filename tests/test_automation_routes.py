import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import ANY, PropertyMock, call, patch

import server
from automation_pool import AutomationPool
from test_item_editor import sample_item


class AutomationRouteTests(unittest.TestCase):
    def test_multi_shop_batch_creates_every_pair_atomically_and_one_worker(self):
        # The HTTP entry point routes an account across every physical pool.
        pool = server.automation_pool(1)
        for detail_id in (123, 124):
            pool.put_item(detail_id, 'site', 0, {'title': '测试商品'})
        payload = {'detailIds': [123, 124], 'shopIds': [456, 789, 456], 'site': 'US',
                   'confirmation': 'AUTOMATE_AND_SAVE_BATCH', 'editConcurrency': 2}
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            for shops in ([], [456, 0], '456', [True], [1.2]):
                self.assertEqual(self.request('POST', '/api/automation/batch-run', {**payload, 'shopIds': shops})[0], 400)
                self.assertEqual(pool.list_jobs(), [])
            status, body = self.request('POST', '/api/automation/batch-run', payload)
            self.assertEqual(status, 202)
            self.assertEqual({(j['detailId'], j['shopId']) for j in body['data']['jobs']},
                             {(123, 456), (123, 789), (124, 456), (124, 789)})
            self.assertTrue(all(not j['publishAfterSave'] for j in body['data']['jobs']))
            worker.return_value.start.assert_called_once()
            self.assertEqual(len(pool.start_next_queued_jobs(2)), 2)
            self.assertEqual(pool.start_next_queued_jobs(2), [])
            self.assertEqual(self.request('POST', '/api/automation/batch-run', payload)[0], 400)
            self.assertEqual(len(pool.list_jobs()), 4)

    def test_batch_run_accepts_200_workers_and_rejects_noninteger_concurrency(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        payload = {'detailIds': [123], 'shopId': 456, 'site': 'US', 'price': '160.56',
                   'confirmation': 'AUTOMATE_AND_SAVE_BATCH'}
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            for value in (0, -1, 2.5, True, '2.5', 2 ** 53):
                status, _ = self.request('POST', '/api/automation/batch-run', {**payload, 'editConcurrency': value})
                self.assertEqual(status, 400)
            worker.assert_not_called()
            self.assertEqual(pool.list_jobs(), [])
            status, result = self.request('POST', '/api/automation/batch-run', {**payload, 'editConcurrency': 200})
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['editConcurrency'], 200)
            self.assertEqual(worker.call_args.kwargs['args'][-1], 200)
            self.assertFalse(result['data']['jobs'][0]['publishAfterSave'])

    def test_remove_publish_queue_requires_confirmation_origin_and_never_calls_vendor(self):
        pool = server.AUTOMATION_POOL
        for detail, job_id in ((123, 'a' * 32), (124, 'b' * 32)):
            pool.put_item(detail, 'site', 0, {'title': '商品'})
            pool.start_job(job_id, detail, 456, 'US')
            pool.update_job(job_id, 'publish', 'ready_publish', '待发布')
        payload = {'jobIds': ['a' * 32, 'b' * 32], 'confirmation': 'REMOVE_PUBLISH_QUEUE_ITEMS'}
        path = '/api/automation/publish-queue'
        with patch.object(server.AppHandler, '_vendor') as vendor:
            self.assertEqual(self.request('DELETE', path, payload, {'Origin': 'http://example.com'})[0], 403)
            self.assertEqual(self.request('DELETE', path, {'jobIds': payload['jobIds']})[0], 400)
            self.assertEqual(len(pool.ready_publish_jobs()), 2)
            status, body = self.request('DELETE', path, payload)
            self.assertEqual(status, 200)
            self.assertEqual(body['data']['removed'], 2)
            self.assertEqual(pool.ready_publish_jobs(), [])
            self.assertEqual(len(pool.list_jobs()), 2)
            self.assertEqual(self.request('DELETE', path, payload)[0], 400)
            vendor.assert_not_called()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        original_pool = server.AUTOMATION_POOL
        server.AUTOMATION_POOL = AutomationPool(Path(directory.name) / "pool.sqlite3")
        self.addCleanup(setattr, server, "AUTOMATION_POOL", original_pool)
        original_slots = server.ACCOUNT_SLOT_STORE
        server.ACCOUNT_SLOT_STORE = server.AccountSlotStore(Path(directory.name) / 'accounts.sqlite3')
        self.addCleanup(setattr, server, 'ACCOUNT_SLOT_STORE', original_slots)
        for mocked in (patch.object(server.RUNTIME, 'lanes', {}),
                       patch.object(server, 'ACCOUNT_POOLS', {}),
                       patch.object(server, 'SHARED_SERVICES', {}),
                       patch.object(server.MiaoShouClient, 'post', side_effect=AssertionError('Unexpected remote request in route test'))):
            mocked.start()
            self.addCleanup(mocked.stop)
        people_patch = patch.object(server, 'COLLECTION_PEOPLE', server.CollectionPeople(
            server.PersonnelStore(Path(directory.name) / 'people.sqlite3')))
        people_patch.start()
        self.addCleanup(people_patch.stop)
        # Slot deletion checks must inspect isolated orders, never the user's live history.
        orders_patch = patch.object(server, 'ORDER_SYNC_STORE', server.OrderSyncStore(
            Path(directory.name) / 'orders.sqlite3'))
        orders_patch.start()
        self.addCleanup(orders_patch.stop)

        item = {"title": "测试商品", "imgUrls": ["https://example.com/p.jpg"], "skuMap": {"blue": {"stock": 9}}}
        detail_patch = patch.object(server.AppHandler, "_get_item", return_value=item)
        self.detail_mock = detail_patch.start()
        self.addCleanup(detail_patch.stop)
        credentials_patch = patch.object(server.MiaoShouClient, "configured", new_callable=PropertyMock, return_value=True)
        credentials_patch.start()
        self.addCleanup(credentials_patch.stop)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.AppHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        body = json.dumps(data).encode("utf-8") if data is not None else None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_selected_item_moves_to_local_pool_without_vendor_write(self):
        status, added = self.request("POST", "/api/automation/pool", {"detailId": 123, "mode": "site", "shopId": 0})
        self.assertEqual(status, 200)
        self.assertEqual(added["data"]["detailId"], 123)

        status, listed = self.request("GET", "/api/automation/pool")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["items"]), 1)
        self.assertNotIn("item", listed["data"]["items"][0])

        status, detail = self.request("GET", "/api/automation/pool/123")
        self.assertEqual(status, 200)
        self.assertEqual(detail["data"]["item"]["skuMap"]["blue"]["stock"], 9)

        status, _ = self.request("DELETE", "/api/automation/pool/123")
        self.assertEqual(status, 200)
        status, listed = self.request("GET", "/api/automation/pool")
        self.assertEqual(listed["data"]["items"], [])

    def test_collection_account_registry_keeps_slot_two_and_discovers_later_slots_without_secrets(self):
        with patch.dict(os.environ, {
            'MIAOSHOU_APP_KEY': 'primary-key', 'MIAOSHOU_APP_SECRET': 'primary-secret',
            'MIAOSHOU_ACCOUNT_3_NAME': '北美账号',
            'MIAOSHOU_ACCOUNT_4_APP_KEY': 'fourth-key', 'MIAOSHOU_ACCOUNT_4_APP_SECRET': 'fourth-secret',
        }, clear=True):
            slots = server.miaoshou_account_slots()
            status, response = self.request('GET', '/api/miaoshou-accounts')
        self.assertEqual([slot['id'] for slot in slots], [1, 2, 3, 4])
        self.assertTrue(slots[0]['configured'])
        self.assertFalse(slots[1]['configured'])
        self.assertFalse(slots[2]['configured'])
        self.assertTrue(slots[3]['configured'])
        self.assertEqual(status, 200)
        self.assertEqual([account['id'] for account in response['data']['accounts']], [1, 2, 3, 4])
        self.assertNotIn('APP_SECRET', str(response))
        self.assertNotIn('fourth-secret', str(response))

    def test_empty_account_add_delete_is_local_persistent_and_protects_credentials(self):
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_4_APP_KEY': 'partial-secret-key'}, clear=True), \
                patch.object(server.AppHandler, '_vendor') as vendor:
            self.assertEqual(self.request('POST', '/api/miaoshou-accounts', {})[0], 400)
            payload = {'confirmation': 'ADD_EMPTY_COLLECTION'}
            self.assertEqual(self.request('POST', '/api/miaoshou-accounts', payload,
                                          {'Origin': 'https://example.com'})[0], 403)
            status, response = self.request('POST', '/api/miaoshou-accounts', payload)
            self.assertEqual(status, 201)
            self.assertEqual(response['data']['id'], 3)
            self.assertNotIn('partial-secret-key', str(response))
            deletion = {'confirmation': 'DELETE_EMPTY_COLLECTION'}
            for slot in (1, 4, 999):
                self.assertEqual(self.request('DELETE', f'/api/miaoshou-accounts/{slot}', deletion)[0], 400)
            self.assertEqual(self.request('DELETE', '/api/miaoshou-accounts/2', {})[0], 400)
            self.assertEqual(self.request('DELETE', '/api/miaoshou-accounts/2', deletion,
                                          {'Origin': 'https://example.com'})[0], 403)
            status, response = self.request('DELETE', '/api/miaoshou-accounts/2', deletion)
            self.assertEqual(status, 200)
            self.assertEqual([a['id'] for a in response['data']['accounts']], [1, 3, 4])
            response = self.request('GET', '/api/miaoshou-accounts')[1]
            self.assertEqual([a['id'] for a in response['data']['accounts']], [1, 3, 4])
            self.assertEqual(self.request('GET', '/account_slots.sqlite3')[0], 404)
            vendor.assert_not_called()

    def test_deleted_collect_box_job_can_restore_from_local_snapshot(self):
        snapshot = sample_item()
        server.AUTOMATION_POOL.put_item(123, 'site', 0, snapshot)
        server.AUTOMATION_POOL.start_job('d' * 32, 123, 456, 'US', price='160.50')
        server.AUTOMATION_POOL.pause_for_problem('d' * 32, 'preflight', '此产品在采集箱中已被删除')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), \
             patch.object(server.AppHandler, '_vendor', return_value={'data': {'collectBoxDetailId': 789}}) as vendor, \
             patch.object(server, 'Thread') as worker:
            status, result = self.request('POST', '/api/automation/restore-deleted', {
                'jobId': 'd' * 32, 'confirmation': 'RESTORE_DELETED_AUTOMATION_ITEM',
            })
        self.assertEqual(status, 202)
        self.assertEqual(result['data']['detailId'], 789)
        vendor.assert_called_once_with(server.HALF_CREATE, ANY)
        self.assertEqual(server.AUTOMATION_POOL.get_job('d' * 32)['status'], 'canceled')
        self.assertIn('新的采集箱 ID 789', server.AUTOMATION_POOL.get_job('d' * 32)['message'])
        replacement = result['data']['job']
        self.assertEqual((replacement['detailId'], replacement['shopId'], replacement['site']), (789, 456, 'US'))
        self.assertFalse(replacement['publishAfterSave'])
        self.assertEqual(replacement['status'], 'queued')
        worker.return_value.start.assert_called_once()

    def test_restore_deleted_collect_box_truncates_only_overlong_english_title(self):
        unchanged = {'multiLanguageTitleMap': {'en': ' title with spaces '}}
        self.assertFalse(server.truncate_restore_english_title(unchanged))
        self.assertEqual(unchanged['multiLanguageTitleMap']['en'], ' title with spaces ')

        snapshot = sample_item()
        snapshot['multiLanguageTitleMap'] = {'en': 'e' * 251}
        server.AUTOMATION_POOL.put_item(123, 'site', 0, snapshot)
        server.AUTOMATION_POOL.start_job('e' * 32, 123, 456, 'US', price='160.50')
        server.AUTOMATION_POOL.pause_for_problem('e' * 32, 'preflight', '此产品在采集箱中已被删除')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), \
             patch.object(server.AppHandler, '_vendor', return_value={'data': {'collectBoxDetailId': 789}}) as vendor, \
             patch.object(server, 'Thread'):
            status, _ = self.request('POST', '/api/automation/restore-deleted', {
                'jobId': 'e' * 32, 'confirmation': 'RESTORE_DELETED_AUTOMATION_ITEM',
            })
        self.assertEqual(status, 202)
        restored = vendor.call_args.args[1]['siteCollectItemInfo']
        self.assertEqual(restored['multiLanguageTitleMap']['en'], 'e' * 250)
        self.assertEqual(restored['title'], 'Sample product')
        message = server.AUTOMATION_POOL.get_job('e' * 32)['message']
        self.assertIn('英文标题已安全截断至 250 字符', message)

    def test_multiple_deleted_collect_box_jobs_can_be_restored_without_waiting_for_each_edit(self):
        for detail_id, job_id in ((123, 'a' * 32), (124, 'b' * 32)):
            server.AUTOMATION_POOL.put_item(detail_id, 'site', 0, sample_item())
            server.AUTOMATION_POOL.start_job(job_id, detail_id, 456, 'US', price='160.50')
            server.AUTOMATION_POOL.pause_for_problem(job_id, 'preflight', '此产品在采集箱中已被删除')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), \
             patch.object(server.AppHandler, '_vendor', side_effect=[
                 {'data': {'collectBoxDetailId': 789}}, {'data': {'collectBoxDetailId': 790}},
             ]), patch.object(server, 'Thread') as worker:
            first_status, first = self.request('POST', '/api/automation/restore-deleted', {
                'jobId': 'a' * 32, 'confirmation': 'RESTORE_DELETED_AUTOMATION_ITEM',
            })
            second_status, second = self.request('POST', '/api/automation/restore-deleted', {
                'jobId': 'b' * 32, 'confirmation': 'RESTORE_DELETED_AUTOMATION_ITEM',
            })
        self.assertEqual((first_status, second_status), (202, 202))
        self.assertEqual((first['data']['job']['status'], second['data']['job']['status']), ('queued', 'queued'))
        self.assertEqual(worker.return_value.start.call_count, 2)

    def test_multiple_collect_box_items_can_be_staged_in_one_local_batch(self):
        payload = {'items': [
            {'detailId': 123, 'mode': 'site', 'shopId': 0},
            {'detailId': 124, 'mode': 'site', 'shopId': 0},
        ]}
        status, result = self.request('POST', '/api/automation/pool/batch', payload)
        self.assertEqual(status, 200)
        self.assertEqual([item['detailId'] for item in result['data']['items']], [123, 124])
        self.assertCountEqual([item['detailId'] for item in server.automation_pool(1).list_items()], [124, 123])
        self.assertCountEqual([entry.args for entry in self.detail_mock.call_args_list], [(123, 'site', 0), (124, 'site', 0)])
        self.detail_mock.reset_mock()
        status, repeated = self.request('POST', '/api/automation/pool/batch', {
            'items': [{'detailId': 123, 'mode': 'site', 'shopId': 0}] * 2,
        })
        self.assertEqual(status, 200)
        self.assertEqual(repeated['data']['items'], [])
        self.assertEqual(len(repeated['data']['skipped']), 2)
        self.detail_mock.assert_not_called()

    def test_single_duplicate_skips_read_and_keeps_existing_data(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '原商品'})
        before = pool.get_item(123)
        status, result = self.request('POST', '/api/automation/pool', {'detailId': 123})
        self.assertEqual(status, 200)
        self.assertTrue(result['data']['skipped'])
        self.assertNotIn('item', result['data'])
        self.detail_mock.assert_not_called()
        self.assertEqual(pool.get_item(123), before)

    def test_single_pool_staging_falls_back_without_claiming_or_publishing(self):
        snapshot = {'title': '站点完整资料', 'skuMap': {'blue': {'stock': 10}}}
        self.detail_mock.side_effect = [RuntimeError('未选择预发布店铺'), snapshot]
        with patch.object(server.AppHandler, '_vendor') as vendor:
            status, result = self.request('POST', '/api/automation/pool', {'detailId': 123, 'mode': 'shop', 'shopId': 456})
            vendor.assert_not_called()
        self.assertEqual(status, 200)
        self.assertEqual((result['data']['mode'], result['data']['shopId']), ('site', 0))
        self.assertEqual(server.automation_pool(1).get_item(123)['item'], snapshot)

    def test_batch_staging_continues_after_bad_attribute_and_keeps_decimal_values(self):
        reason = 'numberValue: 期望 int, 实际 double(85.5)'
        snapshot = {'title': '正常商品', 'attributes': [{'values': [{'numberValue': 85.5}]}]}
        def read(detail_id, *args):
            if detail_id == 124:
                raise RuntimeError(reason)
            return snapshot
        self.detail_mock.side_effect = read
        status, result = self.request('POST', '/api/automation/pool/batch', {
            'items': [{'detailId': i, 'mode': 'site'} for i in (123, 124, 125)],
        })
        self.assertEqual(status, 200)
        self.assertEqual([item['detailId'] for item in result['data']['items']], [123, 125])
        self.assertEqual(result['data']['failures'], [{'detailId': 124, 'error': reason}])
        self.assertEqual(self.detail_mock.call_count, 3)
        self.assertIsNone(server.AUTOMATION_POOL.get_item(124))
        for detail_id in (123, 125):
            self.assertEqual(server.automation_pool(1).get_item(detail_id)['item'], snapshot)

    def test_batch_staging_reports_every_failure_without_adding_incomplete_items(self):
        self.detail_mock.side_effect = RuntimeError('详情读取失败')
        status, result = self.request('POST', '/api/automation/pool/batch', {
            'items': [{'detailId': i, 'mode': 'site'} for i in (123, 124)],
        })
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['items'], [])
        self.assertEqual([item['detailId'] for item in result['data']['failures']], [123, 124])
        self.assertEqual(server.AUTOMATION_POOL.list_items(), [])

    def test_batch_staging_continues_after_local_write_failure(self):
        pool = server.automation_pool(1)
        put_item = pool.put_item

        def write(detail_id, *args, **kwargs):
            if detail_id == 123:
                raise sqlite3.OperationalError('database locked')
            return put_item(detail_id, *args, **kwargs)

        with patch.object(pool, 'put_item', side_effect=write):
            status, result = self.request('POST', '/api/automation/pool/batch', {
                'items': [{'detailId': i, 'mode': 'site'} for i in (123, 124)],
            })
        self.assertEqual(status, 200)
        self.assertEqual([item['detailId'] for item in result['data']['items']], [124])
        self.assertEqual(result['data']['failures'][0]['detailId'], 123)
        self.assertNotIn('database locked', result['data']['failures'][0]['error'])

    def test_invalid_batch_is_rejected_before_any_read_or_write(self):
        status, _ = self.request('POST', '/api/automation/pool/batch', {
            'items': [{'detailId': 123}, {'detailId': 124, 'mode': 'invalid'}],
        })
        self.assertEqual(status, 400)
        self.detail_mock.assert_not_called()
        self.assertEqual(server.AUTOMATION_POOL.list_items(), [])

    def test_pool_batch_rejects_more_than_five_hundred_items_before_any_read(self):
        status, _ = self.request('POST', '/api/automation/pool/batch', {
            'items': [{'detailId': detail_id, 'mode': 'site'} for detail_id in range(1, 502)],
        })
        self.assertEqual(status, 400)
        self.detail_mock.assert_not_called()

    def test_collect_box_id_search_reads_the_matching_detail_not_source_id_filter(self):
        status, result = self.request('GET', '/api/products?collectBoxDetailId=123')
        self.assertEqual(status, 200)
        detail = result['data']['data']['detailList'][0]
        self.assertEqual(detail['collectBoxDetailId'], 123)
        self.assertEqual(detail['title'], '测试商品')
        self.assertEqual(detail['thumbnail'], 'https://example.com/p.jpg')
        self.detail_mock.assert_called_once_with(123, 'site')
        status, _ = self.request('GET', '/api/products?collectBoxDetailId=not-an-id')
        self.assertEqual(status, 400)

    def test_collect_box_list_allows_pages_beyond_the_old_100_page_limit(self):
        with patch.object(server.AppHandler, '_vendor', return_value={'data': {'detailList': []}}) as vendor:
            status, result = self.request('GET', '/api/products?page=1500')
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['data']['detailList'], [])
        vendor.assert_called_once_with(server.HALF_BOX_LIST, {'pageNo': 1500, 'pageSize': 20})

    def test_collect_box_page_summary_measures_30000_items_without_reading_every_page(self):
        total_items = 30_000
        requested_pages = []

        def vendor(path, payload):
            self.assertEqual(path, server.HALF_BOX_LIST)
            self.assertEqual(payload['pageSize'], server.COLLECTION_SUMMARY_SCAN_SIZE)
            requested_pages.append(payload['pageNo'])
            start = (payload['pageNo'] - 1) * payload['pageSize']
            size = max(0, min(payload['pageSize'], total_items - start))
            return {'data': {'detailList': [{}] * size}}

        summary = server.measure_collect_box_pagination(vendor)
        self.assertEqual(summary, {'totalItems': 30_000, 'totalPages': 1_500})
        self.assertLess(len(requested_pages), 20)

    def test_cross_origin_post_is_rejected(self):
        status, _ = self.request("POST", "/api/automation/pool", {"detailId": 123}, {"Origin": "http://example.com"})
        self.assertEqual(status, 403)

    def test_claim_writes_once_and_retry_skips_already_claimed_shop(self):
        before = {"title": "测试商品", "collectBoxDetailShopList": []}
        after = {"title": "测试商品", "collectBoxDetailShopList": [{"shopId": 456}]}
        server.AUTOMATION_POOL.put_item(123, "site", 0, before)
        self.detail_mock.side_effect = [before, after]
        payload = {"detailId": 123, "shopId": 456, "confirmation": "CLAIM_AUTOMATION_POOL"}
        with patch.dict(os.environ, {"APP_MODE": "live"}), patch.object(server.AppHandler, "_vendor", return_value={"result": "success"}) as vendor, patch.object(server.time, "sleep"):
            status, result = self.request("POST", "/api/automation/claim", payload)
            self.assertEqual(status, 200)
            self.assertFalse(result["data"]["alreadyClaimed"])
            vendor.assert_called_once_with(server.HALF_CLAIM_TO_SHOP, {"detailIds": [123], "shopIds": [456]})
            self.detail_mock.side_effect = None
            self.detail_mock.return_value = after
            status, result = self.request("POST", "/api/automation/claim", payload)
            self.assertEqual(status, 200)
            self.assertTrue(result["data"]["alreadyClaimed"])
            vendor.assert_called_once()
        self.assertEqual(server.AUTOMATION_POOL.get_item(123)["claims"][0]["status"], "claimed")

    def test_changed_item_is_claimed_without_comparing_the_snapshot_version(self):
        server.AUTOMATION_POOL.put_item(123, "site", 0, {"title": "旧标题"})
        self.detail_mock.return_value = {"title": "新标题", "collectBoxDetailShopList": []}
        payload = {"detailId": 123, "shopId": 456, "confirmation": "CLAIM_AUTOMATION_POOL"}
        after = {"title": "新标题", "collectBoxDetailShopList": [{"shopId": 456}]}
        self.detail_mock.side_effect = [{"title": "新标题", "collectBoxDetailShopList": []}, after]
        with patch.dict(os.environ, {"APP_MODE": "live"}), patch.object(server.AppHandler, "_vendor", return_value={"result": "success"}) as vendor, patch.object(server.time, "sleep"):
            status, _ = self.request("POST", "/api/automation/claim", payload)
            self.assertEqual(status, 200)
            vendor.assert_called_once_with(server.HALF_CLAIM_TO_SHOP, {"detailIds": [123], "shopIds": [456]})
        self.assertEqual(server.AUTOMATION_POOL.get_item(123)["claims"][0]["status"], "claimed")

    def test_shop_mode_snapshot_does_not_trigger_an_extra_version_read_before_claim(self):
        shop_view = {"title": "店铺视图", "collectBoxDetailShopList": []}
        site_view = {"title": "站点视图", "collectBoxDetailShopList": []}
        after = {"title": "站点视图", "collectBoxDetailShopList": [{"shopId": 456}]}
        server.AUTOMATION_POOL.put_item(123, "shop", 789, shop_view)
        self.detail_mock.side_effect = [site_view, after]
        payload = {"detailId": 123, "shopId": 456, "confirmation": "CLAIM_AUTOMATION_POOL"}
        with patch.dict(os.environ, {"APP_MODE": "live"}), patch.object(server.AppHandler, "_vendor", return_value={"result": "success"}) as vendor, patch.object(server.time, "sleep"):
            status, _ = self.request("POST", "/api/automation/claim", payload)
            self.assertEqual(status, 200)
            vendor.assert_called_once()
        self.assertEqual(self.detail_mock.call_args_list, [
            call(123, "site"), call(123, "site"),
        ])

    def test_rate_limit_is_reported_without_retrying_write(self):
        item = {"title": "测试商品", "collectBoxDetailShopList": []}
        server.AUTOMATION_POOL.put_item(123, "site", 0, item)
        self.detail_mock.return_value = item
        payload = {"detailId": 123, "shopId": 456, "confirmation": "CLAIM_AUTOMATION_POOL"}
        with patch.dict(os.environ, {"APP_MODE": "live"}), patch.object(server.AppHandler, "_vendor", side_effect=RuntimeError("账户接口每秒请求频率超限")) as vendor:
            status, _ = self.request("POST", "/api/automation/claim", payload)
            self.assertEqual(status, 429)
            vendor.assert_called_once()

    def test_demo_mode_and_missing_confirmation_never_write(self):
        server.AUTOMATION_POOL.put_item(123, "site", 0, {"title": "测试商品"})
        payload = {"detailId": 123, "shopId": 456, "confirmation": "CLAIM_AUTOMATION_POOL"}
        with patch.dict(os.environ, {"APP_MODE": "demo"}), patch.object(server.AppHandler, "_vendor") as vendor:
            status, _ = self.request("POST", "/api/automation/claim", payload)
            self.assertEqual(status, 409)
            vendor.assert_not_called()
        with patch.dict(os.environ, {"APP_MODE": "live"}), patch.object(server.AppHandler, "_vendor") as vendor:
            status, _ = self.request("POST", "/api/automation/claim", {"detailId": 123, "shopId": 456})
            self.assertEqual(status, 400)
            vendor.assert_not_called()

    def test_automatic_run_requires_confirmation_and_exposes_persisted_progress(self):
        server.AUTOMATION_POOL.put_item(123, "site", 0, {
            "title": "测试商品", "imgUrls": ["https://example.com/p.jpg"],
        })
        payload = {"detailId": 123, "shopId": 456, "site": "US"}
        with patch.dict(os.environ, {"APP_MODE": "live"}), \
             patch.object(server.DeepSeekClient, "configured", new_callable=PropertyMock, return_value=True), \
             patch.object(server, "Thread") as worker:
            status, _ = self.request("POST", "/api/automation/run", payload)
            self.assertEqual(status, 400)
            worker.assert_not_called()
            status, result = self.request("POST", "/api/automation/run", {
                **payload, "confirmation": "AUTOMATE_AND_PUBLISH_ONE",
            })
            self.assertEqual(status, 202)
            worker.return_value.start.assert_called_once()
            job_id = result["data"]["jobId"]
            status, jobs = self.request("GET", "/api/automation/jobs")
            self.assertEqual(status, 200)
            self.assertEqual(jobs["data"]["jobs"][0]["jobId"], job_id)
            self.assertEqual(jobs["data"]["jobs"][0]["thumbnail"], "https://example.com/p.jpg")
            status, job = self.request("GET", f"/api/automation/jobs/{job_id}")
            self.assertEqual(status, 200)
            self.assertEqual(job["data"]["events"][0]["step"], "queued")
            status, _ = self.request("POST", "/api/automation/run", {
                **payload, "confirmation": "AUTOMATE_AND_PUBLISH_ONE",
            })
            self.assertEqual(status, 400)
            worker.return_value.start.assert_called_once()

    def test_batch_run_queues_multiple_items_with_a_configurable_interval(self):
        for detail_id in (123, 124):
            server.AUTOMATION_POOL.put_item(detail_id, 'site', 0, {
                'title': f'测试商品 {detail_id}', 'imgUrls': ['https://example.com/p.jpg'],
            })
        payload = {'detailIds': [123, 124], 'shopId': 456, 'site': 'US', 'price': '99.99', 'intervalSeconds': 15}
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, _ = self.request('POST', '/api/automation/batch-run', payload)
            self.assertEqual(status, 400)
            worker.assert_not_called()
            status, result = self.request('POST', '/api/automation/batch-run', {
                **payload, 'confirmation': 'AUTOMATE_AND_PUBLISH_BATCH',
            })
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['intervalSeconds'], 15)
            self.assertEqual([job['status'] for job in result['data']['jobs']], ['queued', 'queued'])
            prices = [Decimal(job['price']) for job in result['data']['jobs']]
            self.assertTrue(all(Decimal('99.99') <= price <= Decimal('119.99') for price in prices))
            self.assertTrue(all(len(job['price'].split('.')[1]) == 2 for job in result['data']['jobs']))
            worker.return_value.start.assert_called_once()
        status, _ = self.request('DELETE', '/api/automation/pool/batch', {
            'detailIds': [123, 124], 'confirmation': 'REMOVE_AUTOMATION_POOL_BATCH',
        })
        self.assertEqual(status, 400)

    def test_price_floor_generates_two_decimal_values_within_twenty_yuan(self):
        prices = [server._randomize_automation_price('160.56') for _ in range(20)]
        self.assertTrue(all(Decimal('160.56') <= Decimal(price) <= Decimal('180.56') for price in prices))
        self.assertTrue(all(len(price.split('.')[1]) == 2 for price in prices))
        with self.assertRaisesRegex(ValueError, '999980'):
            server._randomize_automation_price('999980.01')

    def test_source_price_multiplier_uses_each_sku_price(self):
        item = {'skuMap': {
            'first': {'price': '100'},
            'second': {'price': '88.50'},
        }}
        self.assertEqual(server._source_sku_price_multiplier_declarations(item, '1.5'), {
            'first': '150.00', 'second': '132.75',
        })
        with self.assertRaisesRegex(ValueError, '有效的妙手原始价格'):
            server._source_sku_price_multiplier_declarations({'skuMap': {'first': {'price': '0'}}}, '1.5')
        with self.assertRaisesRegex(ValueError, '最多保留四位小数'):
            server._source_sku_price_multiplier_declarations(item, '1.00001')

    def test_batch_run_can_use_source_price_multiplier(self):
        server.AUTOMATION_POOL.put_item(123, 'site', 0, {
            'title': '测试商品', 'skuMap': {'first': {'price': '100'}},
        })
        payload = {
            'detailIds': [123], 'shopId': 456, 'site': 'US',
            'priceMode': 'source_multiplier', 'priceMultiplier': '1.5',
            'confirmation': 'AUTOMATE_AND_SAVE_BATCH',
        }
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, result = self.request('POST', '/api/automation/batch-run', payload)
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['priceMode'], 'source_multiplier')
            self.assertEqual(result['data']['jobs'][0]['price'], '150.00')
            self.assertEqual(result['data']['jobs'][0]['skuPrices'], {'first': '150.00'})
            worker.return_value.start.assert_called_once()

    def test_batch_removal_only_changes_local_pool(self):
        for detail_id in (123, 124):
            server.AUTOMATION_POOL.put_item(detail_id, 'site', 0, {'title': f'测试商品 {detail_id}'})
        status, result = self.request('DELETE', '/api/automation/pool/batch', {
            'detailIds': [123, 124], 'confirmation': 'REMOVE_AUTOMATION_POOL_BATCH',
        })
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['detailIds'], [123, 124])
        self.assertEqual(server.AUTOMATION_POOL.list_items(), [])

    def test_paused_job_can_be_resumed_with_a_valid_choice_or_canceled(self):
        server.AUTOMATION_POOL.put_item(123, 'site', 0, {'title': '测试商品'})
        server.AUTOMATION_POOL.start_job('f' * 32, 123, 456, 'US')
        server.AUTOMATION_POOL.pause_for_choice('f' * 32, 'warehouse', [{'id': '88', 'name': '备用仓'}])
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, _ = self.request('POST', '/api/automation/resume', {
                'jobId': 'f' * 32, 'selectedId': 'wrong', 'confirmation': 'RESUME_AUTOMATION_JOB',
            })
            self.assertEqual(status, 400)
            worker.assert_not_called()
            status, result = self.request('POST', '/api/automation/resume', {
                'jobId': 'f' * 32, 'selectedId': '88', 'confirmation': 'RESUME_AUTOMATION_JOB',
            })
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['choices']['warehouse']['selectedId'], '88')
            worker.return_value.start.assert_called_once()
        server.AUTOMATION_POOL.pause_for_problem('f' * 32, 'preflight', '店铺暂不可用')
        status, result = self.request('POST', '/api/automation/cancel', {
            'jobId': 'f' * 32, 'confirmation': 'CANCEL_PAUSED_AUTOMATION',
        })
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['status'], 'canceled')

    def test_resume_launches_independent_workers_and_recovers_launch_failure(self):
        pool = server.AUTOMATION_POOL
        for detail_id, job_id in ((123, 'a' * 32), (124, 'b' * 32), (125, 'c' * 32)):
            pool.put_item(detail_id, 'site', 0, {'title': '商品'})
            pool.start_job(job_id, detail_id, 456, 'US')
            pool.pause_for_problem(job_id, 'prepare', '缺少图片')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            for job_id in ('a' * 32, 'b' * 32):
                status, body = self.request('POST', '/api/automation/resume', {
                    'jobId': job_id, 'confirmation': 'RESUME_AUTOMATION_JOB'})
                self.assertEqual(status, 202)
                self.assertFalse(body['data']['publishAfterSave'])
            self.assertEqual(worker.return_value.start.call_count, 2)
            status, _ = self.request('POST', '/api/automation/resume', {
                'jobId': 'a' * 32, 'confirmation': 'RESUME_AUTOMATION_JOB'})
            self.assertEqual(status, 400)
            self.assertEqual(worker.return_value.start.call_count, 2)
            worker.return_value.start.side_effect = RuntimeError('cannot start')
            status, _ = self.request('POST', '/api/automation/resume', {
                'jobId': 'c' * 32, 'confirmation': 'RESUME_AUTOMATION_JOB'})
            self.assertEqual(status, 500)
            self.assertEqual(pool.get_job('c' * 32)['status'], 'needs_input')


    def test_delete_job_record_requires_terminal_status_and_same_origin(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        job_id = 'a' * 32
        pool.start_job(job_id, 123, 456, 'US')
        path = f'/api/automation/jobs/{job_id}'
        with patch.object(server.AppHandler, '_vendor') as vendor:
            status, _ = self.request('DELETE', path, headers={'Origin': 'http://example.com'})
            self.assertEqual(status, 403)
            status, _ = self.request('DELETE', '/api/automation/jobs/not-a-job-id')
            self.assertEqual(status, 400)
            status, _ = self.request('DELETE', path)
            self.assertEqual(status, 409)
            pool.update_job(job_id, 'complete', 'done', '已提交')
            status, result = self.request('DELETE', path)
            self.assertEqual(status, 200)
            self.assertTrue(result['data']['deleted'])
            status, listed = self.request('GET', '/api/automation/jobs')
            self.assertEqual(status, 200)
            self.assertEqual(listed['data']['jobs'], [])
            status, _ = self.request('GET', path)
            self.assertEqual(status, 404)
            status, _ = self.request('DELETE', path)
            self.assertEqual(status, 404)
            vendor.assert_not_called()

    def test_batch_delete_only_hides_finished_task_records(self):
        pool = server.AUTOMATION_POOL
        for detail_id, job_id in ((123, 'e' * 32), (124, 'f' * 32)):
            pool.put_item(detail_id, 'site', 0, {'title': f'测试商品 {detail_id}'})
            pool.start_job(job_id, detail_id, 456, 'US')
            pool.update_job(job_id, 'complete', 'done', '已提交')
        path = '/api/automation/jobs/batch'
        payload = {'jobIds': ['e' * 32, 'f' * 32], 'confirmation': 'DELETE_AUTOMATION_JOB_RECORDS'}
        status, result = self.request('DELETE', path, payload)
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['deleted'], 2)
        self.assertEqual(pool.list_jobs(), [])

    def test_stop_and_clear_tasks_requires_confirmation_and_preserves_local_goods(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        pool.put_item(124, 'site', 0, {'title': '待发布商品'})
        pool.start_job('a' * 32, 123, 456, 'US')
        pool.update_job('a' * 32, 'publish', 'ready_publish', '等待发布')
        pool.start_job('b' * 32, 124, 456, 'US')
        path = '/api/automation/jobs/clear'

        self.assertEqual(self.request('POST', path, {'confirmation': 'STOP_AND_CLEAR_AUTOMATION_JOBS'},
                                      {'Origin': 'http://example.com'})[0], 403)
        self.assertEqual(self.request('POST', path, {})[0], 400)
        status, result = self.request('POST', path, {'confirmation': 'STOP_AND_CLEAR_AUTOMATION_JOBS'})

        self.assertEqual(status, 200)
        self.assertEqual(result['data'], {'cleared': 1, 'stopped': 1})
        self.assertEqual([job['jobId'] for job in pool.list_jobs()], ['a' * 32])
        self.assertEqual([job['jobId'] for job in pool.ready_publish_jobs()], ['a' * 32])
        self.assertIsNotNone(pool.get_item(123))

    def test_cancel_one_edit_task_rejects_publish_queue_and_requires_confirmation(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        pool.start_job('a' * 32, 123, 456, 'US')
        path = '/api/automation/cancel-edit'
        self.assertEqual(self.request('POST', path, {'jobId': 'a' * 32,
                                                      'confirmation': 'CANCEL_EDIT_AUTOMATION_JOB'},
                                      {'Origin': 'http://example.com'})[0], 403)
        self.assertEqual(self.request('POST', path, {'jobId': 'a' * 32})[0], 400)
        status, result = self.request('POST', path, {'jobId': 'a' * 32,
                                                      'confirmation': 'CANCEL_EDIT_AUTOMATION_JOB'})
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['status'], 'canceled')
        self.assertEqual(self.request('POST', path, {'jobId': 'a' * 32,
                                                      'confirmation': 'CANCEL_EDIT_AUTOMATION_JOB'})[0], 400)

    def test_duplicate_publication_creates_a_new_task_without_extra_confirmation(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        previous = 'b' * 32
        pool.start_job(previous, 123, 456, 'US')
        pool.update_job(previous, 'save', 'running', '正在保存')
        pool.update_job(previous, 'save', 'failed', '结果不确定')
        payload = {'detailId': 123, 'shopId': 456, 'site': 'US',
                   'confirmation': 'AUTOMATE_AND_PUBLISH_ONE'}
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, result = self.request('POST', '/api/automation/run', payload)
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['events'][0]['message'], '任务已创建')
            worker.return_value.start.assert_called_once()

    def test_retry_starts_save_only_worker_and_rejects_duplicate_requests(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        job_id = 'a' * 32
        pool.start_job(job_id, 123, 456, 'US', price='161.23')
        pool.update_job(job_id, 'save', 'needs_review', '属性缺失')
        path = '/api/automation/retry'
        payload = {'jobId': job_id, 'confirmation': 'RETRY_AUTOMATION_SAVE'}
        with patch.dict(os.environ, {'APP_MODE': 'demo'}):
            self.assertEqual(self.request('POST', path, payload)[0], 409)
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            self.assertEqual(self.request('POST', path, payload, {'Origin': 'https://example.com'})[0], 403)
            self.assertEqual(self.request('POST', path, {'jobId': job_id})[0], 400)
            status, result = self.request('POST', path, payload)
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['price'], '161.23')
            self.assertFalse(result['data']['publishAfterSave'])
            worker.return_value.start.assert_called_once()
            invocation = worker.call_args.kwargs
            self.assertIs(invocation['target'], server.run_automation_job_then_continue_queue)
            with patch.object(server, 'run_automation_job') as edit:
                invocation['target'](*invocation['args'])
                self.assertTrue(edit.call_args.kwargs['defer_publish'])
                self.assertFalse(edit.call_args.kwargs['force_publish'])
            self.assertEqual(self.request('POST', path, payload)[0], 400)
            worker.return_value.start.assert_called_once()

    def test_retry_thread_start_failure_leaves_task_retryable(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        job_id = 'b' * 32
        pool.start_job(job_id, 123, 456, 'US')
        pool.update_job(job_id, 'save', 'failed', '失败')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('no threads')
            status, _ = self.request('POST', '/api/automation/retry', {
                'jobId': job_id, 'confirmation': 'RETRY_AUTOMATION_SAVE',
            })
            self.assertEqual(status, 500)
            self.assertEqual(pool.get_job(job_id)['status'], 'failed')

    def test_force_publish_requires_explicit_confirmation_and_reviewed_job(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        job_id = 'c' * 32
        pool.start_job(job_id, 123, 456, 'US')
        path = '/api/automation/force-publish'
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, _ = self.request('POST', path, {'jobId': job_id})
            self.assertEqual(status, 400)
            status, _ = self.request('POST', path, {
                'jobId': job_id, 'confirmation': 'FORCE_OVERWRITE_AND_PUBLISH',
            })
            self.assertEqual(status, 400)
            pool.update_job(job_id, 'verify', 'needs_review', '库存读回不一致')
            status, result = self.request('POST', path, {
                'jobId': job_id, 'confirmation': 'FORCE_OVERWRITE_AND_PUBLISH',
            })
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['status'], 'running')
            worker.return_value.start.assert_called_once()

    def test_publish_queue_requires_explicit_release_of_saved_jobs(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '测试商品'})
        job_id = 'd' * 32
        pool.start_job(job_id, 123, 456, 'US', publish_after_save=False, interval_seconds=12)
        pool.update_job(job_id, 'publish', 'ready_publish', '等待统一发布')
        path = '/api/automation/publish-ready'
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, _ = self.request('POST', path, {})
            self.assertEqual(status, 400)
            status, result = self.request('POST', path, {
                'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS',
            })
            self.assertEqual(status, 202)
            self.assertEqual(result['data']['jobs'][0]['jobId'], job_id)
            self.assertEqual(result['data']['publishProgress']['total'], 1)
            self.assertEqual(result['data']['publishProgress']['status'], 'running')
            status, refreshed = self.request('GET', '/api/automation/jobs')
            self.assertEqual(status, 200)
            self.assertEqual(refreshed['data']['publishProgress'], result['data']['publishProgress'])
            worker.return_value.start.assert_called_once()
        if server._automation_publish_lock.locked():
            server._automation_publish_lock.release()

    def test_publish_queue_releases_only_requested_ready_count_while_other_edits_continue(self):
        pool = server.AUTOMATION_POOL
        ready_ids = []
        for detail in (123, 124, 125):
            job_id = f'{detail:032x}'
            ready_ids.append(job_id)
            pool.put_item(detail, 'site', 0, {'title': f'商品 {detail}'})
            pool.start_job(job_id, detail, 456, 'US', publish_after_save=False)
            pool.update_job(job_id, 'publish', 'ready_publish', '等待发布')
        pool.put_item(126, 'site', 0, {'title': '仍在编辑的商品'})
        pool.start_job('e' * 32, 126, 456, 'US', publish_after_save=False)
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, result = self.request('POST', '/api/automation/publish-ready', {
                'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS', 'publishCount': 2,
            })
        self.assertEqual(status, 202)
        self.assertEqual([job['jobId'] for job in result['data']['jobs']], ready_ids[:2])
        self.assertEqual(result['data']['publishProgress']['total'], 2)
        self.assertEqual([job['jobId'] for job in pool.ready_publish_jobs()], ready_ids)
        worker.return_value.start.assert_called_once()
        pool.finish_publish_batch(result['data']['publishProgress']['batchId'])
        if server._automation_publish_lock.locked():
            server._automation_publish_lock.release()

    def test_publish_queue_can_release_only_the_selected_store(self):
        pool = server.AUTOMATION_POOL
        jobs = []
        for detail, shop_id in ((123, 456), (124, 789), (125, 456)):
            job_id = f'{detail:032x}'
            jobs.append(job_id)
            pool.put_item(detail, 'site', 0, {'title': f'商品 {detail}'})
            pool.start_job(job_id, detail, shop_id, 'US', publish_after_save=False)
            pool.update_job(job_id, 'publish', 'ready_publish', '等待发布')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            status, result = self.request('POST', '/api/automation/publish-ready', {
                'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS', 'shopId': 456,
            })
        self.assertEqual(status, 202)
        self.assertEqual(result['data']['shopId'], 456)
        self.assertEqual([job['jobId'] for job in result['data']['jobs']], [jobs[0], jobs[2]])
        self.assertEqual([job['jobId'] for job in pool.ready_publish_jobs(789)], [jobs[1]])
        self.assertEqual(result['data']['publishProgress']['total'], 2)
        worker.return_value.start.assert_called_once()
        pool.finish_publish_batch(result['data']['publishProgress']['batchId'])
        if server._automation_publish_lock.locked():
            server._automation_publish_lock.release()

    def test_publish_multiple_shops_filters_before_combined_count_and_rejects_invalid_selection(self):
        pool = server.AUTOMATION_POOL
        for detail, shop_id in ((123, 456), (124, 999), (125, 789), (126, 456)):
            job_id = f'{detail:032x}'
            pool.put_item(detail, 'site', 0, {'title': f'商品 {detail}'})
            pool.start_job(job_id, detail, shop_id, 'US', publish_after_save=False)
            pool.update_job(job_id, 'publish', 'ready_publish', '等待发布')
        payload = {'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS'}
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            for ids in ([], '456', [True], [0], [-1], ['456'], [1.5], {}):
                self.assertEqual(self.request('POST', '/api/automation/publish-ready', {**payload, 'shopIds': ids})[0], 400)
            worker.assert_not_called()
            status, result = self.request('POST', '/api/automation/publish-ready', {
                **payload, 'shopIds': [456, 789, 456], 'publishCount': 2})
        try:
            self.assertEqual(status, 202)
            self.assertEqual([job['detailId'] for job in result['data']['jobs']], [123, 125])
            self.assertEqual(result['data']['shopIds'], [456, 789])
            self.assertEqual(result['data']['publishProgress']['total'], 2)
            self.assertEqual(len(pool.ready_publish_jobs(999)), 1)
            worker.return_value.start.assert_called_once()
        finally:
            if pool.publish_progress():
                pool.finish_publish_batch(pool.publish_progress()['batchId'])
            if server._automation_publish_lock.locked():
                server._automation_publish_lock.release()

    def test_publish_thread_start_failure_releases_lock_and_preserves_queue(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '商品'})
        pool.start_job('d' * 32, 123, 456, 'US', publish_after_save=False)
        pool.update_job('d' * 32, 'publish', 'ready_publish', '等待统一发布')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('cannot start thread')
            status, _ = self.request('POST', '/api/automation/publish-ready', {
                'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS', 'intervalSeconds': 3})
        self.assertEqual(status, 500)
        self.assertFalse(server._automation_publish_lock.locked())
        self.assertEqual(pool.publish_progress()['status'], 'interrupted')
        self.assertEqual(pool.publish_progress()['remaining'], 1)
        self.assertEqual(len(pool.ready_publish_jobs()), 1)

    def test_publish_progress_read_failure_before_start_does_not_leave_active_batch(self):
        pool = server.AUTOMATION_POOL
        pool.put_item(123, 'site', 0, {'title': '商品'})
        pool.start_job('d' * 32, 123, 456, 'US', publish_after_save=False)
        pool.update_job('d' * 32, 'publish', 'ready_publish', '等待统一发布')
        with patch.dict(os.environ, {'APP_MODE': 'live'}), patch.object(server, 'Thread') as worker, \
             patch.object(pool, 'publish_progress', side_effect=sqlite3.OperationalError('read failed')):
            status, _ = self.request('POST', '/api/automation/publish-ready', {
                'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS', 'intervalSeconds': 3})
            worker.assert_not_called()
        self.assertEqual(status, 500)
        self.assertFalse(server._automation_publish_lock.locked())
        self.assertEqual(pool.publish_progress()['status'], 'interrupted')
        self.assertEqual(len(pool.ready_publish_jobs()), 1)


if __name__ == "__main__":
    unittest.main()
