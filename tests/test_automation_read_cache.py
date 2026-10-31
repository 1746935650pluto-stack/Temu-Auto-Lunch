import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock, patch

from automation_read_cache import AutomationReadCache


class ReadCacheTests(unittest.TestCase):
    def observed_future(self, waiting):
        class ObservedFuture(Future):
            def result(self, *args, **kwargs):
                waiting.set()
                return super().result(*args, **kwargs)
        return patch('automation_read_cache.Future', ObservedFuture)

    def test_unrelated_writes_preserve_inflight_reuse_and_completed_cache(self):
        for group in [('a', 2), ('b', 1), ('b', 2)]:
            with self.subTest(write_group=group):
                cache = AutomationReadCache()
                started, waiting, release = (threading.Event() for _ in range(3))
                def read_once():
                    started.set()
                    self.assertTrue(release.wait(3))
                    return {'value': [1]}
                read = Mock(side_effect=read_once)
                def get():
                    return cache.get(('a', 1, 'site', 0), read, ttl=60, valid=lambda _: True)
                with self.observed_future(waiting), ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(get)
                    try:
                        self.assertTrue(started.wait(2))
                        second = executor.submit(get)
                        self.assertTrue(waiting.wait(2))
                        cache.begin_write(group)
                        cache.end_write(group)
                    finally:
                        release.set()
                    left, right = first.result(3), second.result(3)
                left['value'].append(2)
                self.assertEqual(right, {'value': [1]})
                self.assertEqual(get(), {'value': [1]})
                read.assert_called_once()
                self.assertFalse(cache.pending)

    def test_matching_write_makes_waiting_reader_refetch_after_write(self):
        cache = AutomationReadCache()
        started, waiting, release = (threading.Event() for _ in range(3))
        def read_value():
            if not started.is_set():
                started.set()
                self.assertTrue(release.wait(3))
                return {'value': 'old'}
            return {'value': 'fresh'}
        read = Mock(side_effect=read_value)
        def get():
            return cache.get(('a', 1, 'site', 0), read, ttl=60, valid=lambda _: True)
        with self.observed_future(waiting), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(get)
            try:
                self.assertTrue(started.wait(2))
                second = executor.submit(get)
                self.assertTrue(waiting.wait(2))
                cache.begin_write(('a', 1))
                # Failed writes must still retire the old flight at end_write.
                cache.end_write(('a', 1))
            finally:
                release.set()
            first.result(3)
            self.assertEqual(second.result(3), {'value': 'fresh'})
        self.assertEqual(read.call_count, 2)
        self.assertEqual(get(), {'value': 'fresh'})
        self.assertEqual(get(), {'value': 'fresh'})
        self.assertEqual(read.call_count, 3)

    def test_old_completion_does_not_remove_new_flight_for_same_product(self):
        cache = AutomationReadCache()
        old_started, new_started, waiting, old_release, new_release = (threading.Event() for _ in range(5))
        def old_read():
            old_started.set()
            self.assertTrue(old_release.wait(3))
            return {'value': 'old'}
        def new_read():
            new_started.set()
            self.assertTrue(new_release.wait(3))
            return {'value': 'fresh'}
        def get(read):
            return cache.get(('a', 1, 'shop', 3), read, ttl=60, valid=lambda _: True)
        unexpected_read = Mock(side_effect=AssertionError('new flight should be shared'))
        with self.observed_future(waiting), ThreadPoolExecutor(max_workers=3) as executor:
            first = executor.submit(get, old_read)
            try:
                self.assertTrue(old_started.wait(2))
                cache.begin_write(('a', 1))
                cache.end_write(('a', 1))
                second = executor.submit(get, new_read)
                self.assertTrue(new_started.wait(2))
                old_release.set()
                first.result(3)
                third = executor.submit(get, unexpected_read)
                self.assertTrue(waiting.wait(2))
            finally:
                old_release.set()
                new_release.set()
            self.assertEqual(second.result(3), {'value': 'fresh'})
            self.assertEqual(third.result(3), {'value': 'fresh'})
        self.assertEqual(get(unexpected_read), {'value': 'fresh'})
        unexpected_read.assert_not_called()
        self.assertFalse(cache.pending)

    def test_shared_failure_releases_waiters_and_does_not_cache_error(self):
        cache = AutomationReadCache()
        started, waiting, release = (threading.Event() for _ in range(3))
        def failed():
            started.set()
            self.assertTrue(release.wait(3))
            raise RuntimeError('synthetic failure')
        read = Mock(side_effect=failed)
        def get(reader):
            return cache.get(('a', 1), reader, ttl=60, valid=lambda _: True)
        with self.observed_future(waiting), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(get, read)
            try:
                self.assertTrue(started.wait(2))
                second = executor.submit(get, read)
                self.assertTrue(waiting.wait(2))
            finally:
                release.set()
            for future in (first, second):
                with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                    future.result(3)
        read.assert_called_once()
        self.assertFalse(cache.pending)
        self.assertEqual(get(lambda: {'value': 'fresh'}), {'value': 'fresh'})

    def test_overlapping_writes_bypass_cache_until_last_writer_finishes(self):
        cache = AutomationReadCache()
        read = Mock(side_effect=lambda: {'value': read.call_count})
        def get(mode, shop):
            return cache.get(('a', 1, mode, shop), read, ttl=60, valid=lambda _: True)
        get('site', 0)
        get('shop', 3)
        cache.begin_write(('a', 1))
        cache.begin_write(('a', 1))
        cache.end_write(('a', 1))
        self.assertNotEqual(get('site', 0), get('site', 0))
        self.assertNotEqual(get('shop', 3), get('shop', 3))
        cache.end_write(('a', 1))
        self.assertEqual(get('site', 0), get('site', 0))
        self.assertEqual(get('shop', 3), get('shop', 3))
        self.assertFalse(cache.writers)

    def test_overlapping_reads_share_response_but_not_mutable_objects(self):
        cache = AutomationReadCache()
        started, release = threading.Event(), threading.Event()
        read = Mock(side_effect=lambda: (started.set(), release.wait(3), {'value': [1]})[-1])
        def get():
            return cache.get(('scope', 1), read, ttl=60, valid=lambda value: True)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(get) for _ in range(4)]
            self.assertTrue(started.wait(2))
            release.set()
            values = [future.result(3) for future in futures]
        read.assert_called_once()
        values[0]['value'].append(2)
        self.assertEqual(get(), {'value': [1]})
        self.assertEqual(values[1], {'value': [1]})

    def test_expiry_incomplete_failures_and_scopes(self):
        cache = AutomationReadCache(max_entries=2)
        read = Mock(side_effect=[RuntimeError('failed'), {}, {'value': 1}, {'value': 2}, {'value': 3}])
        def get(scope):
            return cache.get(scope, read, ttl=5, valid=lambda value: 'value' in value)
        with self.assertRaises(RuntimeError):
            get('a')
        self.assertEqual(get('a'), {})
        self.assertEqual(get('a'), {'value': 1})
        self.assertEqual(get('b'), {'value': 2})
        with patch('automation_read_cache.time.monotonic', return_value=10**15):
            self.assertEqual(get('a'), {'value': 3})
        self.assertEqual(read.call_count, 5)
        get('b')
        self.assertLessEqual(len(cache.entries), 2)

    def test_write_fences_inflight_response_even_if_write_fails(self):
        cache = AutomationReadCache()
        started, release = threading.Event(), threading.Event()
        old = Mock(side_effect=lambda: (started.set(), release.wait(3), {'value': 'old'})[-1])
        def get(read):
            return cache.get('item', read, ttl=60, valid=lambda value: True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(get, old)
            self.assertTrue(started.wait(2))
            cache.begin_write()
            self.assertEqual(get(lambda: {'value': 'during'}), {'value': 'during'})
            cache.end_write()
            release.set()
            future.result(3)
        self.assertEqual(get(lambda: {'value': 'fresh'}), {'value': 'fresh'})

    def test_write_invalidation_preserves_unrelated_accounts_and_products(self):
        cache = AutomationReadCache()
        read = Mock(return_value={'value': 1})
        def get(scope, detail):
            return cache.get((scope, detail, 'site', 0), read, ttl=60, valid=lambda value: True)
        for scope, detail in [('a', 1), ('a', 2), ('b', 1)]:
            get(scope, detail)
        cache.begin_write(('a', 1))
        get('a', 2)
        get('b', 1)
        self.assertEqual(read.call_count, 3)
        get('a', 1)
        cache.end_write(('a', 1))
        get('a', 1)
        self.assertEqual(read.call_count, 5)

