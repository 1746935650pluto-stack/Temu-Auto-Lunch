import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, call, patch

import server
from automation_pool import AutomationPool


class PoolImportSpeedTests(unittest.TestCase):
    def test_duplicates_skip_remote_reads_and_preserve_existing_snapshot(self):
        self.pool.put_item(123, 'site', 0, {'title': '原资料'})
        before = self.pool.get_item(123)
        read = Mock(return_value={'title': '新商品'})
        result = server.stage_pool_batch(self.pool, [(123, 'site', 0), (124, 'site', 0), (124, 'site', 0)], read)
        read.assert_called_once_with(124, 'site', 0)
        self.assertEqual([item['detailId'] for item in result['items']], [124])
        self.assertEqual([item['detailId'] for item in result['skipped']], [123, 124])
        self.assertEqual(self.pool.get_item(123), before)
        self.assertEqual(result['failures'], [])

    def test_concurrent_import_of_same_id_has_one_winner(self):
        # Both reads start before either insert, exercising the unique-key guard.
        self.pool.list_items()
        barrier = threading.Barrier(2)
        def read(*args):
            barrier.wait(timeout=3)
            return {'title': threading.current_thread().name}
        with ThreadPoolExecutor(max_workers=2) as workers:
            tasks = [workers.submit(server.stage_pool_item, self.pool, read, 123, 'site', 0) for _ in range(2)]
            results = [task.result(timeout=5) for task in tasks]
        self.assertEqual(sum(not item['skipped'] for item in results), 1)
        self.assertEqual(len(self.pool.list_items()), 1)
        self.assertEqual(results[0]['title'], results[1]['title'])

    def test_missing_pre_publish_shop_falls_back_to_site_and_persists_actual_source(self):
        snapshot = {'title': '完整资料', 'imgUrls': ['https://example.com/img.jpg'], 'skuMap': {'blue': {'stock': 9}}}
        read = Mock(side_effect=[RuntimeError('未选择预发布店铺'), snapshot])
        result = server.stage_pool_batch(self.pool, [(123, 'shop', 456)], read)
        self.assertEqual(result['failures'], [])
        self.assertEqual(read.call_args_list, [call(123, 'shop', 456), call(123, 'site', 0)])
        stored = self.pool.get_item(123)
        self.assertEqual((stored['mode'], stored['shopId'], stored['item']), ('site', 0, snapshot))

    def test_valid_shop_details_preserved_and_unrelated_errors_do_not_fallback(self):
        read = Mock(return_value={'title': '店铺定制标题'})
        self.assertEqual(server.read_pool_snapshot(read, 123, 'shop', 456), ('shop', 456, {'title': '店铺定制标题'}))
        for message in ('鉴权失败', '请求频率超限', 'numberValue: 期望 int', '读取超时'):
            read = Mock(side_effect=RuntimeError(message))
            with self.assertRaisesRegex(RuntimeError, message):
                server.read_pool_snapshot(read, 123, 'shop', 456)
            read.assert_called_once_with(123, 'shop', 456)
        read = Mock(side_effect=RuntimeError('未选择预发布店铺'))
        with self.assertRaises(RuntimeError):
            server.read_pool_snapshot(read, 123, 'site', 0)
        read.assert_called_once()

    def test_snapshot_enrichment_keeps_old_attributes_and_uses_fresh_matching_values(self):
        snapshot = {'attributes': [
            {'templatePid': 10, 'name': '材质', 'values': [{'vid': 1, 'name': '旧值'}]},
            {'templatePid': 20, 'name': '颜色', 'values': [{'vid': 2, 'name': '红色'}]},
        ]}
        fresh = {'attributes': [
            {'templatePid': 10, 'name': '材质', 'values': [{'vid': 3, 'name': '新值'}]},
            {'templatePid': 30, 'name': '用途', 'values': [{'vid': 4, 'name': '日用'}]},
        ]}
        merged, changed = server.enrich_snapshot_attributes(snapshot, fresh)
        self.assertTrue(changed)
        self.assertEqual([row['name'] for row in merged['attributes']], ['材质', '颜色', '用途'])
        self.assertEqual(merged['attributes'][0]['values'][0]['name'], '新值')

    def test_both_source_failures_keep_id_and_reasons_and_do_not_stage_partial_data(self):
        read = Mock(side_effect=[RuntimeError('未选择预发布店铺'), RuntimeError('采集箱详情不存在')])
        result = server.stage_pool_batch(self.pool, [(123, 'shop', 456)], read)
        self.assertEqual(result['items'], [])
        self.assertEqual(result['failures'][0]['detailId'], 123)
        self.assertIn('未选择预发布店铺', result['failures'][0]['error'])
        self.assertIn('采集箱详情不存在', result['failures'][0]['error'])
        self.assertIsNone(self.pool.get_item(123))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pool = AutomationPool(Path(directory.name) / 'pool.sqlite3')

    def test_bounded_reads_overlap_slow_first_does_not_block_refill_or_persistence(self):
        concurrency = server.POOL_IMPORT_CONCURRENCY
        # The fixture has eight inputs; a faster importer may expose more
        # workers than that, but it must not wait for workers that have no
        # item to read.
        active_workers = min(concurrency, 8)
        barrier = threading.Barrier(active_workers)
        release_first = threading.Event()
        fifth_saved = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum = 0
        source = {'title': '完整商品', 'imgUrls': ['https://example.com/image.jpg'],
                  'attributes': [{'values': [{'numberValue': 85.5}]}], 'skuMap': {'blue': {'stock': 100}}}

        def read(detail_id, mode, shop_id):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                if detail_id <= active_workers:
                    barrier.wait(timeout=3)
                if detail_id == 1:
                    if not release_first.wait(5):
                        raise RuntimeError('test release timed out')
                if detail_id == 3:
                    raise RuntimeError('numberValue 读取失败')
                return source
            finally:
                with lock:
                    active -= 1

        original_put = self.pool.put_item
        def put(detail_id, *args, **kwargs):
            result = original_put(detail_id, *args, **kwargs)
            if detail_id == 5:
                fifth_saved.set()
            return result

        with patch.object(self.pool, 'put_item', side_effect=put), ThreadPoolExecutor(max_workers=1) as coordinator:
            task = coordinator.submit(server.stage_pool_batch, self.pool, [(i, 'site', 0) for i in range(1, 9)], read)
            try:
                self.assertTrue(fifth_saved.wait(4), 'later products must enter the pool before slow first finishes')
                self.assertFalse(task.done())
                self.assertIsNone(self.pool.get_item(1))
                self.assertEqual(self.pool.get_item(5)['item'], source)
            finally:
                release_first.set()
            result = task.result(timeout=5)
        self.assertEqual(maximum, active_workers)
        self.assertEqual([item['detailId'] for item in result['items']], [1, 2, 4, 5, 6, 7, 8])
        self.assertEqual(result['failures'], [{'detailId': 3, 'error': 'numberValue 读取失败'}])
        self.assertEqual(len(self.pool.list_items()), 7)

    def test_multiple_batches_share_the_global_read_threads_and_preserve_modes(self):
        active = 0
        maximum = 0
        calls = []
        lock = threading.Lock()
        def read(detail_id, mode, shop_id):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                calls.append((detail_id, mode, shop_id))
            try:
                time.sleep(0.02)
                return {'title': str(detail_id)}
            finally:
                with lock:
                    active -= 1
        groups = [[(i, 'shop', 456) for i in range(1, 9)], [(i, 'site', 0) for i in range(9, 17)]]
        with ThreadPoolExecutor(max_workers=2) as coordinators:
            tasks = [coordinators.submit(server.stage_pool_batch, self.pool, group, read) for group in groups]
            results = [task.result(timeout=5) for task in tasks]
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, server.POOL_IMPORT_CONCURRENCY)
        self.assertCountEqual(calls, groups[0] + groups[1])
        self.assertEqual([len(result['items']) for result in results], [8, 8])
        self.assertEqual(len(self.pool.list_items()), 16)
