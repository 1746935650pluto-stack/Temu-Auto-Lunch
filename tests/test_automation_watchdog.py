from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import time

from automation_pool import AutomationPool
from automation_watchdog import AutomationWatchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pool = AutomationPool(self.root / 'pool.sqlite3')
        self.service = SimpleNamespace(dispatch_lock=Lock(), run_jobs=Mock(),
                                       accounts=lambda: [{'id': 1, 'configured': True, 'blocked': False}])
        self.backend = SimpleNamespace(ROOT=self.root, os=os, ACCOUNT_MUTATIONS_ACTIVE=0,
            AUTOMATION_EDIT_SCHEDULER_LOCK=Lock(), _automation_publish_lock=Lock(),
            shared_automation=lambda: self.service, automation_pool=lambda _: self.pool)
        self.watch = AutomationWatchdog(self.backend, clock=lambda: time.time() + 600)
        mocked = patch.dict(os.environ, {'APP_MODE': 'live'})
        mocked.start(); self.addCleanup(mocked.stop)

    def test_default_checks_once_per_minute_without_changing_recovery_grace(self):
        self.assertEqual(self.watch.interval, 60)
        self.assertEqual(self.watch.grace, 120)
        self.watch.stop_event = Mock()
        self.watch.stop_event.is_set.side_effect = [False, True]
        with patch.object(self.watch, 'tick') as tick:
            self.watch.run()
        tick.assert_called_once_with()
        self.watch.stop_event.wait.assert_called_once_with(60)

    def queue(self, detail=1):
        self.pool.put_item(detail, 'site', 0, {'title': str(detail)})
        return self.pool.queue_jobs([detail], 123, 'US')[0]['jobId']

    def test_claim_hydration_failure_rolls_back_status_and_events(self):
        first, second = self.queue(1), self.queue(2)
        read = self.pool._read_job
        def fail_second(connection, job):
            if job == second:
                raise sqlite3.OperationalError('fixture')
            return read(connection, job)
        before = self.pool.list_jobs()
        with patch.object(self.pool, '_read_job', side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.pool.start_next_queued_jobs(8)
        self.assertEqual(self.pool.list_jobs(), before)

    def test_only_never_started_saves_requeue_uncertain_work_goes_to_review(self):
        safe, uncertain, publish = self.queue(1), self.queue(2), self.queue(3)
        self.pool.start_next_queued_jobs(8)
        self.pool.update_job(uncertain, 'claim', 'running', 'possible remote operation')
        self.pool.update_job(publish, 'publish', 'running', 'manual publish')
        result = self.pool.recover_orphaned_edit_jobs('9999')
        self.assertEqual(result, {'requeued': 1, 'needsReview': 1})
        self.assertEqual(self.pool.get_job(safe)['status'], 'queued')
        self.assertEqual(self.pool.get_job(uncertain)['status'], 'needs_review')
        self.assertEqual(self.pool.get_job(publish)['status'], 'running')
        self.assertEqual(self.pool.recover_orphaned_edit_jobs('9999'), {'requeued': 0, 'needsReview': 0})

    def test_watchdog_recovers_idle_queue_once_without_publishing_or_remote_calls(self):
        job = self.queue()
        self.pool.start_next_queued_jobs(8)
        with patch('automation_watchdog.Thread') as thread:
            self.watch.tick()
            self.watch.tick()
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
        self.assertEqual(self.pool.get_job(job)['status'], 'queued')
        self.assertEqual(self.watch.snapshot()['requeued'], 1)
        self.service.run_jobs.assert_not_called()
        self.watch._dispatch()
        self.service.run_jobs.assert_called_once_with(8)

    def test_watchdog_restarts_safe_shared_queued_transfers(self):
        self.service.queued_transfer_count = Mock(return_value=3)
        self.service.dispatch = Mock()
        with patch('automation_watchdog.Thread') as thread:
            self.watch.tick()
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['args'], (True,))
        self.watch._dispatch(True)
        self.service.dispatch.assert_called_once_with()

    def test_live_workers_demo_recent_jobs_and_publish_lock_are_not_recovered(self):
        job = self.queue()
        self.pool.start_next_queued_jobs(8)
        with patch('automation_watchdog.Thread') as thread:
            for lock in (self.backend.AUTOMATION_EDIT_SCHEDULER_LOCK, self.backend._automation_publish_lock,
                         self.service.dispatch_lock):
                with lock:
                    self.watch.tick()
            with patch.dict(os.environ, {'APP_MODE': 'demo'}):
                self.watch.tick()
            self.watch.clock = time.time
            self.watch.tick()
            thread.assert_not_called()
        self.assertEqual(self.pool.get_job(job)['status'], 'running')

    def test_error_does_not_disable_later_checks_or_leak_exception_text(self):
        self.queue()
        with patch.object(self.pool, 'recover_orphaned_edit_jobs', side_effect=sqlite3.OperationalError('secret')):
            with self.assertLogs('automation_watchdog', level='ERROR') as log:
                self.watch.tick()
            self.assertNotIn('secret', str(log.output))
        self.assertEqual(self.watch.snapshot()['status'], 'check_failed')
        with patch('automation_watchdog.Thread'):
            self.watch.tick()
        self.assertEqual(self.watch.snapshot()['status'], 'recovering')

    def test_blocked_accounts_are_never_resumed(self):
        job = self.queue()
        self.pool.start_next_queued_jobs(8)
        self.service.accounts = lambda: [{'id': 1, 'configured': True, 'blocked': True}]
        with patch('automation_watchdog.Thread') as thread:
            self.watch.tick()
            thread.assert_not_called()
        self.assertEqual(self.pool.get_job(job)['status'], 'running')

    def test_recent_primary_does_not_hide_orphan_in_third_pool(self):
        from partitioned_pool import PartitionedPool
        second = AutomationPool(self.root / 'second.sqlite3')
        third = AutomationPool(self.root / 'third.sqlite3')
        routed = PartitionedPool(self.pool, second, third, 1)
        self.queue(1)
        third.put_item(9, 'site', 0, {'title': 'orphan'})
        job = third.queue_jobs([9], 123, 'US')[0]['jobId']
        third.start_next_queued_jobs(1)
        with closing(third._connect()) as db, db:
            db.execute("UPDATE automation_jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (job,))
        self.backend.automation_pool = lambda _: routed
        self.watch.clock = time.time
        with patch('automation_watchdog.Thread'):
            self.watch.tick()
        self.assertEqual(third.get_job(job)['status'], 'queued')
        self.assertEqual(self.watch.snapshot()['requeued'], 1)


if __name__ == '__main__':
    unittest.main()
