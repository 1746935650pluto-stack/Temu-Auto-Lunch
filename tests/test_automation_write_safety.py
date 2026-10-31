"""Unknown write outcomes must not be replayed by retry or restart recovery."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from automation_pool import AutomationPool
from account_runtime import failure_flags, has_unknown_write_outcome
from test_automation_workflow import FakeAI
from test_item_editor import sample_item


class AutomationWriteSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pool = AutomationPool(Path(directory.name) / 'pool.sqlite3')
        self.source = sample_item()
        self.source['collectBoxDetailShopList'] = [{'shopId': 1234}]
        self.pool.put_item(777, 'site', 0, self.source)
        self.calls = []

    def vendor(self, path, payload):
        self.calls.append(path)
        data = {}
        if path == server.SHOP_LIST:
            data = {'shopList': [{'shopId': 1234}]}
        elif path == server.HALF_SITES:
            data = {'publishSiteOptions': [{'key': 'US'}]}
        elif path == server.HALF_FREIGHT:
            data = {'shopIdAndFreightTemplateListMap': {'1234': [
                {'site': 'US', 'templateName': '通用', 'templateId': 11}]}}
        elif path == server.HALF_WAREHOUSE:
            data = {'shopIdAndWarehouseListMap': {'1234': [
                {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}
        return {'data': data}

    def get_item(self, *_args):
        return copy.deepcopy(self.source)

    def run_job(self, vendor, suffix='a', ai=None):
        job_id = suffix * 32
        self.pool.start_job(job_id, 777, 1234, 'US', publish_after_save=False)
        server.run_automation_job(self.pool, job_id, vendor, self.get_item,
                                  lambda *_: {'ready': True}, ai or FakeAI(), defer_publish=True)
        return self.pool.get_job(job_id)

    def test_save_gateway_failure_is_submitted_once_and_stays_in_review_after_recovery(self):
        def vendor(path, payload):
            result = self.vendor(path, payload)
            if path == server.HALF_SHOP_SAVE:
                raise RuntimeError('MiaoShou returned HTTP 504: httpGatewayTimeout')
            return result
        with patch.object(server.time, 'sleep'):
            result = self.run_job(vendor)
        self.assertEqual(self.calls.count(server.HALF_SHOP_SAVE), 1)
        self.assertEqual((result['status'], result['autoOverrideAttempts']), ('needs_review', 0))
        self.assertNotIn(server.HALF_PUBLISH, self.calls)
        self.assertEqual(self.pool.claim_pending_auto_overrides(), [])

    def test_claim_gateway_failure_does_not_claim_twice_or_start_save(self):
        self.source['collectBoxDetailShopList'] = []
        def vendor(path, payload):
            result = self.vendor(path, payload)
            if path == server.HALF_CLAIM_TO_SHOP:
                raise RuntimeError('MiaoShou returned HTTP 504: httpGatewayTimeout')
            return result
        with patch.object(server.time, 'sleep'):
            result = self.run_job(vendor)
        self.assertEqual(self.calls.count(server.HALF_CLAIM_TO_SHOP), 1)
        self.assertEqual((result['status'], result['step']), ('needs_review', 'claim'))
        self.assertNotIn(server.HALF_SHOP_SAVE, self.calls)

    def test_publish_gateway_failure_is_submitted_once_and_not_auto_recovered(self):
        job_id = 'p' * 32
        self.pool.start_job(job_id, 777, 1234, 'US', publish_after_save=False)
        self.pool.update_job(job_id, 'publish', 'ready_publish', 'saved')
        self.pool.start_next_ready_publish_job()
        def vendor(path, payload):
            self.calls.append(path)
            raise RuntimeError('MiaoShou returned HTTP 504: httpGatewayTimeout')
        with patch.object(server.time, 'sleep'):
            server.publish_automation_job(self.pool, job_id, vendor)
        self.assertEqual(self.calls, [server.HALF_PUBLISH])
        self.assertEqual(self.pool.get_job(job_id)['status'], 'needs_review')
        self.assertEqual(self.pool.claim_pending_auto_overrides(), [])

    def test_definite_validation_rejection_has_one_bounded_retry(self):
        def vendor(path, payload):
            result = self.vendor(path, payload)
            if path == server.HALF_SHOP_SAVE and self.calls.count(path) == 1:
                raise RuntimeError('groupIdAndValueMap is required')
            return result
        result = self.run_job(vendor)
        self.assertEqual(self.calls.count(server.HALF_SHOP_SAVE), 2)
        self.assertEqual((result['status'], result['autoOverrideAttempts']), ('ready_publish', 1))
        self.assertNotIn(server.HALF_PUBLISH, self.calls)

    def test_second_failure_after_explicit_rejection_is_not_replayed(self):
        def vendor(path, payload):
            result = self.vendor(path, payload)
            if path == server.HALF_SHOP_SAVE:
                raise RuntimeError('groupIdAndValueMap is required' if self.calls.count(path) == 1 else 'connection reset')
            return result
        result = self.run_job(vendor)
        self.assertEqual(self.calls.count(server.HALF_SHOP_SAVE), 2)
        self.assertEqual((result['status'], result['autoOverrideAttempts']), ('needs_review', 1))
        self.assertEqual(self.pool.claim_pending_auto_overrides(), [])

    def attribute_fixture(self, error_factory):
        self.source['title'] = '成人帽子'
        self.source['attributes'] = []
        self.pool.put_item(777, 'site', 0, self.source)
        rule = {'name': '适用人群', 'pid': 1, 'templatePid': 101, 'refPid': 1,
                'valueType': 'SELECT', 'required': False, 'chooseMaxNum': 1,
                'values': [{'vid': 10, 'name': '18+'}, {'vid': 20, 'name': '3+'}]}
        ai = FakeAI()
        ai.infer_attribute = Mock(return_value={'uncertain': False, 'values': [{'vid': 10}], 'evidence': '成人帽子'})

        def vendor(path, payload):
            result = self.vendor(path, payload)
            if path == server.HALF_CATEGORY_ATTRIBUTE_RULES:
                return {'data': {'productAttributeRules': [rule]}}
            if path == server.HALF_CATEGORIES:
                return {'data': {'cateTree': {'123': {'cid': '123', 'nameChinese': '帽子'}}}}
            if path == server.HALF_SHOP_SAVE:
                error = error_factory(payload)
                if error is not None:
                    raise error
            return result
        return vendor, ai

    def test_pure_missing_attribute_repair_still_saves_after_completion(self):
        vendor, ai = self.attribute_fixture(lambda payload: RuntimeError('产品属性【适用人群】必填')
            if not payload['shopCollectItemInfo']['attributes'] else None)
        with patch.object(server.time, 'sleep'):
            result = self.run_job(vendor, ai=ai)
        self.assertEqual(self.calls.count(server.HALF_SHOP_SAVE), 2)
        self.assertEqual((result['status'], result['autoOverrideAttempts']), ('ready_publish', 0))
        ai.infer_attribute.assert_called_once()
        self.assertNotIn(server.HALF_PUBLISH, self.calls)

    def test_missing_attribute_with_unknown_save_outcome_never_replays_or_recovers(self):
        messages = ['request timeout', 'HTTP 504', 'HTTP504', 'HTTP:504', 'HTTP status 502',
                    'connection reset', 'rate limit; HTTP504', 'rate limit; HTTP500']
        errors = [RuntimeError(f'{message}; 产品属性【适用人群】必填') for message in messages]
        caused = RuntimeError('产品属性【适用人群】必填')
        caused.__cause__ = TimeoutError('write did not confirm')
        errors.append(caused)
        for index, error in enumerate(errors):
            with self.subTest(error=str(error)):
                self.calls.clear()
                vendor, ai = self.attribute_fixture(lambda _payload, error=error: error)
                with patch.object(server.time, 'sleep'):
                    result = self.run_job(vendor, suffix=chr(ord('b') + index), ai=ai)
                self.assertEqual(self.calls.count(server.HALF_SHOP_SAVE), 1)
                self.assertEqual((result['status'], result['step'], result['autoOverrideAttempts']),
                                 ('needs_review', 'save', 0))
                ai.infer_attribute.assert_not_called()
                self.assertNotIn(server.HALF_PUBLISH, self.calls)
                reopened = AutomationPool(self.pool.path)
                self.assertEqual(reopened.claim_pending_auto_overrides(), [])
                self.assertEqual(reopened.get_job(result['jobId'])['status'], 'needs_review')

    def test_throttle_mixed_with_unknown_outcome_is_never_retried_at_request_layer(self):
        for message in ('rate limit; HTTP504', '账户接口每秒请求频率超限; HTTP:504',
                        'HTTP429; HTTP500', 'rate limit; connection reset'):
            with self.subTest(message=message):
                request = Mock(side_effect=RuntimeError(message))
                with patch.object(server.time, 'sleep'), self.assertRaises(RuntimeError):
                    server.miao_request(request, retry_safe=False)
                request.assert_called_once()
        self.assertEqual(failure_flags(RuntimeError('HTTP504')), (False, True))
        self.assertEqual(failure_flags(RuntimeError('product 504 missing')), (False, False))
        attributed = RuntimeError('HTTP504; rate limit')
        attributed.code = 429
        self.assertTrue(has_unknown_write_outcome(attributed))
        self.assertEqual(failure_flags(attributed), (True, True))


if __name__ == '__main__':
    unittest.main()
