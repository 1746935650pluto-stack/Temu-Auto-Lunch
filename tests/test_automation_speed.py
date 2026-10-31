import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import server


class AutomationSpeedTests(unittest.TestCase):
    def test_batch_config_single_flight_is_payload_scoped_and_copy_isolated(self):
        vendor = Mock(return_value={'data': {'rows': [1]}})
        cached = server.AutomationBatchConfigCache(vendor)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: cached(server.HALF_FREIGHT, {'shopId': 1}), range(30)))
        self.assertEqual(vendor.call_count, 1)
        results[0]['data']['rows'].append(2)
        self.assertEqual(results[1]['data']['rows'], [1])
        self.assertEqual(cached(server.HALF_FREIGHT, {'shopId': 1})['data']['rows'], [1])
        cached(server.HALF_FREIGHT, {'shopId': 2})
        self.assertEqual(vendor.call_count, 2)
        server.AutomationBatchConfigCache(vendor)(server.HALF_FREIGHT, {'shopId': 1})
        self.assertEqual(vendor.call_count, 3)

    def test_expiry_failure_and_write_or_detail_requests_never_reuse_data(self):
        vendor = Mock(return_value={'data': {}})
        cached = server.AutomationBatchConfigCache(vendor)
        with patch.object(server.time, 'monotonic', return_value=1):
            cached(server.HALF_SITES, {})
        with patch.object(server.time, 'monotonic', return_value=62):
            cached(server.HALF_SITES, {})
        self.assertEqual(vendor.call_count, 2)
        for path in (server.HALF_SHOP_SAVE, server.HALF_CLAIM_TO_SHOP, server.HALF_PUBLISH, server.HALF_SHOP_DETAIL):
            cached(path, {})
            cached(path, {})
        self.assertEqual(vendor.call_count, 10)
        vendor.side_effect = [RuntimeError('failed'), {'error': 'bad'}, {'data': {'ok': True}}]
        with self.assertRaises(RuntimeError):
            cached(server.HALF_WAREHOUSE, {})
        cached(server.HALF_WAREHOUSE, {})
        self.assertTrue(cached(server.HALF_WAREHOUSE, {})['data']['ok'])

    def test_language_requests_overlap_and_keep_original_sources(self):
        barrier = threading.Barrier(2)
        calls = []
        def polish(title, language):
            calls.append((title, language))
            barrier.wait(timeout=3)
            return language + '-result'
        client = Mock(polish_title=polish)
        self.assertEqual(server.automation_titles(client, {'title': '原题', 'multiLanguageTitleMap': {'en': 'original'}}),
                         ('zh-result', 'en-result'))
        self.assertCountEqual(calls, [('原题', 'zh'), ('original', 'en')])

    def test_language_failure_propagates_after_both_requests_complete(self):
        finished = threading.Event()
        def polish(title, language):
            if language == 'zh':
                raise RuntimeError('AI failed')
            finished.set()
            return 'English'
        with self.assertRaisesRegex(RuntimeError, 'AI failed'):
            server.automation_titles(Mock(polish_title=polish), {'title': '原题'})
        self.assertTrue(finished.is_set())
