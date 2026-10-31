import unittest
import threading
from unittest.mock import Mock, patch

import server
import shared_automation
import test_shared_automation as fixtures


class PartitionedPublishingTests(unittest.TestCase):
    setUp = fixtures.SharedAutomationTests.setUp
    request = fixtures.SharedAutomationTests.request
    stop_server = fixtures.SharedAutomationTests.stop_server

    def ready(self, account, detail=123):
        pool = server.automation_pool(account)
        if detail != 123:
            pool.put_item(detail, 'site', 0, self.remote[(account, 123)])
        job = pool.queue_jobs([detail], 7, 'US')[0]
        pool.update_job(job['jobId'], 'save', 'ready_publish', '等待发布')
        return job

    def ready_in_pool(self, partition, detail):
        physical = server.automation_pool(1).pools[partition - 1]
        physical.put_item(detail, 'site', 0, self.remote[(1, 123)])
        job = physical.queue_jobs([detail], 7, 'US')[0]
        physical.update_job(job['jobId'], 'save', 'ready_publish', 'saved')
        return job

    def run_background(self, refs, publish, wait_interval):
        threads = []
        def factory(**kwargs):
            thread = threading.Thread(**kwargs)
            threads.append(thread)
            return thread
        patches = [patch.object(shared_automation, 'Thread', factory),
                   patch.object(server, 'publish_automation_job', publish),
                   patch.object(server.automation_pool(1), 'wait_for_running_publish_interval', wait_interval)]
        for mocked in patches:
            mocked.start()
        def finish():
            for thread in threads:
                thread.join(10)
            for mocked in reversed(patches):
                mocked.stop()
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.service.publish({'confirmation': 'PUBLISH_SHARED_READY_JOBS',
            'jobs': [{'accountId': 1, 'jobId': job['jobId']} for job in refs], 'intervalSeconds': 1})
        return finish

    def test_three_pools_can_have_submissions_in_flight_at_the_same_time(self):
        jobs = [self.ready_in_pool(partition, 500 + partition) for partition in (1, 2, 3)]
        started = {partition: threading.Event() for partition in (1, 2, 3)}
        release = threading.Event()
        def publish(pool, job_id, _vendor):
            started[pool._job_pool(job_id).pool_id].set()
            release.wait(8)
            pool.update_job(job_id, 'complete', 'done', 'accepted')
        finish = self.run_background(jobs, publish, lambda _: True)
        try:
            self.assertTrue(all(event.wait(3) for event in started.values()))
        finally:
            release.set()
            finish()
        self.assertFalse(server.RUNTIME.lane(1).publish_lock.locked())

    def test_one_pool_cooldown_does_not_sleep_other_pools(self):
        jobs = [self.ready_in_pool(partition, 600 + partition) for partition in (1, 2)]
        cooling, second_done, release = threading.Event(), threading.Event(), threading.Event()
        def publish(pool, job_id, _vendor):
            if pool._job_pool(job_id).pool_id == 2:
                cooling.wait(3)
                second_done.set()
            pool.update_job(job_id, 'complete', 'done', 'accepted')
        def cooldown(batch):
            if batch.startswith('1:'):
                cooling.set()
                release.wait(8)
            return True
        finish = self.run_background(jobs, publish, cooldown)
        try:
            self.assertTrue(cooling.wait(3))
            self.assertTrue(second_done.wait(3))
            self.assertFalse(release.is_set())
        finally:
            release.set()
            finish()

    def test_uncertain_result_is_not_replayed_and_unselected_jobs_stay_ready(self):
        first, second, unselected = [self.ready_in_pool(p, 700 + p) for p in (1, 2, 3)]
        seen = []
        def publish(pool, job_id, _vendor):
            seen.append(job_id)
            if job_id == first['jobId']:
                raise TimeoutError('unknown remote outcome')
            pool.update_job(job_id, 'complete', 'done', 'accepted')
        finish = self.run_background([first, second], publish, lambda _: True)
        finish()
        pool = server.automation_pool(1)
        self.assertCountEqual(seen, [first['jobId'], second['jobId']])
        self.assertEqual(pool.get_job(first['jobId'])['status'], 'needs_review')
        self.assertEqual(pool.get_job(unselected['jobId'])['status'], 'ready_publish')

    def test_new_pool_appended_during_active_submission_starts_without_waiting(self):
        first, second = [self.ready_in_pool(p, 750 + p) for p in (1, 2)]
        first_started, second_started, release = (threading.Event() for _ in range(3))
        seen = []
        def publish(pool, job_id, _vendor):
            seen.append(job_id)
            if job_id == first['jobId']:
                first_started.set()
                release.wait(8)
            else:
                second_started.set()
            pool.update_job(job_id, 'complete', 'done', 'accepted')
        finish = self.run_background([first], publish, lambda _: True)
        try:
            self.assertTrue(first_started.wait(3))
            result = self.service.publish({'confirmation': 'PUBLISH_SHARED_READY_JOBS',
                'jobs': [{'accountId': 1, 'jobId': second['jobId']}], 'intervalSeconds': 2})
            self.assertTrue(result['joinedExisting'])
            self.assertTrue(second_started.wait(3))
            self.assertFalse(release.is_set())
        finally:
            release.set()
            finish()
        self.assertCountEqual(seen, [first['jobId'], second['jobId']])

    def test_one_physical_pool_never_starts_two_submissions_concurrently(self):
        jobs = [self.ready_in_pool(1, detail) for detail in (801, 802, 803)]
        active, maximum, seen = 0, 0, []
        lock = threading.Lock()
        def publish(pool, job_id, _vendor):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            pool.update_job(job_id, 'complete', 'done', 'accepted')
            with lock:
                seen.append(job_id)
                active -= 1
        finish = self.run_background(jobs, publish, lambda _: True)
        finish()
        self.assertEqual(maximum, 1)
        self.assertCountEqual(seen, [job['jobId'] for job in jobs])

    def test_legacy_start_drains_shared_append_into_other_pool(self):
        first = self.ready(1)
        with patch.object(server, 'Thread') as thread:
            status, body = self.request('POST', '/api/automation/publish-ready', {
                'confirmation': 'PUBLISH_READY_AUTOMATION_JOBS', 'intervalSeconds': 1})
        self.assertEqual(status, 202, body)
        self.assertEqual(thread.call_args.kwargs['target'], self.service._publish_worker)
        second = self.ready(1, 125)
        with patch.object(shared_automation, 'Thread') as duplicate:
            result = self.service.publish({'confirmation': 'PUBLISH_SHARED_READY_JOBS',
                'jobs': [{'accountId': 1, 'jobId': second['jobId']}], 'intervalSeconds': 1})
            duplicate.assert_not_called()
        self.assertTrue(result['joinedExisting'])
        seen = []
        def publish(pool, job_id, vendor):
            seen.append(job_id)
            pool.update_job(job_id, 'publish', 'submitted', '已提交')
        with patch.object(server, 'publish_automation_job', side_effect=publish), \
             patch.object(server.automation_pool(1), 'wait_for_running_publish_interval', return_value=True):
            thread.call_args.kwargs['target'](*thread.call_args.kwargs['args'])
        self.assertCountEqual(seen, [first['jobId'], second['jobId']])
        self.assertFalse(server.RUNTIME.lane(1).publish_lock.locked())

    def test_second_worker_start_failure_does_not_unlock_or_interrupt_first_worker(self):
        jobs = [self.ready(account) for account in (1, 2)]
        first_thread, failed_thread = Mock(), Mock()
        failed_thread.start.side_effect = RuntimeError('cannot start')
        with patch.object(shared_automation, 'Thread', side_effect=[first_thread, failed_thread]):
            with self.assertRaisesRegex(RuntimeError, 'cannot start'):
                self.service.publish({'confirmation': 'PUBLISH_SHARED_READY_JOBS',
                    'jobs': [{'accountId': account, 'jobId': job['jobId']}
                             for account, job in zip((1, 2), jobs)], 'intervalSeconds': 1})
        self.assertTrue(server.RUNTIME.lane(1).publish_lock.locked())
        self.assertFalse(server.RUNTIME.lane(2).publish_lock.locked())
        self.assertEqual(server.automation_pool(1).publish_progress()['status'], 'running')
        self.assertEqual(server.automation_pool(2).publish_progress()['status'], 'interrupted')
        server.RUNTIME.lane(1).publish_lock.release()


if __name__ == '__main__':
    unittest.main()
