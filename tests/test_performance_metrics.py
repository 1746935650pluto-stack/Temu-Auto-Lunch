"""Adaptive limits and metrics use only fake calls and disposable SQLite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import io
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from account_runtime import AccountRuntime, AdaptiveRequestGate, failure_flags
from automation_pool import AutomationPool
from performance_metrics import LATENCY_SAMPLE_LIMIT, PerformanceMetrics


class AdaptivePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.metrics = PerformanceMetrics()
        self.runtime = AccountRuntime(self.metrics)

    def test_good_samples_ramp_within_existing_cap_and_pressure_is_account_local(self):
        now = [10.0]
        gate = AdaptiveRequestGate(clock=lambda: now[0])
        self.assertEqual(gate.limit, 2)
        for _ in range(100):
            gate.observe(True, 25)
        self.assertEqual(gate.limit, 6)
        gate.observe(False, 25, pressure=True)
        self.assertEqual(gate.limit, 3)
        for _ in range(10):
            gate.observe(False, 25, pressure=True)
            gate.observe(True, 25)
        self.assertEqual(gate.limit, 3)
        now[0] += 6
        for _ in range(8):
            gate.observe(True, 25)
        self.assertEqual(gate.limit, 4)
        self.runtime.lane(1).requests.observe(False, 50, pressure=True)
        self.assertEqual(self.runtime.lane(1).requests.limit, 1)
        self.assertEqual(self.runtime.lane(2).requests.limit, 2)

    def test_slow_or_failed_samples_never_raise_the_limit(self):
        gate = AdaptiveRequestGate()
        for _ in range(32):
            gate.observe(True, 9000)
            gate.observe(False, 20)
        self.assertEqual(gate.limit, 2)

    def test_account_waiter_does_not_hold_global_capacity(self):
        runtime = AccountRuntime(self.metrics, global_cap=1)
        lane = runtime.lane(1)
        lane.requests.limit = 1
        queued = threading.Event()
        completed = threading.Event()
        def waiting():
            queued.set()
            with runtime.request(1, 0):
                pass
        def other():
            with runtime.request(2, 0):
                completed.set()
        with ThreadPoolExecutor(max_workers=2) as executor:
            with lane.requests:
                waiter = executor.submit(waiting)
                self.assertTrue(queued.wait(1))
                second = executor.submit(other)
                self.assertTrue(completed.wait(1), 'a busy account must not reserve global capacity')
                second.result(timeout=1)
            waiter.result(timeout=1)

    def test_global_bound_and_exception_safe_release(self):
        runtime = AccountRuntime(self.metrics, global_cap=2)
        lock = threading.Lock()
        active = maximum = 0
        def call(index):
            nonlocal active, maximum
            with runtime.request(index % 3 + 1, 0):
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    time.sleep(.005)
                finally:
                    with lock:
                        active -= 1
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(call, range(30)))
        self.assertEqual(maximum, 2)
        with self.assertRaises(TimeoutError):
            with runtime.request(2, 0):
                raise TimeoutError('secret-bearing payload must never be retained')
        with runtime.request(2, 0):
            pass
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot['global']['active'], 0)
        self.assertEqual(snapshot['global']['queued'], 0)
        self.assertEqual(snapshot['global']['requestCount'], 32)
        self.assertEqual(snapshot['global']['timeoutCount'], 1)
        self.assertNotIn('secret-bearing', str(snapshot))
        self.assertTrue(all(lane.requests.active == 0 for lane in runtime.lanes.values()))

    def test_pacing_wait_holds_no_global_permit(self):
        runtime = AccountRuntime(self.metrics, global_cap=1)
        lane = runtime.lane(1)
        lane.last_request = time.monotonic()
        sleeping = threading.Event()
        release = threading.Event()
        def delay(_):
            sleeping.set()
            self.assertTrue(release.wait(2))
        def slow():
            with runtime.request(1, 1.15):
                pass
        with patch('account_runtime.time.sleep', side_effect=delay), ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(slow)
            try:
                self.assertTrue(sleeping.wait(1))
                with runtime.request(2, 0):
                    pass
            finally:
                release.set()
            task.result(timeout=1)

    def test_failure_classification_inspects_timeout_cause_without_matching_product_id(self):
        error = RuntimeError('Unable to reach MiaoShou API')
        error.__cause__ = URLError(TimeoutError('timed out'))
        self.assertEqual(failure_flags(error), (False, True))
        self.assertEqual(failure_flags(RuntimeError('MiaoShou returned HTTP 429')), (True, False))
        self.assertEqual(failure_flags(RuntimeError('product 429 missing')), (False, False))

    def test_rate_flag_expires_after_one_minute_without_erasing_five_minute_stats(self):
        now = [1000.0]
        metrics = PerformanceMetrics(clock=lambda: now[0])
        metrics.start_request(2)
        metrics.finish_request(2, 25, rate_limited=True)
        now[0] += 59
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot['rateLimitWindowSeconds'], 60)
        self.assertEqual(snapshot['accounts'][0]['rateLimitedRecentCount'], 1)
        now[0] += 1
        row = metrics.snapshot()['accounts'][0]
        self.assertEqual(row['rateLimitedRecentCount'], 0)
        self.assertEqual(row['rateLimitedCount'], 1)
        self.assertEqual(row['requestCount'], 1)
        metrics.start_request(2)
        metrics.finish_request(2, 25, timed_out=True)
        self.assertEqual(metrics.snapshot()['accounts'][0]['rateLimitedRecentCount'], 0)
        metrics.start_request(2)
        metrics.finish_request(2, 25, rate_limited=True)
        self.assertEqual(metrics.snapshot()['accounts'][0]['rateLimitedRecentCount'], 1)

    def test_fixed_window_and_bounded_samples_keep_actual_request_count(self):
        now = [1000.0]
        metrics = PerformanceMetrics(clock=lambda: now[0])
        for index in range(3000):
            metrics.start_request(2)
            metrics.finish_request(2, index, rate_limited=index == 0)
        metrics.sqlite_wait(None, 25)
        row = metrics.snapshot()['accounts'][0]
        self.assertEqual(row['requestCount'], 3000)
        self.assertEqual(row['latencySampleCount'], LATENCY_SAMPLE_LIMIT)
        self.assertEqual(row['rateLimitedCount'], 1)
        self.assertEqual(len(metrics.accounts[2]['buckets']), 1)
        self.assertEqual(metrics.snapshot()['global']['unattributedSqliteWaitCount'], 1)
        now[0] += 301
        row = metrics.snapshot()['accounts'][0]
        self.assertEqual(row['requestCount'], 0)
        self.assertIsNone(row['latencyP95Ms'])

    def test_snapshot_includes_configured_idle_accounts_without_creating_lanes(self):
        snapshot = self.runtime.snapshot(account_ids=(1, 4, 6))
        self.assertEqual([row['accountId'] for row in snapshot['accounts']], [1, 4, 6])
        self.assertTrue(all(row['requestCount'] == 0 and row['latencyP95Ms'] is None
                            for row in snapshot['accounts']))
        self.assertEqual(self.runtime.lanes, {})

    def test_pool_wait_uses_owning_account_and_successful_steps_count_once(self):
        with tempfile.TemporaryDirectory() as directory, patch('automation_pool.RUNTIME', self.runtime):
            pool = AutomationPool(Path(directory) / 'test.sqlite3')
            pool.account_id = 2
            with closing(pool._connect()):
                pass
            started = threading.Event()
            def connect():
                started.set()
                with closing(pool._connect()):
                    pass
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pool._connection_lock:
                    task = executor.submit(connect)
                    self.assertTrue(started.wait(1))
                    threading.Event().wait(.07)
                task.result(timeout=1)
            pool.put_item(123, 'site', 0, {'title': 'test'})
            pool.start_job('a' * 32, 123, 456, 'US')
            for _ in range(2):
                pool.update_job('a' * 32, 'save', 'done', 'saved')
                pool.update_job('a' * 32, 'publish', 'done', 'accepted')
            row = self.runtime.snapshot(2)['accounts'][0]
            self.assertEqual(row['sqliteWaitCount'], 1)
            self.assertGreaterEqual(row['sqliteWaitMs'], 50)
            # Local SQLite contention is visible in diagnostics but is not a
            # MiaoShou timeout/limit response, so it must not shrink the
            # remote gate and indirectly throttle all later edit admission.
            self.assertEqual(row['limit'], 2)
            self.assertEqual((row['editCompleted'], row['publishCompleted']), (1, 1))


class PerformanceVendorTests(unittest.TestCase):
    def setUp(self):
        # tests/run_tests.py establishes disposable storage and the network
        # audit guard before this module can import the application.
        import server
        self.server = server
        self.metrics = PerformanceMetrics()
        self.runtime = AccountRuntime(self.metrics)
        self.patches = [patch.object(server, 'RUNTIME', self.runtime),
                        patch('account_runtime.time.sleep'), patch.object(server, 'MIAOSHOU_RATE_LIMIT_RETRIES', 1)]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.handler = server.AppHandler.__new__(server.AppHandler)

    def test_actual_vendor_attempts_count_once_and_unknown_write_is_not_replayed(self):
        server = self.server
        success = {'result': 'success', 'code': 'success'}
        with patch.object(server, 'MiaoShouClient') as client:
            client.return_value.post.side_effect = [{'code': '请求频率超限'}, success]
            result = self.handler._account_vendor(server.HALF_SITE_DETAIL, {}, 3)
            self.assertEqual(result, success)
            self.assertEqual(client.return_value.post.call_count, 2)
            row = self.runtime.snapshot(3)['accounts'][0]
            self.assertEqual((row['requestCount'], row['rateLimitedCount']), (2, 1))
            client.return_value.post.reset_mock()
            client.return_value.post.side_effect = RuntimeError('MiaoShou returned HTTP 504')
            with self.assertRaises(RuntimeError):
                server.miao_request(lambda: self.handler._account_vendor(server.HALF_SHOP_SAVE, {}, 3),
                                    3, retry_safe=False)
            self.assertEqual(client.return_value.post.call_count, 1)
            self.assertEqual(self.runtime.snapshot(3)['accounts'][0]['timeoutCount'], 1)

    def test_performance_route_reads_no_database_or_vendor(self):
        self.handler.raw_requestline = b'GET /api/performance?accountId=4 HTTP/1.1\r\n'
        self.handler.rfile = io.BytesIO(b'Host: localhost\r\n\r\n')
        self.handler._same_origin = lambda: True
        self.handler._json = Mock()
        with (patch.object(self.server, 'MiaoShouClient', side_effect=AssertionError('no credentials/vendor')),
              patch.object(self.server, 'automation_pool', side_effect=AssertionError('no database')),
              patch.object(self.server, 'order_account', side_effect=AssertionError('no account database')),
              patch.object(self.server, 'configured_miaoshou_account_slots', return_value=[
                  {'id': 1, 'configured': True}, {'id': 4, 'configured': True}])):
            self.assertTrue(self.handler.parse_request())
            self.handler.do_GET()
        status, payload = self.handler._json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(payload['accounts'][0]['accountId'], 4)
        self.assertEqual(payload['limits']['perAccountCap'], 6)
        self.assertEqual(payload['limits']['detailPaceSeconds'], self.server.POOL_IMPORT_DETAIL_INTERVAL_SECONDS)
        self.assertEqual(payload['limits']['retryPaceSeconds'], self.server.MIAOSHOU_REQUEST_MIN_INTERVAL_SECONDS)

    def test_unsafe_retry_requires_unambiguous_rate_limit_and_keeps_read_compatibility(self):
        for message, expected in (('MiaoShou returned HTTP 429', 2), ('请求频率超限', 2),
                                  ('MiaoShou returned HTTP 504', 1), ('timeout after rate limit', 1)):
            call = Mock(side_effect=RuntimeError(message))
            with self.assertRaises(RuntimeError):
                self.server.miao_request(call, 4, retry_safe=False)
            self.assertEqual(call.call_count, expected, message)
        call = Mock(side_effect=[RuntimeError('MiaoShou returned HTTP 504'), {'data': 'ok'}])
        self.assertEqual(self.server.miao_request(call, 4), {'data': 'ok'})
        self.assertEqual(call.call_count, 2)
        call = Mock(side_effect=[RuntimeError('MiaoShou returned HTTP 500'), {'data': 'ok'}])
        self.assertEqual(self.server.miao_request(call, 4), {'data': 'ok'})
        self.assertEqual(call.call_count, 2)


if __name__ == '__main__':
    unittest.main()
