import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from automation_pool import AutomationPool
from test_item_editor import sample_item
from test_automation_workflow import FakeAI


class MultiShopClaimTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pool = AutomationPool(Path(directory.name) / 'pool.sqlite3')
        self.pool.put_item(777, 'site', 0, sample_item())
        self.jobs = self.pool.queue_jobs([777], [111, 222], 'US')
        self.writes = []
        self.visible = {44}
        self.ready = {44}
        self.delayed = set()

    def vendor(self, path, payload):
        self.assertEqual(path, server.HALF_CLAIM_TO_SHOP)
        self.writes.append(copy.deepcopy(payload))
        # Model the destructive replacement semantics that triggered the bug.
        self.ready = set(payload['shopIds'])
        return {'data': {}}

    def get_item(self, detail_id, mode, shop_id=0):
        self.assertEqual(detail_id, 777)
        if mode == 'site':
            return {'collectBoxDetailShopList': [{'shopId': sid} for sid in self.visible]}
        if shop_id not in self.ready or shop_id in self.delayed:
            raise RuntimeError('未选择预发布店铺')
        return {'shopId': shop_id, 'title': '商品'}

    def claim(self, shop_id):
        return server.ensure_automation_shop_claim(self.pool, 777, shop_id, self.vendor,
                                                  self.get_item, lambda: None, lambda _: None)

    def test_queued_siblings_and_existing_shops_are_claimed_together_once(self):
        with patch.object(server.time, 'sleep'):
            self.assertEqual(self.claim(111)['shopId'], 111)
            self.assertEqual(self.claim(222)['shopId'], 222)
        self.assertEqual(self.writes, [{'detailIds': [777], 'shopIds': [44, 111, 222]}])

    def test_visible_claim_is_not_enough_and_delayed_store_does_not_block_sibling(self):
        self.visible |= {111, 222}
        self.ready |= {111, 222}
        self.delayed.add(111)
        with patch.object(server.time, 'sleep'):
            with self.assertRaises(server.AutomationClaimPending):
                self.claim(111)
            self.assertEqual(self.claim(222)['shopId'], 222)
        self.assertEqual(self.writes, [])

    def test_retry_preserves_prior_submission_when_site_list_lags(self):
        self.delayed.add(111)
        with patch.object(server.time, 'sleep'):
            with self.assertRaises(server.AutomationClaimPending):
                self.claim(111)
            self.delayed.clear()
            self.claim(111)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.ready, {44, 111, 222})
        # After the confirmation grace period, even a re-claim retains every
        # already submitted store rather than shrinking to the retrying one.
        self.pool.claim_state(777)['submittedAt'] -= 301
        self.claim(222)
        self.assertEqual(self.writes[-1]['shopIds'], [44, 111, 222])

    def test_cancel_and_unrelated_errors_are_not_retried_as_claim_delays(self):
        def canceled():
            raise server.AutomationJobCanceled('取消')
        with self.assertRaises(server.AutomationJobCanceled):
            server.ensure_automation_shop_claim(self.pool, 777, 111, self.vendor,
                                                self.get_item, canceled, lambda _: None)
        self.assertEqual(self.writes, [])
        self.visible |= {111, 222}
        def denied(detail_id, mode, shop_id=0):
            if mode == 'shop':
                raise RuntimeError('无接口权限')
            return self.get_item(detail_id, mode, shop_id)
        with patch.object(server.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '无接口权限'):
                server.ensure_automation_shop_claim(self.pool, 777, 111, self.vendor,
                                                    denied, lambda: None, lambda _: None)
            sleep.assert_not_called()

    def test_expired_coordination_does_not_restore_unrelated_old_store(self):
        state = self.pool.claim_state(777)
        state['submitted'] = {333}
        state['submittedAt'] = server.time.monotonic() - 301
        self.claim(111)
        self.assertEqual(self.writes[-1]['shopIds'], [44, 111, 222])

    def test_canceled_queued_store_is_not_added_to_new_claim(self):
        job = next(j for j in self.jobs if j['shopId'] == 222)
        self.pool.cancel_edit_job(job['jobId'])
        self.claim(111)
        self.assertEqual(self.writes[-1]['shopIds'], [44, 111])

    def test_parallel_edits_use_one_claim_and_keep_independent_shop_configuration(self):
        source = sample_item()
        barrier = threading.Barrier(2)
        saved = {}
        reads = {111: 0, 222: 0}

        def get_item(detail_id, mode, shop_id=0):
            if mode == 'site':
                # Site listing intentionally remains stale throughout the test.
                return {**copy.deepcopy(source), **self.get_item(detail_id, mode)}
            reads[shop_id] += 1
            if reads[shop_id] < 3:
                raise RuntimeError('未选择预发布店铺')
            return {**copy.deepcopy(source), **self.get_item(detail_id, mode, shop_id)}

        def vendor(path, payload):
            if path == server.HALF_CLAIM_TO_SHOP:
                return self.vendor(path, payload)
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 111}, {'shopId': 222}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                sid = payload['shopId']
                return {'data': {'shopIdAndFreightTemplateListMap': {str(sid): [
                    {'site': 'US', 'templateName': '通用', 'templateId': sid + 1}]}}}
            if path == server.HALF_WAREHOUSE:
                sid = payload['shopId']
                return {'data': {'shopIdAndWarehouseListMap': {str(sid): [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': sid + 2}]}}}
            if path == server.HALF_CATEGORY_ATTRIBUTE_RULES:
                return {'data': {'productAttributeRules': []}}
            if path == server.HALF_SHOP_SAVE:
                item = copy.deepcopy(payload['shopCollectItemInfo'])
                saved[int(item['shopId'])] = item
                barrier.wait(timeout=5)  # Fails if editing is accidentally serialized.
                return {'data': {}}
            raise AssertionError(f'Unexpected API: {path}')

        with patch.object(server, 'miao_request', side_effect=lambda call, account_id=None, **kwargs: call()), patch.object(server.time, 'sleep'):
            server.run_queued_automation_jobs(self.pool, vendor, get_item, lambda *_: {'ready': True},
                                              FakeAI(), edit_concurrency=2)
        self.assertEqual([j['status'] for j in self.pool.list_jobs()], ['ready_publish', 'ready_publish'])
        self.assertEqual(len(self.writes), 1)
        for sid in (111, 222):
            self.assertEqual(str(saved[sid]['siteFreightTemplateList'][0]['freightTemplateId']), str(sid + 1))
            self.assertEqual(str(saved[sid]['siteWarehouseList'][0]['warehouseId']), str(sid + 2))

    def test_claim_timeout_is_not_force_overridden_or_saved(self):
        self.delayed.add(111)
        active = self.pool.start_next_queued_jobs(1)[0]
        with patch.object(server, 'miao_request', side_effect=lambda call, account_id=None, **kwargs: call()), patch.object(server.time, 'sleep'):
            server.run_automation_job(self.pool, active['jobId'], self.vendor, self.get_item,
                                      lambda *_: {'ready': True}, FakeAI(), defer_publish=True)
        job = self.pool.get_job(active['jobId'])
        self.assertEqual(job['status'], 'needs_review')
        self.assertEqual(job['autoOverrideAttempts'], 0)
        self.assertEqual(len(self.writes), 1)
