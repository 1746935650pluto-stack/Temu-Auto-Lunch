import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from automation_pool import AutomationPool
from partitioned_pool import PartitionedPool
from account_runtime import AccountRuntime


class PartitionedPoolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.primary = AutomationPool(self.root / 'pool.sqlite3')
        self.primary.put_item(101, 'site', 0, {'title': 'legacy'})
        self.pool = self.reopen()

    def reopen(self):
        return PartitionedPool(AutomationPool(self.root / 'pool.sqlite3'),
                               AutomationPool(self.root / 'pool-2.sqlite3'),
                               AutomationPool(self.root / 'pool-3.sqlite3'), 1)

    def test_legacy_preserved_new_goods_split_and_restart_keeps_routes(self):
        self.assertEqual(self.pool.get_item(101)['poolId'], 1)
        for detail in (102, 103):
            self.pool.put_item(detail, 'site', 0, {'title': str(detail)})
        self.assertEqual(self.pool.pools[0].item_ids(), {101, 102})
        self.assertEqual(self.pool.pools[1].item_ids(), {103})
        reopened = self.reopen()
        self.assertEqual(reopened.item_ids(), {101, 102, 103})
        self.assertEqual(reopened.get_item(101)['item']['title'], 'legacy')
        self.assertEqual(reopened.get_item(103)['poolId'], 2)

    def test_new_item_uses_the_least_loaded_pool_without_moving_existing_items(self):
        # IDs used to decide the destination by a modulus.  Make pools 1 and
        # 2 busy, then prove a new item goes to the otherwise-idle pool 3.
        self.pool.pools[0].put_item(201, 'site', 0, {'title': 'busy-1'})
        self.pool.pools[1].put_item(202, 'site', 0, {'title': 'busy-2'})
        self.pool.pools[0].queue_jobs([201], 7, 'US')
        self.pool.pools[1].queue_jobs([202], 7, 'US')
        self.pool.put_item(104, 'site', 0, {'title': 'third'})
        self.assertEqual(self.pool.get_item(104)['poolId'], 3)
        reopened = self.reopen()
        self.assertEqual(reopened.get_item(101)['poolId'], 1)
        self.assertEqual(reopened.get_item(104)['poolId'], 3)

    def test_queue_rebalances_unused_staged_items_but_never_ready_jobs(self):
        # These snapshots emulate a collection list accumulated before pool 3
        # existed. They have no task history, so they may be moved at queue
        # time to use every idle editing lane.
        for detail in (301, 302, 303):
            self.pool.pools[0].put_item(detail, 'site', 0, {'title': str(detail)})
        jobs = self.pool.queue_jobs([301, 302, 303], 7, 'US')
        self.assertEqual({job['poolId'] for job in jobs}, {1, 2, 3})
        self.assertEqual(sum(301 in pool.item_ids() for pool in self.pool.pools), 1)
        for job in jobs:
            self.pool.update_job(job['jobId'], 'save', 'ready_publish', 'saved')
        before = {job['detailId']: job['poolId'] for job in self.pool.list_jobs()}
        self.pool.queue_jobs([301, 302, 303], 7, 'US', dedupe_pairs=True)
        after = {job['detailId']: job['poolId'] for job in self.pool.list_jobs()
                 if job['detailId'] in before}
        self.assertEqual(after, before)

    def test_duplicate_imports_stay_in_one_pool(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(executor.map(lambda _: self.pool.put_item(
                103, 'site', 0, {'title': 'new'}, only_if_new=True), range(12)))
        self.assertEqual(sum(not r['skipped'] for r in rows), 1)
        self.assertEqual(sum(103 in pool.item_ids() for pool in self.pool.pools), 1)

    def test_job_routes_fair_claiming_and_account_total_limit(self):
        for detail in (102, 103, 104, 105):
            self.pool.put_item(detail, 'site', 0, {'title': str(detail)})
        jobs = self.pool.queue_jobs([102, 103, 104, 105], 7, 'US',
                                   price=['160', '170', '180', '190'])
        first = self.pool.start_next_queued_jobs(2)
        self.assertEqual({r['poolId'] for r in first}, {1, 2})
        self.assertEqual(self.pool.start_next_queued_jobs(2), [])
        for row in first:
            self.pool.update_job(row['jobId'], 'save', 'ready_publish', 'ok')
        self.assertEqual(len(self.pool.start_next_queued_jobs(2)), 2)
        reopened = self.reopen()
        self.assertEqual(len(reopened.job_states([r['jobId'] for r in jobs])), 4)
        self.assertEqual({r['detailId']: r['price'] for r in reopened.list_jobs()},
                         {102: '160', 103: '170', 104: '180', 105: '190'})

    def test_missing_item_in_second_partition_does_not_queue_first(self):
        with self.assertRaises(ValueError):
            self.pool.queue_jobs([101, 103], 7, 'US')
        self.assertEqual(self.pool.list_jobs(), [])

    def test_ready_release_is_durable_and_updates_both_pool_intervals(self):
        self.pool.put_item(103, 'site', 0, {'title': 'new'})
        jobs = self.pool.queue_jobs([101, 103], 7, 'US')
        for row in jobs:
            self.pool.update_job(row['jobId'], 'save', 'ready_publish', 'ok')
        token = self.pool.begin_publish_batch([r['jobId'] for r in jobs], 10)
        reopened = self.reopen()
        self.assertEqual(reopened.publish_progress()['remaining'], 2)
        reopened.update_running_publish_intervals(1)
        self.assertEqual(reopened.running_publish_interval(token), 1)
        seen = set()
        while row := reopened.start_next_ready_publish_job(token):
            seen.add(row['jobId'])
            reopened.update_job(row['jobId'], 'publish', 'done', 'accepted')
        reopened.finish_publish_batch(token)
        self.assertEqual(seen, {r['jobId'] for r in jobs})
        self.assertEqual(reopened.publish_progress()['remaining'], 0)

    def test_recovery_never_releases_unconfirmed_publication(self):
        self.pool.put_item(103, 'site', 0, {'title': 'new'})
        jobs = self.pool.queue_jobs([101, 103], 7, 'US')
        for row in jobs:
            self.pool.update_job(row['jobId'], 'save', 'ready_publish', 'ok')
        token = self.pool.begin_publish_batch([r['jobId'] for r in jobs], 1)
        self.pool.start_next_ready_publish_job(token)
        reopened = self.reopen()
        reopened.recover_interrupted_jobs()
        self.assertIsNone(reopened.start_next_ready_publish_job(token))
        self.assertFalse(any(r['status'] == 'done' for r in reopened.list_jobs()))

    def test_duplicate_release_does_not_append_to_other_pool_or_change_interval(self):
        self.pool.put_item(103, 'site', 0, {'title':'new'})
        jobs = self.pool.queue_jobs([101,103], 7, 'US')
        for row in jobs:
            self.pool.update_job(row['jobId'], 'save', 'ready_publish', 'ok')
        by_detail = {row['detailId']: row for row in jobs}
        released_pool = self.pool._job_pool(by_detail[103]['jobId'])
        self.pool.begin_publish_batch([by_detail[103]['jobId']], 10)
        with self.assertRaisesRegex(ValueError, '已在发布队列中'):
            self.pool.append_to_running_publish_batch([row['jobId'] for row in jobs], 1)
        self.assertTrue(all(p.publish_progress() is None for p in self.pool.pools if p is not released_pool))
        self.assertEqual(released_pool.publish_progress()['intervalSeconds'], 10)
        with self.assertRaisesRegex(ValueError, '已在发布队列中'):
            released_pool.append_to_running_publish_batch([by_detail[103]['jobId']], 1)

    def test_each_database_uses_wal(self):
        for pool in self.pool.pools:
            self.assertEqual(pool.read_rows('PRAGMA journal_mode')[0]['journal_mode'], 'wal')

    def test_local_database_busy_recovery_visits_every_partition(self):
        self.pool.put_item(103, 'site', 0, {'title': 'secondary'})
        jobs = self.pool.queue_jobs([101, 103], 7, 'US')
        for job in jobs:
            self.pool.pause_for_problem(job['jobId'], 'claim', 'database is locked')
        self.assertEqual(self.pool.requeue_local_database_busy_jobs(), 2)
        self.assertTrue(all(self.pool.get_job(job['jobId'])['status'] == 'queued' for job in jobs))

    def test_controlled_rebalance_moves_only_plain_queued_jobs_and_preserves_job_identity(self):
        # Simulate a large legacy queue which pre-dates the second and third
        # physical databases.  Direct pool writes intentionally concentrate it
        # in pool 1 so the maintenance action has work to distribute.
        for detail in range(201, 207):
            self.pool.pools[0].put_item(detail, 'site', 0, {'title': str(detail)})
        jobs = self.pool.pools[0].queue_jobs(list(range(201, 207)), 7, 'US')
        running = self.pool.pools[0].start_next_queued_jobs(1)[0]
        held = next(job for job in jobs if job['jobId'] != running['jobId'])
        self.pool.pools[0].update_job(held['jobId'], 'save', 'ready_publish', 'saved')

        result = self.pool.rebalance_plain_queued_jobs()
        self.assertEqual((result['movedJobs'], result['movedDetails']), (3, 3))
        self.assertEqual(result['afterLoads'], [2, 2, 1])
        self.assertEqual(self.pool.get_job(running['jobId'])['poolId'], 1)
        self.assertEqual(self.pool.get_job(held['jobId'])['poolId'], 1)
        moved = [job for job in jobs if self.pool.get_job(job['jobId'])['poolId'] in {2, 3}]
        self.assertEqual(len(moved), 3)
        self.assertEqual({self.pool.get_job(job['jobId'])['poolId'] for job in moved}, {2, 3})
        self.assertEqual({job['jobId'] for job in self.pool.list_jobs()}, {job['jobId'] for job in jobs})
        self.assertTrue(all(any(event['step'] == 'routing' for event in self.pool.get_job(job['jobId'])['events'])
                            for job in moved))

    def test_controlled_rebalance_keeps_terminal_history_in_source_but_moves_current_queue(self):
        source = self.pool.pools[0]
        source.put_item(401, 'site', 0, {'title': 'history then queued'})
        historical = source.queue_jobs([401], 7, 'US')[0]
        source.update_job(historical['jobId'], 'complete', 'done', 'finished earlier')
        queued = source.queue_jobs([401], 8, 'US')[0]
        for detail in (402, 403):
            source.put_item(detail, 'site', 0, {'title': str(detail)})
            source.queue_jobs([detail], 8, 'US')

        result = self.pool.rebalance_plain_queued_jobs()
        self.assertEqual(result['movedJobs'], 2)
        self.assertEqual(self.pool.get_job(historical['jobId'])['poolId'], 1)
        self.assertIn(self.pool.get_job(queued['jobId'])['poolId'], {2, 3})
        self.assertEqual(self.pool.get_item(401)['poolId'], self.pool.get_job(queued['jobId'])['poolId'])


class AccountLaneTests(unittest.TestCase):
    def test_account_backoff_and_publish_locks_are_independent(self):
        runtime = AccountRuntime()
        first, second = runtime.lane(1), runtime.lane(2)
        first.backoff(60)
        self.assertEqual(second.last_request, 0)
        first.publish_lock.acquire()
        self.assertTrue(second.publish_lock.acquire(blocking=False))
        second.publish_lock.release()
        first.publish_lock.release()
        self.assertIs(runtime.lane(1), first)

    def test_blocked_imports_do_not_occupy_other_account_executor(self):
        runtime = AccountRuntime()
        release = threading.Event()
        try:
            futures = [runtime.lane(1).imports.submit(release.wait, 5) for _ in range(6)]
            self.assertEqual(runtime.lane(2).imports.submit(lambda: 'ok').result(timeout=1), 'ok')
        finally:
            release.set()
            for lane in runtime.lanes.values():
                lane.imports.shutdown(wait=True)
