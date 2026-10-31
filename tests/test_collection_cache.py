from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock

from collection_cache import CollectionReadCache


def response(*rows):
    return {'data': {'detailList': [{'collectBoxDetailId': item, 'title': title} for item, title in rows]}}


class CollectionCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'cache.sqlite3'
        self.cache = CollectionReadCache(self.path)

    def test_fresh_page_and_restart_use_cache_without_remote_reads(self):
        reader = Mock(return_value=response((1, 'one'), (2, 'two')))
        first = self.cache.get('a', 'products:1', reader)
        self.assertEqual(first['cache']['added'], 2)
        again = CollectionReadCache(self.path).get('a', 'products:1', reader)
        self.assertTrue(again['cache']['cached'])
        self.assertEqual(again['data'], first['data'])
        self.assertEqual(reader.call_count, 1)
        again['data']['detailList'][0]['title'] = 'mutated by caller'
        self.assertEqual(self.cache.get('a', 'products:1', reader)['data']['detailList'][0]['title'], 'one')

    def test_delta_updates_only_changed_rows_and_does_not_delete_other_pages(self):
        self.cache.get('a', 'products:1', lambda: response((1, 'one'), (2, 'two')))
        self.cache.get('a', 'products:2', lambda: response((3, 'three')))
        result = self.cache.get('a', 'products:1', lambda: response((4, 'new'), (1, 'renamed'), (2, 'two')), force=True)
        self.assertEqual((result['cache']['added'], result['cache']['updated'], result['cache']['unchanged']), (1, 1, 1))
        # Page boundaries can move, so other pages become stale, not deleted.
        cached, checked = self.cache._read('a', 'products:2')
        self.assertEqual(checked, 0)
        self.assertEqual(cached['data']['detailList'][0]['collectBoxDetailId'], 3)
        result = self.cache.get('a', 'products:2', lambda: response((3, 'three')), force=True)
        self.assertEqual(result['cache']['unchanged'], 1)

    def test_scope_and_credentials_are_isolated(self):
        self.cache.get('slot-1-key-1', 'products:1', lambda: response((1, 'one')))
        reader = Mock(return_value=response((1, 'two')))
        other = self.cache.get('slot-2-key-2', 'products:1', reader)
        self.assertEqual(other['data']['detailList'][0]['title'], 'two')
        self.assertFalse(other['cache']['cached'])
        self.assertEqual(other['cache']['added'], 1)
        self.assertEqual(reader.call_count, 1)

    def test_expired_page_returns_immediately_then_single_background_refresh(self):
        self.cache.get('a', 'products:1', lambda: response((1, 'old')))
        started, finish = Event(), Event()
        def reader():
            started.set()
            finish.wait(2)
            return response((1, 'new'))
        result = self.cache.get('a', 'products:1', reader, ttl=-1)
        self.assertTrue(result['cache']['refreshing'])
        self.assertEqual(result['data']['detailList'][0]['title'], 'old')
        self.assertTrue(started.wait(1))
        duplicate = Mock()
        self.assertTrue(self.cache.get('a', 'products:1', duplicate)['cache']['refreshing'])
        duplicate.assert_not_called()
        finish.set()
        for _ in range(100):
            with self.cache._lock:
                if not self.cache._running:
                    break
            time.sleep(.01)
        self.assertFalse(self.cache._running)
        self.assertEqual(self.cache.get('a', 'products:1', duplicate)['data']['detailList'][0]['title'], 'new')
        duplicate.assert_not_called()

    def test_failures_keep_old_data_and_cannot_turn_malformed_response_into_empty(self):
        original = response((1, 'one'))
        self.cache.get('a', 'products:1', lambda: deepcopy(original))
        for reader in (Mock(side_effect=RuntimeError('offline')), Mock(return_value={'data': {}})):
            result = self.cache.get('a', 'products:1', reader, force=True)
            self.assertEqual(result['data'], original['data'])
            self.assertTrue(result['cache']['error'])
        no_retry = Mock()
        self.cache.get('a', 'products:1', no_retry, ttl=-1)
        no_retry.assert_not_called()
        # An explicit successful empty page is valid, but is not proof that IDs were deleted globally.
        result = self.cache.get('a', 'products:1', lambda: response(), force=True)
        self.assertEqual(result['data']['detailList'], [])
        result = self.cache.get('a', 'products:2', lambda: response((1, 'one')))
        self.assertEqual(result['cache']['unchanged'], 1)


if __name__ == '__main__':
    unittest.main()
