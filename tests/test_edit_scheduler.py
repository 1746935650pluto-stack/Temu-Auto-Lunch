from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from automation_pool import AutomationPool
from partitioned_pool import PartitionedPool
from shared_automation import SharedAutomation
from edit_scheduler import admission_order, linked_account_edit_limit


class EditSchedulerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.pools = {i: AutomationPool(root / f'{i}.sqlite3') for i in range(1, 7)}
        self.b = SimpleNamespace(AUTOMATION_EDIT_SCHEDULER_LOCK=threading.Lock(),
                                 automation_pool=self.pools.__getitem__, DeepSeekClient=lambda _: None)
        worker = SimpleNamespace(_vendor=None, _get_item=None, _readiness=None, _get_source_item=None)
        self.service = SimpleNamespace(b=self.b, is_paused=lambda: False, job_creation_lock=threading.Lock(),
            accounts=lambda: [{'id': i, 'configured': True, 'blocked': False} for i in self.pools],
            assert_job=lambda *_: None, worker=lambda _: worker)

    def queue(self, account, count):
        pool = self.pools[account]
        for i in range(count):
            pool.put_item(i + 1, 'site', 0, {'title': str(i)})
        return pool.queue_jobs(list(range(1, count + 1)), 123, 'US')

    def test_idle_accounts_do_not_reserve_slots_and_newcomer_gets_next_slot(self):
        self.queue(1, 12)
        gate = threading.Semaphore(0)
        filled, newcomer = threading.Event(), threading.Event()
        lock = threading.Lock()
        count = 0
        errors = []
        def run(pool, job, *_args, **kwargs):
            nonlocal count
            self.assertTrue(kwargs['defer_publish'])
            if pool is self.pools[1]:
                with lock:
                    count += 1
                    if count == 4:
                        filled.set()
                gate.acquire(timeout=5)
            else:
                newcomer.set()
            pool.update_job(job, 'save', 'ready_publish', 'test')
        self.b.run_automation_job = run
        def schedule():
            try:
                SharedAutomation.run_jobs(self.service, 8)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=schedule)
        thread.start()
        try:
            self.assertTrue(filled.wait(3), 'the default two-route request lane must admit four edit workers')
            self.queue(2, 1)
            gate.release()
            self.assertTrue(newcomer.wait(3), 'new account must get a released slot')
        finally:
            for _ in range(20):
                gate.release()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)

    def test_rotation_serves_less_loaded_accounts_first(self):
        accounts = [{'id': i} for i in (1, 2, 3)]
        self.assertEqual([a['id'] for a in admission_order(accounts, {1: 8, 2: 1, 3: 1}, 2)], [3, 2, 1])

    def test_local_edit_cap_tracks_adaptive_remote_limit_with_small_buffer(self):
        self.assertEqual([linked_account_edit_limit(value) for value in (1, 2, 3, 4, 6)], [2, 4, 6, 8, 8])
        self.assertEqual(linked_account_edit_limit('bad'), 8)

    def test_running_publish_does_not_consume_edit_capacity_across_partitions(self):
        pool = PartitionedPool(self.pools[1], self.pools[2], self.pools[3], 1)
        for i in range(1, 7):
            pool.put_item(i, 'site', 0, {'title': str(i)})
        jobs = pool.queue_jobs(list(range(1, 7)), 123, 'US')
        pool.update_job(jobs[0]['jobId'], 'publish', 'running', 'test')
        started = pool.start_next_queued_jobs(3)
        self.assertEqual(len(started), 3)


if __name__ == '__main__':
    unittest.main()
