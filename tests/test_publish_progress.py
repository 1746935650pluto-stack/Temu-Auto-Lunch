import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from automation_pool import AutomationPool


class PublishProgressTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'pool.sqlite3'
        self.pool = AutomationPool(self.path)
        # These tests assert the user-selected batch interval. Request-rate
        # pacing is covered separately; a mocked sleep does not advance it.
        for name, value in (('_miao_next_request_at', 0.0), ('MIAOSHOU_REQUEST_MIN_INTERVAL_SECONDS', 0.0)):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ready(self, detail):
        job_id = f'{detail:032x}'
        self.pool.put_item(detail, 'site', 0, {'title': '商品'})
        self.pool.start_job(job_id, detail, 456, 'US', publish_after_save=False)
        self.pool.update_job(job_id, 'save', 'done', '已保存')
        self.pool.update_job(job_id, 'publish', 'ready_publish', '待发布')
        return job_id

    def test_progress_persists_counts_separately_and_survives_history_deletion(self):
        self.assertIsNone(self.pool.publish_progress())
        ids = [self.ready(detail) for detail in (1, 2, 3)]
        batch = self.pool.begin_publish_batch(ids, 3)
        self.assertEqual(self.pool.publish_progress()['total'], 3)
        first = self.pool.start_next_ready_publish_job(batch)
        self.assertEqual(self.pool.publish_progress()['currentDetailId'], first['detailId'])
        self.pool.update_job(first['jobId'], 'publish', 'done', '妙手接受')
        self.pool.update_job(first['jobId'], 'complete', 'done', '完成')
        second = self.pool.start_next_ready_publish_job(batch)
        self.pool.update_job(second['jobId'], 'publish', 'needs_review', '响应不确定')
        self.pool.remove_publish_queue_jobs([ids[2]])
        self.pool.finish_publish_batch(batch)
        result = self.pool.publish_progress()
        self.assertEqual((result['submitted'], result['needsReview'], result['removed'], result['remaining']), (1, 1, 1, 0))
        self.assertEqual(result['handled'], 3)
        self.assertEqual(result['status'], 'completed')
        self.pool.delete_job_record(first['jobId'])
        self.assertEqual(AutomationPool(self.path).publish_progress(), result)
        # Retrying a failed save must not rewrite this batch's outcome.
        self.pool.retry_job(second['jobId'])
        self.pool.update_job(second['jobId'], 'publish', 'ready_publish', '再次保存')
        self.assertEqual(self.pool.publish_progress(), result)

    def test_restart_marks_uncertain_submission_and_keeps_remaining_held(self):
        ids = [self.ready(detail) for detail in (1, 2)]
        batch = self.pool.begin_publish_batch(ids, 3)
        self.pool.start_next_ready_publish_job(batch)
        self.pool.recover_interrupted_jobs()
        result = self.pool.publish_progress()
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['needsReview'], 1)
        self.assertEqual(result['remaining'], 1)
        self.assertIsNone(self.pool.start_next_ready_publish_job(batch))
        self.assertEqual(self.pool.get_job(ids[1])['status'], 'ready_publish')

    def test_worker_publishes_only_released_batch_not_new_arrivals(self):
        first = self.ready(1)
        batch = self.pool.begin_publish_batch([first], 3)
        later = self.ready(2)
        vendor = Mock(return_value={'result': 'success'})
        self.assertTrue(server._automation_publish_lock.acquire(blocking=False))
        with patch.object(server.time, 'sleep') as sleep:
            server.run_ready_publish_jobs(self.pool, vendor, 3, batch)
        self.assertFalse(server._automation_publish_lock.locked())
        vendor.assert_called_once()
        self.assertEqual(vendor.call_args.args[1]['detailIds'], [1])
        sleep.assert_not_called()
        self.assertEqual(self.pool.get_job(later)['status'], 'ready_publish')
        self.assertEqual(self.pool.publish_progress()['submitted'], 1)

    def test_running_batch_interval_can_change_without_recreating_it(self):
        first = self.ready(1)
        batch = self.pool.begin_publish_batch([first], 3)
        self.assertEqual(self.pool.running_publish_interval(batch), 3)
        self.assertEqual(self.pool.update_running_publish_intervals(17), [batch])
        self.assertEqual(self.pool.running_publish_interval(batch), 17)
        self.pool.finish_publish_batch(batch)
        self.assertEqual(self.pool.update_running_publish_intervals(8), [])

    def test_publish_can_start_while_another_product_is_being_edited(self):
        publish_job = self.ready(1)
        self.pool.put_item(2, 'site', 0, {'title': '正在编辑的商品'})
        self.pool.queue_jobs([2], 456, 'US')
        editing_job = self.pool.start_next_queued_job()
        self.assertEqual(editing_job['status'], 'running')
        batch = self.pool.begin_publish_batch([publish_job], 3)
        released = self.pool.start_next_ready_publish_job(batch)
        self.assertEqual(released['jobId'], publish_job)
        self.assertEqual(self.pool.get_job(editing_job['jobId'])['status'], 'running')

    def test_worker_failure_is_counted_and_does_not_stop_remaining_submissions(self):
        ids = [self.ready(detail) for detail in (1, 2)]
        batch = self.pool.begin_publish_batch(ids, 3)
        vendor = Mock(side_effect=[RuntimeError('unknown'), {'result': 'success'}])
        self.assertTrue(server._automation_publish_lock.acquire(blocking=False))
        with patch.object(server.time, 'sleep') as sleep:
            server.run_ready_publish_jobs(self.pool, vendor, 3, batch)
        self.assertEqual(vendor.call_count, 2)
        sleep.assert_called_once_with(3)
        result = self.pool.publish_progress()
        self.assertEqual((result['submitted'], result['needsReview'], result['remaining']), (1, 1, 0))

    def test_empty_or_changed_queue_rejected_and_duplicate_batch_prevented(self):
        first = self.ready(1)
        for ids in ([], [first, 'f' * 32]):
            with self.assertRaises(ValueError):
                self.pool.begin_publish_batch(ids, 3)
        batch = self.pool.begin_publish_batch([first], 3)
        with self.assertRaises(ValueError):
            self.pool.begin_publish_batch([first], 3)
        self.pool.finish_publish_batch(batch)
        self.assertEqual(self.pool.publish_progress()['status'], 'interrupted')
        self.assertEqual(self.pool.publish_progress()['remaining'], 1)
